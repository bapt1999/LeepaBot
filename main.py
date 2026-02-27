import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from core.logic import process_message

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

@bot.command()
async def ping(ctx):
    await ctx.send('Pong!')

@bot.event
async def on_message(message):
    print("MESSAGE RECEIVED:", message.content)

    if message.author == bot.user:
        return

    # Pass the full message and the bot user object to the logic layer
    response = await process_message(message, bot.user)
    
    if response:
        await message.channel.send(response)

    await bot.process_commands(message)


bot.run(TOKEN)