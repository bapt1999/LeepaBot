import os
import math
import logging
import aiofiles
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class LoreDatabase:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        # UPDATED: Swapped to the new gemini-embedding-001 model endpoint
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={self.api_key}"
        self.server_vectors = {} 
        self._client = None

    async def get_client(self):
        """Safely fetches or creates the async HTTP client inside the event loop."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def get_embedding(self, text: str) -> list[float]:
        """Pings Gemini to convert a text string into a mathematical vector."""
        if not self.api_key:
            logger.error("No Gemini API key found for embeddings.")
            return []
        
        payload = {
            "model": "models/gemini-embedding-001", # UPDATED: The new state-of-the-art model
            "content": {"parts": [{"text": text}]}
        }
        try:
            client = await self.get_client()
            response = await client.post(self.endpoint, json=payload)
            result = response.json()
            
            if "error" in result:
                logger.error(f"Gemini API Error during embedding: {result['error']}")
                return []
                
            return result.get("embedding", {}).get("values", [])
        except Exception as e:
            logger.error(f"Embedding calculation failed: {e}")
            return []

    def cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Pure Python calculation of the angle between two multi-dimensional vectors."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot_product = sum(a * b for a, b in zip(v1, v2))
        magnitude1 = math.sqrt(sum(a * a for a in v1))
        magnitude2 = math.sqrt(sum(b * b for b in v2))
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        return dot_product / (magnitude1 * magnitude2)

    async def load_and_chunk_lore(self, server_id: str):
        """Reads the .txt file and vectorizes each paragraph into RAM."""
        safe_id = "".join(c for c in server_id if c.isalnum() or c in ("_", "-"))
        
        # Force absolute pathing so it never loses the file
        base_dir = os.path.dirname(os.path.abspath(__file__))
        lore_file_path = os.path.join(base_dir, "lores", f"{safe_id}.txt")
        
        if safe_id in self.server_vectors:
            return 

        logger.info(f"Attempting to load lore file from: {lore_file_path}")

        try:
            async with aiofiles.open(lore_file_path, "r", encoding="utf-8") as file:
                raw_text = await file.read()
            
            chunks = [chunk.strip() for chunk in raw_text.split("\n\n") if chunk.strip()]
            
            self.server_vectors[safe_id] = []
            for chunk in chunks:
                vector = await self.get_embedding(chunk)
                if vector:
                    self.server_vectors[safe_id].append({"text": chunk, "vector": vector})
            
            logger.info(f"Successfully loaded and vectorized {len(self.server_vectors[safe_id])} lore chunks for server {safe_id}.")
        except FileNotFoundError:
            logger.warning(f"Lore file not found at {lore_file_path}. Operating without lore.")
            self.server_vectors[safe_id] = [] 
        except Exception as e:
            logger.error(f"Failed to load lore for {safe_id}: {e}")
            self.server_vectors[safe_id] = []

    async def get_relevant_lore(self, server_id: str, user_query: str) -> str:
        """Finds the single most mathematically relevant paragraph for the current prompt."""
        safe_id = "".join(c for c in server_id if c.isalnum() or c in ("_", "-"))
        
        if safe_id not in self.server_vectors:
            await self.load_and_chunk_lore(server_id)
            
        lore_data = self.server_vectors.get(safe_id, [])
        if not lore_data:
            return "No specific server lore established yet."
            
        query_vector = await self.get_embedding(user_query)
        if not query_vector:
            return "No specific server lore established yet."
            
        best_chunk = ""
        max_similarity = -1.0
        
        for item in lore_data:
            sim = self.cosine_similarity(query_vector, item["vector"])
            if sim > max_similarity:
                max_similarity = sim
                best_chunk = item["text"]
                
        logger.info(f"Max similarity score for query: {max_similarity:.3f}")
        
        if max_similarity > 0.25:
            return f"RELEVANT SERVER LORE:\n{best_chunk}"
            
        return "No specific server lore established yet."
    
    async def add_dynamic_lore(self, server_id: str, new_facts: list):
        """Appends new facts to the text file and instantly vectorizes them into RAM."""
        if not new_facts:
            return

        safe_id = "".join(c for c in server_id if c.isalnum() or c in ("_", "-"))
        base_dir = os.path.dirname(os.path.abspath(__file__))
        lore_file_path = os.path.join(base_dir, "lores", f"{safe_id}.txt")

        if safe_id not in self.server_vectors:
            self.server_vectors[safe_id] = []

        try:
            async with aiofiles.open(lore_file_path, "a", encoding="utf-8") as file:
                for fact in new_facts:
                    clean_fact = fact.strip()
                    if not clean_fact:
                        continue
                        
                    # Write to the physical text file
                    await file.write(f"\n\n{clean_fact}")

                    # Vectorize and add to live RAM immediately
                    vector = await self.get_embedding(clean_fact)
                    if vector:
                        self.server_vectors[safe_id].append({"text": clean_fact, "vector": vector})

            logger.info(f"Dynamically added {len(new_facts)} permanent lore chunks for server {safe_id}.")
        except Exception as e:
            logger.error(f"Failed to dynamically add lore for {safe_id}: {e}")