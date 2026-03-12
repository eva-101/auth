from flask import Flask, request, jsonify
import requests
import os
import time
from datetime import datetime
import ast
import threading
import json

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
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
            print(f"[KEEPALIVE] status={resp.status_code} resp={resp.text[:200]}")
        except Exception as e:
            print(f"[KEEPALIVE] error: {e}")
        time.sleep(KEEPALIVE_INTERVAL)


def start_keepalive_thread():
    t = threading.Thread(target=keepalive_bot, daemon=True)
    t.start()


def download_account(username: str) -> str:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/accounts/{username}.txt"}}',
    }
    r = requests.post(
        "https://content.dropboxapi.com/2/files/download",
        headers=headers,
    )
    r.raise_for_status()
    return r.text


def upload_account(username: str, content: str) -> bool:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/accounts/{username}.txt", "mode": "overwrite"}}',
        "Content-Type": "application/octet-stream",
    }
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers=headers,
        data=content.encode(),
    )
    r.raise_for_status()
    return True


def download_devices_registry() -> dict:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": '{"path": "/accounts/_devices.json"}',
    }
    try:
        r = requests.post(
            "https://content.dropboxapi.com/2/files/download",
            headers=headers,
        )
        r.raise_for_status()
        content = r.text
        data = json.loads(content)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def upload_devices_registry(registry: dict) -> None:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": '{"path": "/accounts/_devices.json", "mode": "overwrite"}',
        "Content-Type": "application/octet-stream",
    }
    content = json.dumps(registry, separators=(",", ":"), ensure_ascii=False)
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers=headers,
        data=content.encode("utf-8"),
    )
    r.raise_for_status()


@app.route("/create_account", methods=["POST"])
def create_account():
    data = request.json or {}

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    avatar_url = (data.get("avatar_url") or "").strip()
    hwid = (data.get("hwid") or "").strip()

    if not username or not password:
        return jsonify({"error": True, "status": "Username and password required"}), 400

    if not hwid:
        return jsonify({"error": True, "status": "HWID required"}), 400

    if len(username) < 3:
        return jsonify({"error": True, "status": "Username too short"}), 400
    if len(password) < 4:
        return jsonify({"error": True, "status": "Password too short"}), 400

    devices = download_devices_registry()
    if hwid in devices and devices[hwid] != username:
        return jsonify({"error": True, "status": "This device already has an account"}), 403

    try:
        _ = download_account(username)
        return jsonify({"error": True, "status": "Username already exists"}), 409
    except Exception:
        pass

    account_data = {
        "username": username,
        "password": password,
        "avatar_url": avatar_url,
        "created_at": datetime.now().isoformat(),
        "hwid": hwid,
    }

    content = "\n".join(f"{k}={v}" for k, v in account_data.items())
    try:
        upload_account(username, content)
    except Exception as e:
        return jsonify({"error": True, "status": f"Error saving account: {e}"}), 500

    devices[hwid] = username
    try:
        upload_devices_registry(devices)
    except Exception as e:
        print(f"[DEVICES] error updating registry: {e}")

    return jsonify({"error": False, "status": "Account created", "account": account_data}), 201


@app.route("/login_account", methods=["POST"])
def login_account():
    data = request.json or {}

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    hwid = (data.get("hwid") or "").strip()

    if not username or not password:
        return jsonify({"error": True, "status": "Username and password required"}), 400

    if not hwid:
        return jsonify({"error": True, "status": "HWID required"}), 400

    try:
        content = download_account(username)
    except Exception:
        return jsonify({"error": True, "status": "Account not found"}), 404

    acc = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        acc[k.strip()] = v.strip()

    if acc.get("password") != password:
        return jsonify({"error": True, "status": "Incorrect password"}), 403

    saved_hwid = acc.get("hwid", "")
    if saved_hwid and saved_hwid != hwid:
        return jsonify({"error": True, "status": "HWID mismatch"}), 403

    # Si la cuenta no tenía HWID aún, se lo seteamos ahora
    if not saved_hwid:
        acc["hwid"] = hwid
        new_content = "\n".join(f"{k}={v}" for k, v in acc.items())
        try:
            upload_account(username, new_content)
        except Exception:
            pass

    # Validar / actualizar registry de dispositivos
    devices = download_devices_registry()
    owner = devices.get(hwid)
    if owner and owner != username:
        return jsonify({"error": True, "status": "This device already has another account"}), 403

    devices[hwid] = username
    try:
        upload_devices_registry(devices)
    except Exception as e:
        print(f"[DEVICES] error updating registry: {e}")

    # === SOLO JUEGOS de /elementos ===
    try:
        files = list_files("/elementos")
        games = [f for f in files if f["name"].lower().endswith(".zip")]
    except Exception as e:
        games = []
        print(f"[GAMES] error listing games: {e}")

    return jsonify({
        "error": False,
        "status": "Login successful",
        "account": acc,
        "games": games    # solo juegos de /elementos
    }), 200




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


if __name__ == "__main__":
    import logging

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    start_keepalive_thread()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


