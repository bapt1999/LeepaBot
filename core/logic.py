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
# BOT CONCURRENCY & RATE LIMITING
# ==========================================
OTHER_BOT_ID = int(os.getenv('OTHER_BOT_ID', 0)) 
OTHER_BOT_REPLY_CAP = 1 

# Global state for response interception
active_processing_locks = {}

active_channel_memories = {}
max_messages_in_memory = 20

# ==========================================
# RESPONSE PROBABILITY MATRIX
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
# PRE-COMPILED REGEX PATTERNS & KEYWORDS
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
        
        # Fallback to explicit API fetch if message is absent from local cache
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


async def evaluate_cost_function(message, bot_user) -> str:
    # Phase 0: Loop Prevention
    if message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0:
        pingpong_depth = await get_reply_chain_depth(message, bot_user)
        if pingpong_depth >= (OTHER_BOT_REPLY_CAP * 2):
            return "IGNORE"

    raw_content = message.content.strip()
    content_lower = raw_content.lower()
    word_count = len(raw_content.split())
    
    # Identify target bot account for probability dampening
    is_rival_bot = (message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0)
    
    # Phase 1: Guaranteed Engagement overrides
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

    # Phase 2: Conversational Hooks
    is_question = bool(REGEX_QUESTION.search(content_lower)) or '?' in raw_content
    
    if is_question:
        prob = PROBABILITIES["QUESTION"] * (0.5 if is_rival_bot else 1.0)
        if random.random() < prob: 
            return "GENERAL_CHAT"

    if 0 < word_count <= 5:
        prob = PROBABILITIES["QUICK_BANTER"] * (0.5 if is_rival_bot else 1.0)
        if random.random() < prob: 
            return "QUICK_BANTER"

    # Phase 3: Sentiment & Energy Matching
    is_yelling = raw_content.isupper() and len(raw_content) > 5
    
    if is_yelling:
        prob = PROBABILITIES["YELLING"] * (0.5 if is_rival_bot else 1.0)
        if random.random() < prob: 
            return "YELLING"
            
    if any(word in content_lower for word in SHITPOST_KEYWORDS):
        prob = PROBABILITIES["SHITPOST"] * (0.5 if is_rival_bot else 1.0)
        if random.random() < prob:
            return "SHITPOST"

    if word_count > 500:
        prob_wall = PROBABILITIES["WALL_OF_TEXT"] * (0.5 if is_rival_bot else 1.0)
        prob_construct = PROBABILITIES["CONSTRUCTIVE"] * (0.5 if is_rival_bot else 1.0)
        
        if random.random() < prob_wall: 
            return "WALL_OF_TEXT" 
        if random.random() < prob_construct: 
            return "CONSTRUCTIVE_RESPONSE"

    # Phase 4: Ambient Conversation
    prob_ambient = PROBABILITIES["AMBIENT"] * (0.5 if is_rival_bot else 1.0)
    if random.random() < prob_ambient: 
        return "GENERAL_CHAT"
        
    return "IGNORE"


async def background_summarize(local_memory, extracted_text: str):
    """Executes asynchronous memory compression without blocking the main event loop."""
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
    # Interception Matrix: Detect concurrent bot responses
    if message.author.id == OTHER_BOT_ID and OTHER_BOT_ID != 0 and message.reference:
        target_id = message.reference.message_id
        if target_id in active_processing_locks:
            # Verify lock immunity status before flagging for abortion
            if active_processing_locks[target_id] != "IMMUNE":
                active_processing_locks[target_id] = True
                logger.info(f"Concurrent response detected for message {target_id}. Aborting execution.")

    local_memory = get_channel_memory(message.channel.id)
    local_memory.add_message(message.author.display_name, message.content)
    
    server_id = str(message.guild.id) if message.guild else "DM"
    
    # Memory Pipeline Triggers
    overflow_text = local_memory.extract_overflow_for_summary()
    if overflow_text:
        asyncio.create_task(background_summarize(local_memory, overflow_text))
        
    # Execute macro pattern extraction at defined intervals
    if local_memory.total_message_count % 50 == 0 and local_memory.running_summary:
        logger.info(f"Interval threshold reached. Initiating macro pattern extraction.")
        asyncio.create_task(extract_recurring_patterns(server_id, local_memory.running_summary))
    
    tag = await evaluate_cost_function(message, bot_user)
    
    if tag == "IGNORE":
        return ""
        
    context_block = local_memory.get_context_block()
    named_target_message = f"{message.author.display_name}: {message.content}"
    
    # Concurrency Lock Registration
    if tag in ["DIRECT_ENGAGEMENT", "QUOTED_ENGAGEMENT"]:
        active_processing_locks[message.id] = "IMMUNE"
    else:
        active_processing_locks[message.id] = False
    
    # Yield control to the asynchronous event loop during LLM generation
    response_data = await generate_chat_response(context_block, tag, named_target_message, server_id)
    
    # Validate lock state post-generation
    if active_processing_locks.get(message.id) is True:
        # Clean lock and abort response
        del active_processing_locks[message.id]
        return ""
        
    # Clean lock and proceed with response execution
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