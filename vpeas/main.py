import discord
from discord.ext import commands, tasks
from discord import app_commands
import os, uuid, subprocess, asyncio
from datetime import datetime
import aiohttp
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# CONFIG
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
LOG_WEBHOOK_URL = os.getenv("LOG_WEBHOOK_URL")

DATA_DIR = "vps_data"
WATERMARK = "IGL Nodes"
ADMIN_ROLE = "Admin"

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -----------------------------
# HELPERS
# -----------------------------
async def send_webhook_log(message):
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(LOG_WEBHOOK_URL, json={"content": message})
        except:
            pass

def log_action(user, action, vps_id=None):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {user} -> {action} (VPS: {vps_id})\n"
    with open(os.path.join(DATA_DIR, "vps_log.txt"), "a") as f:
        f.write(entry)
    asyncio.create_task(send_webhook_log(entry))

def is_admin(member):
    return ADMIN_ROLE in [role.name for role in member.roles]

def is_owner(member):
    return member.id == OWNER_ID

def save_vps_info(vps_id, info):
    path = os.path.join(DATA_DIR, f"{vps_id}.txt")
    with open(path, "w") as f:
        for k,v in info.items():
            f.write(f"{k}: {v}\n")

def get_vps_info(vps_id):
    path = os.path.join(DATA_DIR, f"{vps_id}.txt")
    if not os.path.exists(path):
        return None
    info = {}
    with open(path, "r") as f:
        for line in f:
            if ": " in line:
                k,v = line.strip().split(": ",1)
                info[k] = v
    return info

def get_vps_uptime(info):
    if "Creation_Time" not in info:
        return "Unknown"
    created = datetime.fromisoformat(info["Creation_Time"])
    delta = datetime.utcnow() - created
    days, rem = divmod(delta.total_seconds(), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{int(days)}d {int(hours)}h {int(minutes)}m"

# -----------------------------
# VPS CREATION (Docker)
# -----------------------------
async def create_vps(ram, cpu):
    vps_id = str(uuid.uuid4())[:8]
    container_name = f"vps_{vps_id}"
    username = f"user_{vps_id}"
    password = str(uuid.uuid4())[:12]

    # Create Docker container
    subprocess.run([
        "docker","run","-dit",
        "--name", container_name,
        "--memory", f"{ram}g",
        "--cpus", str(cpu),
        "ubuntu:latest","bash"
    ], check=True)

    tmate_session = f"tmate://{vps_id}"  # Placeholder for Tmate

    info = {
        "VPS_ID": vps_id,
        "Provider": "docker",
        "Username": username,
        "Password": password,
        "Server_ID": container_name,
        "IP": "Local",
        "RAM": f"{ram}GB",
        "CPU": cpu,
        "Creation_Time": datetime.utcnow().isoformat(),
        "Tmate": tmate_session
    }
    save_vps_info(vps_id, info)
    return info

# -----------------------------
# WATCHDOG 24/7
# -----------------------------
@tasks.loop(minutes=5)
async def vps_watchdog():
    vps_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".txt") and f != "vps_log.txt"]
    for file in vps_files:
        info = get_vps_info(file.replace(".txt",""))
        try:
            result = subprocess.run(["docker","ps","-f",f"name={info['Server_ID']}","--format","{{.Status}}"],
                                    capture_output=True, text=True)
            if not result.stdout.strip():
                subprocess.run(["docker","start",info['Server_ID']])
        except Exception as e:
            print(f"Watchdog error for VPS {info['VPS_ID']}: {e}")

# -----------------------------
# COMMANDS
# -----------------------------
@bot.command()
async def create_vps_cmd(ctx, ram: int=1, cpu: int=1):
    try:
        info = await create_vps(ram, cpu)
        log_action(ctx.author, "CREATE VPS", info["VPS_ID"])
        embed = discord.Embed(title="🎉 VPS Created!", color=0x1abc9c)
        for k,v in info.items():
            embed.add_field(name=k, value=f"{v}", inline=True)
        embed.set_footer(text=f"💧 Managed by {WATERMARK}")
        await ctx.author.send(embed=embed)
        await ctx.send(f"✅ VPS `{info['VPS_ID']}` created! Check your DMs. 💧 {WATERMARK}")
    except Exception as e:
        await ctx.send(f"❌ Failed to create VPS: {e}")

@bot.command()
async def list_vps(ctx):
    vps_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".txt") and f != "vps_log.txt"]
    if not vps_files:
        await ctx.send("No VPS found.")
        return
    embed = discord.Embed(title="📊 VPS List", color=0x3498db)
    for file in vps_files:
        info = get_vps_info(file.replace(".txt",""))
        embed.add_field(
            name=f"{info['VPS_ID']} ({info['Provider']})",
            value=f"IP: {info['IP']}\nUptime: {get_vps_uptime(info)}\nUser: {info['Username']}\nTmate: {info.get('Tmate','N/A')}",
            inline=False
        )
    embed.set_footer(text=f"💧 Managed by {WATERMARK}")
    await ctx.send(embed=embed)

@bot.command()
async def delete_vps(ctx, vps_id: str):
    info = get_vps_info(vps_id)
    if not info:
        await ctx.send("VPS not found.")
        return
    try:
        subprocess.run(["docker","rm","-f",info["Server_ID"]])
        os.remove(os.path.join(DATA_DIR, f"{vps_id}.txt"))
        log_action(ctx.author, "DELETE VPS", vps_id)
        await ctx.send(f"✅ VPS `{vps_id}` deleted. 💧 {WATERMARK}")
    except Exception as e:
        await ctx.send(f"❌ Failed to delete VPS: {e}")

# -----------------------------
# OWNER COMMAND: ADD ADMIN
# -----------------------------
@tree.command(name="add_admin", description="Give a user admin role")
@app_commands.checks.has_permissions(administrator=True)
async def add_admin(interaction: discord.Interaction, user: discord.Member):
    if not is_owner(interaction.user):
        await interaction.response.send_message("❌ Only the owner can add admins.", ephemeral=True)
        return
    role = discord.utils.get(interaction.guild.roles, name=ADMIN_ROLE)
    if not role:
        role = await interaction.guild.create_role(name=ADMIN_ROLE, color=discord.Color.green())
    await user.add_roles(role)
    await interaction.response.send_message(f"✅ {user.mention} is now an admin.", ephemeral=True)

# -----------------------------
# ON READY
# -----------------------------
@bot.event
async def on_ready():
    await tree.sync()
    vps_watchdog.start()
    print(f"Bot connected as {bot.user} | Managed by {WATERMARK}")

# -----------------------------
# RUN BOT
# -----------------------------
bot.run(BOT_TOKEN)
