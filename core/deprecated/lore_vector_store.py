import os
import math
import json
import time
import logging
import aiofiles
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class LoreDatabase:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={self.api_key}"
        self.server_vectors = {} 
        self._client = None
        self.commit_counters = {} # Tracks when to trigger the batch purge

    async def get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def get_embedding(self, text: str) -> list[float]:
        if not self.api_key:
            return []
        
        payload = {
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": text}]}
        }
        try:
            client = await self.get_client()
            response = await client.post(self.endpoint, json=payload)
            result = response.json()
            return result.get("embedding", {}).get("values", [])
        except Exception as e:
            logger.error(f"Embedding calculation failed: {e}")
            return []

    def cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot_product = sum(a * b for a, b in zip(v1, v2))
        magnitude1 = math.sqrt(sum(a * a for a in v1))
        magnitude2 = math.sqrt(sum(b * b for b in v2))
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        return dot_product / (magnitude1 * magnitude2)

    async def _save_json(self, safe_id: str):
        """Saves the database to disk, stripping vectors to keep the file human-readable."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        lore_file_path = os.path.join(base_dir, "lores", f"{safe_id}.json")
        
        save_data = []
        for item in self.server_vectors.get(safe_id, []):
            save_data.append({
                "text": item["text"],
                "added_at": item.get("added_at", int(time.time())),
                "last_accessed": item.get("last_accessed", int(time.time())),
                "hits": item.get("hits", 0),
                "is_core": item.get("is_core", False)
            })
            
        try:
            async with aiofiles.open(lore_file_path, "w", encoding="utf-8") as file:
                await file.write(json.dumps(save_data, indent=4, ensure_ascii=False))
            logger.info(f"[LTM TRACER] SUCCESS: Physically overwrote {safe_id}.json on disk.")
        except Exception as e:
            logger.error(f"[LTM TRACER] CRITICAL: Failed to save JSON for {safe_id}: {e}")

    async def load_and_chunk_lore(self, server_id: str):
        """Loads the JSON, assigns VIP status to migrated files, and calculates vectors."""
        safe_id = "".join(c for c in server_id if c.isalnum() or c in ("_", "-"))
        base_dir = os.path.dirname(os.path.abspath(__file__))
        lore_file_path = os.path.join(base_dir, "lores", f"{safe_id}.json")
        
        if safe_id in self.server_vectors:
            return 

        try:
            async with aiofiles.open(lore_file_path, "r", encoding="utf-8") as file:
                raw_json = json.loads(await file.read())
            
            self.server_vectors[safe_id] = []
            for item in raw_json:
                # VIP OVERRIDE: If the item lacks a 'last_accessed' key, it is from the migration script.
                if "last_accessed" not in item:
                    item["last_accessed"] = int(time.time())
                    item["is_core"] = True 

                vector = await self.get_embedding(item["text"])
                if vector:
                    item["vector"] = vector
                    self.server_vectors[safe_id].append(item)
            
            # Immediately save to lock in the VIP upgrades
            await self._save_json(safe_id)
            logger.info(f"Loaded and vectorized {len(self.server_vectors[safe_id])} JSON chunks for server {safe_id}.")
            
        except FileNotFoundError:
            self.server_vectors[safe_id] = [] 
        except Exception as e:
            logger.error(f"Failed to load JSON lore for {safe_id}: {e}")
            self.server_vectors[safe_id] = []

    async def get_relevant_lore(self, server_id: str, user_query: str) -> str:
        """Finds all core memories and appends the single most relevant dynamic memory."""
        safe_id = "".join(c for c in server_id if c.isalnum() or c in ("_", "-"))
        
        if safe_id not in self.server_vectors:
            await self.load_and_chunk_lore(server_id)
            
        lore_data = self.server_vectors.get(safe_id, [])
        if not lore_data:
            return "No specific server lore established yet."
            
        core_block = []
        best_index = -1
        max_similarity = -1.0
        
        query_vector = await self.get_embedding(user_query)
        if not query_vector:
            # If embedding fails, just return core memories if any exist
            for item in lore_data:
                if item.get("is_core"):
                    core_block.append(f"- {item['text']}")
            return "\n".join(core_block) if core_block else "No specific server lore established yet."
            
        # Filter logic: Grab all core lore, run similarity ONLY on dynamic lore
        for i, item in enumerate(lore_data):
            if item.get("is_core"):
                core_block.append(f"- {item['text']}")
            else:
                sim = self.cosine_similarity(query_vector, item["vector"])
                if sim > max_similarity:
                    max_similarity = sim
                    best_index = i
                    
        # THE HIT TRIGGER for dynamic memory
        dynamic_hit = ""
        if max_similarity > 0.25 and best_index != -1:
            self.server_vectors[safe_id][best_index]["hits"] += 1
            self.server_vectors[safe_id][best_index]["last_accessed"] = int(time.time())
            
            await self._save_json(safe_id)
            dynamic_hit = f"\nRELEVANT RECENT LORE:\n- {self.server_vectors[safe_id][best_index]['text']}"
            
        # Concatenate the core block and the single dynamic hit
        final_lore = []
        if core_block:
            final_lore.append("PERMANENT CORE LORE:")
            final_lore.extend(core_block)
            
        if dynamic_hit:
            final_lore.append(dynamic_hit)
            
        return "\n".join(final_lore).strip() if final_lore else "No specific server lore established yet."

    async def add_dynamic_lore(self, server_id: str, new_facts: list):
        """Appends new facts and triggers the batch decay purge every 5 commits."""
        if not new_facts:
            return

        safe_id = "".join(c for c in server_id if c.isalnum() or c in ("_", "-"))
        
        if safe_id not in self.server_vectors:
            await self.load_and_chunk_lore(server_id)

        current_time = int(time.time())
        
        for fact in new_facts:
            clean_fact = fact.strip()
            if not clean_fact:
                continue
                
            vector = await self.get_embedding(clean_fact)
            if vector:
                self.server_vectors[safe_id].append({
                    "text": clean_fact,
                    "vector": vector,
                    "added_at": current_time,
                    "last_accessed": current_time,
                    "hits": 1, 
                    "is_core": False
                })

        await self._save_json(safe_id)
        
        # THE MODULO TRIGGER: Run the decay math every 5 new memory commits
        self.commit_counters[safe_id] = self.commit_counters.get(safe_id, 0) + 1
        if self.commit_counters[safe_id] % 5 == 0:
            await self.purge_decayed_memories(safe_id)

    async def purge_decayed_memories(self, safe_id: str):
        """Calculates the dynamic half-life and physically deletes forgotten memories."""
        logger.info(f"Triggering batch decay purge for server {safe_id}...")
        current_time = int(time.time())
        survivors = []
        
        # lambda_0 is tuned so 1 hit reaches a score of 0.1 in roughly 24 hours (86400 seconds)
        lambda_0 = 0.0000266 

        for item in self.server_vectors.get(safe_id, []):
            # CORE OVERRIDE: 10 hits makes the memory immortal
            if item.get("hits", 0) >= 10:
                item["is_core"] = True

            if item.get("is_core"):
                survivors.append(item)
                continue

            hits = max(1, item.get("hits", 1))
            delta_t = current_time - item.get("last_accessed", current_time)

            # R = H * e^(-(lambda_0 / H) * delta_t)
            R = hits * math.exp(-(lambda_0 / hits) * delta_t)

            if R >= 0.1:
                survivors.append(item)
            else:
                logger.info(f"Purged decayed memory: {item['text'][:40]}...")

        self.server_vectors[safe_id] = survivors
        await self._save_json(safe_id)