import os
import base64
import logging
import httpx
from dotenv import load_dotenv
from core.api_handler import get_http_client

load_dotenv()
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Vision engines. Kept separate from the chat PROFILES in api_handler on purpose:
# vision requirements (multimodal input, low latency, cheap) differ from chat.
VISION_MODEL_PRIMARY = "gemini-2.5-flash"
# NOTE: the previous fallback (meta-llama/llama-3.2-11b-vision-instruct) is
# delisted from OpenRouter on July 17, 2026. Llama 4 Scout is its natively
# multimodal, cheaper successor.
VISION_MODEL_FALLBACK = "meta-llama/llama-4-scout"

# Restrictive instructions to prevent LLM filler and hallucination.
VISION_PROMPT = (
    "Analyze this image. Identify the subject, any visible text, and the visual medium/vibe "
    "(e.g., photograph, compressed meme, AI-generated, screenshot). "
    "Output ONLY a dense, plain-text description. Do not use conversational filler like 'This image shows'."
)

async def fetch_image_as_base64(url: str, client: httpx.AsyncClient) -> tuple[str | None, str | None]:
    """Downloads the image into memory and converts it to base64 for Gemini."""
    try:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        mime_type = response.headers.get('Content-Type', 'image/jpeg')
        b64_image = base64.b64encode(response.content).decode('utf-8')
        return b64_image, mime_type
    except Exception as e:
        logger.error(f"Failed to download image from {url}: {e}")
        return None, None

async def analyze_with_gemini(b64_image: str, mime_type: str, client: httpx.AsyncClient) -> str | None:
    """Primary vision engine using Gemini via native REST."""
    if not GEMINI_API_KEY:
        return None

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{VISION_MODEL_PRIMARY}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"text": VISION_PROMPT},
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": b64_image
                    }
                }
            ]
        }]
    }

    try:
        response = await client.post(endpoint, json=payload, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Gemini vision analysis failed: {e}")
        return None

async def analyze_with_openrouter(url: str, client: httpx.AsyncClient) -> str | None:
    """Fallback vision engine using OpenRouter. Takes the raw URL, so it also
    covers the case where the local image download failed."""
    if not OPENROUTER_API_KEY:
        return None

    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": VISION_MODEL_FALLBACK,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": url}}
                ]
            }
        ]
    }

    try:
        response = await client.post(endpoint, headers=headers, json=payload, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"OpenRouter vision analysis failed: {e}")
        return None

async def analyze_image(attachment_url: str) -> str:
    """Main entry point. Attempts Gemini, falls back to OpenRouter.
    Each failure path logs its actual cause before falling through."""
    client = await get_http_client()

    b64_image, mime_type = await fetch_image_as_base64(attachment_url, client)

    if b64_image and mime_type:
        gemini_result = await analyze_with_gemini(b64_image, mime_type, client)
        if gemini_result:
            logger.info(f"[VISION] Described by {VISION_MODEL_PRIMARY} ({len(gemini_result)} chars).")
            return f"[ATTACHMENT - Image Description: {gemini_result}]"
        logger.warning("[VISION] Primary vision engine failed. Falling back to OpenRouter.")
    else:
        logger.warning("[VISION] Image download failed; Gemini skipped. Falling back to OpenRouter via direct URL.")

    openrouter_result = await analyze_with_openrouter(attachment_url, client)
    if openrouter_result:
        logger.info(f"[VISION] Described by {VISION_MODEL_FALLBACK} ({len(openrouter_result)} chars).")
        return f"[ATTACHMENT - Image Description: {openrouter_result}]"

    logger.error("[VISION] All vision engines failed for this image.")
    return "[ATTACHMENT - Image Description: Unreadable or corrupted visual data.]"