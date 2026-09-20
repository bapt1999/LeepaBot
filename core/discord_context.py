import logging

import discord


logger = logging.getLogger(__name__)


def _display_name(user) -> str:
    """Returns the name people use for this member in the current server."""
    return (
        getattr(user, "display_name", None)
        or getattr(user, "name", None)
        or "Unknown"
    )


def _author_facts(author) -> dict:
    return {
        "display_name": _display_name(author),
        "discord_id": str(author.id),
        "is_bot": bool(getattr(author, "bot", False)),
    }


def _timestamp(message) -> str | None:
    created_at = getattr(message, "created_at", None)
    if created_at is None:
        return None
    return created_at.isoformat().replace("+00:00", "Z")


def _location_facts(message) -> dict:
    guild = getattr(message, "guild", None)
    channel = message.channel

    if guild is None:
        return {
            "kind": "direct_message",
            "server": None,
            "channel": {
                "id": str(channel.id),
                "name": getattr(channel, "name", None) or "Direct message",
            },
            "thread": None,
        }

    server = {
        "id": str(guild.id),
        "name": guild.name,
    }

    if isinstance(channel, discord.Thread):
        parent = channel.parent
        return {
            "kind": "server_thread",
            "server": server,
            "channel": {
                "id": str(parent.id) if parent is not None else None,
                "name": getattr(parent, "name", None),
            },
            "thread": {
                "id": str(channel.id),
                "name": channel.name,
            },
        }

    return {
        "kind": "server_channel",
        "server": server,
        "channel": {
            "id": str(channel.id),
            "name": getattr(channel, "name", None),
        },
        "thread": None,
    }


def _attachment_facts(message, image_descriptions: dict[int, str]) -> list[dict]:
    attachments = []

    for attachment in message.attachments:
        facts = {
            "id": str(attachment.id),
            "filename": attachment.filename,
            "content_type": attachment.content_type,
            "size_bytes": attachment.size,
        }

        description = image_descriptions.get(attachment.id)
        if description:
            facts["image_description"] = description

        attachments.append(facts)

    return attachments


def _message_facts(
    message,
    image_descriptions: dict[int, str] | None = None,
) -> dict:
    return {
        "id": str(message.id),
        "content": message.content or "",
        "created_at": _timestamp(message),
        "author": _author_facts(message.author),
        "attachments": _attachment_facts(message, image_descriptions or {}),
    }


def _reference_channel(message):
    reference = message.reference
    channel_id = getattr(reference, "channel_id", None)

    if channel_id is None or channel_id == message.channel.id:
        return message.channel

    guild = getattr(message, "guild", None)
    if guild is None:
        return None

    channel = guild.get_channel(channel_id)
    if channel is None and hasattr(guild, "get_thread"):
        channel = guild.get_thread(channel_id)
    return channel


async def _resolve_reply(message) -> tuple[dict | None, int | None]:
    reference = getattr(message, "reference", None)
    if reference is None or reference.message_id is None:
        return None, None

    resolved = reference.resolved
    if isinstance(resolved, discord.Message):
        return _message_facts(resolved), resolved.author.id

    if isinstance(resolved, discord.DeletedReferencedMessage):
        return {"status": "could_not_be_loaded"}, None

    channel = _reference_channel(message)
    if channel is None or not hasattr(channel, "fetch_message"):
        return {"status": "could_not_be_loaded"}, None

    try:
        referenced_message = await channel.fetch_message(reference.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        logger.info(
            "Could not load referenced Discord message %s: %s",
            reference.message_id,
            exc,
        )
        return {"status": "could_not_be_loaded"}, None

    return _message_facts(referenced_message), referenced_message.author.id


async def collect_discord_context(
    message,
    image_descriptions: dict[int, str] | None = None,
) -> tuple[dict, int | None]:
    """Collects Discord facts and the referenced author's ID for private routing."""
    reply_to, referenced_author_id = await _resolve_reply(message)

    context = {
        "location": _location_facts(message),
        "message": _message_facts(message, image_descriptions),
        "reply_to": reply_to,
    }
    return context, referenced_author_id