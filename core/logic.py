import asyncio
import random
import re
import os
import logging
from dotenv import load_dotenv
from core.api_handler import generate_chat_response, summarize_chat_logs, extract_recurring_patterns
from core.memory_queue import ShortTermMemory

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
BAPT_DISCORD_ID = int(os.getenv('BAPT_DISCORD_ID', 0))

# ==========================================
# BOT INTERACTION LIMITS (The Loop Slayer)
# ==========================================
# Securely pulled from the .env file
OTHER_BOT_ID = int(os.getenv('OTHER_BOT_ID', 0)) 
OTHER_BOT_REPLY_CAP = 1 # Maximum back-and-forth replies permitted

active_channel_memories = {}
max_messages_in_memory = 20

# ==========================================
# LEEPA'S CONTROL PANEL (Tuning Knobs)
# ==========================================
PROBABILITIES = {
    "QUESTION": 0.30,
    "QUICK_BANTER": 0.10,
    "YELLING": 0.35,
    "SHITPOST": 0.05,
    "WALL_OF_TEXT": 0.08,
    "CONSTRUCTIVE": 0.10,
    "AMBIENT": 0.10
}

# ==========================================
# PRE-COMPILED PATTERNS & LISTS
# ==========================================
REGEX_NAMED = re.compile(r'\b(leepa|leep)\b')
REGEX_VIP = re.compile(r'\b(hun|sweetie)\b')
REGEX_QUESTION = re.compile(r'^(who|what|where|when|why|how|por qué|cómo|pourquoi|comment)\b')

PHYSICS_KEYWORDS = ['physics', 'física', 'physique', 'quantum', 'relativity', 'thermodynamics', 'mechanics', 'electromagnetism', 'optics', 'particle', 'astrophysics', 'cosmology', 'string theory', 'dark matter', 'dark energy', 'wavefunction', 'superposition', 'entanglement', 'uncertainty principle', 'schrodinger', 'newtonian', 'einstein', 'planck', 'bohr', 'heisenberg', 'de broglie', 'higgs', 'fermion', 'boson', 'lepton', 'quark', 'gluon', 'photon', 'graviton', 'atomic', 'semiconductor', 'condensed matter', 'fluid dynamics', 'statistical mechanics', 'string theory']
SHITPOST_KEYWORDS = ['lmao', 'lol', 'bruh', 'based', 'cringe', 'fr fr', 'no cap', 'skill issue', '💀', '🤡', 'cheh', '67']


def get_channel_memory(channel_id: int) -> ShortTermMemory:
    if channel_id not in active_channel_memories:
        active_channel_memories[channel_id] = ShortTermMemory(max_size=max_messages_in_memory)  
    return active_channel_memories[channel_id]


async def get_reply_chain_depth(message, bot_user) -> int:
    """Climbs the Discord reply tree backwards, fetching from API if cache fails."""
    count = 0
    current_msg = message
    
    while current_msg.reference and current_msg.reference.message_id:
        parent_msg = current_msg.reference.resolved
        
        # If the parent message is not in my local cache, forcefully fetch it
        if parent_msg is None:
            try:
                parent_msg = await current_msg.channel.fetch_message(current_msg.reference.message_id)
            except Exception as e:
                logger.warning(f"Could not fetch parent message for loop check: {e}")
                break
                
        if parent_msg.author.id in [bot_user.id, OTHER_BOT_ID]:
            count += 1
            current_msg = parent_msg
        else:
            break
            
    return count


async def evaluate_cost_function(message, bot_user) -> str:
    # Phase 0: The Infinite Loop Slayer
    if message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0:
        pingpong_depth = await get_reply_chain_depth(message, bot_user)
        if pingpong_depth >= (OTHER_BOT_REPLY_CAP * 2):
            return "IGNORE"

    raw_content = message.content.strip()
    content_lower = raw_content.lower()
    word_count = len(raw_content.split())
    
    # Phase 1: Guaranteed Engagement (Hard Overrides)
    is_mentioned = bot_user in message.mentions  
    is_replied_to = message.reference and message.reference.resolved and message.reference.resolved.author == bot_user 
    is_named = bool(REGEX_NAMED.search(content_lower))
    is_creator_hun = (message.author.id == BAPT_DISCORD_ID) and bool(REGEX_VIP.search(content_lower))
    
    if is_mentioned or is_named or is_creator_hun:
        return "DIRECT_ENGAGEMENT"
    
    if is_replied_to:
        return "QUOTED_ENGAGEMENT"

    if any(word in content_lower for word in PHYSICS_KEYWORDS):
        return "PHYSICS_EXPLANATION"

    # Phase 2: Conversational Hooks (Questions & Banter)
    is_question = bool(REGEX_QUESTION.search(content_lower)) or '?' in raw_content
    
    if is_question:
        if random.random() < PROBABILITIES["QUESTION"]: 
            return "GENERAL_CHAT"

    if 0 < word_count <= 5:
        if random.random() < PROBABILITIES["QUICK_BANTER"]: 
            return "QUICK_BANTER"

    # Phase 3: Chaos & Energy Matching (Shitposts, Yelling, Lore Dumps)
    is_yelling = raw_content.isupper() and len(raw_content) > 5
    
    if is_yelling:
        if random.random() < PROBABILITIES["YELLING"]: 
            return "YELLING"
            
    if any(word in content_lower for word in SHITPOST_KEYWORDS):
        if random.random() < PROBABILITIES["SHITPOST"]:
            return "SHITPOST"

    if word_count > 500:
        if random.random() < PROBABILITIES["WALL_OF_TEXT"]: 
            return "WALL_OF_TEXT" 
        if random.random() < PROBABILITIES["CONSTRUCTIVE"]: 
            return "CONSTRUCTIVE_RESPONSE"

    # Phase 4: The Ambient Vibe (Random Chatter)
    if random.random() < PROBABILITIES["AMBIENT"]: 
        return "GENERAL_CHAT"
        
    return "IGNORE"


async def background_summarize(local_memory, extracted_text: str):
    """Runs asynchronously to compress memory without blocking the chat flow."""
    try:
        new_summary = await summarize_chat_logs(extracted_text, local_memory.running_summary)
        if new_summary:
            local_memory.update_running_summary(new_summary)
            logger.info(f"Memory compressed. New summary length: {len(new_summary)} chars.")
        else:
            local_memory.is_summarizing = False 
    except Exception as e:
        logger.error(f"Background compression crashed: {e}")
        local_memory.is_summarizing = False

async def process_message(message, bot_user) -> str:
    local_memory = get_channel_memory(message.channel.id)
    local_memory.add_message(message.author.display_name, message.content)
    
    server_id = str(message.guild.id) if message.guild else "DM"
    
    # --- 1. SHORT-TERM COMPRESSION TRIGGER ---
    overflow_text = local_memory.extract_overflow_for_summary()
    if overflow_text:
        asyncio.create_task(background_summarize(local_memory, overflow_text))
        
    # --- 2. MACRO PATTERN EXTRACTION TRIGGER ---
    # Every 50 messages, scan the summary for recurring jokes and permanent facts
    if local_memory.total_message_count % 50 == 0 and local_memory.running_summary:
        logger.info(f"Message 50 reached. Scanning summary for long-term patterns...")
        asyncio.create_task(extract_recurring_patterns(server_id, local_memory.running_summary))
    # -------------------------------
    
    tag = await evaluate_cost_function(message, bot_user)
    
    if tag == "IGNORE":
        return ""
        
    context_block = local_memory.get_context_block()
    
    named_target_message = f"{message.author.display_name}: {message.content}"
    response_data = await generate_chat_response(context_block, tag, named_target_message, server_id)
    
    reply_text = response_data.get("response", "").strip()
    reaction_emoji = response_data.get("reaction_emoji", "").strip()
    
    if reaction_emoji:
        try:
            await message.add_reaction(reaction_emoji)
        except Exception as e:
            logger.error(f"Discord API failed to add reaction '{reaction_emoji}': {e}")
    
    if reply_text:
        try:
            await message.reply(reply_text)
            local_memory.add_message("Leepa", reply_text)
        except Exception as e:
            logger.error(f"Discord API failed to reply: {e}")
            
    return ""