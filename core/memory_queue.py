from collections import deque, OrderedDict
from datetime import datetime, timezone
import json
import logging
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# MEMORY LENGTHS
# ---------------------------------------------------------
CORE_MEMORY_SIZE = 20       # The base number of messages kept in active memory.
OVERFLOW_CHUNK_SIZE = 15    # The number of messages sliced off and summarized when the limit is hit.

class ShortTermMemory:
    def __init__(self):
        self.overflow_limit = CORE_MEMORY_SIZE + OVERFLOW_CHUNK_SIZE
        self.messages = deque()
        self.running_summary = ""
        self.is_summarizing = False
        self.reaction_events = OrderedDict()

    def record_reaction(self, target: dict, action: str, actor: dict | None, emoji: dict | None, burst: bool = False):
        """Keep the latest observed changes without using ordinary message slots."""
        key = target["id"]
        now = time.monotonic()
        self._prune_reactions(now)
        entry = self.reaction_events.setdefault(key, {"message": target, "events": OrderedDict(), "updated": now})
        entry["message"] = target
        entry["updated"] = now
        events = entry["events"]
        emoji_key = (emoji or {}).get("id") or (emoji or {}).get("name")
        if action == "clear":
            events.clear()
        elif action == "clear_emoji":
            for event_key, event in list(events.items()):
                old = event.get("emoji") or {}
                if (old.get("id") or old.get("name")) == emoji_key:
                    del events[event_key]
        event_key = (str((actor or {}).get("discord_id", "")), emoji_key, burst)
        events.pop(event_key, None)
        events[event_key] = {
            "action": action, "actor": actor, "emoji": emoji, "burst": burst,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        while len(events) > 8:
            events.popitem(last=False)
        self.reaction_events.move_to_end(key)
        while len(self.reaction_events) > 6:
            self.reaction_events.popitem(last=False)

    def _prune_reactions(self, now):
        for key, entry in list(self.reaction_events.items()):
            if now - entry["updated"] > 86400:
                del self.reaction_events[key]

    def add_message(
        self,
        author: str,
        content: str,
        message_id: int | str | None = None,
        author_id: int | str | None = None,
    ):
        """Adds a message to the right side of the queue."""
        self.messages.append({
            "message_id": str(message_id) if message_id is not None else None,
            "author_id": str(author_id) if author_id is not None else None,
            "author": author,
            "content": content,
        })

    def update_message(
        self,
        message_id: int | str,
        author: str,
        content: str,
        author_id: int | str | None = None,
    ) -> bool:
        """Updates a remembered Discord message without changing its position."""
        target_id = str(message_id)

        for remembered_message in reversed(self.messages):
            if remembered_message.get("message_id") != target_id:
                continue

            remembered_message["author"] = author
            remembered_message["content"] = content
            if author_id is not None:
                remembered_message["author_id"] = str(author_id)
            return True

        return False

    def get_context_block(self) -> str:
        """Assembles the payload block, fusing the compressed summary with the raw recent messages."""
        context_parts = []

        if self.running_summary:
            context_parts.append(f"[PREVIOUS CONVERSATION SUMMARY: {self.running_summary}]")

        context_parts.append("--- RECENT MESSAGES ---")
        for msg in self.messages:
            context_parts.append(f"{msg['author']}: {msg['content']}")

        from core import prompts
        if getattr(prompts, "USE_REACTION_FEEDBACK", False):
            self._prune_reactions(time.monotonic())
            records = []
            size = 0
            for entry in reversed(self.reaction_events.values()):
                record = json.dumps({"message": entry["message"], "latest_observed_changes": list(entry["events"].values())}, ensure_ascii=False)
                if size + len(record) > 6500:
                    break
                records.append(record)
                size += len(record)
            if records:
                context_parts.append("--- RECENT REACTIONS TO LEEPA (OBSERVED CHANGES, NOT A COMPLETE ROSTER) ---")
                context_parts.extend(reversed(records))

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
