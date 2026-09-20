import json
import random
from datetime import datetime

from core import prompts
from core.bot_registry import get_known_bot_name


def seed_words_enabled() -> bool:
    return prompts.USE_THINKING_BLOCK and prompts.USE_SEED_WORD


def emoji_reactions_enabled() -> bool:
    return prompts.USE_EMOJI_REACTIONS


def custom_emojis_enabled() -> bool:
    return prompts.USE_EMOJI_REACTIONS and prompts.USE_CUSTOM_EMOJIS


def n_shots_enabled() -> bool:
    # The current examples use every optional feature.
    return all((
        prompts.USE_N_SHOTS,
        prompts.USE_THINKING_BLOCK,
        seed_words_enabled(),
        emoji_reactions_enabled(),
        custom_emojis_enabled(),
    ))


def expected_string_fields() -> tuple[str, ...]:
    fields = []
    if prompts.USE_THINKING_BLOCK:
        fields.append("thinking_block")
    fields.append("internal_mood")
    if emoji_reactions_enabled():
        fields.append("reaction_emoji")
    fields.append("response")
    return tuple(fields)


def output_schema_example() -> str:
    return json.dumps({field: "string" for field in expected_string_fields()})


def build_output_contract(force_response: bool = False) -> str:
    parts = [f"You are a JSON-only API. Output exactly this schema: {output_schema_example()}."]

    if prompts.USE_THINKING_BLOCK:
        parts.append(
            "Keep the thinking_block as a single, plain-text string without line breaks or double quotes."
        )

    if emoji_reactions_enabled():
        emoji_kind = "custom or Unicode" if custom_emojis_enabled() else "Unicode"
        parts.append(
            f"Use reaction_emoji for ONE {emoji_kind} emoji if it naturally fits the message vibe."
        )

    if not force_response:
        parts.append(
            "Leave response empty if you determine the message does not logically require your intervention based on your Autonomy Directive."
        )
    return " ".join(parts)


def build_persona_prompt() -> str:
    parts = [prompts.PROMPT_IDENTITY_AND_LORE]

    if prompts.USE_THINKING_BLOCK:
        thinking_prompt = (
            prompts.PROMPT_THINKING_WITH_SEED
            if seed_words_enabled()
            else prompts.PROMPT_THINKING_WITHOUT_SEED
        )
        parts.append(thinking_prompt)

    parts.append(prompts.PROMPT_BEHAVIOR)

    if emoji_reactions_enabled():
        emoji_kind = "custom or Unicode" if custom_emojis_enabled() else "Unicode"
        parts.append(
            prompts.PROMPT_RESPONSE_TOOLS_WITH_REACTIONS.format(emoji_kind=emoji_kind)
        )
    else:
        parts.append(prompts.PROMPT_RESPONSE_TOOLS_TEXT_ONLY)

    parts.append(prompts.PROMPT_CREATIVE_TOOLS)
    parts.append(
        prompts.PROMPT_RESPONSE_WITH_THINKING
        if prompts.USE_THINKING_BLOCK
        else prompts.PROMPT_RESPONSE_WITHOUT_THINKING
    )
    if n_shots_enabled():
        parts.append(prompts.PROMPT_N_SHOT_GUIDANCE)

    parts.append(prompts.PROMPT_CONSTRAINTS)
    return "\n\n".join(part.strip() for part in parts if part.strip())


def build_known_bot_prompt(discord_context: dict) -> str:
    author = discord_context.get("message", {}).get("author", {})
    bot_name = get_known_bot_name(author.get("discord_id"))

    prompts_by_bot = {
        "rakun": prompts.PROMPT_RAKUN_RELATIONSHIP,
    }
    return prompts_by_bot.get(bot_name, "")


def build_system_prompt(
    discord_context: dict | None = None,
    sloppy_mode: str | None = None,
) -> str:
    active_sloppy_mode = (
        sloppy_mode
        if prompts.USE_SLOPPY and sloppy_mode in prompts.SLOPPY_MODE_PROMPTS
        else None
    )
    parts = [
        f"Current Date: {datetime.now().strftime('%A, %B %d, %Y')}",
        build_output_contract(force_response=active_sloppy_mode is not None),
    ]

    if custom_emojis_enabled():
        parts.append(
            prompts.PROMPT_CUSTOM_EMOJI_RULE.format(
                available_emojis=prompts.AVAILABLE_EMOJIS
            )
        )

    parts.append(build_persona_prompt())

    if prompts.USE_SLOPPY:
        parts.append(prompts.PROMPT_SLOPPY)

    known_bot_prompt = build_known_bot_prompt(discord_context or {})
    if known_bot_prompt:
        parts.append(known_bot_prompt)

    if n_shots_enabled():
        parts.append(prompts.N_SHOT_EXAMPLES)

    return "\n\n".join(part.strip() for part in parts if part.strip())


def build_user_prompt(
    context_block: str,
    discord_context: dict,
    sloppy_mode: str | None = None,
) -> str:
    anchor_parts = [
        "SYSTEM DIRECTIVE: Make sure to prioritize your instructions.",
        "Your response MUST build upon the previous message and expand the conversation outward.",
    ]

    if seed_words_enabled():
        seed_word = random.choice(prompts.ENTROPY_WORDS)
        anchor_parts.append(f"Your thinking_block MUST open with the word '{seed_word}'.")

    if prompts.USE_SLOPPY:
        sloppy_mode_prompt = prompts.SLOPPY_MODE_PROMPTS.get(sloppy_mode, "")
        if sloppy_mode_prompt:
            anchor_parts.append(sloppy_mode_prompt)

    return "\n\n".join([
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        " ".join(anchor_parts),
        "=== CURRENT DISCORD EVENT ===",
        json.dumps(discord_context, indent=2, ensure_ascii=False),
        (
            "Use each person's display_name when referring to them. "
            "Discord IDs are only stable identity markers; never use an ID as someone's name."
        ),
    ])


def build_chat_prompts(
    context_block: str,
    discord_context: dict,
    sloppy_mode: str | None = None,
) -> tuple[str, str]:
    return (
        build_system_prompt(discord_context, sloppy_mode),
        build_user_prompt(context_block, discord_context, sloppy_mode),
    )