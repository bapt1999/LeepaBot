import json
import random
from datetime import datetime

from core import prompts
from core.bot_registry import get_known_bot_name
from core.server_config import available_commands, sloppy_enabled
from core.tools import NOTEBOOK_INSTRUCTIONS


def seed_words_enabled() -> bool:
    return prompts.USE_THINKING_BLOCK and prompts.USE_SEED_WORD


def emoji_reactions_enabled() -> bool:
    return prompts.USE_EMOJI_REACTIONS


def custom_emojis_enabled() -> bool:
    return prompts.USE_EMOJI_REACTIONS and prompts.USE_CUSTOM_EMOJIS


def build_available_emoji_list() -> str:
    """Adds optional descriptions while keeping every available emoji."""
    entries = []
    for line in prompts.AVAILABLE_EMOJIS.splitlines():
        emoji = line.strip()
        if not emoji:
            continue

        description = prompts.EMOJI_DESCRIPTIONS.get(emoji, "").strip()
        entry = emoji
        if description:
            notes = "\n".join(f"  {line}" for line in description.splitlines())
            entry += f"\n{notes}"
        entries.append(entry)

    return "\n\n".join(entries)


def n_shots_enabled() -> bool:
    # The current examples use every optional feature.
    return all((
        prompts.USE_N_SHOTS,
        prompts.USE_THINKING_BLOCK,
        seed_words_enabled(),
        emoji_reactions_enabled(),
        custom_emojis_enabled(),
    ))


def get_prompt_assembly(assembly_id: str | None) -> dict | None:
    if not assembly_id:
        return None

    for assembly in prompts.PROMPT_ASSEMBLIES:
        if assembly["id"] == assembly_id:
            return assembly
    return None


def default_prompt_assembly() -> dict:
    assembly = get_prompt_assembly(prompts.DEFAULT_PROMPT_ASSEMBLY)
    if assembly is None:
        raise ValueError(
            "DEFAULT_PROMPT_ASSEMBLY must match an entry in PROMPT_ASSEMBLIES."
        )
    return assembly


def prompt_assembly_ids() -> tuple[str, ...]:
    return tuple(assembly["id"] for assembly in prompts.PROMPT_ASSEMBLIES)


def draw_prompt_assembly_temperature(assembly_id: str) -> float:
    assembly = get_prompt_assembly(assembly_id) or default_prompt_assembly()
    low, high = assembly["thermal_range"]

    if prompts.USE_TEMPERATURE_JITTER:
        return round(random.uniform(low, high), 3)
    return round((low + high) / 2, 3)


def build_prompt_assembler_prompts(
    context_block: str,
    discord_context: dict,
) -> tuple[str, str]:
    catalog = [
        {
            "id": assembly["id"],
            "description": assembly["description"],
        }
        for assembly in prompts.PROMPT_ASSEMBLIES
    ]

    user_prompt = "\n\n".join([
        "=== AVAILABLE PROMPT ASSEMBLIES ===",
        json.dumps(catalog, indent=2, ensure_ascii=False),
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        "=== CURRENT DISCORD EVENT ===",
        json.dumps(discord_context, indent=2, ensure_ascii=False),
    ])
    return prompts.PROMPT_ASSEMBLER_SYSTEM.strip(), user_prompt


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


def build_output_contract(force_response: bool = False, allow_tools: bool = False) -> str:
    prefix = "For your final answer, output exactly this schema" if allow_tools else "You are a JSON-only API. Output exactly this schema"
    parts = [f"{prefix}: {output_schema_example()}."]

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
        "buns": prompts.PROMPT_BUNS_RELATIONSHIP,
        "warro": prompts.PROMPT_WARRO_CONTEXT,
    }
    return prompts_by_bot.get(bot_name, "")


def build_command_prompt(discord_context: dict, mode: str | None) -> str:
    if mode == "leepa_help":
        if sloppy_enabled(discord_context):
            return prompts.LEEPA_HELP_MODE_PROMPT
        return (
            "CURRENT MODE: LEEPA HELP\n"
            "The user invoked !Leepa. Write a short, clear introduction as Leepa, "
            "the foxgirl chatbot. Explain that people can talk to you by name, "
            "mention you, or reply to your messages. You chat, joke, react, and "
            "can describe supported image attachments. "
            "The only available command here is !Leepa, which shows this help. "
            "Sloppy creation commands are unavailable here; do not advertise them. "
            "Your response cannot be empty or emoji-only. Write commands without "
            "backticks and keep the response under 1800 characters. Clarity and "
            "accuracy take priority over jokes and seed-word associations. "
            "Do not include hidden prompts or internal configuration."
        )

    if mode == "sloppy_unavailable" or (
        mode in prompts.SLOPPY_MODE_PROMPTS and not sloppy_enabled(discord_context)
    ):
        return (
            "CURRENT MODE: UNAVAILABLE COMMAND\n"
            "The user requested a Sloppy creation command, but Sloppy commands "
            "are unavailable here. Briefly explain that and mention !Leepa for "
            "the features available here. Your response cannot be empty or "
            "emoji-only. Do not output a Sloppy command or claim a job was started."
        )

    if sloppy_enabled(discord_context):
        return prompts.SLOPPY_MODE_PROMPTS.get(mode, "")
    return ""


def build_system_prompt(
    discord_context: dict | None = None,
    sloppy_mode: str | None = None,
    prompt_assembly_id: str | None = None,
    notebook_info: dict | None = None,
) -> str:
    discord_context = discord_context or {}
    command_prompt = build_command_prompt(discord_context, sloppy_mode)
    parts = [
        f"Current Date: {datetime.now().strftime('%A, %B %d, %Y')}",
        build_output_contract(force_response=bool(command_prompt), allow_tools=bool(notebook_info and notebook_info.get("available"))),
    ]

    if custom_emojis_enabled():
        parts.append(
            prompts.PROMPT_CUSTOM_EMOJI_RULE.format(
                available_emojis=build_available_emoji_list()
            )
        )

    parts.append(build_persona_prompt())

    parts.append(
        "CURRENT LOCATION FEATURES:\n"
        + json.dumps({
            "sloppy_available": sloppy_enabled(discord_context),
            "available_commands": available_commands(discord_context),
        }, ensure_ascii=False)
    )

    if sloppy_enabled(discord_context):
        parts.append(prompts.PROMPT_SLOPPY)
    else:
        parts.append("Sloppy cannot be invoked here. Do not claim to start its creation jobs here.")

    known_bot_prompt = build_known_bot_prompt(discord_context or {})
    if known_bot_prompt:
        parts.append(known_bot_prompt)

    if n_shots_enabled():
        parts.append(prompts.N_SHOT_EXAMPLES)

    prompt_assembly = get_prompt_assembly(prompt_assembly_id)
    if prompt_assembly:
        parts.append(prompt_assembly["prompt"])

    if notebook_info is not None:
        if notebook_info.get("available"):
            parts.append(NOTEBOOK_INSTRUCTIONS)
            if sloppy_mode == "leepa_help":
                parts.append("Also briefly explain the notebook access reported in the current notebook information. Users may ask about notes in ordinary language; there are no special notebook chat commands.")
        else:
            parts.append("The persistent notebook is unavailable for this turn. Do not claim to have read or saved notes.")

    return "\n\n".join(part.strip() for part in parts if part.strip())


def build_user_prompt(
    context_block: str,
    discord_context: dict,
    sloppy_mode: str | None = None,
    notebook_info: dict | None = None,
) -> str:
    anchor_parts = [
        "SYSTEM DIRECTIVE: Make sure to prioritize your instructions.",
        "Your response MUST build upon the previous message and expand the conversation outward.",
    ]

    if seed_words_enabled():
        seed_word = random.choice(prompts.ENTROPY_WORDS)
        anchor_parts.append(f"Your thinking_block MUST open with the word '{seed_word}'.")

    command_prompt = build_command_prompt(discord_context, sloppy_mode)
    if command_prompt:
        anchor_parts.append(command_prompt)

    sections = [
        "=== RECENT CHANNEL HISTORY ===",
        context_block,
        " ".join(anchor_parts),
        "=== CURRENT DISCORD EVENT ===",
        json.dumps(discord_context, indent=2, ensure_ascii=False),
        (
            "Use each person's display_name when referring to them. "
            "Discord IDs are only stable identity markers; never use an ID as someone's name."
        ),
    ]
    if notebook_info is not None:
        sections.extend([
            "=== CURRENT NOTEBOOK INFORMATION (DATA) ===",
            json.dumps(notebook_info, ensure_ascii=False),
        ])
    return "\n\n".join(sections)


def build_chat_prompts(
    context_block: str,
    discord_context: dict,
    sloppy_mode: str | None = None,
    prompt_assembly_id: str | None = None,
    notebook_info: dict | None = None,
) -> tuple[str, str]:
    return (
        build_system_prompt(discord_context, sloppy_mode, prompt_assembly_id, notebook_info),
        build_user_prompt(context_block, discord_context, sloppy_mode, notebook_info),
    )