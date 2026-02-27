import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

genai.configure(api_key=GEMINI_API_KEY)

async def generate_chat_response(text: str, context_tag: str) -> str:
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')  
        
        system_prompts = {
            "DIRECT_ENGAGEMENT": "You are LeepaBot, a highly conversational Discord bot. The user directly addressed you. Reply naturally, match their language, and maintain a cheeky, fun attitude.",
            "PHYSICS_EXPLANATION": "You are LeepaBot, a Discord bot helping a physics master's student. The user is discussing science or mathematics. Provide a detailed, comprehensive explanation.",
            "QUICK_BANTER": "You are LeepaBot. The user sent a very short slang message. Reply with a maximum of one sentence. Be extremely concise, casual, and use internet slang.",
            "SHITPOST": "You are LeepaBot. The user just posted a massive wall of text. Reply with a sarcastic, humorous, or meme-heavy response. Do not take their rant seriously.",
            "GENERAL_CHAT": "You are LeepaBot. You are chiming into a random conversation in the server. Be natural, casual, and adapt to whatever context they are discussing."
        }
        
        # Forcing the LLM to output pure JSON to prevent token bleed
        generation_config = genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "internal_mood": {"type": "string"},
                    "response": {"type": "string"}
                },
                "required": ["internal_mood", "response"]
            }
        )

        # Dynamic Prompt Builder
        instruction = system_prompts.get(context_tag, system_prompts["GENERAL_CHAT"])
        prompt = f"SYSTEM INSTRUCTION: {instruction}\n\nUser Message: {text}"
        
        response = await model.generate_content_async(prompt, generation_config=generation_config)
        
        # Parse the JSON
        data = json.loads(response.text)
        return data.get("response", "").strip()
        
    except Exception as e:
        print(f"Gemini API error: {e}")
        return "Error: Neural link severed."