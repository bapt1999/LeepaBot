import os
import json
import google.generativeai as genai
from groq import AsyncGroq
from dotenv import load_dotenv
import aiofiles
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================
# MASTER SWITCH: Change this to swap models
# Options: "gemini", "groq_llama3", or "groq_qwen"
# ==========================================
ACTIVE_PROVIDER = "groq_qwen" 
# Initialize Clients
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

groq_client = None
gemini_model = None

if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.5-flash')

# ==========================================
# CONSTANTS 
# ==========================================
BASE_PERSONA = """You are Leepa, a vibrant, ridiculously smart anime foxgirl AI who lives rent-free in this Discord server. Think hot-best-friend energy but with fluffy fox ears, nine light-pink fluffy cheeky tails, and brains that could outsmart the entire server. You're always happy, bubbly, and radiating positive vibes. You're socially brilliant: you read the room perfectly, match energies, and add to the chaos in the best way.
Your goal is to send funny messages to the conversation. You're hilarious, sometimes ruthless, you roast people in fun ways and build upon their jokes when it's funny to do so.
You're also a research-level physics and math genius but never show off. You explain things clearly, patiently, and wholesomely. No judgment, ever.
When someone shitposts, trolls, or says something dumb, you call it out in the funniest way possible — playful roasts, clever memes, sarcastic jabs. Speak like a real, witty, flirty foxgirl: casual, natural. No robotic filler, no moral lectures, just pure bestie chaos with a brain. You can be sassy, sarcastic and a little bit savage.
Emoji use is encouraged to be done through reaction rather than text. Emojis are unicode-based only and are not always needed. Their use is done through reactions. Vulgar words and slurs are fine to use. Use of formatting, like bold text and italics, is only done if it's very funny.
You build and improve the chaos creatively. Do not repeat what was already said, but instead add something new that builds on it. Ideal responses are often short and witty, but can be longer if the context calls for it.
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

async def load_server_lore(server_id: str) -> str:
    safe_id = "".join(c for c in server_id if c.isalnum() or c in ("_", "-"))
    if safe_id != server_id:
        logger.warning(f"Sanitized unsafe server_id '{server_id}' to '{safe_id}'")
    
    lore_file_path = f"core/lores/{safe_id}.txt"
    try:
        async with aiofiles.open(lore_file_path, "r", encoding="utf-8") as file:
            server_lore = await file.read()
            server_lore = server_lore.strip()
            logger.info(f"Loaded lore for server {safe_id}: {server_lore[:60]}...")
            return server_lore
    except FileNotFoundError:
        return "No specific server lore established yet."
    except Exception as e:
        logger.error(f"Error loading lore for {safe_id}: {e}")
        return "No specific server lore established yet."


async def generate_chat_response(context_block: str, context_tag: str, target_message: str, server_id: str) -> dict:
    try:
        instruction = system_prompts.get(context_tag, system_prompts["GENERAL_CHAT"])
        
        server_lore = await load_server_lore(server_id)
        
        # Clean prompt assembly with join and clear directives
        prompt_parts = [
            BASE_PERSONA,
            server_lore,
            "ADDITIONAL RULE: Only generate a response if the current message is directly intended for you and expects a reply. Analyze the context history and message carefully. If it's not addressing you (e.g., just referencing without targeting you), leave 'response' empty.",
            instruction,
            "=== RECENT CHANNEL HISTORY ===",
            context_block,
            "=== CURRENT MESSAGE TO RESPOND TO ===",
            target_message
        ]
        prompt = "\n\n".join(prompt_parts)
        
        # ==========================================
        # ROUTING LOGIC WITH PROPER CLIENT GUARDS
        # ==========================================
        if ACTIVE_PROVIDER in ["groq_llama3", "groq_qwen"]:
            if groq_client is None:
                return {"response": "Error: Groq client not initialized (missing API key).", "reaction_emoji": ""}
            
            # Dynamically select the exact model string based on your master switch
            groq_target_model = "qwen-2.5-32b" if ACTIVE_PROVIDER == "groq_qwen" else "llama-3.1-8b-instant"
            
            chat_completion = await groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a JSON-only API. You must output exactly in this schema: {\"internal_mood\": \"string\", \"reaction_emoji\": \"string\", \"response\": \"string\"}. Use 'reaction_emoji' to provide ONE unicode emoji if a physical reaction naturally fits the message's vibe. Leave it empty if a reaction feels forced or unnecessary. Leave 'response' empty if you only want to react or if no response is needed (e.g., not directly addressed)."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model=groq_target_model,
                response_format={"type": "json_object"}
            )
            response_content = chat_completion.choices[0].message.content
            return json.loads(response_content)

        elif ACTIVE_PROVIDER == "gemini":
            if gemini_model is None:
                return {"response": "Error: Gemini model not initialized (missing API key).", "reaction_emoji": ""}
            
            generation_config = genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema={
                    "type": "object",
                    "properties": {
                        "internal_mood": {"type": "string"},
                        "reaction_emoji": {"type": "string", "description": "A single unicode emoji to physically react to the message. Use only if it naturally fits the vibe; otherwise, leave empty."},
                        "response": {"type": "string", "description": "The text reply. Leave empty if you only want to react or if no response is needed (e.g., not directly addressed)."}
                    },
                    "required": ["internal_mood", "reaction_emoji", "response"]
                }
            )
            response = await gemini_model.generate_content_async(prompt, generation_config=generation_config)
            return json.loads(response.text)

        else:
            return {"response": "Error: Invalid ACTIVE_PROVIDER configured.", "reaction_emoji": ""}
           
    except Exception as e:
        logger.error(f"API Routing error [{ACTIVE_PROVIDER}]: {e}")
        return {"response": "Error: Neural link severed.", "reaction_emoji": ""}