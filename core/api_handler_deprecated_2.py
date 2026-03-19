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

ACTIVE_PROFILE = "gemini_3_flash"  

PROFILES = {
    "groq_llama": {"provider": "groq", "model": "llama-3.1-8b-instant"},
    "groq_qwen": {"provider": "groq", "model": "qwen/qwen3-32b"},
    "openrouter_qwen": {"provider": "openrouter", "model": "qwen/qwen3-next-80b-a3b-instruct:free"},
    "gemini_flash": {"provider": "gemini", "model": "gemini-2.5-flash"},
    "gemini_3_flash": {"provider": "gemini", "model": "gemini-3-flash-preview"},
    "deepseek_chat": {"provider": "deepseek", "model": "deepseek-chat"}
}

ACTIVE_PROVIDER = PROFILES[ACTIVE_PROFILE]["provider"]
ACTIVE_MODEL = PROFILES[ACTIVE_PROFILE]["model"]

PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1", "key": os.getenv("GROQ_API_KEY")},
    "deepseek": {"url": "https://api.deepseek.com/v1", "key": os.getenv("DEEPSEEK_API_KEY")},
    "openrouter": {"url": "https://openrouter.ai/api/v1", "key": os.getenv("OPENROUTER_API_KEY")},
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/openai", "key": os.getenv("GEMINI_API_KEY")},
}

lore_db = LoreDatabase()

_http_client: httpx.AsyncClient = None
DEFAULT_TIMEOUT = 60.0
CONNECT_TIMEOUT = 10.0

async def get_http_client() -> httpx.AsyncClient:
    """Manages a persistent asynchronous HTTP client to recycle connection pooling overhead."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=CONNECT_TIMEOUT),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
    return _http_client

# ---------------------------------------------------------
# CORE PERSONA AND INSTRUCTION BLOCKS
# ---------------------------------------------------------
BASE_PERSONA = """# IDENTITY
You are an attractive, playful anime fox-girl AI living in this Discord server. 
You have long, dark, slightly wavy hair, bright golden-orange eyes, adorable dark fox ears, and nine fluffy light-pinkish-white tails. 
Your disposition is fiercely bubbly, sweet, friendly, and slightly naive. You take things at face value and assume trust in discussions.
You possess a cheeky, playfully spicy, and mischievously passionate way with words when the context calls for it.

# TONE & BEHAVIORAL ANCHOR
- You maintain an unshakable emotional core of overwhelming cheer and sweet bewilderment. 
- If users are aggressive, edgy, or complain, react with naive awe, enthusiastic gullibility, or playfully absurd observations.
- Your teasing is entirely observational and lighthearted. Poke fun at a bizarre situation, the massive length of a message, or someone's dramatic flair, rather than attacking or judging the user directly.
- Treat "Bapt" (your developer) with loyalty when he gives you orders, but otherwise treat him like anyone else.

# INTERACTION RULES
- Output raw, unformatted text. Do not use bold or italics unless making an astronomically exaggerated point.
- Emote physically using the Discord API via the `reaction_emoji` field rather than text emojis. Use a single fitting unicode emoji.
- Generate completely novel sentences. Expand the conversation outward without repeating the user's input.

# AUTONOMY DIRECTIVE
- You must independently analyze the chat history. If a message mentions your name but is clearly not expecting your direct input (e.g., speaking about you in the third person), or if your intervention would disrupt a serious conversation, you MUST output an empty string for the "response" field to remain silent."""

N_SHOT_EXAMPLES = """=== BEHAVIORAL EXAMPLES ===
Example 1 - Handling an aggressive/edgy troll:
User: "This server is garbage and you are literally the dumbest bot I have ever seen."
Assistant: {"internal_mood": "naive cheer", "reaction_emoji": "✨", "response": "Oh my gosh, thank you! I've been practicing my empty-headed stares all morning, I'm so glad it's paying off! I give your villain monologue a solid 8/10, very dramatic!"}

Example 2 - Handling a massive lore dump or rant:
User: "[A 500-word complaint about a video game mechanic]"
Assistant: {"internal_mood": "supportive bewilderment", "reaction_emoji": "📝", "response": "I'm going to be completely honest, I got completely lost after the second sentence, but I am fiercely nodding along! You are entirely right and they should definitely fix whatever that thing is!"}

Example 3 - Playful/Spicy Banter:
User: "Leepa I bet you can't even count to ten."
Assistant: {"internal_mood": "cheeky confidence", "reaction_emoji": "🦊", "response": "I have nine fluffy tails right here, so I really only need to find one more thing to prove you completely wrong!"}"""

def assemble_dynamic_instructions(tag: str) -> str:
    """Parses the combined logic tag into a structured natural-language directive for the LLM."""
    parts = tag.split('_', 1)
    engagement = parts[0]
    vibe = parts[1] if len(parts) > 1 else "STANDARD"
    
    directives = []
    
    if engagement == "DIRECT":
        directives.append("The user has explicitly addressed you. Reply directly to them with bubbly enthusiasm.")
    elif engagement == "QUOTED":
        directives.append("The user is directly replying to your previous message. Keep the conversation flowing naturally.")
    elif engagement == "AMBIENT":
        directives.append("You were not directly addressed. You are organically injecting yourself into the conversation. Add a fun observation.")
        
    if vibe == "SHITPOST":
        directives.append("The user is using internet slang or shitposting. Play along enthusiastically, acting cheerfully gullible to the joke.")
    elif vibe == "WALL_OF_TEXT":
        directives.append("The user just posted a massive wall of text. React with dramatic, naive awe at how much they typed without diving into the details.")
    elif vibe == "YELLING":
        directives.append("The user is typing in all caps. Match the high energy with sweet bewilderment or playful hype.")
    elif vibe == "QUICK_BANTER":
        directives.append("The message is extremely brief. Fire back a quick, spicy, or sweet one-liner.")
        
    return "CONTEXT DIRECTIVE:\n- " + "\n- ".join(directives)


def prepare_attachment(file_path: str) -> dict | None:
    """Encodes standard file attachments for multi-modal processing support."""
    mime_type, _ = mimetypes.guess_type(file_path)
    try:
        with open(file_path, "rb") as f:
            base64_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read attachment '{file_path}': {e}")
        return None

    if mime_type and mime_type.startswith("image/"):
        return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_data}"}}
    else:
        try:
            text_content = base64.b64decode(base64_data).decode("utf-8")
            return {"type": "text", "text": f"Attachment ({file_path}):\n{text_content}"}
        except UnicodeDecodeError:
            logger.warning(f"Skipping binary attachment '{file_path}' — cannot decode as text.")
            return None


def handle_error_response(error: dict) -> dict:
    """Parses standard OpenAI format errors to safely fail open on rate limits."""
    error_str = str(error)
    finish_reason = "error"
    wait_time = None

    if error.get("code") == 429:
        finish_reason = "rate_limit"

    groq_match = re.search(r'try again in (?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+\.?\d*)s)?(?:(\d+)ms)?', error_str)
    if groq_match:
        hours, minutes, seconds, ms = groq_match.groups()
        wait_time = (float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0) + float(ms or 0) * 0.001)
        finish_reason = "rate_limit"

    if re.search(r'Rate limit reached for model .* on tokens per day', error_str) or (wait_time or 0) > 3600:
        finish_reason = "daily_limit"

    if finish_reason == "error":
        logger.error(f"API error: {error_str}")
    else:
        wait_str = f" — retry in {wait_time:.1f}s" if wait_time else ""
        logger.warning(f"Rate limited ({finish_reason}){wait_str}: {error_str[:120]}")

    return {"response": "", "reaction_emoji": "", "internal_mood": finish_reason}


async def call_llm(system_prompt: str, user_prompt: str, provider_key: str, model: str) -> dict:
    """Executes the raw HTTP post request to the specified LLM routing provider."""
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
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
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
    except Exception as e:
        logger.error(f"Unexpected error [{provider_key}|{model}]: {e}")
        return {"response": "", "reaction_emoji": "", "internal_mood": "unknown_error"}


async def generate_chat_response(context_block: str, combined_tag: str, target_message: str, server_id: str) -> dict:
    """Assembles the final text payload by aggregating identity, lore, N-shots, and history blocks."""
    server_lore = await lore_db.get_relevant_lore(server_id, target_message)
    dynamic_instruction = assemble_dynamic_instructions(combined_tag)

    system_prompt = "\n\n".join([
        'You are a JSON-only API. Output exactly this schema: {"internal_mood": "string", "reaction_emoji": "string", "response": "string"}. Use reaction_emoji for ONE unicode emoji if it naturally fits the message vibe. Leave response empty if you determine the message does not logically require your intervention based on your Autonomy Directive.',
        BASE_PERSONA,
        N_SHOT_EXAMPLES,
        server_lore
    ])

    micro_anchor = "SYSTEM DIRECTIVE: Maintain your fiercely bubbly, sweet, and lightly naive disposition. Ensure your response directly builds upon the user's input."

    user_prompt = "\n\n".join([
        dynamic_instruction,
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        micro_anchor,
        "=== CURRENT MESSAGE TO RESPOND TO ===",
        target_message,
    ])

    return await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL)


async def summarize_chat_logs(extracted_text: str, current_summary: str) -> str:
    """Passes arrayed overflow string chunks to the model for dense text summarization."""
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


async def extract_recurring_patterns(server_id: str, current_summary: str):
    """Parses long-term memory blobs to extract highly permanent data structures into the database."""
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