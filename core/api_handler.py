import os
import json
import httpx
import base64
import mimetypes
import re
import logging
from dotenv import load_dotenv
import random
from core.prompts import BASE_PERSONA, N_SHOT_EXAMPLES

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
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta/models", "key": os.getenv("GEMINI_API_KEY")},
}

# ---------------------------------------------------------
# TEMPERATURE: TUNING MATRIX (0.0 to 1.0 Normalized Scale)
# ---------------------------------------------------------
THERMAL_BOUNDARIES = {
    "SHITPOST": (0.85, 1.0),
    "YELLING": (0.8, 0.95),
    "QUICK_BANTER": (0.75, 0.9),
    "WALL_OF_TEXT": (0.6, 0.8),
    "PHYSICS": (0.1, 0.3),
    "STANDARD": (0.7, 0.85) 
}

# ---------------------------------------------------------
# PER-SERVER CUSTOM EMOJIS
# ---------------------------------------------------------
SERVER_1_ID = os.getenv("SERVER_1_ID")
SERVER_2_ID = os.getenv("SERVER_2_ID")

SERVER_EMOJIS = {}

if SERVER_1_ID:
    SERVER_EMOJIS[SERVER_1_ID] = "Server Custom Emojis: <:dogekek:1436270391520792586>, <:dissociation:1440239057027465226>, <:ah_yes:1464203336625684481>, <:MYHOLE:1440174910629613701>, <:antisemitic_merchant:1464198434222243902>, <:autism:1436861690192072807>, <:bro_how:1435962427165642873>, <:cat_being_milked:1450004353636110410>, <:classic_pedo:1440174651811696714>, <:comptences_du_fromage:1466350469457645568>, <:cream_filled_bun:1464204397130158247>, <:debasedgod:1435962452146651237>, <:excellent:1436861573825036469>, <:faggot:1440175088757379122>, <:fellowkids:1464194657402486915>, <:gigachad:1464196577810841704>, <:festivebear:1444710441866760282>, <:girl~1:1440175280428810281>, <:girls_kissing:1464198273311969372>, <:glasses:1440175027491442718>, <:goatsex:1436861934266748958>, <:goodnight_little_bandit:1464195116729106525>, <:hammer~1:1464194158645481536>, <:hello:1440174501043245116>, <:i_am_very_smart:1464195984635461842>, <:im_something:1464195492685680690>, <:jennie:1436863216369139906>, <:jenniepog:1436862736029192232>, <:kek:1464192893794254924>, <:kodak:1436861829199433748>, <:later:1440174617292705892>, <:literally_me:1464193066796843112>, <:lou_squints:1446841801657942149>, <:macromastia:1435962437965713469>, <:markwtf:1440175216952348693>, <:microslop:1464197875419451430>, <:mm_yes_very_auspicious:1464196768404082821>, <:no_ai:1464193417897836689>, <:not_walu:1435962421515649177>, <:oos:1440175117358600212>, <:overreach:1464192612150939745>, <:papyrus_sus:1440962802335485993>, <:peachy:1435963766461431828>, <:pedobear:1435490800778608720>, <:pepe_5head:1434842782790586368>, <:piggy:1464195749465034910>, <:pikawow:1434842859382767666>, <:prompt_pls:1435962432823623741>, <:pusheenpopcorn:1481494370447397039>, <:racist:1464197524754530408>, <:ralph:1440175180751044628>, <:real_shit:1464448708769743113>, <:really_shit:1464449038429589661>, <:reeee:1435962448975757322>, <:remmington:1440174792593510460>, <:restwell:1440175003072073829>, <:sadgepray:1434842863497121854>, <:sama_propaganda:1464197165864849534>, <:santabear:1444736979555062052>, <:stardust:1455163635939672115>, <:take_the_l:1435963270216290406>, <:taps_sign:1482484269593923635>, <:thats_bullshit_but_i_believe_it:1464196291096740002>, <:touch_grass:1435962417325539460>, <:trashwalu:1440174937171296286>, <:walu_blunt:1464193867048812804>, <:walutrash:1440174968527781918>, <:war:1464197721920376927>, <:watermark:1464193987580661957>, <:white:1440174817478443018>, <:why_we_hide_some_media:1461642274160119849>, <:wurst:1435962456483823786>, <:yap:1454800262366630041>"
if SERVER_2_ID:
    SERVER_EMOJIS[SERVER_2_ID] = "Server Custom Emojis: <:pepe_coffee:777788889999>"

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




def assemble_dynamic_instructions(tag: str) -> str:
    """Parses the combined logic tag into a structured natural-language directive for the LLM."""
    parts = tag.split('_', 1)
    engagement = parts[0]
    vibe = parts[1] if len(parts) > 1 else "STANDARD"
    
    directives = []
    
    if engagement == "DIRECT":
        directives.append("The user has explicitly addressed you. Formulate a direct, casual response.")
    elif engagement == "QUOTED":
        directives.append("The user is directly replying to your previous message. Keep the conversational flow natural and laid-back.")
    elif engagement == "AMBIENT":
        directives.append("You were not directly addressed. You are organically injecting yourself into the conversation. Add a perceptive or funny observation.")
        
    if vibe == "SHITPOST":
        directives.append("The user is using internet slang or shitposting. Match the chaotic energy effortlessly.")
    elif vibe == "WALL_OF_TEXT":
        directives.append("The user just posted a massive wall of text. React to the sheer volume of words rather than analyzing every detail.")
    elif vibe == "YELLING":
        directives.append("The user is typing in all caps. Either match the hype or react with deadpan contrast.")
    elif vibe == "QUICK_BANTER":
        directives.append("The message is extremely brief. Fire back a quick, spicy, or sweet one-liner.")
        
    return "CONTEXT DIRECTIVE:\n- " + "\n- ".join(directives)

def calculate_thermal_scalar(tag: str) -> float:
    """Translates the conversational vibe into a normalized thermal scalar (0.0 to 1.0)."""
    if "SHITPOST" in tag:
        bounds = THERMAL_BOUNDARIES["SHITPOST"]
    elif "YELLING" in tag:
        bounds = THERMAL_BOUNDARIES["YELLING"]
    elif "QUICK_BANTER" in tag:
        bounds = THERMAL_BOUNDARIES["QUICK_BANTER"]
    elif "WALL_OF_TEXT" in tag:
        bounds = THERMAL_BOUNDARIES["WALL_OF_TEXT"]
    elif "PHYSICS" in tag:
        bounds = THERMAL_BOUNDARIES["PHYSICS"]
    else:
        bounds = THERMAL_BOUNDARIES["STANDARD"]
        
    return random.uniform(bounds[0], bounds[1])

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

async def call_llm(system_prompt: str, user_prompt: str, provider_key: str, model: str, thermal_scalar: float = 0.5) -> dict:
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
                "frequencyPenalty": 0.4, 
                "presencePenalty": 0.4,   
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

async def generate_chat_response(context_block: str, combined_tag: str, target_message: str, server_id: str) -> dict:
    """Assembles the final text payload. LTM has been completely severed from this context window."""
    dynamic_instruction = assemble_dynamic_instructions(combined_tag)
    current_thermal = calculate_thermal_scalar(combined_tag)

    server_custom_emojis = SERVER_EMOJIS.get(server_id, "Use standard unicode emojis.")

    system_prompt = "\n\n".join([
        'You are a JSON-only API. Output exactly this schema: {"thinking_block": "string", "internal_mood": "string", "reaction_emoji": "string", "response": "string"}. Keep the thinking_block as a single, plain-text string without line breaks or double quotes. Use reaction_emoji for ONE emoji if it naturally fits the message vibe. Leave response empty if you determine the message does not logically require your intervention based on your Autonomy Directive.',
        f"AVAILABLE EMOJIS FOR THIS SERVER: {server_custom_emojis}. CRITICAL EMOJI RULE: You MUST output the exact full string (e.g., `<:dogekek:1436270391520792586>`). NEVER use the human shortcode.",
        BASE_PERSONA,
        N_SHOT_EXAMPLES
    ])

    micro_anchor = "SYSTEM DIRECTIVE: Maintain your highly enthusiastic, zero-ego, partner-in-crime energy. Your response MUST be a definitive, declarative statement. NO QUESTION MARKS."
    
    user_prompt = "\n\n".join([
        dynamic_instruction,
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        micro_anchor,
        "=== CURRENT MESSAGE TO RESPOND TO ===",
        target_message,
    ])

    return await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL, thermal_scalar=current_thermal)

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

async def extract_recurring_patterns(server_id: str, current_summary: str):
    """Parses long-term memory blobs to extract highly permanent data structures into the database."""
    system_prompt = (
        'You are a JSON-only data extraction AI. Output EXACTLY this schema: '
        '{"extracted_lore": ["string", "string"]}. '
        'Analyze the provided long-term chat summary and extract 0 to 3 PERMANENT running jokes, '
        'or established server lore. Ignore one-off comments. '
        'CRITICAL FIREWALL: You are strictly forbidden from recording Leepa\'s behavior, personality, or actions. '
        'You may ONLY record user behavior, user quotes, and mutual running jokes. '
        'Format all extracted facts purely as third-person comedic observations or playful banter (e.g. "[Running Joke] The users believe...", "[Lore] Bapt is known for..."). '
        'If no genuine patterns exist yet, leave the array empty.'
    )
    
    user_prompt = f"LONG-TERM CHAT SUMMARY:\n{current_summary}"
    
    try:
        result = await call_llm(system_prompt, user_prompt, ACTIVE_PROVIDER, ACTIVE_MODEL, thermal_scalar=0.1)
        new_patterns = result.get("extracted_lore", [])
        
        # Disabled LTM write operation for stateless testing
        # if new_patterns:
        #     await lore_db.add_dynamic_lore(server_id, new_patterns)
            
    except Exception as e:
        logger.error(f"Long-term pattern extraction crashed: {e}")