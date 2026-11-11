import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from core import prompts


load_dotenv()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerSettings:
    sloppy: bool = False


def _read_server_ids(variable_name: str) -> set[int]:
    server_ids = set()
    for value in os.getenv(variable_name, "").split(","):
        value = value.strip()
        if not value:
            continue
        if not value.isascii() or not value.isdecimal():
            raise ValueError(f"{variable_name} must contain comma-separated Discord server IDs.")
        server_id = int(value)
        if not 0 < server_id < 2**64:
            raise ValueError(f"{variable_name} contains an invalid Discord server ID.")
        server_ids.add(server_id)
    return server_ids


# Servers not listed here, including DMs, use the default settings.
DEFAULT_SERVER_SETTINGS = ServerSettings()
SERVER_SETTINGS = {
    server_id: ServerSettings(sloppy=True)
    for server_id in _read_server_ids("SLOPPY_SERVER_IDS")
}

if prompts.USE_SLOPPY and not SERVER_SETTINGS:
    logger.warning("SLOPPY_SERVER_IDS is empty; Sloppy is disabled on every server.")


def get_server_settings(discord_context: dict | None) -> ServerSettings:
    location = (discord_context or {}).get("location", {})
    server = location.get("server") or {}
    try:
        server_id = int(server.get("id"))
    except (TypeError, ValueError):
        return DEFAULT_SERVER_SETTINGS
    return SERVER_SETTINGS.get(server_id, DEFAULT_SERVER_SETTINGS)


def sloppy_enabled(discord_context: dict | None) -> bool:
    return prompts.USE_SLOPPY and get_server_settings(discord_context).sloppy


def get_command_mode(content: str, discord_context: dict) -> str | None:
    words = content.split(maxsplit=1)
    if not words:
        return None
    if words[0] == "!Leepa":
        return "leepa_help"

    mode = prompts.SLOPPY_MODE_TRIGGERS.get(words[0])
    if mode is None:
        return None
    if mode == "leepa_help":
        return mode
    return mode if sloppy_enabled(discord_context) else "sloppy_unavailable"


def available_commands(discord_context: dict | None) -> list[str]:
    commands = ["!Leepa"]
    if sloppy_enabled(discord_context):
        commands.extend(
            command for command in prompts.SLOPPY_MODE_TRIGGERS
            if command != "!Leepa"
        )
    return commands


def starts_sloppy_command(content: str) -> bool:
    return any(
        content.casefold().startswith(f"!{mode}".casefold())
        for mode in prompts.SLOPPY_MODE_TRIGGERS.values()
        if mode != "leepa_help"
    )


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} must be true or false.")
    return value in {"true", "1"}


USE_NOTEBOOK = _flag("USE_NOTEBOOK", False)
NOTEBOOK_WRITES = _flag("NOTEBOOK_WRITES", True)
NOTEBOOK_DMS = _flag("NOTEBOOK_DMS", False)
NOTEBOOK_ROOT = os.getenv("NOTEBOOK_ROOT", "~/Leepa_sb")
NOTEBOOK_SERVER_IDS = _read_server_ids("NOTEBOOK_SERVER_IDS")
NOTEBOOK_SHARED_CHANNEL_IDS = _read_server_ids("NOTEBOOK_SHARED_CHANNEL_IDS")


def notebook_scope(discord_context: dict) -> str | None:
    if not USE_NOTEBOOK:
        return None
    location = discord_context.get("location", {})
    server = location.get("server") or {}
    channel = location.get("channel") or {}
    thread = location.get("thread") or {}
    try:
        channel_id = int(channel["id"])
        if channel_id <= 0:
            return None
        if not server:
            return f"dms/{channel_id}" if NOTEBOOK_DMS else None
        server_id = int(server["id"])
        if server_id not in NOTEBOOK_SERVER_IDS:
            return None
        if thread:
            thread_id = int(thread["id"])
            return f"servers/{server_id}/threads/{thread_id}" if thread_id > 0 else None
        if channel_id in NOTEBOOK_SHARED_CHANNEL_IDS:
            return f"servers/{server_id}/shared"
        return f"servers/{server_id}/channels/{channel_id}"
    except (KeyError, TypeError, ValueError):
        return None