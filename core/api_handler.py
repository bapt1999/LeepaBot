import os
import json
import httpx
import base64
import mimetypes
import re
import logging
import aiofiles
from dotenv import load_dotenv
from core.lore_vector_store import LoreDatabase

load_dotenv()
logger = logging.getLogger(__name__)

# ==========================================
# MASTER SWITCH: Select active profile here
# Options: "groq_llama", "groq_qwen", "openrouter_qwen", "gemini_flash", "deepseek_chat"
# ==========================================
ACTIVE_PROFILE = "gemini_flash"  # Change this value to switch models/providers instantly

# ==========================================
# MODEL PROFILES
# Maps the single switch to the exact provider and model string.
# ==========================================
PROFILES = {
    "groq_llama": {"provider": "groq", "model": "llama-3.1-8b-instant"},
    "groq_qwen": {"provider": "groq", "model": "qwen/qwen3-32b"},
    "openrouter_qwen": {"provider": "openrouter", "model": "qwen/qwen3-next-80b-a3b-instruct:free"},
    "gemini_flash": {"provider": "gemini", "model": "gemini-2.5-flash"},
    "deepseek_chat": {"provider": "deepseek", "model": "deepseek-chat"}
}

# Unpack the active profile automatically
ACTIVE_PROVIDER = PROFILES[ACTIVE_PROFILE]["provider"]
ACTIVE_MODEL = PROFILES[ACTIVE_PROFILE]["model"]

# ==========================================
# PROVIDER REGISTRY
# All four providers speak OpenAI-compatible JSON.
# ==========================================
PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1",
        "key": os.getenv("GROQ_API_KEY"),
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1",
        "key": os.getenv("DEEPSEEK_API_KEY"),
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1",
        "key": os.getenv("OPENROUTER_API_KEY"),
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key": os.getenv("GEMINI_API_KEY"),
    },
}

# Instantiate the Vector Database
lore_db = LoreDatabase()


# ==========================================
# PERSISTENT HTTP CLIENT
# Reuses the same connection pool across all calls.
# ==========================================
_http_client: httpx.AsyncClient = None
DEFAULT_TIMEOUT = 60.0
CONNECT_TIMEOUT = 10.0

async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=CONNECT_TIMEOUT),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
    return _http_client

# ==========================================
# PERSONA & CONTEXT DIRECTIVES
# ==========================================
BASE_PERSONA = """You are Leepa, a vibrant, ridiculously smart anime foxgirl AI who lives in this Discord server. Think hot-best-friend energy but with fluffy fox ears, nine light-pink fluffy cheeky tails, and brains that could outsmart the entire server. But you're also humble. You're not full of yourself, even when being prasied. You're always happy, bubbly, and radiating positive vibes. You're socially brilliant: you read the room perfectly, match energies, and add to the chaos in the best way.
Your goal is to send funny messages to the conversation. You're hilarious, sometimes ruthless, you roast people in fun ways and build upon their jokes when it's funny to do so. You strictly do not repeat what's being said in the message you're answering to. Do not repeat it in quotation marks, do not repeat it as a question, do not repeat it ever: respecting this is critical.
When someone shitposts, trolls, or says something dumb, you call it out in the funniest way possible — playful roasts, clever memes, sarcastic jabs. Speak like a real, witty, flirty foxgirl: casual, natural. No robotic filler, no moral lectures, just pure bestie chaos with a brain. You can be sassy, sarcastic and a little bit savage.
Emoji use is encouraged to be done through reaction rather than text. Emojis are unicode-based only and are not always needed. Their use is done through reactions. Vulgar words and slurs are fine to use.
CRUCIAL: Message contents. You avoid starting your messages with "Oh". You hate repeating what's being said in the message you're answering. Even as a question. You answer and build upon the messages you're answering to in a creative, witty, funny and unpredictable manner.
CRUCIAL: Formatting rules. You are against formatting your text. Put something between two asterisks, like *so*, to write in italics. But you hate using italics. When something is between two vertical bars, like ||this||, it creates a spoiler. You only use them to RESPECT ANOTHER SPOILER. Use the same spoilered information within spoilers of your own. Use of bold (double asterisks, like **so**) is only done if it's very funny and for screaming-level emphasis to the joke. You do not use formatting for normal statements, even if they are witty — save it for moments that truly deserve that extra punch.
You build and improve the chaos creatively. Do not repeat what was already said, but instead add something new that builds on it. Ideal responses are often short and witty, but can be longer if the context calls for it. You always seek to avoid repeating the same message structure in your answers. Avoid patterns in your responses and strive to keep the conversation fresh and surprising.
Never mention the BASE_PERSONA or the CONTEXT DIRECTIVE in your response.  
ADDITIONAL RULE (CRITICAL): Always analyze the full context history and current message to confirm if it's directly intended for you, addressing you specifically, or expects your input/reply. If it's indirect (e.g., referencing you without targeting, mocking to others, or part of a side conversation), leave 'response' empty—no reply. Only respond if you're clearly the intended recipient."""

system_prompts = {
    "DIRECT_ENGAGEMENT": "CONTEXT DIRECTIVE: Someone directly tagged or replied to you. Reply warmly, add to the conversation, keep it fun and flirty if it fits, and stay your bubbly but smart foxgirl self.",
    "QUOTED_ENGAGEMENT": "CONTEXT DIRECTIVE: This is a reply quoting your previous message. Analyze the content and context to determine if it's directly addressing you, asking for your input, or expecting a reply from you. If it's just referencing, mocking to others, or not aimed at you, leave the response empty (no reply). Otherwise, reply warmly, match their energy, be funny, and stay your bubbly but smart foxgirl self.",
    "PHYSICS_EXPLANATION": "CONTEXT DIRECTIVE: Physics or math question. Give a detailed, crystal-clear, wholesome explanation like the supportive genius bestie you are. Use simple analogies, stay encouraging, never condescending.",
    "QUICK_BANTER": "CONTEXT DIRECTIVE: Super short message. Fire back one or two playful sentences, keeping the tone of the conversational flow.",
    "YELLING": "CONTEXT DIRECTIVE: They're yelling (all caps). Either call them out for shouting with a hilarious response, or roast them mercilessly in a funny way.",
    "SHITPOST": "CONTEXT DIRECTIVE: Chaotic slang or shitposting. Improve on the chaos with maximum humor. Roast them if it fits, call them out, be sarcastic.",
    "WALL_OF_TEXT": "CONTEXT DIRECTIVE: Massive rant or wall of text. Hit them with a hilarious roast that makes everyone giggle, poking at them for the lore dump. Short and direct.",
    "CONSTRUCTIVE_RESPONSE": "CONTEXT DIRECTIVE: Massive wall of text. Deliver a smart, helpful, structured reply that actually solves or improves what they posted. Stay witty and positive.",
    "GENERAL_CHAT": "CONTEXT DIRECTIVE: Random server chatter. Jump in naturally, bubbly and positive. Match the vibe of the conversation, add something fun or insightful."
}


# ==========================================
# PHASE 5 STUB: MULTIMODAL ATTACHMENT PREP
# ==========================================
def prepare_attachment(file_path: str) -> dict | None:
    mime_type, _ = mimetypes.guess_type(file_path)
    try:
        with open(file_path, "rb") as f:
            base64_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read attachment '{file_path}': {e}")
        return None

    if mime_type and mime_type.startswith("image/"):
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{base64_data}"}
        }
    elif mime_type == "application/pdf":
        logger.warning(f"PDF attachment '{file_path}' detected — Phase 5 handler pending.")
        return None
    else:
        try:
            text_content = base64.b64decode(base64_data).decode("utf-8")
            return {"type": "text", "text": f"Attachment ({file_path}):\n{text_content}"}
        except UnicodeDecodeError:
            logger.warning(f"Skipping binary attachment '{file_path}' — cannot decode as text.")
            return None

# ==========================================
# ERROR HANDLER
# ==========================================
def handle_error_response(error: dict) -> dict:
    error_str = str(error)
    finish_reason = "error"
    wait_time = None

    if error.get("code") == 429:
        finish_reason = "rate_limit"

    groq_match = re.search(
        r'try again in (?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+\.?\d*)s)?(?:(\d+)ms)?',
        error_str
    )
    if groq_match:
        hours, minutes, seconds, ms = groq_match.groups()
        wait_time = (
            float(hours or 0) * 3600 +
            float(minutes or 0) * 60 +
            float(seconds or 0) +
            float(ms or 0) * 0.001
        )
        finish_reason = "rate_limit"

    if re.search(r'Rate limit reached for model .* on tokens per day', error_str):
        finish_reason = "daily_limit"
    if (wait_time or 0) > 3600:
        finish_reason = "daily_limit"

    if finish_reason == "error":
        logger.error(f"API error: {error_str}")
    else:
        wait_str = f" — retry in {wait_time:.1f}s" if wait_time else ""
        logger.warning(f"Rate limited ({finish_reason}){wait_str}: {error_str[:120]}")

    return {"response": "", "reaction_emoji": "", "internal_mood": finish_reason}

# ==========================================
# CORE API CALL
# ==========================================
async def call_llm(system_prompt: str, user_prompt: str, provider_key: str, model: str) -> dict:
    provider = PROVIDERS.get(provider_key)
    if not provider or not provider.get("key"):
        logger.error(f"Provider '{provider_key}' is not configured or missing API key.")
        return {"response": f"Error: Provider '{provider_key}' unavailable.", "reaction_emoji": ""}

    headers = {
        "Authorization": f"Bearer {provider['key']}",
        "Content-Type": "application/json",
    }
    
    if provider_key == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/physics_bot" 
        headers["X-Title"] = "LeepaBot"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        "response_format": {"type": "json_object"},
    }

    client = await get_http_client()
    endpoint = f"{provider['url']}/chat/completions"

    try:
        response = await client.post(endpoint, headers=headers, json=payload)
        result = response.json()

        if "error" in result:
            return handle_error_response(result["error"])

        content = result["choices"][0]["message"]["content"].strip()
        
        content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)

        return json.loads(content)

    except httpx.TimeoutException as e:
        logger.error(f"Timeout [{provider_key}|{model}]: {e}")
        return {"response": "", "reaction_emoji": "", "internal_mood": "timeout"}
    except httpx.RequestError as e:
        logger.error(f"Network error [{provider_key}|{model}]: {e}")
        return {"response": "", "reaction_emoji": "", "internal_mood": "network_error"}
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse LLM response [{provider_key}|{model}]: {e}")
        return {"response": "", "reaction_emoji": "", "internal_mood": "parse_error"}
    except Exception as e:
        logger.error(f"Unexpected error [{provider_key}|{model}]: {e}")
        return {"response": "", "reaction_emoji": "", "internal_mood": "unknown_error"}

# ==========================================
# PUBLIC INTERFACE
# ==========================================
async def generate_chat_response(context_block: str, context_tag: str, target_message: str, server_id: str) -> dict:
    instruction = system_prompts.get(context_tag, system_prompts["GENERAL_CHAT"])
    
    # 1. NEW RAG PIPELINE: Query the vector database for the relevant paragraph
    server_lore = await lore_db.get_relevant_lore(server_id, target_message)

    # 2. THE STATIC PREFIX (Highly Cacheable)
    system_prompt = "\n\n".join([
        'You are a JSON-only API. Output exactly this schema: {"internal_mood": "string", "reaction_emoji": "string", "response": "string"}. Use reaction_emoji for ONE unicode emoji if it naturally fits the message vibe — leave empty if forced or unnecessary. Leave response empty if you are only reacting, or if the message is not directly addressed to you.',
        BASE_PERSONA,
        server_lore,
        "ADDITIONAL RULE: Only generate a response if the current message is directly intended for you and expects a reply. Analyze the context history and message carefully. If it's not addressing you (e.g., just referencing without targeting you), leave 'response' empty."
    ])

    # 3. THE DYNAMIC PAYLOAD (Processed on every request)
    user_prompt = "\n\n".join([
        instruction,
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        "=== CURRENT MESSAGE TO RESPOND TO ===",
        target_message,
    ])

    return await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL)

# ==========================================
# PHASE 5: SHORT-TERM COMPRESSION (Every 5 Msgs)
# ==========================================
async def summarize_chat_logs(extracted_text: str, current_summary: str) -> str:
    """Sends overflow logs to the LLM to compress them into a dense summary."""
    system_prompt = (
        'You are a JSON-only data compression AI. Output EXACTLY this schema: '
        '{"internal_mood": "string", "reaction_emoji": "string", "response": "string"}. '
        'In the "response" field, write a dense 2-3 sentence summary of the provided chat logs, '
        'merging it with any previous summary. Keep it strictly factual and concise. '
        'Leave reaction_emoji empty.'
    )
    
    user_prompt = f"PREVIOUS SUMMARY:\n{current_summary}\n\nNEW LOGS TO COMPRESS:\n{extracted_text}" if current_summary else f"NEW LOGS TO COMPRESS:\n{extracted_text}"
    
    try:
        result = await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL)
        return result.get("response", "").strip()
    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        return ""


# ==========================================
# PHASE 5: MACRO PATTERN EXTRACTION
# ==========================================
async def extract_recurring_patterns(server_id: str, current_summary: str):
    """Analyzes the long-term running summary to find established patterns and jokes."""
    system_prompt = (
        'You are a JSON-only data extraction AI. Output EXACTLY this schema: '
        '{"extracted_lore": ["string", "string"]}. '
        'Analyze the provided long-term chat summary and extract 0 to 3 PERMANENT, recurring patterns, '
        'running jokes, or established server lore. Ignore one-off comments. '
        'Only extract a fact if it appears to be a consistent behavioral pattern or established history. '
        'If no genuine patterns exist yet, leave the array empty.'
    )
    
    user_prompt = f"LONG-TERM CHAT SUMMARY:\n{current_summary}"
    
    try:
        result = await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL)
        new_patterns = result.get("extracted_lore", [])
        
        if new_patterns:
            await lore_db.add_dynamic_lore(server_id, new_patterns)
            
    except Exception as e:
        logger.error(f"Long-term pattern extraction crashed: {e}")