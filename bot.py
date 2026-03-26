import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import os

load_dotenv()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree  # Shorthand for the slash command tree


# --- Events ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await bot.load_extension("Functions.get_manga")
        synced = await tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# --- Slash Commands ---
@tree.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")


@tree.command(name="hello", description="Say hello to someone")
@app_commands.describe(user="The user to greet")
async def hello(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    await interaction.response.send_message(f"Hello, {target.mention}!")


@tree.command(name="echo", description="Repeat your message back")
@app_commands.describe(message="The message to echo")
async def echo(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message)


# --- Run ---
bot.run(os.environ["DISCORD_TOKEN"])
