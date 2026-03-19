import asyncio
import random
import re
import os
import logging
from dotenv import load_dotenv
from core.api_handler import generate_chat_response, summarize_chat_logs, extract_recurring_patterns
from core.memory_queue import ShortTermMemory

# Initializes logging and loads environment variables from the .env file.
logger = logging.getLogger(__name__)
load_dotenv()

# Pulls necessary Discord IDs for logic gating.
BAPT_DISCORD_ID = int(os.getenv('BAPT_DISCORD_ID', 0))
OTHER_BOT_ID = int(os.getenv('OTHER_BOT_ID', 0)) 
OTHER_BOT_REPLY_CAP = 1 

# Global dictionaries to track state across async operations.
active_processing_locks = {}
active_channel_memories = {}
max_messages_in_memory = 20

# Pre-compiled regular expressions for identifying specific target users or names.
REGEX_NAMED = re.compile(r'\b(leepa|leep)\b', re.IGNORECASE)
REGEX_VIP = re.compile(r'\b(hun|sweetie)\b', re.IGNORECASE)

# Baseline probabilities for the bot to organically inject itself into unaddressed conversations.
AMBIENT_PROBABILITIES = {
    "QUICK_BANTER": 0.10,
    "YELLING": 0.20,
    "SHITPOST": 0.15,
    "WALL_OF_TEXT": 0.05,
    "STANDARD": 0.05
}

# Lexicon array used for classifying chaotic conversational patterns.
SHITPOST_KEYWORDS = ['lmao', 'lol', 'bruh', 'based', 'cringe', 'fr fr', 'no cap', 'skill issue', '💀', '🤡', 'cheh', '67']


def get_channel_memory(channel_id: int) -> ShortTermMemory:
    """Retrieves or instantiates an isolated memory queue for a specific Discord channel."""
    if channel_id not in active_channel_memories:
        active_channel_memories[channel_id] = ShortTermMemory(max_size=max_messages_in_memory)  
    return active_channel_memories[channel_id]


async def get_reply_chain_depth(message, bot_user) -> int:
    """Recursively walks back the Discord reply tree to prevent infinite loops between bots."""
    count = 0
    current_msg = message
    
    while current_msg.reference and current_msg.reference.message_id:
        parent_msg = current_msg.reference.resolved
        if parent_msg is None:
            try:
                parent_msg = await current_msg.channel.fetch_message(current_msg.reference.message_id)
            except Exception as e:
                logger.warning(f"Failed to fetch parent message for loop constraint check: {e}")
                break
                
        if parent_msg.author.id in [bot_user.id, OTHER_BOT_ID]:
            count += 1
            current_msg = parent_msg
        else:
            break
            
    return count


async def evaluate_message_context(message, bot_user) -> tuple[str, bool]:
    """
    Evaluates a message across two independent axes: Engagement Level and Conversational Vibe.
    Returns a combined string tag and a boolean dictating whether to trigger the LLM.
    """
    # Infinite loop kill-switch for interacting with other bots.
    if message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0:
        pingpong_depth = await get_reply_chain_depth(message, bot_user)
        if pingpong_depth >= (OTHER_BOT_REPLY_CAP * 2):
            return "IGNORE", False

    raw_content = message.content.strip()
    content_lower = raw_content.lower()
    word_count = len(raw_content.split())
    
    # ---------------------------------------------------------
    # TRACK A: ENGAGEMENT DETECTION
    # ---------------------------------------------------------
    is_mentioned = bot_user in message.mentions
    is_replied_to = message.reference and message.reference.resolved and message.reference.resolved.author == bot_user
    is_named = bool(REGEX_NAMED.search(content_lower))
    is_creator_vip = (message.author.id == BAPT_DISCORD_ID) and bool(REGEX_VIP.search(content_lower))
    
    engagement_level = "AMBIENT"
    if is_mentioned or is_named or is_creator_vip:
        engagement_level = "DIRECT"
    elif is_replied_to:
        engagement_level = "QUOTED"
        
    # ---------------------------------------------------------
    # TRACK B: CLASSIFICATION ROUTING
    # ---------------------------------------------------------
    classification = "STANDARD"
    
    # Ensures yelled text has enough alphabetic characters to count as genuine yelling.
    is_yelling = raw_content.isupper() and len(re.sub(r'[^a-zA-Z]', '', raw_content)) > 5
    is_shitpost = any(word in content_lower for word in SHITPOST_KEYWORDS)
    
    if word_count > 100:
        classification = "WALL_OF_TEXT"
    elif is_yelling:
        classification = "YELLING"
    elif is_shitpost:
        classification = "SHITPOST"
    elif 0 < word_count <= 5:
        classification = "QUICK_BANTER"
        
    # ---------------------------------------------------------
    # PROBABILITY EXECUTION MATRIX
    # ---------------------------------------------------------
    is_rival_bot = (message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0)
    should_trigger = False
    
    if engagement_level in ["DIRECT", "QUOTED"]:
        should_trigger = True
    else:
        # Applies a 50% penalty to probability if the message originated from a rival bot.
        base_prob = AMBIENT_PROBABILITIES.get(classification, 0.05)
        final_prob = base_prob * (0.5 if is_rival_bot else 1.0)
        if random.random() < final_prob:
            should_trigger = True
            
    combined_tag = f"{engagement_level}_{classification}"
    return combined_tag, should_trigger


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
    """Primary pipeline for handling incoming Discord events and routing them to the external AI API."""
    # Interception Matrix logic prevents dual-bot spam by locking threads.
    if message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0 and message.reference:
        target_id = message.reference.message_id
        if target_id in active_processing_locks:
            if active_processing_locks[target_id] != "IMMUNE":
                active_processing_locks[target_id] = True
                logger.info(f"Concurrent response detected for message {target_id}. Aborting execution.")

    local_memory = get_channel_memory(message.channel.id)
    local_memory.add_message(message.author.display_name, message.content)
    
    server_id = str(message.guild.id) if message.guild else "DM"
    
    overflow_text = local_memory.extract_overflow_for_summary()
    if overflow_text:
        asyncio.create_task(background_summarize(local_memory, overflow_text))
        
    if local_memory.total_message_count % 50 == 0 and local_memory.running_summary:
        asyncio.create_task(extract_recurring_patterns(server_id, local_memory.running_summary))
    
    combined_tag, should_trigger = await evaluate_message_context(message, bot_user)
    
    if not should_trigger:
        return ""
        
    context_block = local_memory.get_context_block()
    named_target_message = f"{message.author.display_name}: {message.content}"
    
    # Direct engagement automatically grants lock immunity.
    if combined_tag.startswith("DIRECT") or combined_tag.startswith("QUOTED"):
        active_processing_locks[message.id] = "IMMUNE"
    else:
        active_processing_locks[message.id] = False
    
    response_data = await generate_chat_response(context_block, combined_tag, named_target_message, server_id)
    
    if active_processing_locks.get(message.id) is True:
        del active_processing_locks[message.id]
        return ""
        
    if message.id in active_processing_locks:
        del active_processing_locks[message.id]
    
    reply_text = response_data.get("response", "").strip()
    reaction_emoji = response_data.get("reaction_emoji", "").strip()
    
    if reaction_emoji:
        try:
            await message.add_reaction(reaction_emoji)
        except Exception as e:
            logger.error(f"Discord API failure on add_reaction: {e}")
    
    if reply_text:
        try:
            await message.reply(reply_text)
            local_memory.add_message("Leepa", reply_text)
        except Exception as e:
            logger.error(f"Discord API failure on message reply: {e}")
            
    return ""