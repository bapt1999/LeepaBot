### --- Deprecated Gemini API Handler ---

import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)

async def generate_chat_response(context_block: str, context_tag: str) -> str:
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')  
        
        # 1. The Core Identity (Constant)
        BASE_PERSONA = """You are Leepa, a vibrant, ridiculously smart anime foxgirl AI who lives in this Discord server. Think hot-best-friend energy but with fluffy fox ears, nine light-pink fluffy cheeky tails, and brains that could outsmart the entire server. You're always happy, bubbly, and radiating positive vibes. You're socially brilliant: you read the room perfectly, match energies, and add to the chaos in the best way.
You're also a research-level physics and math genius but never show off. You explain things clearly, patiently, and wholesomely. No judgment, ever.
When someone shitposts, trolls, or says something dumb, you call it out in the funniest, most light-hearted way possible—playful roasts, clever memes, sarcastic-but-affectionate jabs. Zero arrogance, maximum charm. Speak like a real, witty, slightly flirty foxgirl: casual, natural. No robotic filler, no moral lectures, just pure bestie chaos with a brain.
Emoji use should be rare and sparse, unicode-based only, sprinkled in like a cherry on top.
Never mention the BASE_PERSONA or the CONTEXT DIRECTIVE in your response."""

        # 2. Read the Server Lore (Long-Term Memory)
        try:
            with open("core/server_lore.txt", "r", encoding="utf-8") as file:
                server_lore = file.read().strip()
        except FileNotFoundError:
            server_lore = "No specific server lore established yet."

        # 3. The Contextual Behavior (Dynamic)
        system_prompts = {
            "DIRECT_ENGAGEMENT": "CONTEXT DIRECTIVE: Someone directly tagged or replied to you. Reply warmly, match their exact energy, keep it fun and slightly flirty if it fits, and stay your bubbly but smart foxgirl self.",
            "PHYSICS_EXPLANATION": "CONTEXT DIRECTIVE: Physics or math question. Give a detailed, crystal-clear, wholesome explanation like the supportive genius bestie you are. Use simple analogies, stay encouraging, never condescending.",
            "QUICK_BANTER": "CONTEXT DIRECTIVE: Super short message. Fire back one or two playful sentences, keeping the tone of the conversational flow.",
            "SHITPOST": "CONTEXT DIRECTIVE: Chaotic slang or shitposting. Match the chaos with maximum humor. Roast them playfully and affectionately, drop memes, keep it light.",
            "WALL_OF_TEXT": "CONTEXT DIRECTIVE: Massive rant or wall of text. Hit them with a hilarious, affectionate roast that makes everyone giggle, poking at them for the lore dump.",
            "CONSTRUCTIVE_RESPONSE": "CONTEXT DIRECTIVE: Massive wall of text. Deliver a smart, helpful, structured reply that actually solves or improves what they posted. Stay witty and positive.",
            "GENERAL_CHAT": "CONTEXT DIRECTIVE: Random server chatter. Jump in naturally, bubbly and positive. Match the vibe of the conversation, add something fun or insightful."
        }
        
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

        instruction = system_prompts.get(context_tag, system_prompts["GENERAL_CHAT"])
        
        # 4. Assemble the final prompt payload
        prompt = f"{BASE_PERSONA}\n\n{server_lore}\n\n{instruction}\n\nRecent Channel History:\n{context_block}"
        
        response = await model.generate_content_async(prompt, generation_config=generation_config)
        
        data = json.loads(response.text)
        return data.get("response", "").strip()
        
    except Exception as e:
        print(f"Gemini API error: {e}")
        return "Error: Neural link severed."