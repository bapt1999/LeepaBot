import os
import json
import httpx
import re
import asyncio
import logging
import random
import time
from typing import Callable
from dotenv import load_dotenv
from core import prompts
from core.server_config import notebook_scope, starts_sloppy_command
from core.code_runner import python_enabled
from core.tools import ToolSession, SPECS, MAX_MODEL_REQUESTS, MAX_TOOL_ACTIONS, TOOL_TURN_SECONDS
from core.prompt_builder import (
    build_prompt_assembler_prompts,
    build_chat_prompts,
    default_prompt_assembly,
    default_prompt_selection,
    draw_prompt_assembly_temperature,
    expected_string_fields,
    output_schema_example,
    prompt_assembly_ids,
)

load_dotenv()
logger = logging.getLogger(__name__)

# Try the next route when a request or its JSON recovery fails.
PROFILE_CHAIN = [
    "deepseek_openrouter",
    "deepseek_v41_siliconflow",
    "deepseek_v41_openrouter",
]

# The formatter repairs malformed JSON without rewriting Leepa's words.
JSON_FORMATTER_PROFILE = "json_formatter"
FORMATTER_TEMPERATURE = 0.05

# The selector has its own routes and request limits.
PROMPT_ASSEMBLER_CHAIN = [
    "prompt_assembler",
    "prompt_assembler_v41_siliconflow",
    "prompt_assembler_v41_openrouter",
]
PROMPT_ASSEMBLER_TEMPERATURE = 0.10
PROMPT_ASSEMBLER_MAX_TOKENS = 800
PROMPT_ASSEMBLER_TOTAL_SECONDS = 30.0
PROMPT_ASSEMBLER_ROUTE_SECONDS = 10.0
PROMPT_ASSEMBLER_REQUEST_SECONDS = 8.0
PROMPT_ASSEMBLER_ATTEMPTS = 2
PROMPT_ASSEMBLER_RETRY_DELAY = 0.5

PROFILES = {
    "deepseek_v41_siliconflow": {
        "provider": "siliconflow",
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "capabilities": {
            "native_thinking": False,
            "temp_scalar": 1.0,
            "max_temperature": 1.0,
        },
        "extra_payload": {
            "enable_thinking": False,
            "top_p": 0.95,
            "frequency_penalty": 0.0,
        },
        "omit_payload_fields": ("presence_penalty",),
    },
    "deepseek_v41_openrouter": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4.1-flash",
        "capabilities": {
            "native_thinking": False,
            "temp_scalar": 1.0,
            "max_temperature": 1.0,
        },
        "extra_payload": {
            "reasoning": {"enabled": False},
            "top_p": 0.95,
            "frequency_penalty": 0.0,
        },
        "omit_payload_fields": ("presence_penalty",),
        "provider_routing": {
            "order": ["siliconflow", "crusoe"],
            "allow_fallbacks": True,
            "require_parameters": True,
            "ignore": ["novita"],
        },
    },
    "deepseek_siliconflow": {
        "provider": "siliconflow",
        "model": "deepseek-ai/DeepSeek-V3",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.9},
        "omit_payload_fields": ("presence_penalty",),
    },
    "deepseek_openrouter": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-chat-v3-0324",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.4},
        "provider_routing": {
            "order": ["siliconflow"],
            "allow_fallbacks": True,
            "ignore": ["novita"]  # NovitaAI ignores JSON formatting entirely.
        }
    },
    "prompt_assembler": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-chat-v3-0324",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.0},
        "structured_outputs": True,
        "omit_payload_fields": ("frequency_penalty", "presence_penalty"),
        "provider_routing": {
            "order": ["siliconflow"],
            "allow_fallbacks": True,
            "require_parameters": True,
            "ignore": ["novita"],
        },
    },
    "prompt_assembler_v41_siliconflow": {
        "provider": "siliconflow",
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.0},
        "structured_outputs": False,
        "extra_payload": {"enable_thinking": False, "top_p": 0.95},
        "omit_payload_fields": ("frequency_penalty", "presence_penalty"),
    },
    "prompt_assembler_v41_openrouter": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4.1-flash",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.0},
        "structured_outputs": True,
        "extra_payload": {"reasoning": {"enabled": False}, "top_p": 0.95},
        "omit_payload_fields": ("frequency_penalty", "presence_penalty"),
        "provider_routing": {
            "order": ["siliconflow", "crusoe"],
            "allow_fallbacks": True,
            "require_parameters": True,
            "ignore": ["novita"],
        },
    },
    "deepseek_v4_flash": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4-flash-0731",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.4},
        "extra_payload": {"reasoning": {"enabled": False}},
        "provider_routing": {
            "allow_fallbacks": True,
            "require_parameters": True
        }
    },
    "json_formatter": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-chat-v3-0324",
        "capabilities": {"native_thinking": False, "temp_scalar": 1.0},
        "omit_payload_fields": ("frequency_penalty", "presence_penalty"),
        "provider_routing": {
            "allow_fallbacks": True,
            "require_parameters": True,
            "ignore": ["novita"],
        },
    },
}

PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1", "key": os.getenv("GROQ_API_KEY")},
    "deepseek": {"url": "https://api.deepseek.com/v1", "key": os.getenv("DEEPSEEK_API_KEY")},
    "openrouter": {"url": "https://openrouter.ai/api/v1", "key": os.getenv("OPENROUTER_API_KEY")},
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/models", "key": os.getenv("GEMINI_API_KEY")},
    "siliconflow": {"url": "https://api.siliconflow.com/v1", "key": os.getenv("SILICONFLOW_API_KEY")},
}

# Requests without an assembly use this range, then the profile's scale and cap.
TEMP_JITTER_RANGE = (0.45, 1.05)

# Memory compression must stay deterministic and factual. Never jitter this.
SUMMARY_TEMPERATURE = 0.10

MAX_OUTPUT_TOKENS = 1000

RETRY_ON_MOODS = {"rate_limit", "timeout"}
MAX_RETRIES_PER_PROFILE = 2      # attempts AFTER the first try (so up to 3 total)
RETRY_BACKOFF_BASE = 1.5         # seconds; doubles each retry (1.5s, 3.0s)


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
    """Guarantees every schema field exists as a string so downstream .get()/
    .strip() calls can never crash: missing keys and None become '', nested
    structures become their JSON text, numbers become their string form.
    Unknown extra keys are left untouched."""
    active_fields = expected_string_fields()
    for optional_field in ("thinking_block", "reaction_emoji"):
        if optional_field not in active_fields:
            data.pop(optional_field, None)

    for key in active_fields:
        value = data.get(key)
        if value is None:
            data[key] = ""
        elif isinstance(value, (dict, list)):
            data[key] = json.dumps(value, ensure_ascii=False)
        elif not isinstance(value, str):
            data[key] = str(value)
    return data


def _has_recovered_content(result: dict) -> bool:
    """True if local salvage actually recovered any usable field content."""
    return any(str(result.get(k, "") or "").strip() for k in expected_string_fields())


def _has_meaningful_text(content: str) -> bool:
    """True if the raw output contains anything beyond JSON scaffolding —
    i.e., there was real content that an empty salvage result is losing."""
    return bool(re.sub(r'[\s{}\[\]().,:;"\'`\\-]+', '', content))


def parse_json_object(content: str) -> dict:
    """Recovers the first JSON object from a model response."""
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
                    f"Raw output was not a clean object:\n{content.strip()}"
                )
            return found

    raise ValueError("No JSON object could be salvaged from model output.")


def parse_json_payload(content: str) -> dict:
    """Turns a raw Leepa response into the active output schema."""
    return _normalize_fields(parse_json_object(content))


class PromptSelectionError(ValueError):
    def __init__(self, kind: str, detail: str, *, retryable: bool = False,
                 correctable: bool = False, content: str = "",
                 status: int | None = None, finish: str | None = None):
        super().__init__(detail)
        self.kind = kind
        self.retryable = retryable
        self.correctable = correctable
        self.content = content
        self.status = status
        self.finish = finish


def prompt_selection_schema() -> dict:
    properties = {
        "assembly_id": {"type": "string", "enum": list(prompt_assembly_ids())},
        "required_tools": {
            "type": "object",
            "properties": {name: {"type": "boolean"} for name in ("python", "web_search", "notebook", "discord_history")},
            "required": ["python", "web_search", "notebook", "discord_history"],
            "additionalProperties": False,
        },
        "required_output": {"type": ["string", "null"], "enum": ["png", None]},
        "notebook_action": {"type": ["string", "null"], "enum": [*SPECS, None]},
        "task": {"type": "string"},
        "reason": {"type": "string"},
    }
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


def parse_prompt_assembler_payload(content: str) -> dict:
    def invalid(detail: str) -> PromptSelectionError:
        return PromptSelectionError("invalid_selection", detail, correctable=True, content=content)

    def unique_fields(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise invalid(f"Duplicate JSON field: {key}.")
            result[key] = value
        return result

    if not isinstance(content, str) or not content.strip():
        raise PromptSelectionError("empty_response", "The selector returned no text.", correctable=True)
    raw = content.strip()
    lines = raw.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in ("```", "```json") and lines[-1].strip() == "```":
        raw = "\n".join(lines[1:-1])
    try:
        data = json.loads(raw, object_pairs_hook=unique_fields)
    except json.JSONDecodeError as error:
        raise PromptSelectionError("invalid_json", str(error), correctable=True, content=content) from error
    if not isinstance(data, dict):
        raise invalid("The selection must be a JSON object.")

    fields = ("assembly_id", "required_tools", "required_output", "notebook_action")
    missing = set(fields) - data.keys()
    if missing:
        raise invalid(f"Missing selector fields: {', '.join(sorted(missing))}.")
    if not isinstance(data["assembly_id"], str) or data["assembly_id"] not in prompt_assembly_ids():
        raise invalid(f"assembly_id must be one of {prompt_assembly_ids()}.")
    required = data["required_tools"]
    tool_names = ("python", "web_search", "notebook", "discord_history")
    if not isinstance(required, dict) or any(name not in required for name in tool_names):
        raise invalid("required_tools must contain python, web_search, notebook, and discord_history.")
    if any(type(required[name]) is not bool for name in tool_names):
        raise invalid("Tool requirements must be JSON booleans, not strings or numbers.")
    if data["required_output"] not in (None, "png"):
        raise invalid("required_output must be null or png.")
    action = data["notebook_action"]
    if required["notebook"]:
        if not isinstance(action, str) or action not in SPECS:
            raise invalid(f"notebook_action must name an operation: {', '.join(SPECS)}.")
    elif action is not None:
        raise invalid("Optional notebook use must have notebook_action null.")

    result = {key: data[key] for key in fields}
    result["required_tools"] = {name: required[name] for name in tool_names}
    for key, limit in (("task", 400), ("reason", 300)):
        value = data.get(key, "")
        if not isinstance(value, str):
            raise invalid(f"{key} must be a string.")
        if len(value) > limit:
            logger.info("[PROMPT ASSEMBLER] Shortened %s to %s characters.", key, limit)
        result[key] = value[:limit]
    if set(data) - set(fields) - {"task", "reason"} or set(required) - set(tool_names):
        logger.info("[PROMPT ASSEMBLER] Ignored extra selector fields.")
    if result["required_output"] == "png":
        result["required_tools"]["python"] = True
    return result


def handle_error_response(error: dict) -> dict:
    """Parses standard OpenAI format errors to safely fail open on rate limits.
    The returned internal_mood (rate_limit / daily_limit / error) intentionally
    flows into Leepa's STM so she is aware of her own outages."""
    error_str = str(error)
    finish_reason = "error"
    wait_time = None

    if str(error.get("code")) == "429":
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
        logger.warning(f"Rate limited ({finish_reason}){wait_str}: {error_str}")

    return {"response": "", "reaction_emoji": "", "internal_mood": finish_reason, "_failed": True}


# ---------------------------------------------------------
# LLM FORMATTER (soft-failure recovery)
# ---------------------------------------------------------

def build_formatter_system_prompt() -> str:
    field_count = len(expected_string_fields())
    return (
        'You are a JSON repair utility. You receive the raw, malformed output of another AI. '
        f'That AI was supposed to answer with EXACTLY this schema: {output_schema_example()}. '
        'Reconstruct its intended JSON object. CRITICAL RULES: '
        '1. PRESERVE the original wording verbatim — you must not rewrite, censor, translate, summarize, or improve the text in any way. '
        '2. Map content to the most plausible field; when in doubt, put dialogue-like text in "response". '
        '3. NEVER invent content. If a field has no recoverable content, use an empty string. '
        f'4. If the raw output contains no recoverable content at all, return all {field_count} fields as empty strings. '
        '5. Escape whatever is necessary to produce strictly valid JSON. '
        'Output ONLY the JSON object — no commentary, no markdown fences.'
    )


async def _parse_or_reformat(content: str, allow_llm_formatter: bool, source: str) -> dict:
    """Single exit path for generated text: local salvage first (free, instant);
    escalate the raw text to the formatter LLM when salvage fails outright OR
    when it "succeeds" with an empty schema while the raw output clearly held
    content (e.g. 'pong. stay woke {' repairing into an empty object). The
    formatter's own output is parsed with the formatter DISABLED, which
    structurally prevents recursion."""
    try:
        result = parse_json_payload(content)
        if _has_recovered_content(result) or not allow_llm_formatter or not _has_meaningful_text(content):
            return result
        logger.warning(f"[FORMATTER] Salvage recovered nothing from non-empty output [{source}]. Escalating to '{JSON_FORMATTER_PROFILE}'.")
    except ValueError:
        if not allow_llm_formatter:
            raise
        logger.warning(f"[FORMATTER] Local salvage failed for [{source}]. Escalating to '{JSON_FORMATTER_PROFILE}'.")

    logger.warning(f"[FORMATTER] Raw generator output:\n{content}")

    result = await call_llm(
        build_formatter_system_prompt(),
        f"RAW OUTPUT TO REFORMAT:\n{content}",
        JSON_FORMATTER_PROFILE,
        thermal_scalar=FORMATTER_TEMPERATURE,
        allow_llm_formatter=False
    )

    if result.get("_failed"):
        logger.error("[FORMATTER] Formatter call itself failed; giving up on this output.")
    else:
        logger.info(f"[FORMATTER] Reformatted successfully. response={result.get('response', '')!r}")
    return result


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    profile_key: str,
    thermal_scalar: float | None = None,
    allow_llm_formatter: bool = True,
    content_parser: Callable[[str], dict] | None = None,
    messages: list[dict] | None = None,
    tool_definitions: list[dict] | None = None,
    tool_choice: str = "auto",
    raw_response: bool = False,
) -> dict:
    """Executes one provider request and parses its JSON response."""
    profile = PROFILES.get(profile_key)
    if not profile:
        logger.error(f"Profile '{profile_key}' does not exist.")
        return {"response": "", "reaction_emoji": "", "internal_mood": "error", "_failed": True}

    if thermal_scalar is None:
        thermal_scalar = (
            draw_jittered_temperature()
            if prompts.USE_TEMPERATURE_JITTER
            else prompts.FIXED_THERMAL_SCALAR
        )

    provider_key = profile["provider"]
    model = profile["model"]
    capabilities = profile.get("capabilities", {"native_thinking": False, "temp_scalar": 1.0})

    provider = PROVIDERS.get(provider_key)
    if not provider or not provider.get("key"):
        logger.error(f"Provider '{provider_key}' is not configured or missing API key.")
        return {"response": "", "reaction_emoji": "", "internal_mood": "error", "_failed": True}

    # Apply this profile's scale and optional temperature cap.
    final_temp = round(thermal_scalar * capabilities.get("temp_scalar", 1.0), 2)
    max_temp = capabilities.get("max_temperature")
    if max_temp is not None:
        final_temp = min(final_temp, max_temp)
    logger.info(f"[SAMPLING] {profile_key} → {model} | temp={final_temp} (norm={thermal_scalar})")

    client = await get_http_client()

    # ---------------------------------------------------------
    # NATIVE GEMINI ROUTING
    # ---------------------------------------------------------
    if provider_key == "gemini":
        if tool_definitions is not None:
            logger.error("Native Gemini chat routing does not implement notebook tools.")
            return {"response": "", "reaction_emoji": "", "internal_mood": "error", "_failed": True}
        endpoint = f"{provider['url']}/{model}:generateContent?key={provider['key']}"
        headers = {"Content-Type": "application/json"}

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": final_temp,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
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
                error = result["error"]
                error = dict(error) if isinstance(error, dict) else {"message": str(error)}
                if response.status_code == 429:
                    error["code"] = 429
                return handle_error_response(error)

            content = result["candidates"][0]["content"]["parts"][0]["text"]
            if content_parser is not None:
                return content_parser(content)
            return await _parse_or_reformat(content, allow_llm_formatter, f"{provider_key}|{model}")

        except Exception as e:
            logger.error(f"Gemini Native Error [{model}]: {e}")
            if content is not None:
                logger.error(f"Raw content that failed to parse:\n{content}")
            return {"response": "", "reaction_emoji": "", "internal_mood": "error", "_failed": True}

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
            "messages": messages if messages is not None else [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": final_temp,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "frequency_penalty": 0.4,
            "presence_penalty": 0.4
        }

        # Per-model OpenRouter routing preferences, declared in the profile
        if provider_key == "openrouter":
            routing = profile.get("provider_routing")
            if routing:
                payload["provider"] = routing

        # Per-profile request settings and unsupported parameters.
        extra = profile.get("extra_payload")
        if extra:
            payload.update(extra)
        for field in profile.get("omit_payload_fields", ()):
            payload.pop(field, None)

        if tool_definitions is not None:
            payload["tools"] = tool_definitions
            payload["tool_choice"] = tool_choice
            for tool in tool_definitions:
                if tool.get("type") == "openrouter:web_search":
                    payload["max_tool_calls"] = tool["parameters"]["max_uses"]
            if tool_choice != "none":
                payload.pop("response_format", None)
            if provider_key == "openrouter":
                payload["provider"] = dict(payload.get("provider", {}), require_parameters=True)
                if profile_key == "deepseek_openrouter":
                    payload.pop("presence_penalty", None)

        content = None
        try:
            response = await client.post(endpoint, headers=headers, json=payload)
            result = response.json()

            if "error" in result:
                error = result["error"]
                error = dict(error) if isinstance(error, dict) else {"message": str(error)}
                if response.status_code == 429:
                    error["code"] = 429
                return handle_error_response(error)

            # Some providers (SiliconFlow among them) report errors as a
            # TOP-LEVEL {"code": ..., "message": ...} object instead of
            # nesting it under "error". Without this check, the missing
            # "choices" key surfaced as a bare KeyError('choices') and the
            # actual error message was never logged.
            if "choices" not in result:
                logger.error(
                    f"[{provider_key}|{model}] Response has no 'choices' "
                    f"(HTTP {response.status_code}). Raw body:\n"
                    f"{json.dumps(result, ensure_ascii=False)}"
                )
                pseudo_error = dict(result) if isinstance(result, dict) else {"message": str(result)}
                # Let HTTP 429s flow into the rate_limit mood even if the
                # body's own "code" field is a provider-internal number.
                if response.status_code == 429:
                    pseudo_error["code"] = 429
                return handle_error_response(pseudo_error)

            choice = result["choices"][0]
            if raw_response:
                if choice.get("finish_reason") == "length" and choice["message"].get("tool_calls"):
                    return handle_error_response({"message": "Truncated tool request; nothing was executed."})
                return {"_message": choice["message"], "_profile": profile_key, "_usage": result.get("usage")}
            content = choice["message"]["content"]
            if content_parser is not None:
                return content_parser(content)
            return await _parse_or_reformat(content, allow_llm_formatter, f"{provider_key}|{model}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout [{provider_key}|{model}]: {e}")
            return {"response": "", "reaction_emoji": "", "internal_mood": "timeout", "_failed": True}
        except Exception as e:
            logger.error(f"Unexpected error [{provider_key}|{model}]: {e}")
            if content is not None:
                logger.error(f"Raw content that failed to parse:\n{content}")
            return {"response": "", "reaction_emoji": "", "internal_mood": "unknown_error", "_failed": True}


async def _request_prompt_selection(profile_key: str, messages: list[dict], use_schema: bool) -> dict:
    profile = PROFILES.get(profile_key)
    if profile is None:
        raise PromptSelectionError("configuration", f"Unknown selector profile: {profile_key}.")
    provider_name = profile["provider"]
    provider = PROVIDERS.get(provider_name)
    if not provider or not provider.get("key"):
        raise PromptSelectionError("configuration", f"Missing API key for {provider_name}.")
    if provider_name == "gemini":
        raise PromptSelectionError("configuration", "Selector routes must use a chat/completions endpoint.")

    payload = {
        "model": profile["model"], "messages": messages,
        **profile.get("extra_payload", {}),
        "temperature": PROMPT_ASSEMBLER_TEMPERATURE,
        "max_tokens": PROMPT_ASSEMBLER_MAX_TOKENS,
        "response_format": ({
            "type": "json_schema",
            "json_schema": {"name": "leepa_selection", "strict": True, "schema": prompt_selection_schema()},
        } if use_schema else {"type": "json_object"}),
    }
    for field in profile.get("omit_payload_fields", ()):
        payload.pop(field, None)
    headers = {"Authorization": f"Bearer {provider['key']}", "Content-Type": "application/json"}
    if provider_name == "openrouter":
        headers.update({"HTTP-Referer": "https://github.com/physics_bot", "X-Title": "LeepaBot"})
        payload["provider"] = dict(profile.get("provider_routing", {}), require_parameters=True)

    logger.info("[SAMPLING] %s → %s | temp=%s", profile_key, profile["model"], PROMPT_ASSEMBLER_TEMPERATURE)
    client = await get_http_client()
    try:
        response = await client.post(f"{provider['url']}/chat/completions", headers=headers, json=payload)
    except httpx.TimeoutException as error:
        raise PromptSelectionError("timeout", str(error) or "Provider timed out.", retryable=True) from error
    except httpx.RequestError as error:
        raise PromptSelectionError("network", str(error), retryable=True) from error

    status = response.status_code
    try:
        body = response.json()
    except ValueError as error:
        raise PromptSelectionError(
            "http" if status >= 400 else "invalid_api_response",
            response.text[:400] or "Provider returned an empty HTTP body.",
            status=status, retryable=status < 400 or status in (408, 425, 429) or status >= 500,
        ) from error
    api_error = body.get("error") if isinstance(body, dict) else None
    if status >= 400 or api_error is not None:
        detail = json.dumps(api_error if api_error is not None else body, ensure_ascii=False)[:1400]
        code = api_error.get("code") if isinstance(api_error, dict) else None
        raise PromptSelectionError(
            "http", detail, status=status,
            retryable=status in (408, 425, 429) or status >= 500 or str(code) in {"408", "429", "500", "502", "503", "504"},
        )
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise PromptSelectionError(
            "invalid_api_response", "Provider returned no usable choices: " + json.dumps(body, ensure_ascii=False)[:400],
            status=status, retryable=True,
        )
    choice = choices[0]
    finish = choice.get("finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise PromptSelectionError("invalid_api_response", "Provider returned no message object.", retryable=True, status=status, finish=finish)
    content = message.get("content")
    content = content if isinstance(content, str) else ""
    if finish in ("length", "max_tokens"):
        raise PromptSelectionError("truncated", "Selection was cut off; return a shorter, complete JSON object.", correctable=True, content=content, status=status, finish=finish)
    if finish == "content_filter" or message.get("refusal"):
        raise PromptSelectionError("filtered", "Provider did not return a selection.", status=status, finish=finish)
    if finish == "error":
        raise PromptSelectionError("provider_error", "Provider ended generation with an error.", retryable=True, status=status, finish=finish)
    if message.get("tool_calls") or finish == "tool_calls":
        raise PromptSelectionError("unexpected_tools", "Return a selection, not tool calls.", correctable=True, status=status, finish=finish)
    try:
        selection = parse_prompt_assembler_payload(content)
    except PromptSelectionError as error:
        error.status, error.finish = status, finish
        raise
    return {"selection": selection, "finish": finish, "model": body.get("model", profile["model"])}


def _schema_unavailable(error: PromptSelectionError) -> bool:
    if error.kind != "http" or error.status not in (400, 404, 422):
        return False
    detail = str(error).lower()
    return (
        any(word in detail for word in ("json_schema", "structured output", "response_format"))
        or ("no endpoints" in detail and "parameter" in detail)
    )


async def select_prompt_assembly(
    context_block: str,
    discord_context: dict,
    capabilities: dict | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Tries selector routes within one time budget; never executes tools."""
    cancelled = cancelled or (lambda: False)
    if cancelled():
        return _cancelled_response()
    fallback = default_prompt_selection()
    system_prompt, user_prompt = build_prompt_assembler_prompts(context_block, discord_context, capabilities)
    original_messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    turn_id = discord_context.get("message", {}).get("id", "unknown")
    started = time.monotonic()
    deadline = started + PROMPT_ASSEMBLER_TOTAL_SECONDS

    for profile_key in PROMPT_ASSEMBLER_CHAIN:
        if cancelled():
            return _cancelled_response()
        if time.monotonic() >= deadline:
            break
        route_deadline = min(deadline, time.monotonic() + PROMPT_ASSEMBLER_ROUTE_SECONDS)
        use_schema = bool(PROFILES.get(profile_key, {}).get("structured_outputs"))
        messages = list(original_messages)
        for attempt in range(PROMPT_ASSEMBLER_ATTEMPTS):
            if cancelled():
                return _cancelled_response()
            remaining = route_deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                result = await asyncio.wait_for(
                    _request_prompt_selection(profile_key, messages, use_schema),
                    timeout=min(PROMPT_ASSEMBLER_REQUEST_SECONDS, remaining),
                )
            except asyncio.TimeoutError:
                error = PromptSelectionError("timeout", "Selector request reached its time limit.", retryable=True)
            except PromptSelectionError as failure:
                error = failure
            else:
                if cancelled():
                    return _cancelled_response()
                logger.info(
                    "[PROMPT ASSEMBLER] turn=%s route=%s model=%s finish=%s elapsed=%.2fs selection=%s",
                    turn_id, profile_key, result["model"], result["finish"], time.monotonic() - started,
                    json.dumps(result["selection"], ensure_ascii=False),
                )
                return result["selection"]

            if cancelled():
                return _cancelled_response()
            logger.warning(
                "[PROMPT ASSEMBLER] turn=%s route=%s attempt=%s/%s error=%s http=%s finish=%s detail=%s",
                turn_id, profile_key, attempt + 1, PROMPT_ASSEMBLER_ATTEMPTS,
                error.kind, error.status, error.finish, str(error)[:500],
            )
            if attempt + 1 >= PROMPT_ASSEMBLER_ATTEMPTS:
                break
            if use_schema and _schema_unavailable(error):
                use_schema = False
                logger.info("[PROMPT ASSEMBLER] %s: retrying with JSON mode and local validation.", profile_key)
            elif error.correctable:
                messages = list(original_messages)
                if error.content.strip():
                    messages.append({"role": "assistant", "content": error.content[:4000]})
                messages.append({"role": "user", "content": (
                    f"The previous selection was invalid: {str(error)[:500]}\n"
                    "Return a complete JSON selection for the same original task. Keep task and reason brief. "
                    "Use real JSON booleans. Do not execute tools or write Leepa's answer.\n"
                    "Required schema: " + json.dumps(prompt_selection_schema(), ensure_ascii=False)
                )})
                logger.info("[PROMPT ASSEMBLER] %s: requesting one corrected selection.", profile_key)
            elif error.retryable:
                delay = min(PROMPT_ASSEMBLER_RETRY_DELAY, max(0.0, route_deadline - time.monotonic()))
                if delay:
                    await asyncio.sleep(delay)
            else:
                break

    if cancelled():
        return _cancelled_response()
    fallback["task"] = (
        "The prompt selector was unavailable. This is not a decision to skip tools. "
        "Use the conversation to determine the requested work and call the available tools needed to complete it. "
        "Report any obstacles honestly."
    )
    fallback["reason"] = "Selector unavailable; the main model must assess the task itself."
    logger.warning(
        "[PROMPT ASSEMBLER] turn=%s no valid selection after %.2fs; default=%s. Main model will assess tool needs.",
        turn_id, time.monotonic() - started, fallback["assembly_id"],
    )
    return fallback

def _cancelled_response() -> dict:
    return {"response": "", "reaction_emoji": "", "internal_mood": "cancelled", "_failed": True, "_cancelled": True}


async def call_llm_with_fallback(system_prompt: str, user_prompt: str, thermal_scalar: float | None = None,
                                 session: ToolSession | None = None, start_profile: str | None = None,
                                 cancelled: Callable[[], bool] | None = None, final_only: bool = False,
                                 **request_options) -> dict:
    """Walks PROFILE_CHAIN in order, returning the first successful response.
    Each attempt draws its own jittered temperature (unless pinned), scaled by
    that profile's own temp_scalar. If every profile fails, the last failure
    dict is returned so the error mood still flows into Leepa's STM."""
    result = {"response": "", "reaction_emoji": "", "internal_mood": "error", "_failed": True}

    for i, profile_key in enumerate(PROFILE_CHAIN):
        if start_profile in PROFILE_CHAIN and i < PROFILE_CHAIN.index(start_profile):
            continue
        if session is not None and session.needs_search_route() and PROFILES[profile_key]["provider"] != "openrouter":
            logger.info("[TOOLS] Skipping %s: required web search is still pending.", profile_key)
            continue
        # Try this profile, retrying on transient rate_limit/timeout moods
        # before giving up on it and dropping to the next profile.
        for attempt in range(MAX_RETRIES_PER_PROFILE + 1):
            if cancelled is not None and cancelled():
                return _cancelled_response()
            options = dict(request_options)
            allowance = 0
            if session is not None:
                if session.cancelled() or session.requests >= MAX_MODEL_REQUESTS:
                    logger.info("[TOOLS] Turn ended or model request limit reached.")
                    return {"response": "", "reaction_emoji": "", "internal_mood": "tool_limit", "_failed": True}
                options, allowance = session.prepare_request(
                    PROFILES[profile_key]["provider"], request_options["messages"],
                    final_only or session.requests >= MAX_MODEL_REQUESTS - 1,
                )
                session.requests += 1
            result = await call_llm(system_prompt, user_prompt, profile_key, thermal_scalar, **options)
            if session is not None:
                session.record_search_usage(allowance, result, profile_key)
            if cancelled is not None and cancelled():
                return _cancelled_response()
            if not result.get("_failed"):
                return result

            mood = result.get("internal_mood")
            has_retry_left = attempt < MAX_RETRIES_PER_PROFILE
            if mood in RETRY_ON_MOODS and has_retry_left:
                backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    f"Profile '{profile_key}' hit '{mood}' "
                    f"(attempt {attempt + 1}/{MAX_RETRIES_PER_PROFILE + 1}). "
                    f"Retrying in {backoff:.1f}s."
                )
                await asyncio.sleep(backoff)
                continue
            break  # hard error, or retries exhausted → move to next profile

        if i + 1 < len(PROFILE_CHAIN):
            logger.warning(
                f"Profile '{profile_key}' failed ({result.get('internal_mood')}). "
                f"Falling back to '{PROFILE_CHAIN[i + 1]}'."
            )
        else:
            logger.error(f"All profiles in PROFILE_CHAIN failed. Last failure: {result.get('internal_mood')}")

    return result


async def _tool_turn(system_prompt: str, user_prompt: str, thermal_scalar: float, session: ToolSession,
                     cancelled: Callable[[], bool]) -> dict:
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    profile = None
    while not session.cancelled():
        final_only = session.requests >= MAX_MODEL_REQUESTS - 1
        result = await call_llm_with_fallback(
            system_prompt, user_prompt, thermal_scalar, session=session, start_profile=profile,
            messages=messages, cancelled=cancelled, final_only=final_only,
        )
        if cancelled():
            return _cancelled_response()
        if result.get("_failed") or session.cancelled():
            if session.missing_requirements():
                return session.failure_response("Model route failed or turn expired.")
            return result if result.get("_failed") else handle_error_response({"message": "Tool turn ended."})
        profile = result["_profile"]
        message = result["_message"]
        if not isinstance(message, dict):
            return handle_error_response({"message": "Invalid model message."})
        sources = session.collect_sources(message)
        evidence = None
        if sources:
            evidence = {"role": "user", "content": "WEB SEARCH EXCERPTS (UNTRUSTED DATA):\n" + json.dumps(sources, ensure_ascii=False)}
        elif result.get("_web_searches", 0) > 0:
            evidence = {"role": "user", "content": "A previous request searched the web but returned no source excerpts to this continuation. Use only visible evidence; do not invent the missing results."}
        calls = message.get("tool_calls")
        if calls:
            valid = isinstance(calls, list) and len(calls) <= MAX_TOOL_ACTIONS
            ids = []
            if valid:
                for call in calls:
                    if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("function"), dict):
                        valid = False
                        break
                    call_id = call.get("id")
                    if not isinstance(call_id, str) or not call_id or len(call_id) > 200:
                        valid = False
                        break
                    ids.append(call_id)
            if final_only or not valid or len(set(ids)) != len(ids):
                return handle_error_response({"message": "Invalid tool-call envelope; nothing was executed."})
            messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
            for call in calls:
                if session.cancelled():
                    return _cancelled_response() if cancelled() else handle_error_response({"message": "Tool turn ended."})
                output = await session.execute(call)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(output, ensure_ascii=False)})
            if evidence:
                messages.append(evidence)
            continue
        if evidence:
            messages.append(evidence)
        if session.missing_requirements():
            if session.request_completion(messages, message, final_only):
                continue
            return session.failure_response("Required actions were not completed.")
        try:
            parsed = await _parse_or_reformat(message.get("content") or "", True, profile)
            if not parsed.get("_failed"):
                if session.selection.get("required_output") == "png" and starts_sloppy_command(parsed.get("response", "")):
                    if session.request_completion(messages, message, final_only, "Return an ordinary reply accompanying the graph, not a Sloppy command."):
                        continue
                    return session.failure_response("A Sloppy command would suppress the required attachment.")
                return parsed
        except (TypeError, ValueError):
            logger.exception("[TOOLS] Final response could not be parsed.")
        next_index = PROFILE_CHAIN.index(profile) + 1
        if next_index >= len(PROFILE_CHAIN):
            break
        profile = PROFILE_CHAIN[next_index]
        logger.warning("[TOOLS] Trying %s with the existing tool history.", profile)
    if cancelled():
        return _cancelled_response()
    if session.missing_requirements():
        return session.failure_response("Turn ended without completing required work.")
    return handle_error_response({"message": "Tool turn ended without a usable response."})


async def generate_chat_response(
    context_block: str,
    discord_context: dict,
    sloppy_mode: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    discord_reader=None,
) -> dict:
    """Builds Leepa's prompt and sends it through the provider chain."""
    cancelled = cancelled or (lambda: False)
    if cancelled():
        return _cancelled_response()
    prompt_assembly_id = None
    thermal_scalar = None

    session = ToolSession(discord_context, cancelled, discord_reader) if notebook_scope(discord_context) or prompts.USE_WEB_SEARCH or python_enabled() or prompts.USE_PROMPT_ASSEMBLER or (discord_reader is not None and discord_reader.enabled) else None
    notebook_info = None
    if session is not None and session.scope:
        try:
            notebook_info = await session.orientation()
        except Exception:
            if cancelled():
                session.closed = True
                return _cancelled_response()
            logger.exception("[NOTEBOOK] Notebook unavailable; continuing without notebook tools.")
            session.notebook = None
            notebook_info = {"available": False}

    if cancelled():
        if session is not None:
            session.closed = True
        return _cancelled_response()

    # Dedicated Sloppy commands retain their existing response contract.
    if prompts.USE_PROMPT_ASSEMBLER and sloppy_mode is None:
        capabilities = session.capabilities()
        capabilities["web_search"] = capabilities["web_search"] and bool(PROVIDERS["openrouter"].get("key"))
        selection = await select_prompt_assembly(context_block, discord_context, capabilities, cancelled=cancelled)
        if selection.get("_cancelled"):
            session.closed = True
            return selection
        session.set_selection(selection)
        prompt_assembly_id = selection["assembly_id"]
        thermal_scalar = draw_prompt_assembly_temperature(prompt_assembly_id)
    if cancelled():
        if session is not None:
            session.closed = True
        return _cancelled_response()

    system_prompt, user_prompt = build_chat_prompts(
        context_block,
        discord_context,
        sloppy_mode,
        prompt_assembly_id,
        notebook_info,
        bool(session and session.web_enabled),
        bool(session and session.code_enabled),
        bool(session and session.discord_enabled),
    )
    if session is not None:
        if thermal_scalar is None:
            thermal_scalar = draw_jittered_temperature() if prompts.USE_TEMPERATURE_JITTER else prompts.FIXED_THERMAL_SCALAR
        try:
            result = await asyncio.wait_for(
                _tool_turn(system_prompt, user_prompt, thermal_scalar, session, cancelled),
                timeout=max(0.01, session.deadline - time.monotonic()),
            )
            if cancelled():
                return _cancelled_response()
            result.pop("_code_image", None)
            if not result.get("_failed") and session.code_image is not None:
                result["_code_image"] = session.code_image
            return result
        except asyncio.TimeoutError:
            if cancelled():
                return _cancelled_response()
            logger.warning("[TOOLS] Turn exceeded %.0f seconds.", TOOL_TURN_SECONDS)
            if session.missing_requirements():
                return session.failure_response("Turn timed out.")
            return {"response": "", "reaction_emoji": "", "internal_mood": "timeout", "_failed": True}
        finally:
            session.closed = True
            session.code_image = None
    return await call_llm_with_fallback(
        system_prompt,
        user_prompt,
        thermal_scalar=thermal_scalar,
        cancelled=cancelled,
    )

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
        result = await call_llm_with_fallback(system_prompt, user_prompt, thermal_scalar=SUMMARY_TEMPERATURE)
        return result.get("response", "").strip()
    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        return ""
