import os

from dotenv import load_dotenv


load_dotenv()


def _read_discord_id(variable_name: str) -> int:
    value = os.getenv(variable_name, "").strip()
    if not value:
        return 0

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{variable_name} must contain a Discord ID.") from exc


RAKUN_ID = _read_discord_id("RAKUN_ID")
SLOPPY_ID = _read_discord_id("SLOPPY_ID")
BUNS_ID = _read_discord_id("BUNS_ID")


# Add future server bots here after giving them an environment variable above.
KNOWN_BOT_IDS = {
    "rakun": RAKUN_ID,
    "sloppy": SLOPPY_ID,
    "buns": BUNS_ID,
}


def get_known_bot_name(discord_id: int | str | None) -> str | None:
    if discord_id is None:
        return None

    try:
        normalized_id = int(discord_id)
    except (TypeError, ValueError):
        return None

    if normalized_id == 0:
        return None

    for bot_name, bot_id in KNOWN_BOT_IDS.items():
        if bot_id == normalized_id:
            return bot_name

    return None