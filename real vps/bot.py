# bot.py
import os
import json
import secrets
import string
import asyncio
import socket
import time
from pathlib import Path
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import docker

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
HOST_IP = os.getenv("HOST_IP", "127.0.0.1")  # set your server public IP here or in .env

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in environment")

ROOT = Path(__file__).parent
DATA_PATH = ROOT / "data.json"
if not DATA_PATH.exists():
    DATA_PATH.write_text(json.dumps({"vps": {}, "admins": [OWNER_ID]}, indent=2))

def load_data():
    return json.loads(DATA_PATH.read_text())

def save_data(data):
    DATA_PATH.write_text(json.dumps(data, indent=2))

def gen_vps_id(length=10):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def gen_password(length=12):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_="
    return ''.join(secrets.choice(chars) for _ in range(length))

def find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

docker_client = docker.from_env()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

async def send_log(content: str):
    if not WEBHOOK_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(WEBHOOK_URL, json={"content": content})
    except Exception:
        pass

def is_admin(user_id: int):
    data = load_data()
    return user_id in data.get("admins", [])

def build_vps_embed(vps_info: dict):
    embed = discord.Embed(title="IGL Nodes — VPS Created", color=0x2b2d31, timestamp=discord.utils.utcnow())
    embed.add_field(name="ID", value=vps_info["vps_id"], inline=True)
    embed.add_field(name="Memory", value=f"{vps_info['memory']} GB", inline=True)
    embed.add_field(name="CPU", value=f"{vps_info['cpu']} cores", inline=True)
    embed.add_field(name="Disk", value=f"{vps_info['disk']} GB", inline=True)
    embed.add_field(name="Username", value=vps_info["username"], inline=True)
    embed.add_field(name="User Password", value=f"||{vps_info['user_password']}||", inline=True)
    embed.add_field(name="Root Password", value=f"||{vps_info['root_password']}||", inline=True)
    ssh = f"ssh {vps_info['username']}@{vps_info['host_ip']} -p {vps_info['host_ssh_port']}"
    embed.add_field(name="SSH Command", value=f"`{ssh}`", inline=False)
    embed.add_field(name="Tmate Session", value=vps_info.get("tmate_session", "Not available"), inline=False)
    embed.set_footer(text="Created By C H A O S")
    return embed

@tree.command(name="create_vps", description="Create a new VPS (will run a Docker container with SSH + tmate)")
@app_commands.describe(memory="Memory (GB)", cpu="CPU cores (e.g. 0.5)", disk="Disk (GB)", public_ip="(optional) Host public IP to show in embed")
async def create_vps(interaction: discord.Interaction, memory: float = 1.0, cpu: float = 0.5, disk: int = 5, public_ip: str = ""):
    await interaction.response.defer(ephemeral=True)
    requester = interaction.user

    # generate ids and creds
    vps_id = gen_vps_id()
    username = f"user{secrets.token_hex(3)}"
    user_password = gen_password()
    root_password = gen_password()
    host_port = find_free_port()
    host_ip_show = public_ip or HOST_IP

    # ensure VPS image available (build if not)
    image_tag = "iglnodes/vps_image:latest"
    try:
        try:
            docker_client.images.get(image_tag)
        except docker.errors.ImageNotFound:
            # build the image from ./vps
            docker_client.images.build(path=str(ROOT / "vps"), tag=image_tag)
    except Exception as e:
        await interaction.followup.send(f"Failed to build/find VPS image: {e}", ephemeral=True)
        await send_log(f"[ERROR] Image build/get error: {e}")
        return

    mem_limit = f"{int(memory)}g" if memory >= 1 else f"{int(memory*1024)}m"
    nano_cpus = int(cpu * 1e9)

    env = {
        "USER_NAME": username,
        "USER_PASS": user_password,
        "ROOT_PASS": root_password
    }

    try:
        container = docker_client.containers.run(
            image_tag,
            detach=True,
            environment=env,
            ports={'22/tcp': ("0.0.0.0", host_port)},
            mem_limit=mem_limit,
            nano_cpus=nano_cpus,
            name=f"vps_{vps_id.lower()}",
            restart_policy={"Name": "unless-stopped"}
        )
    except Exception as e:
        await interaction.followup.send(f"Failed to start container: {e}", ephemeral=True)
        await send_log(f"[ERROR] Container run failed: {e}")
        return

    # wait briefly for container to initialize and tmate to write file
    tmate_link = "Not available"
    attempts = 0
    while attempts < 8:
        try:
            exec_result = container.exec_run("cat /tmate_session.txt", stdout=True, stderr=True, demux=False)
            output = exec_result.output if hasattr(exec_result, "output") else exec_result[1]
            if output:
                tmate_link = output.decode().strip()
                if tmate_link:
                    break
        except Exception:
            pass
        attempts += 1
        await asyncio.sleep(1)

    vps_info = {
        "vps_id": vps_id,
        "container_id": container.id,
        "memory": memory,
        "cpu": cpu,
        "disk": disk,
        "username": username,
        "user_password": user_password,
        "root_password": root_password,
        "host_ip": host_ip_show,
        "host_ssh_port": host_port,
        "owner_id": requester.id,
        "status": "running",
        "tmate_session": tmate_link,
        "created_at": int(time.time())
    }

    data = load_data()
    data["vps"][vps_id] = vps_info
    save_data(data)

    embed = build_vps_embed(vps_info)
    # reply ephemeral and DM
    try:
        await requester.send(embed=embed)
    except Exception:
        # if DM blocked, still send ephemeral (we do both)
        pass

    await interaction.followup.send(embed=embed, ephemeral=True)
    await send_log(f"[CREATE] VPS {vps_id} created by {requester} ({requester.id})")

@tree.command(name="delete_vps", description="Delete a VPS (admin only)")
@app_commands.describe(vps_id="VPS ID to delete")
async def delete_vps(interaction: discord.Interaction, vps_id: str):
    await interaction.response.defer(ephemeral=True)
    if not is_admin(interaction.user.id):
        await interaction.followup.send("You are not authorized to use this command.", ephemeral=True)
        return

    data = load_data()
    vps = data["vps"].get(vps_id)
    if not vps:
        await interaction.followup.send("VPS not found.", ephemeral=True)
        return

    try:
        container = docker_client.containers.get(vps["container_id"])
        container.stop(timeout=5)
        container.remove()
    except docker.errors.NotFound:
        pass
    except Exception as e:
        await interaction.followup.send(f"Failed to remove container: {e}", ephemeral=True)
        return

    del data["vps"][vps_id]
    save_data(data)
    await interaction.followup.send(f"VPS {vps_id} deleted.", ephemeral=True)
    await send_log(f"[DELETE] VPS {vps_id} deleted by {interaction.user} ({interaction.user.id})")

@tree.command(name="suspend_vps", description="Suspend (stop) a VPS (admin only)")
@app_commands.describe(vps_id="VPS ID to suspend")
async def suspend_vps(interaction: discord.Interaction, vps_id: str):
    await interaction.response.defer(ephemeral=True)
    if not is_admin(interaction.user.id):
        await interaction.followup.send("You are not authorized to use this command.", ephemeral=True)
        return

    data = load_data()
    vps = data["vps"].get(vps_id)
    if not vps:
        await interaction.followup.send("VPS not found.", ephemeral=True)
        return

    try:
        container = docker_client.containers.get(vps["container_id"])
        container.stop()
    except Exception as e:
        await interaction.followup.send(f"Failed to suspend VPS: {e}", ephemeral=True)
        return

    vps["status"] = "stopped"
    save_data(data)
    await interaction.followup.send(f"VPS {vps_id} suspended (stopped).", ephemeral=True)
    await send_log(f"[SUSPEND] VPS {vps_id} suspended by {interaction.user} ({interaction.user.id})")

@tree.command(name="add_admin", description="Add a bot admin (owner only)")
@app_commands.describe(user="User to grant admin")
async def add_admin(interaction: discord.Interaction, user: discord.User):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("Only owner can add admins.", ephemeral=True)
        return
    data = load_data()
    admins = data.get("admins", [])
    if user.id in admins:
        await interaction.followup.send("User is already an admin.", ephemeral=True)
        return
    admins.append(user.id)
    data["admins"] = admins
    save_data(data)
    await interaction.followup.send(f"{user} added as admin.", ephemeral=True)
    await send_log(f"[ADMIN ADD] {user} added by {interaction.user}")

@tree.command(name="delete_admin", description="Remove a bot admin (owner only)")
@app_commands.describe(user="User to remove admin")
async def delete_admin(interaction: discord.Interaction, user: discord.User):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("Only owner can delete admins.", ephemeral=True)
        return
    data = load_data()
    admins = data.get("admins", [])
    if user.id not in admins:
        await interaction.followup.send("User is not an admin.", ephemeral=True)
        return
    admins.remove(user.id)
    data["admins"] = admins
    save_data(data)
    await interaction.followup.send(f"{user} removed from admins.", ephemeral=True)
    await send_log(f"[ADMIN REMOVE] {user} removed by {interaction.user}")

@tree.command(name="list_vps", description="List all VPS (admin only)")
async def list_vps(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not is_admin(interaction.user.id):
        await interaction.followup.send("You are not authorized to use this command.", ephemeral=True)
        return
    data = load_data()
    embed = discord.Embed(title="VPS List", color=0x2b2d31)
    if not data.get("vps"):
        embed.description = "No VPS found."
    else:
        for vps_id, info in data.get("vps", {}).items():
            val = f"Owner: <@{info['owner_id']}> | Status: {info.get('status')}\nSSH: `{info['username']}@{info['host_ip']}:{info['host_ssh_port']}`\nTmate: {info.get('tmate_session','N/A')}"
            embed.add_field(name=vps_id, value=val, inline=False)
    embed.set_footer(text="Created By C H A O S")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    try:
        await tree.sync()
    except Exception:
        pass
    print(f"Bot running as {bot.user} ({bot.user.id})")
    await send_log(f"[BOOT] Bot started as {bot.user} ({bot.user.id})")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
