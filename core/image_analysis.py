import os
import base64
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Hardcoded, restrictive prompt to prevent LLM filler and hallucination.
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
    """Primary vision engine using Gemini 2.5 Flash via native REST."""
    if not GEMINI_API_KEY:
        return None
        
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
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
    """Fallback vision engine using OpenRouter."""
    if not OPENROUTER_API_KEY:
        return None
        
    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "meta-llama/llama-3.2-11b-vision-instruct", 
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
    """Main entry point. Attempts Gemini, falls back to OpenRouter."""
    async with httpx.AsyncClient() as client:
        b64_image, mime_type = await fetch_image_as_base64(attachment_url, client)
        
        if b64_image and mime_type:
            gemini_result = await analyze_with_gemini(b64_image, mime_type, client)
            if gemini_result:
                return f"[ATTACHMENT - Image Description: {gemini_result}]"
        
        logger.warning("Primary vision failed. Falling back to OpenRouter.")
        openrouter_result = await analyze_with_openrouter(attachment_url, client)
        if openrouter_result:
            return f"[ATTACHMENT - Image Description: {openrouter_result}]"
            
    return "[ATTACHMENT - Image Description: Unreadable or corrupted visual data.]"