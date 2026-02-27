import random
import re
from core.api_handler import generate_chat_response
from core.memory_queue import ShortTermMemory

# Initialize the Phase 3A memory queue globally so it persists between events
channel_memory = ShortTermMemory(max_size=30)

def evaluate_cost_function(message, bot_user) -> str:
    content = message.content.lower().strip()
    word_count = len(content.split())
    
    # Tier 1: Hard Overrides (Guaranteed specific tags)
    if bot_user in message.mentions or (message.reference and message.reference.resolved and message.reference.resolved.author == bot_user):
        return "DIRECT_ENGAGEMENT"

    physics_keywords = ['physics', 'física', 'physique', 'quantum', 'integral', 'leepa']
    if any(word in content for word in physics_keywords):
        return "PHYSICS_EXPLANATION"

    # Tier 2: Structural Heuristics (Probabilistic tags)
    is_question = bool(re.match(r'^(who|what|where|when|why|how|por qué|cómo|pourquoi|comment)\b', content)) or '?' in content
    
    if is_question:
        if random.random() < 0.30: # 30% chance to answer questions
            return "GENERAL_CHAT"

    if 0 < word_count <= 5:
        if random.random() < 0.10: # 10% chance to banter short messages
            return "QUICK_BANTER"

    if word_count > 40:
        if random.random() < 0.20: # 20% chance to mock walls of text
            return "SHITPOST"

    # Tier 3: The Ambient Vibe (Baseline default roll)
    if random.random() < 0.05: # 5% chance to chime in on absolutely anything
        return "GENERAL_CHAT"
        
    return "IGNORE"

async def process_message(message, bot_user) -> str:
    # 1. Always ingest the new message into the short-term memory
    channel_memory.add_message(message.author.name, message.content)
    
    # 2. Evaluate if LeepaBot should speak
    tag = evaluate_cost_function(message, bot_user)
    
    # Stage 2: The Bouncer
    if tag == "IGNORE":
        return ""
        
    # 3. Pull the full context block of the last 30 messages
    context_block = channel_memory.get_context_block()
    
    # 4. Send the context block and the tag to the Generator
    response = await generate_chat_response(context_block, tag)
    
    # 5. Append LeepaBot's own response to the memory so she remembers what she just said
    if response:
        channel_memory.add_message("LeepaBot", response)
        
    return response






'''
# Deprecated
def process_message(content: str) -> str:
    text = content.lower()
    
    if "hello" in text:
        return "Hi."
    
    if "integral" in text:
        return "An integral assigns numbers to functions in a way that describes displacement, area, volume, and other concepts."
    
    if len(content) > 50:
        return f"Message character count: {len(content)}"
        
    return ""
'''