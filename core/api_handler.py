import os
import json
import httpx
import re
import logging
import random
from datetime import datetime
from dotenv import load_dotenv
from core.prompts import BASE_PERSONA, N_SHOT_EXAMPLES, AVAILABLE_EMOJIS, ENTROPY_WORDS

load_dotenv()
logger = logging.getLogger(__name__)

ACTIVE_PROFILE = "deepseek_openrouter"

USE_N_SHOTS = True  # Set to True to inject N_SHOT_EXAMPLES into the prompt. Set to False to only operate on her base sysprompt.

PROFILES = {
    "groq_llama": {
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "capabilities": {"native_thinking": False, "temp_scalar": 0.9}
    },
    "groq_qwen": {
        "provider": "groq",
        "model": "qwen/qwen3-32b",
        "capabilities": {"native_thinking": False, "temp_scalar": 0.9}
    },
    "openrouter_qwen": {
        "provider": "openrouter",
        "model": "qwen/qwen3-next-80b-a3b-instruct:free",
        "capabilities": {"native_thinking": False, "temp_scalar": 0.9}
    },
    "deepseek_openrouter": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-chat-v3-0324",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.9},
        "provider_routing": {"only": ["novita", "siliconflow"], "order": ["novita", "siliconflow"]} # Allowed providers, prefer Novita first
    },
    "gemini_flash": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.8}
    },
    "gemini_3_flash": {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "capabilities": {"native_thinking": True, "temp_scalar": 1.8}
    },
    "deepseek_chat": {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "capabilities": {"native_thinking": False, "temp_scalar": 0.9}
    }
}

PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1", "key": os.getenv("GROQ_API_KEY")},
    "deepseek": {"url": "https://api.deepseek.com/v1", "key": os.getenv("DEEPSEEK_API_KEY")},
    "openrouter": {"url": "https://openrouter.ai/api/v1", "key": os.getenv("OPENROUTER_API_KEY")},
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/models", "key": os.getenv("GEMINI_API_KEY")},
}

# ---------------------------------------------------------
# TEMPERATURE: JITTERED HIGH-ENTROPY
# ---------------------------------------------------------
# A fresh normalized scalar is drawn uniformly from this range on every chat
# call, then multiplied by the active profile's temp_scalar to reach that
# provider's usable band. One mechanism, per-model ranges via the scalar:
#   deepseek_openrouter (x1.9): 0.86 - 2.00  (the target: coherent -> feral)
#   gemini (x1.8):              0.81 - 1.89
#   openai-compat (x0.9):       0.41 - 0.95
# Temperature is per-token, so it only bites where the model is genuinely
# uncertain (inside the strings). The JSON frame stays fixed regardless.
TEMP_JITTER_RANGE = (0.45, 1.05)

# Memory compression must stay deterministic and factual. Never jitter this.
SUMMARY_TEMPERATURE = 0.10


def draw_jittered_temperature() -> float:
    """Draws a fresh normalized thermal scalar from the jitter range."""
    return round(random.uniform(*TEMP_JITTER_RANGE), 3)


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
# JSON SALVAGE PIPELINE
# ---------------------------------------------------------
# LLMs — especially at high temperature — do not reliably emit one clean JSON
# object. Observed and anticipated failure shapes: markdown fences, chatter
# around the object, the object wrapped in an array, bare lists/scalars,
# truncated output (unterminated string / unclosed braces), and non-string
# values in string fields. parse_json_payload is the single choke point every
# provider branch goes through, so all of this is handled once, here.

# Fields the downstream logic treats as strings (.strip() etc.).
EXPECTED_STRING_FIELDS = ("thinking_block", "internal_mood", "reaction_emoji", "response")


def _extract_json_objects(content: str) -> list[str]:
    """Returns every top-level {...} substring in the text, respecting string
    literals and escapes, so objects buried in chatter are recovered. If the
    text ends mid-object, the truncated tail is included as a repair candidate."""
    objects = []
    depth = 0
    start = None
    in_string = False
    escape = False

    for i, ch in enumerate(content):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objects.append(content[start:i + 1])
                    start = None

    if depth > 0 and start is not None:
        objects.append(content[start:])  # truncated object; repair may save it

    return objects


def _repair_truncated_json(content: str) -> str:
    """Best-effort repair for output cut off mid-generation: closes an
    unterminated string, drops dangling separators/orphaned keys, and closes
    any unclosed braces/brackets. The result is only used if json.loads
    accepts it, so a failed repair costs nothing."""
    stack = []
    in_string = False
    escape = False

    for ch in content:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    body = content
    if escape:
        body = body[:-1]  # drop a dangling backslash
    if in_string:
        body += '"'

    # Drop separators that would make the closure invalid, e.g. '{"a": "b",'
    # or an orphaned '"key":' with no value.
    body = body.rstrip()
    while body and body[-1] in ",:":
        if body[-1] == ":":
            cut = max(body.rfind(",", 0, len(body) - 1), body.rfind("{", 0, len(body) - 1))
            if cut == -1:
                break
            body = body[:cut + 1] if body[cut] == "{" else body[:cut]
        else:
            body = body[:-1]
        body = body.rstrip()

    for opener in reversed(stack):
        body += "}" if opener == "{" else "]"
    return body


def _first_dict(parsed) -> dict | None:
    """Accepts whatever json.loads produced and digs out the first dict:
    the object itself, or the first dict inside a wrapping list."""
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
    return None


def _normalize_fields(data: dict) -> dict:
    """Coerces the known schema fields to strings so downstream .strip() calls
    can never crash: None becomes '', nested structures become their JSON text,
    numbers become their string form. Unknown extra keys are left untouched."""
    for key in EXPECTED_STRING_FIELDS:
        value = data.get(key, "")
        if value is None:
            data[key] = ""
        elif isinstance(value, (dict, list)):
            data[key] = json.dumps(value, ensure_ascii=False)
        elif not isinstance(value, str):
            data[key] = str(value)
    return data


def parse_json_payload(content: str) -> dict:
    """Turns a raw LLM output string into a usable schema dict, trying
    progressively more aggressive salvage strategies. Raises ValueError only
    when nothing object-shaped can be recovered; the provider branches catch
    that, log the raw content, and fail open as before."""
    raw = content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)

    # Candidate strings, cheapest first: the whole text, then each balanced
    # object found inside it. dict.fromkeys dedupes while preserving order.
    candidates = list(dict.fromkeys([raw] + _extract_json_objects(raw)))

    for cand_idx, candidate in enumerate(candidates):
        for repaired, variant in ((False, candidate), (True, _repair_truncated_json(candidate))):
            if repaired and variant == candidate:
                continue  # repair changed nothing; skip the duplicate attempt
            try:
                parsed = json.loads(variant)
            except (json.JSONDecodeError, ValueError):
                continue
            found = _first_dict(parsed)
            if found is None:
                continue  # valid JSON but no object anywhere (e.g. bare [1])
            if cand_idx > 0 or repaired:
                logger.warning(
                    f"JSON salvage engaged (candidate #{cand_idx}, repaired={repaired}). "
                    f"Raw output was not a clean object:\n{content.strip()[:500]}"
                )
            return _normalize_fields(found)

    raise ValueError("No JSON object could be salvaged from model output.")


def handle_error_response(error: dict) -> dict:
    """Parses standard OpenAI format errors to safely fail open on rate limits.
    The returned internal_mood (rate_limit / daily_limit / error) intentionally
    flows into Leepa's STM so she is aware of her own outages."""
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

async def call_llm(system_prompt: str, user_prompt: str, profile_key: str, thermal_scalar: float | None = None) -> dict:
    """Executes the raw HTTP post request for a given profile, injecting entropy parameters across all providers.
    thermal_scalar=None (the default) draws a fresh jittered temperature for this call.
    Pass an explicit value to pin the temperature (e.g., the summarizer)."""
    profile = PROFILES.get(profile_key)
    if not profile:
        logger.error(f"Profile '{profile_key}' does not exist.")
        return {"response": "", "reaction_emoji": "", "internal_mood": "error"}

    if thermal_scalar is None:
        thermal_scalar = draw_jittered_temperature()

    provider_key = profile["provider"]
    model = profile["model"]
    capabilities = profile.get("capabilities", {"native_thinking": False, "temp_scalar": 1.0})

    provider = PROVIDERS.get(provider_key)
    if not provider or not provider.get("key"):
        logger.error(f"Provider '{provider_key}' is not configured or missing API key.")
        return {"response": f"Error: Provider '{provider_key}' unavailable.", "reaction_emoji": ""}

    # Scale the normalized draw to the provider's band. Computed once, provider-agnostic.
    final_temp = round(thermal_scalar * capabilities.get("temp_scalar", 1.0), 2)
    logger.info(f"[SAMPLING] {profile_key} → {model} | temp={final_temp} (norm={thermal_scalar})")

    client = await get_http_client()

    # ---------------------------------------------------------
    # NATIVE GEMINI ROUTING
    # ---------------------------------------------------------
    if provider_key == "gemini":
        endpoint = f"{provider['url']}/{model}:generateContent?key={provider['key']}"
        headers = {"Content-Type": "application/json"}

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": final_temp,
                "responseMimeType": "application/json"
            }
        }

        if capabilities.get("native_thinking"):
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "HIGH"}

        content = None
        try:
            response = await client.post(endpoint, headers=headers, json=payload)
            result = response.json()

            if "error" in result:
                return handle_error_response(result["error"])

            content = result["candidates"][0]["content"]["parts"][0]["text"]
            return parse_json_payload(content)

        except Exception as e:
            logger.error(f"Gemini Native Error [{model}]: {e}")
            if content is not None:
                logger.error(f"Raw content that failed to parse:\n{content}")
            return {"response": "", "reaction_emoji": "", "internal_mood": "error"}

    # ---------------------------------------------------------
    # STANDARD OPENAI COMPATIBILITY ROUTING
    # ---------------------------------------------------------
    else:
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

        # Per-model OpenRouter routing preferences, declared in the profile
        # ("only" = allowlist, "ignore" = denylist, "order" = priority).
        # require_parameters is deliberately NOT used: it would exclude any
        # backend that ignores response_format (e.g. NovitaAI), collapsing the
        # allowlist to a single provider. The JSON salvage pipeline handles
        # unconstrained output instead.
        if provider_key == "openrouter":
            routing = profile.get("provider_routing")
            if routing:
                payload["provider"] = routing

        content = None
        try:
            response = await client.post(endpoint, headers=headers, json=payload)
            result = response.json()

            if "error" in result:
                return handle_error_response(result["error"])

            content = result["choices"][0]["message"]["content"]
            return parse_json_payload(content)

        except httpx.TimeoutException as e:
            logger.error(f"Timeout [{provider_key}|{model}]: {e}")
            return {"response": "", "reaction_emoji": "", "internal_mood": "timeout"}
        except Exception as e:
            logger.error(f"Unexpected error [{provider_key}|{model}]: {e}")
            if content is not None:
                logger.error(f"Raw content that failed to parse:\n{content}")
            return {"response": "", "reaction_emoji": "", "internal_mood": "unknown_error"}

async def generate_chat_response(context_block: str, engagement_level: str, target_message: str) -> dict:
    """Constructs the system and user prompts, then calls the LLM for a structured JSON response.
    Omitting thermal_scalar lets call_llm draw a fresh jittered temperature."""

    # Current date for context injection
    current_date = datetime.now().strftime("%A, %B %d, %Y") # e.g., "Saturday, June 27, 2026"

    # Base prompt components
    prompt_parts = [
        f"Current Date: {current_date}",
        'You are a JSON-only API. Output exactly this schema: {"thinking_block": "string", "internal_mood": "string", "reaction_emoji": "string", "response": "string"}. Keep the thinking_block as a single, plain-text string without line breaks or double quotes. Use reaction_emoji for ONE emoji if it naturally fits the message vibe. Leave response empty if you determine the message does not logically require your intervention based on your Autonomy Directive.',
        f"AVAILABLE CUSTOM EMOJIS:\n{AVAILABLE_EMOJIS}\n\nCRITICAL EMOJI RULE: You MUST output the exact full string (e.g., `<:dogekek:1436270391520792586>`). NEVER use the human shortcode.",
        BASE_PERSONA
    ]

    # Conditionally inject the N-Shots
    if USE_N_SHOTS:
        prompt_parts.append(N_SHOT_EXAMPLES)

    system_prompt = "\n\n".join(prompt_parts)

    seed_word = random.choice(ENTROPY_WORDS)
    micro_anchor = f"SYSTEM DIRECTIVE: Make sure to prioritize your instructions. Your response MUST build upon the previous message and expand the conversation outward. Your thinking_block MUST open with the word '{seed_word}'."
    engagement_hint = "Context: You were explicitly pinged or mentioned." if engagement_level in ["DIRECT", "QUOTED"] else "Context: This is an ambient conversation. Read the room and decide if jumping in is funny, or if you should stay silent."

    user_prompt = "\n\n".join([
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        micro_anchor,
        "=== CURRENT MESSAGE TO RESPOND TO ===",
        target_message,
        f"[{engagement_hint}]"
    ])

    return await call_llm(system_prompt, user_prompt, ACTIVE_PROFILE)

async def summarize_chat_logs(extracted_text: str, current_summary: str) -> str:
    """Passes arrayed overflow string chunks to the model for dense text summarization."""
    system_prompt = (
        'You are a JSON-only data compression AI. Output EXACTLY this schema: '
        '{"response": "string"}. '
        'In the "response" field, write a dense 2-3 sentence summary of the provided chat logs, '
        'merging it with any previous summary. Keep it strictly factual and concise.'
    )

    user_prompt = f"PREVIOUS SUMMARY:\n{current_summary}\n\nNEW LOGS TO COMPRESS:\n{extracted_text}" if current_summary else f"NEW LOGS TO COMPRESS:\n{extracted_text}"

    try:
        result = await call_llm(system_prompt, user_prompt, ACTIVE_PROFILE, thermal_scalar=SUMMARY_TEMPERATURE)
        return result.get("response", "").strip()
    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        return ""