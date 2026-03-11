import os
import json
import time
import ast
import unicodedata
from datetime import datetime
import threading

import requests
import aiohttp
from flask import Flask, request, jsonify

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.app_commands import AppCommandError


def normalize_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip()


DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DROPBOX_REFRESH = os.environ["DROPBOX_REFRESH"]
DROPBOX_APP_KEY = os.environ["DROPBOX_APP_KEY"]
DROPBOX_APP_SECRET = os.environ["DROPBOX_APP_SECRET"]
ROLE_ID_ALLOWED = int(os.environ["ROLE_ID_ALLOWED"])
STAFF_CHANNEL_ID = int(os.environ["STAFF_CHANNEL_ID"])
STAFF_ROLE_ID = int(os.environ["STAFF_ROLE_ID"])
BANNER_REQUEST = os.environ["BANNER_REQUEST"]
BANNER_APPROVED = os.environ["BANNER_APPROVED"]
KEY_CHANNEL_ID = int(os.environ["KEY_CHANNEL_ID"])
PING_CHANNEL_ID = int(os.environ["PING_CHANNEL_ID"])
DELET_CHANNEL_ID = int(os.environ["DELET_CHANNEL_ID"])
WELCOME_ROLE_ID = int(os.environ["WELCOME_ROLE_ID"])
MY_GUILD_ID = int(os.environ["MY_GUILD_ID"])

LICENSE_LISTS: dict[int, list[str]] = {}
MY_GUILD = discord.Object(id=MY_GUILD_ID)

PRIMARY_KEEP_ALIVE_URL = "https://auth-clco.onrender.com/validate"
PRIMARY_KEEP_ALIVE_KEY = "PING_KEEPALIVE"


app = Flask(__name__)

ACCESS_TOKEN = None
ACCESS_TOKEN_TIME = None
SERVER_START_TIME = time.time()

REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]
APP_KEY = os.environ["APP_KEY"]
APP_SECRET = os.environ["APP_SECRET"]

SELF_BASE_URL = os.environ.get("SELF_BASE_URL", "https://auth-clco.onrender.com")
KEEPALIVE_INTERVAL = int(os.environ.get("KEEPALIVE_INTERVAL", 60))
KEEPALIVE_RUNNING = True


def get_uptime():
    s = int(time.time() - SERVER_START_TIME)
    h = s // 3600
    m = (s % 3600) // 60
    s %= 60
    return f"{h}h {m}m {s}s"


def get_access_token():
    global ACCESS_TOKEN, ACCESS_TOKEN_TIME

    if ACCESS_TOKEN and (datetime.now() - ACCESS_TOKEN_TIME).seconds < 14400:
        return ACCESS_TOKEN

    r = requests.post(
        "https://api.dropbox.com/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id": APP_KEY,
            "client_secret": APP_SECRET,
        },
    )
    r.raise_for_status()

    ACCESS_TOKEN = r.json()["access_token"]
    ACCESS_TOKEN_TIME = datetime.now()
    return ACCESS_TOKEN


def download_license(username: str) -> str:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt"}}',
    }

    r = requests.post(
        "https://content.dropboxapi.com/2/files/download",
        headers=headers,
    )
    r.raise_for_status()
    return r.text


def upload_license(username: str, content: str) -> bool:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt", "mode": "overwrite"}}',
        "Content-Type": "application/octet-stream",
    }

    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers=headers,
        data=content.encode(),
    )
    r.raise_for_status()
    return True


def list_files(folder_path: str):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    r = requests.post(
        "https://api.dropboxapi.com/2/files/list_folder",
        headers=headers,
        json={
            "path": folder_path,
            "recursive": False,
            "include_deleted": False,
        },
    )
    r.raise_for_status()

    files = []

    for f in r.json().get("entries", []):
        if f.get(".tag") != "file":
            continue

        try:
            link_resp = requests.post(
                "https://api.dropboxapi.com/2/files/get_temporary_link",
                headers=headers,
                json={"path": f.get("path_lower")},
            )
            link_resp.raise_for_status()
            files.append(
                {
                    "name": f.get("name"),
                    "url": link_resp.json().get("link"),
                }
            )
        except Exception:
            pass

    return files


def count_licenses() -> int:
    try:
        return len(list_files("/licenses"))
    except Exception:
        return 0


def count_loader_files() -> int:
    try:
        return len(list_files("/loader"))
    except Exception:
        return 0


def keepalive_bot():
    global KEEPALIVE_RUNNING
    url = f"{SELF_BASE_URL}/validate"

    while KEEPALIVE_RUNNING:
        try:
            payload = {
                "username": "PING_KEEPALIVE",
                "password": "",
            }
            headers = {"Content-Type": "application/json"}
            resp = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=10,
            )
            print(f"[KEEPALIVE] status={resp.status_code} resp={resp.text[:200]}")
        except Exception as e:
            print(f"[KEEPALIVE] error: {e}")
        time.sleep(KEEPALIVE_INTERVAL)


def start_keepalive_thread():
    t = threading.Thread(target=keepalive_bot, daemon=True)
    t.start()


@app.route("/games", methods=["GET"])
def games():
    try:
        files = list_files("/elementos")
    except Exception as e:
        return jsonify({"error": True, "status": str(e), "files": []}), 500

    zip_files = [f for f in files if f["name"].lower().endswith(".zip")]

    return jsonify({
        "error": False,
        "status": "OK",
        "files": zip_files
    }), 200


@app.route("/validate", methods=["POST"])
def validate():
    data = request.json or {}

    if data.get("username") == "PING_KEEPALIVE":
        return (
            jsonify(
                {
                    "error": False,
                    "status": "SERVER_ALIVE",
                    "server_time": datetime.now().isoformat(),
                    "uptime": get_uptime(),
                    "licenses_total": count_licenses(),
                    "loader_files": count_loader_files(),
                }
            ),
            200,
        )

    username = data.get("username")
    password = data.get("password", "")
    hwid = data.get("hwid", "")
    cpu_id = data.get("cpu_id", "")
    ram = data.get("ram", "")
    mac = data.get("mac", "")
    disk = data.get("disk", "")
    ip = data.get("ip", "")

    try:
        content = download_license(username)
    except Exception:
        return jsonify({"error": True, "status": "User not found"}), 404

    lines = content.splitlines()
    lic = {}
    roles_dict = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.lower().startswith("roles="):
            try:
                roles_dict = ast.literal_eval(line.split("=", 1)[1])
            except Exception:
                roles_dict = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            lic[key.strip()] = value.strip()

    lic["roles"] = roles_dict

    if lic.get("pass") and lic["pass"] != password:
        return jsonify({"error": True, "status": "Incorrect password"}), 403

    expire_date = datetime.fromisoformat(
        lic.get("expires", "2100-01-01T00:00:00")
    )
    if datetime.now() > expire_date:
        return jsonify({"error": True, "status": "License expired"}), 403

    is_global = lic.get("global", "false").lower() == "true"

    if not is_global:
        updated = False

        for k, v in [
            ("hwid", hwid),
            ("cpu_id", cpu_id),
            ("ram", ram),
            ("mac", mac),
            ("disk", disk),
            ("ip", ip),
        ]:
            if v and not lic.get(k):
                lic[k] = v
                updated = True

        if updated:
            upload_license(username, "\n".join(f"{k}={v}" for k, v in lic.items()))

        for k, v in [("hwid", hwid), ("cpu_id", cpu_id), ("mac", mac)]:
            if v and lic.get(k) and v != lic.get(k):
                return (
                    jsonify({"error": True, "status": f"{k.upper()} mismatch"}),
                    403,
                )

    try:
        loader_files = list_files("/loader")
    except Exception:
        loader_files = []

    try:
        game_files = list_files("/elementos")
    except Exception:
        game_files = []

    return (
        jsonify(
            {
                "error": False,
                "status": "Login successful",
                "license": lic,
                "files": loader_files,
                "games": game_files,
            }
        ),
        200,
    )


def upload_file(path: str, content: str) -> bool:
    token = get_access_token()
    url = "https://content.dropboxapi.com/2/files/upload"
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps(
            {
                "path": path,
                "mode": "overwrite",
                "mute": True,
            }
        ),
        "Content-Type": "application/octet-stream",
    }
    r = requests.post(url, headers=headers, data=content.encode("utf-8"))
    r.raise_for_status()
    return True


def download_file(path: str) -> str:
    token = get_access_token()
    url = "https://content.dropboxapi.com/2/files/download"
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": json.dumps({"path": path}),
    }
    r = requests.post(url, headers=headers)
    r.raise_for_status()
    return r.text


def delete_file(path: str) -> bool:
    token = get_access_token()
    url = "https://api.dropboxapi.com/2/files/delete_v2"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {"path": path}
    r = requests.post(url, headers=headers, data=json.dumps(data))
    if r.status_code in (409, 404):
        return True
    r.raise_for_status()
    return True


def make_license_content(
    user_identifier: str,
    password: str,
    expires_dt: datetime,
    hwid: str = "",
    global_user: bool = False,
    avatar_url: str = "",
    username: str = "",
    roles: list[dict] | None = None,
) -> str:
    roles_dict: dict[str, str] = {}
    if roles:
        for r in roles:
            roles_dict[r["name"]] = r["color"]
    content = (
        f"user={user_identifier}\n"
        f"pass={password}\n"
        f"hwid={hwid}\n"
        f"expires={expires_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"global={str(global_user).lower()}\n"
        f"avatar_url={avatar_url}\n"
        f"username={username}\n"
        f"roles={roles_dict}\n"
    )
    return content


def update_user_fields(
    content: str,
    new_username: str,
    new_avatar: str,
    new_roles: list[dict],
) -> tuple[str, bool]:
    lines = content.splitlines()
    lic: dict[str, str] = {}
    roles_dict: dict[str, str] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("roles="):
            try:
                roles_dict = ast.literal_eval(line.split("=", 1)[1])
            except Exception:
                roles_dict = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            lic[key.strip()] = value.strip()

    changed = False

    if lic.get("username") != new_username:
        lic["username"] = new_username
        changed = True

    if lic.get("avatar_url") != new_avatar:
        lic["avatar_url"] = new_avatar
        changed = True

    roles_dict_new = {r["name"]: r["color"] for r in new_roles}
    if roles_dict != roles_dict_new:
        roles_dict = roles_dict_new
        changed = True

    if not changed:
        return content, False

    new_lines = []
    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue

        lower = line.lower()

        if lower.startswith("username="):
            new_lines.append(f"username={lic.get('username', '')}")
        elif lower.startswith("avatar_url="):
            new_lines.append(f"avatar_url={lic.get('avatar_url', '')}")
        elif lower.startswith("roles="):
            new_lines.append(f"roles={roles_dict}")
        else:
            new_lines.append(line)

    updated_content = "\n".join(new_lines)
    return updated_content, True


def role_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise AppCommandError("This command can only be used in a server.")
        role = interaction.guild.get_role(ROLE_ID_ALLOWED)
        if role is None:
            raise AppCommandError("The admin role was not found in this server.")
        if role in interaction.user.roles:
            return True
        raise AppCommandError("You do not have permission to use this command.")

    return app_commands.check(predicate)


async def clear_keepalive_messages(channel: discord.TextChannel) -> None:
    async for msg in channel.history(limit=200):
        if msg.author == bot.user and msg.embeds:
            try:
                await msg.delete()
            except Exception:
                pass


intents = discord.Intents.default()
intents.members = True


class MyBot(commands.Bot):
    async def setup_hook(self):
        if not keep_alive.is_running():
            keep_alive.start()
        if not scan_usernames.is_running():
            scan_usernames.start()
        await self.tree.sync(guild=MY_GUILD)


bot = MyBot(command_prefix="!", intents=intents)


@tasks.loop(minutes=1)
async def keep_alive():
    await bot.wait_until_ready()
    channel = bot.get_channel(PING_CHANNEL_ID)
    if channel is None:
        return
    headers = {"Content-Type": "application/json"}
    payload = {
        "username": PRIMARY_KEEP_ALIVE_KEY,
        "password": "",
    }
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status_code = 0
    ok = False
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                PRIMARY_KEEP_ALIVE_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                status_code = resp.status
                ok = resp.status == 200
        except Exception:
            ok = False
    desc = "\n".join(
        [
            f"`🕒 Local time: {now}`",
            f"`🌐 Auth server: {status_code} {'✅' if ok else '❌'}`",
        ]
    )
    embed = discord.Embed(
        title="🟢 Server status" if ok else "🔴 Server status",
        description=desc,
        color=discord.Color.green() if ok else discord.Color.red(),
    )
    embed.set_image(
        url="https://i.pinimg.com/736x/33/cc/dc/33ccdc01257438f7deb3bf911fc68dc5.jpg"
    )
    embed.set_footer(text="dev @zuzu ☁ ☂")
    await clear_keepalive_messages(channel)
    await channel.send(embed=embed)


@tasks.loop(minutes=1)
async def scan_usernames():
    await bot.wait_until_ready()
    guild = bot.get_guild(MY_GUILD.id)
    if guild is None:
        return
    for member in guild.members:
        user_id = str(member.id)
        path = f"/licenses/{user_id}.txt"
        try:
            content = download_file(path)
        except Exception:
            continue
        roles_list = [
            {"name": role.name, "color": f"#{role.color.value:06X}"}
            for role in member.roles
            if role.name != "@everyone"
        ]
        updated_content, changed = update_user_fields(
            content,
            new_username=member.display_name,
            new_avatar=(member.display_avatar.url if member.display_avatar else ""),
            new_roles=roles_list,
        )
        if changed:
            try:
                upload_file(path, updated_content)
            except Exception:
                pass


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: AppCommandError,
):
    try:
        await interaction.response.send_message(str(error), ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(str(error), ephemeral=True)


@bot.tree.command(name="clear")
async def clear(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is not None:
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.followup.send(
                "Only the server owner can use this command here.",
                ephemeral=True,
            )
            return
        perms = interaction.channel.permissions_for(interaction.guild.me)
        if not perms.manage_messages:
            await interaction.followup.send(
                "I do not have permission to delete messages in this channel.",
                ephemeral=True,
            )
            return
        deleted = await interaction.channel.purge(limit=None)
        await interaction.followup.send(
            f"Cleanup completed\nDeleted messages: **{len(deleted)}**",
            ephemeral=True,
        )
        return
    deleted_count = 0
    try:
        async for msg in interaction.channel.history(limit=200):
            try:
                await msg.delete()
                deleted_count += 1
            except Exception:
                pass
        await interaction.followup.send(
            f"Cleanup completed\nDeleted messages: **{deleted_count}**",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"Error: {e}",
            ephemeral=True,
        )


@bot.tree.command(
    name="create_id",
    description="Create a license bound to a Discord user ID.",
    guild=MY_GUILD,
)
@app_commands.default_permissions(administrator=True)
@role_check()
@app_commands.describe(
    user_id="User ID",
    day="Day of expiration",
    month="Month of expiration",
    year="Year of expiration",
    hour="Hour of expiration",
    minute="Minute of expiration",
    second="Second of expiration",
)
async def create_id(
    interaction: discord.Interaction,
    user_id: str,
    day: int,
    month: int,
    year: int,
    hour: int,
    minute: int,
    second: int,
):
    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.followup.send(
            embed=discord.Embed(
                title="Invalid ID",
                description=f"The provided ID (**{user_id}**) is not a valid integer.",
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return
    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except Exception:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="User not found",
                    description=f"Could not find a user with ID **{uid}**.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
    try:
        expires_dt = datetime(year, month, day, hour, minute, second)
    except Exception as e:
        await interaction.followup.send(
            embed=discord.Embed(
                title="Invalid date",
                description=str(e),
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )
        return
    username = member.display_name
    avatar_url = member.display_avatar.url if member.display_avatar else ""
    roles_list = [
        {"name": role.name, "color": f"#{role.color.value:06X}"}
        for role in member.roles
        if role.name != "@everyone"
    ]
    content = make_license_content(
        user_identifier=str(uid),
        password="",
        expires_dt=expires_dt,
        avatar_url=avatar_url,
        username=username,
        roles=roles_list,
    )
    path = f"/licenses/{uid}.txt"
    try:
        upload_file(path, content)
        embed_success = discord.Embed(
            title="License created",
            description=(
                f"A license has been created for **{username}**\n"
                f"**Expires:** `{expires_dt.strftime('%Y-%m-%d %H:%M:%S')}`\n"
                f"**Registered roles:** "
                f"`{', '.join([r['name'] for r in roles_list]) or 'None'}`"
            ),
            color=discord.Color.green(),
        )
        embed_success.set_thumbnail(
            url=(
                avatar_url
                or "https://i.pinimg.com/1200x/38/e0/d9/38e0d9f5ceafa302822468bb89f60608.jpg"
            )
        )
        await interaction.followup.send(embed=embed_success, ephemeral=True)
        embed_dm = discord.Embed(
            title="License assigned",
            description=(
                f"Hello **{member.name}**, a license has been assigned to you.\n\n"
                f"**ID:** `{uid}`\n"
                f"**Expires:** `{expires_dt.strftime('%Y-%m-%d %H:%M:%S')}`\n"
                f"**Registered roles:** "
                f"`{', '.join([r['name'] for r in roles_list]) or 'None'}`"
            ),
            color=discord.Color.blue(),
        )
        embed_dm.set_thumbnail(url=avatar_url)
        embed_dm.set_footer(text="License generated automatically by the system.")
        try:
            await member.send(embed=embed_dm)
        except discord.Forbidden:
            await interaction.followup.send(
                f"I could not DM the user `{member}` (private messages disabled).",
                ephemeral=True,
            )
    except Exception as e:
        await interaction.followup.send(
            embed=discord.Embed(
                title="Error creating license",
                description=str(e),
                color=discord.Color.red(),
            ),
            ephemeral=True,
        )


@bot.tree.command(
    name="reset",
    description="Reset HWID and device data for a license.",
    guild=MY_GUILD,
)
@app_commands.default_permissions(administrator=True)
@role_check()
@app_commands.describe(user="User ID or license file name (without extension)")
async def reset(interaction: discord.Interaction, user: str):
    await interaction.response.defer(ephemeral=True)
    path = f"/licenses/{user}.txt"
    try:
        data = download_file(path)
    except Exception:
        embed_error = discord.Embed(
            title="License not found",
            description=f"The license for **{user}** was not found.",
            color=discord.Color.red(),
        )
        embed_error.set_thumbnail(
            url="https://i.pinimg.com/736x/46/53/95/465395a1f02981befd4f30ae8ef6c9c7.jpg"
        )
        await interaction.followup.send(embed=embed_error, ephemeral=True)
        return
    reset_fields = ["hwid", "cpu_id", "ram", "mac", "disk", "ip"]
    new_lines = []
    for line in data.split("\n"):
        cleaned = False
        for field in reset_fields:
            if line.startswith(f"{field}="):
                new_lines.append(f"{field}=")
                cleaned = True
                break
        if not cleaned:
            new_lines.append(line)
    new_content = "\n".join(new_lines)
    upload_file(path, new_content)
    embed_success = discord.Embed(
        title="License reset",
        description=(
            f"The verification data for **{user}** has been reset:\n"
            "**HWID, CPU, RAM, MAC, DISK, IP**"
        ),
        color=discord.Color.green(),
    )
    embed_success.set_thumbnail(
        url="https://i.pinimg.com/736x/46/53/95/465395a1f02981befd4f30ae8ef6c9c7.jpg"
    )
    await interaction.followup.send(embed=embed_success, ephemeral=True)
    try:
        uid = int(user)
    except ValueError:
        return
    guild = interaction.guild
    if guild is None:
        return
    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except Exception:
            return
    if member is None:
        return
    embed_dm = discord.Embed(
        title="Your device data has been reset",
        description=(
            f"Hello **{member.name}**, your device data has been reset:\n"
            "- HWID\n- CPU ID\n- RAM\n- MAC\n- DISK\n- IP\n\n"
            "You can now log in from a new device."
        ),
        color=discord.Color.blue(),
    )
    embed_dm.set_thumbnail(url=member.display_avatar.url)
    embed_dm.set_footer(text="Automatic license system")
    try:
        await member.send(embed=embed_dm)
    except discord.Forbidden:
        await interaction.followup.send(
            f"I could not DM the user `{member}` (private messages disabled).",
            ephemeral=True,
        )


@bot.tree.command(
    name="deletlist",
    description="Show all licenses stored in /licenses.",
    guild=MY_GUILD,
)
@app_commands.default_permissions(administrator=True)
@role_check()
async def deletlist(interaction: discord.Interaction):
    if interaction.channel_id != DELET_CHANNEL_ID:
        await interaction.response.send_message(
            "This command can only be used in the authorized channel.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)
    token = get_access_token()
    url = "https://api.dropboxapi.com/2/files/list_folder"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {"path": "/licenses"}
    r = requests.post(url, headers=headers, json=data)
    r.raise_for_status()
    files = r.json().get("entries", [])
    if not files:
        await interaction.followup.send(
            "There are no licenses registered.",
            ephemeral=True,
        )
        return
    LICENSE_LISTS[interaction.user.id] = [f["name"] for f in files]
    embeds: list[discord.Embed] = []
    count = 1
    for f in files:
        name = f["name"]
        content = download_file("/licenses/" + name)
        lines = dict(
            line.split("=", 1) for line in content.split("\n") if "=" in line
        )
        expires = lines.get("expires", "Unknown")
        username = lines.get("username", name)
        avatar = lines.get("avatar_url", "")
        embed = discord.Embed(
            title=f"{count}. License — {username}",
            description=f"**File:** `{name}`\n**Expires:** `{expires}`",
            color=discord.Color.green(),
        )
        if avatar:
            embed.set_thumbnail(url=avatar)
        else:
            embed.set_thumbnail(
                url="https://i.pinimg.com/736x/ff/3b/13/ff3b13ccc96a3dda7a6213118c0f6901.jpg"
            )
        embeds.append(embed)
        count += 1
    for e in embeds:
        await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(
    name="delet",
    description="Delete one license by its index from /deletlist.",
    guild=MY_GUILD,
)
@app_commands.default_permissions(administrator=True)
@role_check()
@app_commands.describe(numero="License index from /deletlist")
async def delet(interaction: discord.Interaction, numero: int):
    if interaction.channel_id != DELET_CHANNEL_ID:
        await interaction.response.send_message(
            "This command can only be used in the authorized channel.",
            ephemeral=True,
        )
        return
    if interaction.user.id not in LICENSE_LISTS:
        await interaction.response.send_message(
            "Use /deletlist first to see the licenses.",
            ephemeral=True,
        )
        return
    lista = LICENSE_LISTS[interaction.user.id]
    if numero < 1 or numero > len(lista):
        await interaction.response.send_message(
            "Invalid license index.",
            ephemeral=True,
        )
        return
    filename = lista[numero - 1]
    path = f"/licenses/{filename}"
    clean_name, _ = os.path.splitext(filename)
    try:
        delete_file(path)
    except Exception as e:
        await interaction.response.send_message(
            f"Error deleting license: {e}",
            ephemeral=True,
        )
        return
    embed = discord.Embed(
        title=f"License #{numero} deleted",
        description=f"**Deleted license:** `{clean_name}`",
        color=discord.Color.green(),
    )
    embed.set_thumbnail(
        url="https://i.pinimg.com/1200x/35/6a/72/356a723788cedb02731c0b4bb2ff7ffd.jpg"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_member_join(member: discord.Member):
    try:
        role = member.guild.get_role(WELCOME_ROLE_ID)
        if role is None:
            return
        await member.add_roles(role)
        welcome_image_url = (
            "https://i.pinimg.com/originals/68/00/66/680066599cf3ebb730df833c8871277c.gif"
        )
        embed = discord.Embed(
            description="Welcome to the server! Enjoy your experience.",
            color=discord.Color.green(),
        )
        embed.set_author(
            name=member.display_name, icon_url=member.display_avatar.url
        )
        embed.set_image(url=welcome_image_url)
        try:
            dm_channel = await member.create_dm()
            async for msg in dm_channel.history(limit=50):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
            await dm_channel.send(embed=embed)
            print(f"[WELCOME] DM enviado a {member}.")
        except Exception as e:
            print(f"[WELCOME] No pude enviar DM a {member}: {e}")
            if member.guild.system_channel:
                await member.guild.system_channel.send(embed=embed)
    except Exception as e:
        print(f"[WELCOME] Error en on_member_join: {e}")


def run_flask():
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    start_keepalive_thread()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


def run_discord():
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    run_discord()
