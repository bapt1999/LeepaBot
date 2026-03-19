import asyncio
from collections import deque
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# MEMORY LENGTHS
# ---------------------------------------------------------
CORE_MEMORY_SIZE = 20       # The base number of messages kept in active memory.
OVERFLOW_CHUNK_SIZE = 15    # The number of messages sliced off and summarized when the limit is hit.

class ShortTermMemory:
    def __init__(self):
        self.core_limit = CORE_MEMORY_SIZE
        self.overflow_limit = CORE_MEMORY_SIZE + OVERFLOW_CHUNK_SIZE 
        self.messages = deque()
        self.running_summary = ""
        self.is_summarizing = False
        self.total_message_count = 0 

    def add_message(self, author: str, content: str):
        """Adds a message to the right side of the queue."""
        self.messages.append({"author": author, "content": content})
        self.total_message_count += 1 
        
    def get_context_block(self) -> str:
        """Assembles the payload block, fusing the compressed summary with the raw recent messages."""
        context_parts = []
        
        if self.running_summary:
            context_parts.append(f"[PREVIOUS CONVERSATION SUMMARY: {self.running_summary}]")
            
        context_parts.append("--- RECENT MESSAGES ---")
        for msg in self.messages:
            context_parts.append(f"{msg['author']}: {msg['content']}")
            
        return "\n".join(context_parts)
        
    def extract_overflow_for_summary(self) -> str:
        """
        Slices the oldest messages from the left side of the queue.
        Returns them as a formatted string to be sent to the LLM for compression.
        """
        if len(self.messages) >= self.overflow_limit and not self.is_summarizing:
            self.is_summarizing = True
            extracted_text = []
            
            # Pop the oldest messages from the left of the deque based on the defined chunk size
            for _ in range(OVERFLOW_CHUNK_SIZE):
                if self.messages:
                    msg = self.messages.popleft()
                    extracted_text.append(f"{msg['author']}: {msg['content']}")
                    
            return "\n".join(extracted_text)
            
        return ""
        
    def update_running_summary(self, new_summary: str):
        """Unlocks the queue and updates the persistent memory state."""
        self.running_summary = new_summary
        self.is_summarizing = False