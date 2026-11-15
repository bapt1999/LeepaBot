from pathlib import Path
import argparse
import ast
import os
import shutil
import tempfile


CHANGES = {
    'main.py': [
        (r'''
# Imported after logging is configured so the logic layer inherits the handler.
from core.logic import process_message, process_message_edit

TOKEN = os.getenv('DISCORD_TOKEN')
''', r'''
# Imported after logging is configured so the logic layer inherits the handler.
from core.logic import process_message, process_message_edit, process_reaction_event, remember_discord_message

TOKEN = os.getenv('DISCORD_TOKEN')
'''),
        (r'''intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
''', r'''intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

client = discord.Client(intents=intents)
'''),
        (r'''@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # The logic layer handles all Discord actions (replies, reactions) itself.
    await process_message(message, client.user)


''', r'''@client.event
async def on_message(message):
    remember_discord_message(message, client.user)
    if message.author == client.user:
        return

    # The logic layer handles all Discord actions (replies, reactions) itself.
    await process_message(message, client.user, client)


'''),
        (r'''        return

    await process_message_edit(message, client.user)


client.run(TOKEN)
''', r'''        return

    remember_discord_message(message, client.user)
    await process_message_edit(message, client.user)


@client.event
async def on_raw_reaction_add(payload):
    await process_reaction_event(client, payload, "add")


@client.event
async def on_raw_reaction_remove(payload):
    await process_reaction_event(client, payload, "remove")


@client.event
async def on_raw_reaction_clear(payload):
    await process_reaction_event(client, payload, "clear")


@client.event
async def on_raw_reaction_clear_emoji(payload):
    await process_reaction_event(client, payload, "clear_emoji")


client.run(TOKEN)
''')
    ],
    'core/api_handler.py': [
        (r'''        "required_tools": {
            "type": "object",
            "properties": {name: {"type": "boolean"} for name in ("python", "web_search", "notebook")},
            "required": ["python", "web_search", "notebook"],
            "additionalProperties": False,
        },
''', r'''        "required_tools": {
            "type": "object",
            "properties": {name: {"type": "boolean"} for name in ("python", "web_search", "notebook", "discord_history")},
            "required": ["python", "web_search", "notebook", "discord_history"],
            "additionalProperties": False,
        },
'''),
        (r'''        raise invalid(f"assembly_id must be one of {prompt_assembly_ids()}.")
    required = data["required_tools"]
    tool_names = ("python", "web_search", "notebook")
    if not isinstance(required, dict) or any(name not in required for name in tool_names):
        raise invalid("required_tools must contain python, web_search, and notebook.")
    if any(type(required[name]) is not bool for name in tool_names):
        raise invalid("Tool requirements must be JSON booleans, not strings or numbers.")
''', r'''        raise invalid(f"assembly_id must be one of {prompt_assembly_ids()}.")
    required = data["required_tools"]
    tool_names = ("python", "web_search", "notebook", "discord_history")
    if not isinstance(required, dict) or any(name not in required for name in tool_names):
        raise invalid("required_tools must contain python, web_search, notebook, and discord_history.")
    if any(type(required[name]) is not bool for name in tool_names):
        raise invalid("Tool requirements must be JSON booleans, not strings or numbers.")
'''),
        (r'''    sloppy_mode: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Builds Leepa's prompt and sends it through the provider chain."""
''', r'''    sloppy_mode: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    discord_reader=None,
) -> dict:
    """Builds Leepa's prompt and sends it through the provider chain."""
'''),
        (r'''    thermal_scalar = None

    session = ToolSession(discord_context, cancelled) if notebook_scope(discord_context) or prompts.USE_WEB_SEARCH or python_enabled() or prompts.USE_PROMPT_ASSEMBLER else None
    notebook_info = None
    if session is not None and session.scope:
''', r'''    thermal_scalar = None

    session = ToolSession(discord_context, cancelled, discord_reader) if notebook_scope(discord_context) or prompts.USE_WEB_SEARCH or python_enabled() or prompts.USE_PROMPT_ASSEMBLER or (discord_reader is not None and discord_reader.enabled) else None
    notebook_info = None
    if session is not None and session.scope:
'''),
        (r'''        bool(session and session.web_enabled),
        bool(session and session.code_enabled),
    )
    if session is not None:
''', r'''        bool(session and session.web_enabled),
        bool(session and session.code_enabled),
        bool(session and session.discord_enabled),
    )
    if session is not None:
''')
    ],
    'core/prompt_builder.py': [
        (r'''    return {
        "assembly_id": default_prompt_assembly()["id"],
        "required_tools": {"python": False, "web_search": False, "notebook": False},
        "required_output": None,
        "notebook_action": None,
''', r'''    return {
        "assembly_id": default_prompt_assembly()["id"],
        "required_tools": {"python": False, "web_search": False, "notebook": False, "discord_history": False},
        "required_output": None,
        "notebook_action": None,
'''),
        (r'''        json.dumps(discord_context, indent=2, ensure_ascii=False),
    ])
    return prompts.PROMPT_ASSEMBLER_SYSTEM.strip(), user_prompt


''', r'''        json.dumps(discord_context, indent=2, ensure_ascii=False),
    ])
    system_prompt = prompts.PROMPT_ASSEMBLER_SYSTEM.strip() + "\n\n" + prompts.PROMPT_ASSEMBLER_DISCORD.strip()
    return system_prompt, user_prompt


'''),
        (r'''    web_enabled: bool = False,
    python_enabled: bool = False,
) -> str:
    discord_context = discord_context or {}
''', r'''    web_enabled: bool = False,
    python_enabled: bool = False,
    discord_enabled: bool = False,
) -> str:
    discord_context = discord_context or {}
'''),
        (r'''        build_output_contract(
            force_response=bool(command_prompt),
            allow_tools=python_enabled or web_enabled or bool(notebook_info and notebook_info.get("available")),
        ),
    ]
''', r'''        build_output_contract(
            force_response=bool(command_prompt),
            allow_tools=python_enabled or web_enabled or discord_enabled or bool(notebook_info and notebook_info.get("available")),
        ),
    ]
'''),
        (r'''            "web_search_enabled": web_enabled,
            "python_enabled": python_enabled,
            "python_images_enabled": python_enabled and bool(getattr(prompts, "USE_PYTHON_IMAGES", False)),
        }, ensure_ascii=False)
''', r'''            "web_search_enabled": web_enabled,
            "python_enabled": python_enabled,
            "discord_history_enabled": discord_enabled,
            "python_images_enabled": python_enabled and bool(getattr(prompts, "USE_PYTHON_IMAGES", False)),
        }, ensure_ascii=False)
'''),
        (r'''    if web_enabled:
        parts.append(prompts.PROMPT_WEB_SEARCH)

    if help_mode:
''', r'''    if web_enabled:
        parts.append(prompts.PROMPT_WEB_SEARCH)

    if discord_enabled:
        parts.append(prompts.PROMPT_DISCORD_HISTORY)

    if getattr(prompts, "USE_REACTION_FEEDBACK", False):
        parts.append(prompts.PROMPT_REACTION_FEEDBACK)

    if help_mode:
'''),
        (r'''    web_enabled: bool = False,
    python_enabled: bool = False,
) -> tuple[str, str]:
    return (
        build_system_prompt(discord_context, sloppy_mode, prompt_assembly_id, notebook_info, web_enabled, python_enabled),
        build_user_prompt(context_block, discord_context, sloppy_mode, notebook_info),
    )
''', r'''    web_enabled: bool = False,
    python_enabled: bool = False,
    discord_enabled: bool = False,
) -> tuple[str, str]:
    return (
        build_system_prompt(discord_context, sloppy_mode, prompt_assembly_id, notebook_info, web_enabled, python_enabled, discord_enabled),
        build_user_prompt(context_block, discord_context, sloppy_mode, notebook_info),
    )
''')
    ],
    'core/tools.py': [
        (r'''from core.notebook import Notebook, NotebookError
from core import code_runner


''', r'''from core.notebook import Notebook, NotebookError
from core import code_runner
from core import discord_history


'''),
        (r'''
class ToolSession:
    def __init__(self, context: dict, cancelled):
        self.scope = server_config.notebook_scope(context)
        self.notebook = None
''', r'''
class ToolSession:
    def __init__(self, context: dict, cancelled, discord_reader=None):
        self.scope = server_config.notebook_scope(context)
        self.notebook = None
'''),
        (r'''        self.code_blocked = False
        self.notebook_completed = set()
        self.reminders = 0
        self.names = set(SPECS) if server_config.NOTEBOOK_WRITES and self.turn_id else READ_TOOLS
''', r'''        self.code_blocked = False
        self.notebook_completed = set()
        self.discord_reader = discord_reader
        self.discord_calls = 0
        self.discord_read_succeeded = False
        self.reminders = 0
        self.names = set(SPECS) if server_config.NOTEBOOK_WRITES and self.turn_id else READ_TOOLS
'''),
        (r'''            "web_search": self.web_enabled,
            "notebook_tools": sorted(self.names) if self.notebook is not None else [],
        }

    def set_selection(self, selection: dict) -> None:
''', r'''            "web_search": self.web_enabled,
            "notebook_tools": sorted(self.names) if self.notebook is not None else [],
            "discord_tools": sorted(discord_history.SPECS) if self.discord_enabled else [],
        }

    @property
    def discord_enabled(self):
        return bool(self.discord_reader is not None and self.discord_reader.enabled)

    def set_selection(self, selection: dict) -> None:
'''),
        (r'''        if required.get("notebook") and action not in self.notebook_completed:
            missing.append("notebook")
        return missing

''', r'''        if required.get("notebook") and action not in self.notebook_completed:
            missing.append("notebook")
        if required.get("discord_history") and not self.discord_read_succeeded:
            missing.append("discord_history")
        return missing

'''),
        (r'''                if name == "png":
                    available = available and code_runner.images_enabled()
            else:
                available = (
''', r'''                if name == "png":
                    available = available and code_runner.images_enabled()
            elif name == "discord_history":
                available = self.discord_enabled and self.discord_calls < discord_history.MAX_DISCORD_CALLS
            else:
                available = (
'''),
        (r'''    def failure_response(self, detail: str = "") -> dict:
        missing = self.missing_requirements()
        labels = {"web_search": "web search", "python": "Python execution", "png": "the graph attachment", "notebook": "the requested notebook operation"}
        work = ", ".join(labels[name] for name in missing) or "the requested task"
        logger.warning("[TOOLS] turn=%s unfinished=%s %s", self.turn_id, missing, detail)
''', r'''    def failure_response(self, detail: str = "") -> dict:
        missing = self.missing_requirements()
        labels = {"web_search": "web search", "python": "Python execution", "png": "the graph attachment", "notebook": "the requested notebook operation", "discord_history": "the Discord lookup"}
        work = ", ".join(labels[name] for name in missing) or "the requested task"
        logger.warning("[TOOLS] turn=%s unfinished=%s %s", self.turn_id, missing, detail)
'''),
        (r'''        if not final_only and self.code_enabled and not self.code_blocked and self.code_runs < code_runner.MAX_CODE_RUNS:
            definitions.append(code_runner.tool_definition())
        allowance = self.searches_remaining if provider == "openrouter" and not final_only else 0
        if allowance:
''', r'''        if not final_only and self.code_enabled and not self.code_blocked and self.code_runs < code_runner.MAX_CODE_RUNS:
            definitions.append(code_runner.tool_definition())
        if not final_only and self.discord_enabled and self.discord_calls < discord_history.MAX_DISCORD_CALLS:
            definitions.extend(discord_history.tool_definitions())
        allowance = self.searches_remaining if provider == "openrouter" and not final_only else 0
        if allowance:
'''),
        (r'''            f"Web search: {'available' if allowance else 'unavailable'}; {allowance} searches allowed.\n"
            f"Notebook operations available: {0 if final_only else remaining_notes}.\n"
            f"Python runs available: {max(0, code_runner.MAX_CODE_RUNS - self.code_runs) if self.code_enabled and not self.code_blocked and not final_only else 0}.\n"
            f"PNG export: {'available' if self.code_enabled and code_runner.images_enabled() else 'unavailable'}.\n"
            "Use only the tools offered in this request. You may use earlier results as data. "
            "Do not claim searches, execution, or notebook operations that did not happen."
        )
        if any(self.selection.get("required_tools", {}).values()) or self.selection.get("required_output"):
''', r'''            f"Web search: {'available' if allowance else 'unavailable'}; {allowance} searches allowed.\n"
            f"Notebook operations available: {0 if final_only else remaining_notes}.\n"
            f"Discord lookups available: {max(0, discord_history.MAX_DISCORD_CALLS - self.discord_calls) if self.discord_enabled and not final_only else 0}.\n"
            f"Python runs available: {max(0, code_runner.MAX_CODE_RUNS - self.code_runs) if self.code_enabled and not self.code_blocked and not final_only else 0}.\n"
            f"PNG export: {'available' if self.code_enabled and code_runner.images_enabled() else 'unavailable'}.\n"
            "Use only the tools offered in this request. You may use earlier results as data. "
            "Do not claim searches, execution, Discord lookups, or notebook operations that did not happen."
        )
        if any(self.selection.get("required_tools", {}).values()) or self.selection.get("required_output"):
'''),
        (r'''            return {"ok": False, "error": "Python execution failed. No image is queued."}

    async def execute(self, call: dict) -> dict:
        name = call.get("function", {}).get("name", "")
        if name == "run_python":
            return await self._execute_python(call)
        self.actions += 1
        started = time.monotonic()
''', r'''            return {"ok": False, "error": "Python execution failed. No image is queued."}

    async def _execute_discord(self, call: dict) -> dict:
        name = call["function"]["name"]
        self.discord_calls += 1
        started = time.monotonic()
        try:
            if not self.discord_enabled or self.cancelled() or self.discord_calls > discord_history.MAX_DISCORD_CALLS:
                return {"ok": False, "error": "Discord lookup unavailable, cancelled, or out of calls."}
            raw = call["function"].get("arguments", "")
            if not isinstance(raw, str) or len(raw) > 8000:
                raise ValueError("Invalid Discord tool arguments.")
            args = json.loads(raw, object_pairs_hook=_unique_object)
            result = await self.discord_reader.run(name, args, self.cancelled)
            if self.cancelled():
                return {"ok": False, "error": "Turn cancelled; discard this lookup."}
            if result.get("ok") is True and name != "list_discord_channels":
                self.discord_read_succeeded = True
            logger.info("[DISCORD] turn=%s tool=%s channel=%s ok=%s %.2fs", self.turn_id, name, args.get("channel_id", "current"), result.get("ok"), time.monotonic() - started)
            return result
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("[DISCORD] %s failed", name)
            return {"ok": False, "error": "Discord lookup failed; do not claim it succeeded."}

    async def execute(self, call: dict) -> dict:
        name = call.get("function", {}).get("name", "")
        if name == "run_python":
            return await self._execute_python(call)
        if name in discord_history.SPECS:
            return await self._execute_discord(call)
        self.actions += 1
        started = time.monotonic()
''')
    ],
    'core/logic.py': [
        (r'''from core.tools import TOOL_TURN_SECONDS
from core.code_runner import CodeImage, python_enabled, images_enabled

# Initializes logging and loads environment variables from the .env file.
''', r'''from core.tools import TOOL_TURN_SECONDS
from core.code_runner import CodeImage, python_enabled, images_enabled
from core.discord_history import DiscordHistory, ReactionFeedback

# Initializes logging and loads environment variables from the .env file.
'''),
        (r'''active_bot_channels = set()
bot_reply_cooldowns = {}

# Each reply to a bot usually ends the exchange for a minute.
''', r'''active_bot_channels = set()
bot_reply_cooldowns = {}
reaction_feedback = ReactionFeedback()


def remember_discord_message(message, bot_user):
    if bot_user is not None and getattr(prompts, "USE_REACTION_FEEDBACK", False):
        reaction_feedback.remember(message, bot_user.id)


async def process_reaction_event(client, payload, action):
    await reaction_feedback.handle(client, payload, action, get_channel_memory)

# Each reply to a bot usually ends the exchange for a minute.
'''),
        (r'''

async def process_message(message, bot_user) -> None:
    """Handles incoming Discord events and routes them to the external AI API."""
    current_time = time.time()
''', r'''

async def process_message(message, bot_user, client=None) -> None:
    """Handles incoming Discord events and routes them to the external AI API."""
    current_time = time.time()
'''),
        (r'''    active_processing_locks[message.id] = {
        "status": lock_status,
        "expires": time.time() + (TOOL_TURN_SECONDS + 90.0 if notebook_scope(discord_context) or prompts.USE_WEB_SEARCH or python_enabled() else 60.0),
    }

''', r'''    active_processing_locks[message.id] = {
        "status": lock_status,
        "expires": time.time() + (TOOL_TURN_SECONDS + 90.0 if notebook_scope(discord_context) or prompts.USE_WEB_SEARCH or python_enabled() or getattr(prompts, "USE_DISCORD_HISTORY", False) else 60.0),
    }

'''),
        (r'''            sloppy_mode,
            cancelled=lambda: active_processing_locks.get(message.id, {}).get("status") is True,
        )

''', r'''            sloppy_mode,
            cancelled=lambda: active_processing_locks.get(message.id, {}).get("status") is True,
            discord_reader=DiscordHistory(client, message.channel) if client is not None else None,
        )

'''),
        (r'''                    options["file"] = attachment
                sent_reply = await message.reply(reply_text or None, **options)
                reply_was_sent = True
                if code_image:
''', r'''                    options["file"] = attachment
                sent_reply = await message.reply(reply_text or None, **options)
                remember_discord_message(sent_reply, bot_user)
                reply_was_sent = True
                if code_image:
''')
    ],
    'core/memory_queue.py': [
        (r'''from collections import deque
import logging

logger = logging.getLogger(__name__)
''', r'''from collections import deque, OrderedDict
from datetime import datetime, timezone
import json
import logging
import time

logger = logging.getLogger(__name__)
'''),
        (r'''        self.running_summary = ""
        self.is_summarizing = False

    def add_message(
''', r'''        self.running_summary = ""
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
'''),
        (r'''            context_parts.append(f"{msg['author']}: {msg['content']}")

        return "\n".join(context_parts)

''', r'''            context_parts.append(f"{msg['author']}: {msg['content']}")

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

''')
    ],
    'core/server_config.py': [
        (r'''    except (KeyError, TypeError, ValueError):
        return None
''', r'''    except (KeyError, TypeError, ValueError):
        return None


# Extra readable channels, grouped by server. The current channel is always allowed.
# Only add channels whose contents may be shared in Leepa's other chats in that server.
# Threads need their own IDs; listing a parent does not expose its private threads.
DISCORD_HISTORY_CHANNELS = {
    # 123456789012345678: {234567890123456789, 345678901234567890},
}


def discord_channel_allowed(origin, target) -> bool:
    guild = getattr(origin, "guild", None)
    target_guild = getattr(target, "guild", None)
    if guild is None or target_guild is None or guild.id != target_guild.id:
        return False
    return target.id == origin.id or target.id in DISCORD_HISTORY_CHANNELS.get(guild.id, set())
''')
    ],
}

DISCORD_MODULE = r'''import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone

import discord
from discord.http import Route

from core import prompts, server_config


logger = logging.getLogger(__name__)
MAX_DISCORD_CALLS = 6
MAX_PAGE = 10
CALL_SECONDS = 15

CHANNEL = {"type": "string", "description": "Channel or thread ID from the current server. Omit for the current channel/thread."}
LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_PAGE}
POSITION = {"type": "string", "description": "Message ID or ISO 8601 date/time with timezone. A date alone means midnight UTC."}
SPECS = {
    "list_discord_channels": ({"page": {"type": "integer", "minimum": 0}}, [], "List configured readable channels and threads in this server. Follow next_page."),
    "search_discord_messages": ({
        "channel_id": CHANNEL, "query": {"type": "string", "maxLength": 200},
        "author_id": {"type": "string"}, "before": POSITION, "after": POSITION,
        "offset": {"type": "integer", "minimum": 0, "maximum": 9975}, "limit": LIMIT,
    }, ["query"], "Search Discord's index in one channel. Use next_offset for more. Read around a match for context; search results omit reactions."),
    "read_discord_history": ({
        "channel_id": CHANNEL, "before": POSITION, "after": POSITION,
        "around": {"type": "string", "description": "Message ID to read around."}, "limit": LIMIT,
    }, [], "Read recent or older messages. Supply at most one of before, after, around. Follow next_before/next_after; results are chronological."),
    "read_discord_message": ({"channel_id": CHANNEL, "message_id": {"type": "string"}}, ["message_id"], "Read one message, its reply reference, attachments, and current reaction counts."),
    "read_discord_pins": ({
        "channel_id": CHANNEL, "before": {"type": "string", "description": "Pinned-at timestamp returned as next_before."}, "limit": LIMIT,
    }, [], "Read pinned messages, newest pin first. Follow next_before when has_more is true."),
}


def tool_definitions():
    return [{"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": fields, "required": required, "additionalProperties": False},
    }} for name, (fields, required, description) in SPECS.items()]


def snowflake(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
        raise ValueError("Use a valid Discord ID as a string.")
    return int(value)


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Use an ISO 8601 date or timestamp.")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if len(value) == 10:
        result = result.replace(tzinfo=timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Include a timezone, for example +00:00.")
    if result.year < 2015:
        raise ValueError("Use a date from 2015 onward.")
    return result


def position(value, after=False):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return snowflake(value)
    return discord.utils.time_snowflake(timestamp(value), high=after)


def emoji_facts(emoji):
    return {
        "id": str(emoji.id) if emoji.id else None,
        "name": emoji.name, "text": str(emoji),
        "animated": bool(getattr(emoji, "animated", False)),
    }


def channel_facts(channel):
    parent = getattr(channel, "parent", None)
    return {
        "id": str(channel.id), "name": getattr(channel, "name", "Direct message"),
        "is_thread": isinstance(channel, discord.Thread),
        "parent_id": str(parent.id) if parent else None,
        "parent_name": getattr(parent, "name", None),
    }


class DiscordHistory:
    def __init__(self, client, channel):
        self.client = client
        self.origin = channel
        self.guild = getattr(channel, "guild", None)
        self.members = {}

    @property
    def enabled(self):
        return bool(getattr(prompts, "USE_DISCORD_HISTORY", False) and self.guild and self.client.user)

    async def _channel(self, channel_id=None):
        target_id = snowflake(channel_id) if channel_id is not None else self.origin.id
        allowed = server_config.DISCORD_HISTORY_CHANNELS.get(self.guild.id, set())
        if target_id != self.origin.id and target_id not in allowed:
            raise PermissionError("This channel is outside the configured readable channels.")
        channel = self.origin if target_id == self.origin.id else self.client.get_channel(target_id)
        if channel is None:
            channel = await self.client.fetch_channel(target_id)
        if not server_config.discord_channel_allowed(self.origin, channel):
            raise PermissionError("Cross-server Discord access is unavailable.")
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise ValueError("Choose a text channel or a thread, not a category or forum parent.")
        member = self.guild.me
        if member is None:
            member = await self.guild.fetch_member(self.client.user.id)
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.read_message_history:
            raise PermissionError("Leepa needs View Channel and Read Message History here.")
        return channel

    async def _author(self, raw):
        author = raw.get("author") or {}
        author_id = author.get("id")
        member = self.guild.get_member(int(author_id)) if author_id else None
        if member is None and author_id and not raw.get("webhook_id"):
            if author_id not in self.members:
                try:
                    self.members[author_id] = await self.guild.fetch_member(int(author_id))
                except (discord.NotFound, discord.Forbidden):
                    self.members[author_id] = None
            member = self.members[author_id]
        supplied_nick = (raw.get("member") or {}).get("nick")
        return {
            "discord_id": author_id,
            "display_name": getattr(member, "display_name", None) or supplied_nick or author.get("global_name") or author.get("username") or "Unknown member",
            "name_source": "server_member" if member or supplied_nick else "account_name_fallback",
            "is_bot": bool(author.get("bot", False)),
        }

    async def _message(self, raw, channel, *, reactions=True, full=False):
        content = raw.get("content") or ""
        limit = 12000 if full else 1000
        reference = raw.get("message_reference") or {}
        reaction_data = None
        if reactions:
            reaction_data = [{
                "emoji": item.get("emoji"), "count": item.get("count"),
                "count_details": item.get("count_details"),
            } for item in raw.get("reactions", [])[:20]]
        return {
            "id": raw["id"], "author": await self._author(raw),
            "content": content[:limit], "content_truncated": len(content) > limit,
            "created_at": raw.get("timestamp"), "edited_at": raw.get("edited_timestamp"),
            "channel": channel_facts(channel),
            "url": f"https://discord.com/channels/{self.guild.id}/{channel.id}/{raw['id']}",
            "reply_to": {key: reference[key] for key in ("message_id", "channel_id", "guild_id") if key in reference} or None,
            "attachments": [{key: item.get(key) for key in ("filename", "content_type", "size", "url", "description")}
                            for item in raw.get("attachments", [])[:5]],
            "attachments_truncated": len(raw.get("attachments", [])) > 5,
            "embeds": [{"title": (item.get("title") or "")[:200], "description": (item.get("description") or "")[:500], "url": item.get("url")}
                       for item in raw.get("embeds", [])[:3]],
            "embeds_are_excerpts": True,
            "pinned": raw.get("pinned"), "reactions": reaction_data,
            "reactions_retrieved": reactions,
            "reactions_truncated": reactions and len(raw.get("reactions", [])) > 20,
        }

    async def _request(self, path, params=None, **ids):
        result = await self.client.http.request(Route("GET", path, **ids), params=params or {})
        if isinstance(result, dict) and result.get("code") == 110000:
            raise ValueError(f"Discord is still indexing this history. Retry later (suggested delay: {result.get('retry_after', 2)} seconds). This is not an empty search result.")
        return result

    async def run(self, name, args, cancelled):
        if not self.enabled or cancelled():
            return {"ok": False, "error": "Discord history is disabled or this turn was cancelled."}
        try:
            fields, required, _ = SPECS[name]
            if not isinstance(args, dict) or set(args) - fields.keys() or set(required) - args.keys():
                raise ValueError("Arguments must match the documented tool fields.")
            for key, value in args.items():
                field = fields[key]
                if field["type"] == "integer":
                    if type(value) is not int or not field.get("minimum", 0) <= value <= field.get("maximum", 10000):
                        raise ValueError(f"Invalid {key}.")
                elif not isinstance(value, str) or len(value) > field.get("maxLength", 200):
                    raise ValueError(f"Invalid {key}.")
            async with asyncio.timeout(CALL_SECONDS):
                result = await self._run(name, args)
            if cancelled():
                return {"ok": False, "error": "Turn cancelled; discard this lookup."}
            return {"ok": True, **result}
        except (ValueError, PermissionError, discord.Forbidden, discord.NotFound) as exc:
            return {"ok": False, "error": str(exc)}
        except TimeoutError:
            return {"ok": False, "error": "Discord lookup timed out; this does not mean no matching messages exist."}
        except discord.HTTPException as exc:
            return {"ok": False, "error": f"Discord request failed (HTTP {exc.status}, code {exc.code})."}

    async def _run(self, name, args):
        if name == "list_discord_channels":
            ids = sorted({self.origin.id, *server_config.DISCORD_HISTORY_CHANNELS.get(self.guild.id, set())})
            page = args.get("page", 0)
            channels, unavailable = [], []
            for identity in ids[page * MAX_PAGE:(page + 1) * MAX_PAGE]:
                try:
                    channels.append(channel_facts(await self._channel(str(identity))))
                except (ValueError, PermissionError, discord.Forbidden, discord.NotFound):
                    unavailable.append(str(identity))
            return {"channels": channels, "unavailable_ids": unavailable,
                    "next_page": page + 1 if (page + 1) * MAX_PAGE < len(ids) else None}

        channel = await self._channel(args.get("channel_id"))
        channel_id = channel.id
        limit = args.get("limit", MAX_PAGE)
        path = "/channels/{channel_id}/messages"
        if name == "read_discord_message":
            raw = await self._request(path + "/{message_id}", channel_id=channel_id, message_id=snowflake(args["message_id"]))
            return {"message": await self._message(raw, channel, full=True)}

        if name == "read_discord_pins":
            params = {"limit": limit}
            if "before" in args:
                params["before"] = timestamp(args["before"]).isoformat()
            data = await self._request(path + "/pins", params, channel_id=channel_id)
            items = data.get("items", [])
            return {
                "pins": [{"pinned_at": item["pinned_at"], "message": await self._message(item["message"], channel, reactions="reactions" in item["message"])} for item in items[:limit]],
                "has_more": bool(data.get("has_more")),
                "next_before": items[-1]["pinned_at"] if items and data.get("has_more") else None,
            }

        if name == "search_discord_messages":
            query = args["query"].strip()
            if not query:
                raise ValueError("Provide a search phrase; use read_discord_history for browsing.")
            offset = args.get("offset", 0)
            params = {"content": query, "channel_id": [str(channel_id)], "limit": limit, "offset": offset, "sort_by": "timestamp", "sort_order": "desc", "include_nsfw": "true"}
            if "author_id" in args:
                params["author_id"] = [str(snowflake(args["author_id"]))]
            for key, api_key in (("before", "max_id"), ("after", "min_id")):
                if key in args:
                    params[api_key] = str(position(args[key], after=key == "after"))
            data = await self._request("/guilds/{guild_id}/messages/search", params, guild_id=self.guild.id)
            matches, seen = [], set()
            for group in data.get("messages", []):
                for raw in group:
                    if str(raw.get("channel_id")) != str(channel_id) or raw["id"] in seen:
                        continue
                    seen.add(raw["id"])
                    if len(matches) < limit:
                        matches.append(await self._message(raw, channel, reactions=False))
            total = data.get("total_results", 0)
            more = offset + limit < total
            return {"messages": matches, "total_results_estimate": total,
                    "next_offset": offset + limit if more and offset + limit <= 9975 else None,
                    "pagination_limit_reached": more and offset + limit > 9975,
                    "indexing_in_progress": bool(data.get("doing_deep_historical_index")),
                    "search_scope": channel_facts(channel)}

        directions = [key for key in ("before", "after", "around") if key in args]
        if len(directions) > 1:
            raise ValueError("Use only one of before, after, or around.")
        params = {"limit": limit}
        for key in directions:
            params[key] = str(snowflake(args[key]) if key == "around" else position(args[key], after=key == "after"))
        raw_messages = await self._request(path, params, channel_id=channel_id)
        raw_messages.sort(key=lambda raw: int(raw["id"]))
        return {
            "messages": [await self._message(raw, channel) for raw in raw_messages],
            "next_before": raw_messages[0]["id"] if raw_messages else None,
            "next_after": raw_messages[-1]["id"] if raw_messages else None,
            "pagination_note": "These anchors let you browse farther in either direction; they do not guarantee more messages exist.",
        }


class ReactionFeedback:
    """Resolve reaction targets without starting a response turn."""

    def __init__(self):
        self.targets = OrderedDict()
        self.locks = [asyncio.Lock() for _ in range(16)]

    def remember(self, message, bot_id):
        target = None
        if message.author.id == bot_id:
            target = {
                "id": str(message.id), "content_excerpt": message.content[:400],
                "created_at": message.created_at.isoformat(),
                "channel": channel_facts(message.channel),
                "url": message.jump_url,
            }
        self.targets[message.id] = (time.monotonic(), target)
        self.targets.move_to_end(message.id)
        while len(self.targets) > 512:
            self.targets.popitem(last=False)
        return target

    async def handle(self, client, payload, action, get_memory):
        if not getattr(prompts, "USE_REACTION_FEEDBACK", False) or client.user is None:
            return
        user_id = getattr(payload, "user_id", None)
        if user_id == client.user.id or not getattr(payload, "guild_id", None):
            return
        async with self.locks[payload.message_id % len(self.locks)]:
            try:
                async with asyncio.timeout(10):
                    await self._record(client, payload, action, user_id, get_memory)
            except (discord.HTTPException, TimeoutError) as exc:
                logger.info("[REACTION] Could not load message %s: %s", payload.message_id, type(exc).__name__)
            except Exception:
                logger.exception("[REACTION] Could not record message %s", payload.message_id)

    async def _record(self, client, payload, action, user_id, get_memory):
        cached = self.targets.get(payload.message_id)
        if cached and time.monotonic() - cached[0] < 3600:
            target = cached[1]
        else:
            channel = client.get_channel(payload.channel_id)
            if channel is None:
                channel = await client.fetch_channel(payload.channel_id)
            guild = getattr(channel, "guild", None)
            if not guild or guild.id != payload.guild_id or not hasattr(channel, "fetch_message"):
                return
            target = self.remember(await channel.fetch_message(payload.message_id), client.user.id)
        if target is None:
            return
        actor = None
        if user_id is not None:
            guild = client.get_guild(payload.guild_id)
            member = getattr(payload, "member", None) or (guild.get_member(user_id) if guild else None)
            if member is None and guild is not None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.Forbidden):
                    pass
            actor = {
                "discord_id": str(user_id),
                "display_name": getattr(member, "display_name", None) or "Unknown member",
                "is_bot": getattr(member, "bot", None),
            }
        emoji = getattr(payload, "emoji", None)
        get_memory(payload.channel_id).record_reaction(
            target, action, actor, emoji_facts(emoji) if emoji else None,
            bool(getattr(payload, "burst", False)),
        )
        logger.info("[REACTION] message=%s action=%s actor=%s emoji=%s", payload.message_id, action, (actor or {}).get("display_name", "unknown"), emoji or "all")
'''


def updated_text(name, text):
    text = text.rstrip() + "\n"
    if name == "core/prompt_builder.py":
        text = text.replace(
            "Use each person's display_name when referring to them. ",
            "Use each person's display_name, or a shorter version of it, when referring to them. ",
        )
    for number, (old, new) in enumerate(CHANGES[name], 1):
        if text.count(new) == 1:
            continue
        if text.count(old) == 1:
            text = text.replace(old, new, 1)
        else:
            raise ValueError(f"{name}: section {number} differs from the saved version. Nothing was changed.")
    return text


def main():
    parser = argparse.ArgumentParser(description="Install Leepa's Discord history and reaction support.")
    parser.add_argument("--root", default=".", help="LeepaBot folder")
    parser.add_argument("--apply", action="store_true", help="Save the checked changes")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    pending = {}
    for name in CHANGES:
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Missing or unexpected file: {name}. Run this from LeepaBot.")
        original = path.read_text()
        replacement = updated_text(name, original)
        ast.parse(replacement, filename=name)
        if replacement != original:
            pending[name] = replacement
    module_path = root / "core/discord_history.py"
    if module_path.is_symlink():
        raise ValueError("core/discord_history.py must not be a symbolic link.")
    if module_path.exists() and module_path.read_text() != DISCORD_MODULE:
        raise ValueError("A different core/discord_history.py already exists. Nothing was changed.")
    if not module_path.exists():
        pending["core/discord_history.py"] = DISCORD_MODULE
    ast.parse(DISCORD_MODULE)
    prompt_tree = ast.parse((root / "core/prompts.py").read_text())
    defined = {target.id for node in prompt_tree.body if isinstance(node, ast.Assign)
               for target in node.targets if isinstance(target, ast.Name)}
    needed = {"USE_REACTION_FEEDBACK", "USE_DISCORD_HISTORY", "PROMPT_REACTION_FEEDBACK",
              "PROMPT_DISCORD_HISTORY", "PROMPT_ASSEMBLER_DISCORD"}
    if needed - defined:
        raise ValueError("First add the supplied prompts.py fields: " + ", ".join(sorted(needed - defined)))
    if not pending:
        print("This update is already installed.")
        return
    print("Checked: " + ", ".join(pending))
    if not args.apply:
        print("No files changed. Add --apply to save these changes.")
        return
    backup = Path(tempfile.mkdtemp(prefix="leepa-discord-backup-", dir=root.parent))
    originals = {}
    for name in pending:
        path = root / name
        originals[name] = path.read_bytes() if path.exists() else None
        if path.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
    try:
        for name, text in pending.items():
            path = root / name
            descriptor, temporary = tempfile.mkstemp(prefix=".discord-update-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(text)
                os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    except Exception:
        for name, original in originals.items():
            path = root / name
            if original is None:
                path.unlink(missing_ok=True)
            else:
                shutil.copy2(backup / name, path)
        raise
    print(f"Saved. Original files: {backup}")
    print("prompts.py and .env were not modified. Restart Leepa after deploying all changes.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, SyntaxError) as error:
        raise SystemExit(str(error))