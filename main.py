import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    for guild in bot.guilds:
        print(f'In guild: {guild.name} (ID: {guild.id})')




@bot.event
async def on_message(message):
    print("MESSAGE RECEIVED:", message.content)

    if message.author == bot.user:
        return

    if bot.user in message.mentions:
        await message.channel.send("You summoned me?")

    await bot.process_commands(message)



bot.run(TOKEN)