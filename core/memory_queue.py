import asyncio
from collections import deque
import logging

logger = logging.getLogger(__name__)

class ShortTermMemory:
    def __init__(self, max_size=20):
        self.overflow_limit = max_size + 5 
        self.core_limit = max_size
        self.messages = deque()
        self.running_summary = ""
        self.is_summarizing = False
        self.total_message_count = 0 # Tracks the long-term cycle

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
        
    def extract_overflow_for_summary(self, chunk_size=5) -> str:
        """
        Slices the oldest messages from the left side of the queue.
        Returns them as a formatted string to be sent to the LLM for compression.
        """
        if len(self.messages) >= self.overflow_limit and not self.is_summarizing:
            self.is_summarizing = True
            extracted_text = []
            
            # Pop the oldest messages from the left of the deque
            for _ in range(chunk_size):
                if self.messages:
                    msg = self.messages.popleft()
                    extracted_text.append(f"{msg['author']}: {msg['content']}")
                    
            return "\n".join(extracted_text)
            
        return ""
        
    def update_running_summary(self, new_summary: str):
        """Unlocks the queue and updates the persistent memory state."""
        self.running_summary = new_summary
        self.is_summarizing = False