import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import random
import string
import requests
import sqlite3
from datetime import datetime
import docker
from dotenv import load_dotenv

# ---------------- CONFIG ---------------- #
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
OWNER_ID = int(os.getenv("OWNER_ID"))

ADMINS = set()
BOT_WATERMARK = "IGL Nodes"
INTENTS = discord.Intents.default()
INTENTS.message_content = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)
docker_client = docker.from_env()

# ---------------- DATABASE ---------------- #
conn = sqlite3.connect("db.sqlite3")
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    container_name TEXT,
    owner_id INTEGER,
    ip TEXT,
    username TEXT,
    password TEXT,
    tmate_session TEXT,
    cpu INTEGER,
    ram INTEGER,
    disk INTEGER,
    status TEXT,
    created_at TEXT
)""")
conn.commit()

# ---------------- HELPERS ---------------- #
def generate_password(length=12):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def send_webhook(content, embed=None):
    data = {"content": content}
    if embed:
        data["embeds"] = [embed.to_dict()]
    requests.post(WEBHOOK_URL, json=data)

def watermark_embed(embed: discord.Embed):
    embed.set_footer(text=BOT_WATERMARK)
    return embed

def is_owner(interaction: discord.Interaction):
    return interaction.user.id == OWNER_ID

def is_admin(interaction: discord.Interaction):
    return interaction.user.id in ADMINS or is_owner(interaction)

def create_vps_container(name, os_type, cpu, ram, disk):
    """
    Create a Docker container as VPS. Replace image with desired OS images.
    """
    container = docker_client.containers.run(
        image=f"{os_type}:latest",
        name=name,
        detach=True,
        tty=True,
        mem_limit=f"{ram}g",
        cpu_count=cpu
    )
    return container

def start_tmate_session(container_name):
    """
    Generates a tmate SSH session for container access.
    Requires tmate installed on host and inside container.
    """
    cmd = f"docker exec {container_name} tmate -S /tmp/tmate.sock new-session -d"
    os.system(cmd)
    # Get connection string
    result = os.popen(f"docker exec {container_name} tmate -S /tmp/tmate.sock display -p '#{{tmate_ssh}}'").read().strip()
    return result

# ---------------- BOT EVENTS ---------------- #
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.tree.sync()
    watchdog_task.start()  # start 24/7 watchdog

# ---------------- SLASH COMMANDS ---------------- #

# Add admin
@bot.tree.command(name="add_admin", description="Add an admin by ID (Owner only)")
@app_commands.describe(admin_id="Discord ID of the new admin")
async def add_admin(interaction: discord.Interaction, admin_id: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only owner can add admins.", ephemeral=True)
        return
    ADMINS.add(int(admin_id))
    await interaction.response.send_message(f"✅ Added <@{admin_id}> as admin.")
    send_webhook(f"Owner added admin: {admin_id}")

# Create VPS
@bot.tree.command(name="create_vps", description="Deploy a new VPS")
@app_commands.describe(os_type="OS type", ram="RAM GB", cpu="CPU cores", disk="Disk GB")
async def create_vps_command(interaction: discord.Interaction, os_type: str, ram: int, cpu: int, disk: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)
        return

    await interaction.response.send_message("⚡ Deploying VPS...", ephemeral=True)

    # Create container
    container_name = f"vps_{random.randint(1000,9999)}"
    try:
        container = create_vps_container(container_name, os_type, cpu, ram, disk)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to deploy VPS: {e}")
        return

    # Generate credentials
    username = "user" + str(random.randint(1000,9999))
    password = generate_password()
    tmate_session = start_tmate_session(container_name)
    ip = container.attrs['NetworkSettings']['IPAddress']

    # Save to DB
    c.execute("INSERT INTO vps (container_name, owner_id, ip, username, password, tmate_session, cpu, ram, disk, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (container_name, interaction.user.id, ip, username, password, tmate_session, cpu, ram, disk, "running", datetime.utcnow().isoformat()))
    conn.commit()

    # Send embed
    embed = discord.Embed(title="🎉 VPS Deployed", color=0x00ff00)
    embed.add_field(name="IP", value=ip, inline=True)
    embed.add_field(name="Username", value=username, inline=True)
    embed.add_field(name="Password", value=password, inline=True)
    embed.add_field(name="Tmate SSH", value=tmate_session, inline=False)
    embed.add_field(name="Specs", value=f"{cpu} CPU | {ram}GB RAM | {disk}GB Disk", inline=False)
    embed = watermark_embed(embed)
    await interaction.followup.send(embed=embed)
    send_webhook(f"VPS deployed by {interaction.user}: {container_name}")

# Suspend VPS
@bot.tree.command(name="suspend_vps", description="Stop a VPS (temporarily)")
@app_commands.describe(vps_id="VPS ID to suspend")
async def suspend_vps(interaction: discord.Interaction, vps_id: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    c.execute("SELECT container_name FROM vps WHERE id=?", (vps_id,))
    result = c.fetchone()
    if not result:
        await interaction.response.send_message("❌ VPS ID not found.", ephemeral=True)
        return
    container_name = result[0]
    try:
        container = docker_client.containers.get(container_name)
        container.stop()
        c.execute("UPDATE vps SET status=? WHERE id=?", ("stopped", vps_id))
        conn.commit()
        await interaction.response.send_message(f"✅ VPS {vps_id} suspended.")
        send_webhook(f"VPS {vps_id} suspended by {interaction.user}")
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to suspend: {e}")

# Info command
@bot.tree.command(name="info", description="Show VPS info/specs")
@app_commands.describe(vps_id="VPS ID to check")
async def info_vps(interaction: discord.Interaction, vps_id: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    c.execute("SELECT * FROM vps WHERE id=?", (vps_id,))
    vps = c.fetchone()
    if not vps:
        await interaction.response.send_message("❌ VPS ID not found.", ephemeral=True)
        return
    embed = discord.Embed(title=f"VPS Info: {vps[1]}", color=0x00ffcc)
    embed.add_field(name="IP", value=vps[3])
    embed.add_field(name="Username", value=vps[4])
    embed.add_field(name="Password", value=vps[5])
    embed.add_field(name="Tmate SSH", value=vps[6])
    embed.add_field(name="Specs", value=f"{vps[7]} CPU | {vps[8]}GB RAM | {vps[9]}GB Disk")
    embed.add_field(name="Status", value=vps[10])
    embed.add_field(name="Created At", value=vps[11])
    embed = watermark_embed(embed)
    await interaction.response.send_message(embed=embed)

# ---------------- WATCHDOG / 24/7 VPS ---------------- #
@tasks.loop(minutes=5)
async def watchdog_task():
    c.execute("SELECT id, container_name FROM vps WHERE status='running'")
    for vps_id, container_name in c.fetchall():
        try:
            container = docker_client.containers.get(container_name)
            if container.status != "running":
                container.start()
                send_webhook(f"✅ Watchdog restarted VPS {vps_id}: {container_name}")
        except:
            continue

# ---------------- RUN BOT ---------------- #
bot.run(TOKEN)
