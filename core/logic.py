import asyncio
import json
import random
import logging
import os
from dotenv import load_dotenv
from core.api_handler import generate_chat_response, summarize_chat_logs
from core.memory_queue import ShortTermMemory
from core.image_analysis import analyze_image

logger = logging.getLogger(__name__)
load_dotenv()

# Global dictionaries to track state across async operations.
active_processing_locks = {}
active_channel_memories = {}

def get_channel_memory(channel_id: int) -> ShortTermMemory:
    """Retrieves or instantiates an isolated memory queue for a specific Discord channel."""
    if channel_id not in active_channel_memories:
        active_channel_memories[channel_id] = ShortTermMemory()  
    return active_channel_memories[channel_id]

async def evaluate_message_gate(message, bot_user) -> tuple[str, bool]:
    """
    A high-speed probabilistic gate. No semantic reading is done here.
    Determines ONLY if the LLM should wake up and process the context.
    """
    # Absolute kill-switch: Do not process other bots' messages, preventing infinite loops instantly.
    if message.author.bot:
        return "IGNORE", False

    is_mentioned = bot_user in message.mentions
    is_replied_to = message.reference and message.reference.resolved and message.reference.resolved.author == bot_user
    
    # We maintain a lightweight name-check to ensure she always answers when spoken to organically.
    content_lower = message.content.lower()
    is_named = "leepa" in content_lower or "leep" in content_lower

    if is_mentioned or is_replied_to or is_named:
        return "DIRECT", True
    
    # Ambient messages get a flat 5% chance to wake the LLM. The LLM decides what to do next.
    if random.random() < 0.05:
        return "AMBIENT", True
        
    return "IGNORED", False

async def background_summarize(local_memory, extracted_text: str):
    """Offloads the dense memory compression task to a non-blocking background thread."""
    try:
        new_summary = await summarize_chat_logs(extracted_text, local_memory.running_summary)
        if new_summary:
            local_memory.update_running_summary(new_summary)
            logger.info(f"Memory compressed. Active summary length: {len(new_summary)} characters.")
        else:
            local_memory.is_summarizing = False 
    except Exception as e:
        logger.error(f"Background memory compression failed: {e}")
        local_memory.is_summarizing = False

async def process_message(message, bot_user) -> str:
    """Primary pipeline for handling incoming Discord events and routing them to the API."""
    
    # --- VISUAL INTERCEPT BLOCK ---
    content_payload = message.content
    if message.attachments: 
        valid_mime_types = {'image/png', 'image/jpeg', 'image/webp'} 
        for attachment in message.attachments:
            if attachment.content_type in valid_mime_types:
                image_desc = await analyze_image(attachment.url)
                content_payload = f"{content_payload}\n{image_desc}".strip()
    # -----------------------------------

    local_memory = get_channel_memory(message.channel.id)
    local_memory.add_message(message.author.display_name, content_payload)
    
    server_id = str(message.guild.id) if message.guild else "DM"
    
    overflow_text = local_memory.extract_overflow_for_summary()
    if overflow_text:
        asyncio.create_task(background_summarize(local_memory, overflow_text))

    engagement_level, should_trigger = await evaluate_message_gate(message, bot_user)
    
    if not should_trigger:
        return ""
        
    context_block = local_memory.get_context_block()
    named_target_message = f"{message.author.display_name}: {message.content}"
    
    # Direct engagement automatically grants lock immunity.
    if engagement_level == "DIRECT":
        active_processing_locks[message.id] = "IMMUNE"
    else:
        active_processing_locks[message.id] = False
    
    response_data = await generate_chat_response(context_block, engagement_level, named_target_message, server_id)
    print(f"\nRAW JSON OUTPUT:\n{json.dumps(response_data, indent=2)}\n")
    
    # If a parallel operation flagged an interception, abort.
    if active_processing_locks.get(message.id) is True:
        del active_processing_locks[message.id]
        return ""
        
    if message.id in active_processing_locks:
        del active_processing_locks[message.id]
    
    reply_text = response_data.get("response", "").strip()
    reaction_emoji = response_data.get("reaction_emoji", "").strip()
    internal_mood = response_data.get("internal_mood", "neutral").strip()
    thinking_block = response_data.get("thinking_block", "").strip()
    
    # 1. Execute physical Discord actions
    if reaction_emoji:
        try:
            await message.add_reaction(reaction_emoji)
        except Exception as e:
            logger.error(f"Discord API failure on add_reaction: {e}")
    
    if reply_text:
        try:
            await message.reply(reply_text)
        except Exception as e:
            logger.error(f"Discord API failure on message reply: {e}")

    # 2. Construct the dense internal state string for the STM
    state_parts = []
    if thinking_block:
        state_parts.append(f"Thought: {thinking_block}")
    if internal_mood:
        state_parts.append(f"Mood: {internal_mood}")
    if reaction_emoji:
        state_parts.append(f"Emoji: {reaction_emoji}")
        
    state_tag = f"[{' | '.join(state_parts)}]\n" if state_parts else ""
    
    # 3. Log to memory, enforcing object permanence for silences
    if reply_text:
        memory_log = f"{state_tag}{reply_text}"
    else:
        memory_log = f"{state_tag}(Silence)"
        
    local_memory.add_message("Leepa", memory_log)
            
    return ""