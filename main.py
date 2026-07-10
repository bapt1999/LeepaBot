import discord
import os
from dotenv import load_dotenv
from core.logic import process_message

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    for guild in client.guilds:
        print(f'In guild: {guild.name} (ID: {guild.id})')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # The logic layer handles all Discord actions (replies, reactions) itself.
    await process_message(message, client.user)

client.run(TOKEN)