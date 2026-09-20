import asyncio
import json
import random
import re
import os
import logging
import time
from dotenv import load_dotenv
from core import prompts
from core.api_handler import generate_chat_response, summarize_chat_logs
from core.bot_registry import RAKUN_ID, SLOPPY_ID
from core.discord_context import collect_discord_context
from core.memory_queue import ShortTermMemory
from core.image_analysis import analyze_image
from core.reply_chain_tracker import ReplyChainTracker

# Initializes logging and loads environment variables from the .env file.
logger = logging.getLogger(__name__)
load_dotenv()

# Pulls necessary Discord IDs for logic gating.
BAPT_DISCORD_ID = int(os.getenv('BAPT_DISCORD_ID', 0))

# Global dictionaries to track state across async operations.
active_processing_locks = {}
active_channel_memories = {}

# Rakun chains usually stop after one Leepa reply, but can occasionally run longer.
RAKUN_CHAIN_CAPS = (1, 2, 3, 4)
RAKUN_CHAIN_WEIGHTS = (70, 20, 8, 2)
rakun_reply_chains = ReplyChainTracker(
    caps=RAKUN_CHAIN_CAPS,
    weights=RAKUN_CHAIN_WEIGHTS,
    label="Rakun",
)

# Pre-compiled regular expressions for identifying specific target users or names.
# IGNORECASE handles casing; searches run directly on the raw message content.
REGEX_NAMED = re.compile(r'\b(leepa|leep)\b', re.IGNORECASE)
REGEX_VIP = re.compile(r'\b(hun|sweetie)\b', re.IGNORECASE)
REGEX_CUSTOM_EMOJI = re.compile(r'^<a?:[A-Za-z0-9_]+:\d+>$')

# Image MIME types accepted by the Visual Pre-Processor.
VALID_IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp'}


def get_channel_memory(channel_id: int) -> ShortTermMemory:
    """Retrieves or instantiates an isolated memory queue for a specific Discord channel."""
    if channel_id not in active_channel_memories:
        active_channel_memories[channel_id] = ShortTermMemory()
    return active_channel_memories[channel_id]


def get_sloppy_mode(content: str) -> str | None:
    """Returns the forced Sloppy mode when the first token is a Leepa command."""
    if not prompts.USE_SLOPPY:
        return None

    content_parts = content.split(maxsplit=1)
    if not content_parts:
        return None

    return prompts.SLOPPY_MODE_TRIGGERS.get(content_parts[0])


async def build_message_payload(message) -> tuple[str, dict[int, str]]:
    """Builds the text kept in memory and describes supported image attachments."""
    content_parts = [message.content] if message.content else []
    image_descriptions = {}

    for attachment in message.attachments:
        mime = (attachment.content_type or "").split(";")[0].strip().lower()

        if mime in VALID_IMAGE_TYPES:
            logger.info(f"[VISION] Analyzing attachment ({mime}): {attachment.url}")
            image_desc = await analyze_image(attachment.url)
            image_descriptions[attachment.id] = image_desc
            content_parts.append(image_desc)
            continue

        attachment_type = mime or "unknown"
        content_parts.append(
            f"[ATTACHMENT: {attachment.filename} | type: {attachment_type} | "
            f"size: {attachment.size} bytes]"
        )
        logger.info(
            f"[ATTACHMENT] Recorded unsupported media type "
            f"'{attachment.content_type}': {attachment.filename}"
        )

    return "\n".join(content_parts).strip(), image_descriptions


def evaluate_message_context(
    message,
    bot_user,
    referenced_author_id: int | None,
    sloppy_mode: str | None = None,
) -> tuple[str, bool]:
    """
    Evaluates a message's Engagement Level.
    Returns a string tag and a boolean dictating whether to trigger the LLM.
    """
    if sloppy_mode is not None:
        return "DIRECT", True

    content = message.content

    # ---------------------------------------------------------
    # TRACK A: ENGAGEMENT DETECTION
    # ---------------------------------------------------------
    is_mentioned = bot_user in message.mentions
    is_replied_to = referenced_author_id == bot_user.id
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
    is_rakun = (message.author.id == RAKUN_ID and RAKUN_ID != 0)
    should_trigger = False

    if engagement_level in ["DIRECT", "QUOTED"]:
        should_trigger = True
    else:
        # Applies a 30% penalty to probability if the message came from Rakun.
        base_prob = 0.05
        final_prob = base_prob * (0.3 if is_rakun else 1.0)
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


async def process_message_edit(message, bot_user) -> None:
    """Updates the original memory entry without treating an edit as a new message."""
    if message.author.id == bot_user.id:
        return

    content_payload, _ = await build_message_payload(message)
    local_memory = get_channel_memory(message.channel.id)
    is_sloppy = (
        prompts.USE_SLOPPY
        and message.author.id == SLOPPY_ID
        and SLOPPY_ID != 0
    )
    updated = local_memory.update_message(
        message.id,
        message.author.display_name,
        content_payload,
        author_id=message.author.id,
    )

    if not updated and is_sloppy:
        local_memory.add_message(
            message.author.display_name,
            content_payload,
            message_id=message.id,
            author_id=message.author.id,
        )

        overflow_text = local_memory.extract_overflow_for_summary()
        if overflow_text:
            asyncio.create_task(background_summarize(local_memory, overflow_text))

    if not updated and not is_sloppy:
        logger.info(f"Edited message {message.id} is no longer in short-term memory.")
        return

    channel_label = getattr(message.channel, "name", "DM")
    action = "UPDATED" if updated else "ADDED"
    logger.info(
        f">>> EDIT {action} [#{channel_label}] "
        f"{message.author.display_name}: {content_payload}"
    )


async def process_message(message, bot_user) -> None:
    """Primary pipeline for handling incoming Discord events and routing them to the external AI API."""
    current_time = time.time()

    # ---------------------------------------------------------
    # STATE CLEANUP (GHOST LOCKS)
    # ---------------------------------------------------------
    expired_keys = [k for k, v in active_processing_locks.items() if current_time > v.get("expires", 0)]
    for k in expired_keys:
        del active_processing_locks[k]

    # If Rakun replies to a message Leepa is still processing, flip its kill-switch.
    if message.author.id == RAKUN_ID and RAKUN_ID != 0 and message.reference:
        target_id = message.reference.message_id
        lock_data = active_processing_locks.get(target_id)
        if lock_data and lock_data["status"] != "IMMUNE":
            active_processing_locks[target_id]["status"] = True
            logger.info(f"Concurrent response detected for message {target_id}. Aborting execution.")

    # Attachments enter memory even when the message does not trigger Leepa.
    content_payload, image_descriptions = await build_message_payload(message)

    channel_label = getattr(message.channel, "name", "DM")
    logger.info(f">>> INCOMING [#{channel_label}] {message.author.display_name}: {content_payload}")

    local_memory = get_channel_memory(message.channel.id)

    # The current message is kept separate from the history sent to Leepa.
    context_block = local_memory.get_context_block()
    discord_context, referenced_author_id = await collect_discord_context(
        message,
        image_descriptions,
    )

    rakun_chain_id = None
    if message.author.id == RAKUN_ID and RAKUN_ID != 0:
        referenced_message_id = (
            message.reference.message_id
            if message.reference is not None
            else None
        )
        rakun_chain_id = rakun_reply_chains.register_incoming(
            message.id,
            referenced_message_id,
        )

    local_memory.add_message(
        message.author.display_name,
        content_payload,
        message_id=message.id,
        author_id=message.author.id,
    )

    overflow_text = local_memory.extract_overflow_for_summary()
    if overflow_text:
        asyncio.create_task(background_summarize(local_memory, overflow_text))

    # Sloppy is a tool. Its status and result messages remain visible in memory
    # but never enter the conversational response pipeline.
    if prompts.USE_SLOPPY and message.author.id == SLOPPY_ID and SLOPPY_ID != 0:
        return

    sloppy_mode = get_sloppy_mode(message.content)
    engagement_level, should_trigger = evaluate_message_context(
        message,
        bot_user,
        referenced_author_id,
        sloppy_mode,
    )

    if not should_trigger:
        return

    rakun_generation_reserved = False
    if rakun_chain_id is not None:
        rakun_generation_reserved = rakun_reply_chains.try_reserve_generation(
            rakun_chain_id
        )
        if not rakun_generation_reserved:
            logger.info("Rakun reply chain %s is busy or has reached its cap.", rakun_chain_id)
            return

    # Direct engagement automatically grants lock immunity. Locks expire after 60 seconds.
    lock_status = "IMMUNE" if engagement_level in ["DIRECT", "QUOTED"] else False
    active_processing_locks[message.id] = {"status": lock_status, "expires": current_time + 60.0}

    try:
        response_data = await generate_chat_response(
            context_block,
            discord_context,
            sloppy_mode,
        )
    except Exception:
        if rakun_generation_reserved:
            rakun_reply_chains.release_generation(rakun_chain_id)
        raise

    logger.info(f"<<< OUTGOING\n{json.dumps(response_data, indent=2, ensure_ascii=False)}")

    # Matrix Kill-Switch Check. pop() removes the lock in a single step regardless of outcome.
    if active_processing_locks.pop(message.id, {}).get("status") is True:
        if rakun_generation_reserved:
            rakun_reply_chains.release_generation(rakun_chain_id)
        return

    reply_text = response_data.get("response", "").strip()
    reaction_emoji = response_data.get("reaction_emoji", "").strip() if prompts.USE_EMOJI_REACTIONS else ""
    internal_mood = response_data.get("internal_mood", "neutral").strip()
    thinking_block = response_data.get("thinking_block", "").strip() if prompts.USE_THINKING_BLOCK else ""

    if reaction_emoji and not prompts.USE_CUSTOM_EMOJIS and REGEX_CUSTOM_EMOJI.fullmatch(reaction_emoji):
        logger.info("Custom emoji ignored because USE_CUSTOM_EMOJIS is disabled.")
        reaction_emoji = ""

    # 1. Execute physical Discord actions
    if reaction_emoji:
        try:
            await message.add_reaction(reaction_emoji)
        except Exception as e:
            logger.error(f"Discord API failure on add_reaction: {e}")

    sent_reply = None
    if reply_text:
        try:
            sent_reply = await message.reply(reply_text)
        except Exception as e:
            logger.error(f"Discord API failure on message reply: {e}")

    if rakun_generation_reserved:
        sent_reply_id = getattr(sent_reply, "id", None)
        if sent_reply_id is None:
            rakun_reply_chains.release_generation(rakun_chain_id)
        else:
            chain_progress = rakun_reply_chains.record_reply(
                rakun_chain_id,
                sent_reply_id,
            )
            if chain_progress is not None:
                replies_sent, reply_cap = chain_progress
                logger.info(
                    "Rakun reply chain %s: Leepa reply %s/%s.",
                    rakun_chain_id,
                    replies_sent,
                    reply_cap,
                )

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

    local_memory.add_message(
        "Leepa",
        memory_log,
        message_id=getattr(sent_reply, "id", None),
        author_id=bot_user.id,
    )