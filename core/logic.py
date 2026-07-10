import asyncio
import json
import random
import re
import os
import logging
import time
from dotenv import load_dotenv
from core.api_handler import generate_chat_response, summarize_chat_logs
from core.memory_queue import ShortTermMemory
from core.image_analysis import analyze_image

# Initializes logging and loads environment variables from the .env file.
logger = logging.getLogger(__name__)
load_dotenv()

# Pulls necessary Discord IDs for logic gating.
BAPT_DISCORD_ID = int(os.getenv('BAPT_DISCORD_ID', 0))
OTHER_BOT_ID = int(os.getenv('OTHER_BOT_ID', 0))

# Global dictionaries to track state across async operations.
active_processing_locks = {}
active_channel_memories = {}

# Pre-compiled regular expressions for identifying specific target users or names.
# IGNORECASE handles casing; searches run directly on the raw message content.
REGEX_NAMED = re.compile(r'\b(leepa|leep)\b', re.IGNORECASE)
REGEX_VIP = re.compile(r'\b(hun|sweetie)\b', re.IGNORECASE)

# Image MIME types accepted by the Visual Pre-Processor.
VALID_IMAGE_TYPES = {'image/png', 'image/jpeg'}


def get_channel_memory(channel_id: int) -> ShortTermMemory:
    """Retrieves or instantiates an isolated memory queue for a specific Discord channel."""
    if channel_id not in active_channel_memories:
        active_channel_memories[channel_id] = ShortTermMemory()
    return active_channel_memories[channel_id]


def get_referenced_author_id(message) -> int | None:
    """
    Safely resolves the author ID of the message being replied to.
    Returns None if there is no reference, if the referenced message is not in
    the local cache (discord.py only populates `resolved` for cached messages),
    or if the referenced message was deleted (DeletedReferencedMessage has no author).
    """
    ref = message.reference
    if ref is None or ref.resolved is None:
        return None
    author = getattr(ref.resolved, "author", None)
    return author.id if author is not None else None


def evaluate_message_context(message, bot_user) -> tuple[str, bool]:
    """
    Evaluates a message's Engagement Level.
    Returns a string tag and a boolean dictating whether to trigger the LLM.
    """
    parent_author_id = get_referenced_author_id(message)

    # Immediate parent check to prevent bot loops without recursive API fetching.
    if message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0:
        if parent_author_id in (bot_user.id, OTHER_BOT_ID):
            return "IGNORE", False

    content = message.content

    # ---------------------------------------------------------
    # TRACK A: ENGAGEMENT DETECTION
    # ---------------------------------------------------------
    is_mentioned = bot_user in message.mentions
    is_replied_to = parent_author_id == bot_user.id
    is_named = bool(REGEX_NAMED.search(content))
    is_creator_vip = (message.author.id == BAPT_DISCORD_ID) and bool(REGEX_VIP.search(content))

    engagement_level = "AMBIENT"
    if is_mentioned or is_named or is_creator_vip:
        engagement_level = "DIRECT"
    elif is_replied_to:
        engagement_level = "QUOTED"

    # ---------------------------------------------------------
    # PROBABILITY EXECUTION MATRIX
    # ---------------------------------------------------------
    is_rival_bot = (message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0)
    should_trigger = False

    if engagement_level in ["DIRECT", "QUOTED"]:
        should_trigger = True
    else:
        # Applies a 30% penalty to probability if the message originated from a rival bot.
        base_prob = 0.05
        final_prob = base_prob * (0.3 if is_rival_bot else 1.0)
        if random.random() < final_prob:
            should_trigger = True

    return engagement_level, should_trigger


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


async def process_message(message, bot_user) -> None:
    """Primary pipeline for handling incoming Discord events and routing them to the external AI API."""
    current_time = time.time()

    # ---------------------------------------------------------
    # STATE CLEANUP (GHOST LOCKS)
    # ---------------------------------------------------------
    expired_keys = [k for k, v in active_processing_locks.items() if current_time > v.get("expires", 0)]
    for k in expired_keys:
        del active_processing_locks[k]

    # If the rival bot replies to a message Leepa is still processing, flip its kill-switch.
    if message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0 and message.reference:
        target_id = message.reference.message_id
        lock_data = active_processing_locks.get(target_id)
        if lock_data and lock_data["status"] != "IMMUNE":
            active_processing_locks[target_id]["status"] = True
            logger.info(f"Concurrent response detected for message {target_id}. Aborting execution.")

    # --- VISUAL INTERCEPT BLOCK ---
    # Runs before the trigger decision on purpose: image descriptions must enter
    # the STM even for ambient messages, so Leepa retains visual context later.
    content_payload = message.content
    if message.attachments:
        for attachment in message.attachments:
            if attachment.content_type in VALID_IMAGE_TYPES:
                image_desc = await analyze_image(attachment.url)
                content_payload = f"{content_payload}\n{image_desc}".strip()
    # -----------------------------------

    local_memory = get_channel_memory(message.channel.id)
    local_memory.add_message(message.author.display_name, content_payload)

    overflow_text = local_memory.extract_overflow_for_summary()
    if overflow_text:
        asyncio.create_task(background_summarize(local_memory, overflow_text))

    engagement_level, should_trigger = evaluate_message_context(message, bot_user)

    if not should_trigger:
        return

    context_block = local_memory.get_context_block()
    named_target_message = f"{message.author.display_name}: {content_payload}"

    # Direct engagement automatically grants lock immunity. Locks expire after 60 seconds.
    lock_status = "IMMUNE" if engagement_level in ["DIRECT", "QUOTED"] else False
    active_processing_locks[message.id] = {"status": lock_status, "expires": current_time + 60.0}

    response_data = await generate_chat_response(context_block, engagement_level, named_target_message)
    print(f"\nRAW JSON OUTPUT:\n{json.dumps(response_data, indent=2)}\n")

    # Matrix Kill-Switch Check. pop() removes the lock in a single step regardless of outcome.
    if active_processing_locks.pop(message.id, {}).get("status") is True:
        return

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

    # 2. Construct the dense internal state string for the STM.
    # NOTE: Infrastructure moods (rate_limit, timeout, etc.) are intentionally
    # logged into memory. This lets Leepa acknowledge her own outages in-character
    # and doubles as a live debugging trace on the VM console.
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