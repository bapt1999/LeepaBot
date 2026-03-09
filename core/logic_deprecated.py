import random
import re
import os
import logging
from dotenv import load_dotenv
from core.api_handler import generate_chat_response
from core.memory_queue import ShortTermMemory

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
BAPT_DISCORD_ID = int(os.getenv('BAPT_DISCORD_ID', 0))

active_channel_memories = {}
max_messages_in_memory = 20

# ==========================================
# LEEPA'S CONTROL PANEL (Tuning Knobs)
# ==========================================
# Adjust these decimals (0.0 to 1.0) to instantly change how chatty the bot is.
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
# Compiling these once at startup saves CPU cycles on every single message
REGEX_NAMED = re.compile(r'\b(leepa|leep)\b')
REGEX_VIP = re.compile(r'\b(hun|sweetie)\b')
REGEX_QUESTION = re.compile(r'^(who|what|where|when|why|how|por qué|cómo|pourquoi|comment)\b')

PHYSICS_KEYWORDS = ['physics', 'física', 'physique', 'quantum', 'relativity', 'thermodynamics', 'mechanics', 'electromagnetism', 'optics', 'particle', 'astrophysics', 'cosmology', 'string theory', 'dark matter', 'dark energy', 'wavefunction', 'superposition', 'entanglement', 'uncertainty principle', 'schrodinger', 'newtonian', 'einstein', 'planck', 'bohr', 'heisenberg', 'de broglie', 'higgs', 'fermion', 'boson', 'lepton', 'quark', 'gluon', 'photon', 'graviton', 'atomic', 'semiconductor', 'condensed matter', 'fluid dynamics', 'statistical mechanics', 'string theory']
SHITPOST_KEYWORDS = ['lmao', 'lol', 'bruh', 'based', 'cringe', 'fr fr', 'no cap', 'skill issue', '💀', '🤡', 'cheh', '67']


def get_channel_memory(channel_id: int) -> ShortTermMemory:
    """Retrieves or creates a dedicated memory queue for a specific channel."""
    if channel_id not in active_channel_memories:
        active_channel_memories[channel_id] = ShortTermMemory(max_size=max_messages_in_memory)  
    return active_channel_memories[channel_id]


def evaluate_cost_function(message, bot_user) -> str:
    raw_content = message.content.strip()
    content_lower = raw_content.lower()
    word_count = len(raw_content.split())
    
    # Phase 1: Guaranteed Engagement (Hard Overrides)
    is_mentioned = bot_user in message.mentions or (message.reference and message.reference.resolved and message.reference.resolved.author == bot_user)
    is_named = bool(REGEX_NAMED.search(content_lower))
    is_creator_hun = (message.author.id == BAPT_DISCORD_ID) and bool(REGEX_VIP.search(content_lower))
    
    if is_mentioned or is_named or is_creator_hun:
        return "DIRECT_ENGAGEMENT"

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


async def process_message(message, bot_user) -> str:
    local_memory = get_channel_memory(message.channel.id)
    local_memory.add_message(message.author.name, message.content)
    
    tag = evaluate_cost_function(message, bot_user)
    
    if tag == "IGNORE":
        return ""
        
    context_block = local_memory.get_context_block()
    server_id = str(message.guild.id) if message.guild else "DM"
    
    response_data = await generate_chat_response(context_block, tag, message.content, server_id)
    
    reply_text = response_data.get("response", "").strip()
    reaction_emoji = response_data.get("reaction_emoji", "").strip()
    
    if reaction_emoji:
        try:
            await message.add_reaction(reaction_emoji)
        except Exception as e:
            logger.error(f"Discord API failed to add reaction '{reaction_emoji}': {e}")
    
    if reply_text:
        local_memory.add_message("Leepa", reply_text)
        return reply_text
        
    return ""