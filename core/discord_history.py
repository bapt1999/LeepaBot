import asyncio
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
