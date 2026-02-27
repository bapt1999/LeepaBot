from collections import deque
from datetime import datetime

class ShortTermMemory:
    def __init__(self, max_size=30):
        # A double-ended queue that automatically drops the oldest item when full
        self.queue = deque(maxlen=max_size)

    def add_message(self, author_name, content):
        timestamp = datetime.now().strftime("%H:%M")
        formatted_message = f"[{timestamp}] {author_name}: {content}"
        self.queue.append(formatted_message)

    def get_context_block(self) -> str:
        # Weaves the queue into a single string block for the LLM prompt
        return "\n".join(self.queue)