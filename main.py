import discord
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------
# Configured once, at the entry point, so every module's
# logging.getLogger(__name__) call flows through this handler.
# Colours are emitted only to an interactive TTY, so systemd/journald
# capture stays clean (no raw escape codes in the journal).
class PrettyFormatter(logging.Formatter):
    GREY = "\033[90m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD_RED = "\033[1;31m"
    RESET = "\033[0m"

    LEVEL_COLORS = {
        logging.DEBUG: GREY,
        logging.INFO: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
    }

    def __init__(self, use_color: bool):
        super().__init__()
        self.use_color = use_color

    def _paint(self, text: str, color: str) -> str:
        return f"{color}{text}{self.RESET}" if self.use_color else text

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, self.RESET)
        ts = self.formatTime(record, "%H:%M:%S")
        name = record.name.split(".")[-1]  # short module name, e.g. 'api_handler'
        msg = record.getMessage()

        # Tint the whole message for WARNING and above so failures pop visually.
        if record.levelno >= logging.WARNING:
            msg = self._paint(msg, color)

        header = (
            f"{self._paint(ts, self.GREY)} "
            f"{self._paint(f'{record.levelname:<7}', color)} "
            f"{self._paint(f'{name:<13}', self.GREY)}"
        )
        line = f"{header} │ {msg}"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging():
    """Installs the pretty handler on the root logger and quiets noisy libraries.
    Level is controlled by the LOG_LEVEL env var (default INFO); set it to DEBUG
    for maximum verbosity or WARNING to see only problems."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PrettyFormatter(use_color=sys.stdout.isatty()))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # These libraries log a line per HTTP request/heartbeat; keep them out of the way.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.WARNING)


setup_logging()
logger = logging.getLogger(__name__)

# Imported after logging is configured so the logic layer inherits the handler.
from core.logic import process_message

TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user}")
    for guild in client.guilds:
        logger.info(f"In guild: {guild.name} (ID: {guild.id})")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # The logic layer handles all Discord actions (replies, reactions) itself.
    await process_message(message, client.user)

client.run(TOKEN)