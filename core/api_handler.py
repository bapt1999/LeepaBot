import os
import json
import httpx
import base64
import mimetypes
import re
import logging
from dotenv import load_dotenv
from core.prompts import BASE_PERSONA, N_SHOT_EXAMPLES, AVAILABLE_EMOJIS

load_dotenv()
logger = logging.getLogger(__name__)

ACTIVE_PROFILE = "gemini_3_flash"  

USE_N_SHOTS = True  # Set to True to inject N_SHOT_EXAMPLES into the prompt. Set to False to only operate on her base sysprompt.

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
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/models", "key": os.getenv("GEMINI_API_KEY")},
}

# ---------------------------------------------------------
# TEMPERATURE: STATIC HIGH-ENTROPY
# ---------------------------------------------------------
STATIC_TEMPERATURE = 0.85

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

async def call_llm(system_prompt: str, user_prompt: str, provider_key: str, model: str, thermal_scalar: float = 0.85) -> dict:
    """Executes the raw HTTP post request, injecting entropy parameters across all providers."""
    provider = PROVIDERS.get(provider_key)
    if not provider or not provider.get("key"):
        logger.error(f"Provider '{provider_key}' is not configured or missing API key.")
        return {"response": f"Error: Provider '{provider_key}' unavailable.", "reaction_emoji": ""}

    client = await get_http_client()

    # ---------------------------------------------------------
    # NATIVE GEMINI ROUTING
    # ---------------------------------------------------------
    if provider_key == "gemini":
        final_temp = round(thermal_scalar * 1.8, 2)
        endpoint = f"{provider['url']}/{model}:generateContent?key={provider['key']}"
        headers = {"Content-Type": "application/json"}
        
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": final_temp,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingLevel": "HIGH"} 
            }
        }
        
        try:
            response = await client.post(endpoint, headers=headers, json=payload)
            result = response.json()
            
            if "error" in result:
                return handle_error_response(result["error"])
                
            content = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\s*```$", "", content)
            return json.loads(content)
            
        except Exception as e:
            logger.error(f"Gemini Native Error [{model}]: {e}")
            return {"response": "", "reaction_emoji": "", "internal_mood": "error"}

    # ---------------------------------------------------------
    # STANDARD OPENAI COMPATIBILITY ROUTING 
    # ---------------------------------------------------------
    else:
        final_temp = round(thermal_scalar * 0.9, 2)
        endpoint = f"{provider['url']}/chat/completions"
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
            "temperature": final_temp,
            "frequency_penalty": 0.4,
            "presence_penalty": 0.4
        }

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

async def generate_chat_response(context_block: str, engagement_level: str, target_message: str, server_id: str) -> dict:
    """Assembles the final text payload. LTM has been completely severed from this context window."""
    
    # Base prompt components
    prompt_parts = [
        'You are a JSON-only API. Output exactly this schema: {"thinking_block": "string", "internal_mood": "string", "reaction_emoji": "string", "response": "string"}. Keep the thinking_block as a single, plain-text string without line breaks or double quotes. Use reaction_emoji for ONE emoji if it naturally fits the message vibe. Leave response empty if you determine the message does not logically require your intervention based on your Autonomy Directive.',
        f"AVAILABLE CUSTOM EMOJIS:\n{AVAILABLE_EMOJIS}\n\nCRITICAL EMOJI RULE: You MUST output the exact full string (e.g., `<:dogekek:1436270391520792586>`). NEVER use the human shortcode.",
        BASE_PERSONA
    ]

    # Conditionally inject the N-Shots
    if USE_N_SHOTS:
        prompt_parts.append(N_SHOT_EXAMPLES)

    system_prompt = "\n\n".join(prompt_parts)

    micro_anchor = "SYSTEM DIRECTIVE: Maintain your zero-ego, partner-in-crime energy. Your response MUST be a definitive, declarative statement. NO QUESTION MARKS."
    engagement_hint = "Context: You were explicitly pinged or mentioned." if engagement_level in ["DIRECT", "QUOTED"] else "Context: This is an ambient conversation. Read the room and decide if jumping in is funny, or if you should stay silent."
    
    user_prompt = "\n\n".join([
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        micro_anchor,
        "=== CURRENT MESSAGE TO RESPOND TO ===",
        target_message,
        f"[{engagement_hint}]"
    ])

    return await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL, thermal_scalar=STATIC_TEMPERATURE)

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
        result = await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL, thermal_scalar=0.1)
        return result.get("response", "").strip()
    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        return ""