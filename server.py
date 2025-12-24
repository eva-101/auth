from flask import Flask, request, jsonify
import requests, os, time
from datetime import datetime

app = Flask(__name__)

# =========================
# VARIABLES GLOBALES
# =========================

ACCESS_TOKEN = None
ACCESS_TOKEN_TIME = None
SERVER_START_TIME = time.time()

REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]
APP_KEY = os.environ["APP_KEY"]
APP_SECRET = os.environ["APP_SECRET"]

# =========================
# HELPERS SISTEMA
# =========================

def get_uptime():
    s = int(time.time() - SERVER_START_TIME)
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    return f"{h}h {m}m {s}s"

# =========================
# DROPBOX AUTH
# =========================

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
            "client_secret": APP_SECRET
        }
    )
    r.raise_for_status()

    ACCESS_TOKEN = r.json()["access_token"]
    ACCESS_TOKEN_TIME = datetime.now()
    return ACCESS_TOKEN

# =========================
# DROPBOX FUNCIONES
# =========================

def download_license(username):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt"}}'
    }
    
    r = requests.post(
        "https://content.dropboxapi.com/2/files/download",
        headers=headers
    )
    r.raise_for_status()
    return r.text

def upload_license(username, content):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt", "mode": "overwrite"}}',
        "Content-Type": "application/octet-stream"
    }
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers=headers,
        data=content.encode()
    )
    r.raise_for_status()
    return True

def list_files(folder_path):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    r = requests.post(
        "https://api.dropboxapi.com/2/files/list_folder",
        headers=headers,
        json={
            "path": folder_path,
            "recursive": False,
            "include_deleted": False
        }
    )
    r.raise_for_status()

    entries = r.json().get("entries", [])
    files = []

    for f in entries:
        if f.get(".tag") != "file":
            continue

        path = f.get("path_lower")
        name = f.get("name")

        try:
            link_resp = requests.post(
                "https://api.dropboxapi.com/2/files/get_temporary_link",
                headers=headers,
                json={"path": path}
            )
            link_resp.raise_for_status()
            files.append({
                "name": name,
                "url": link_resp.json().get("link")
            })
        except:
            pass

    return files

def count_licenses():
    try:
        return len(list_files("/licenses"))
    except:
        return 0

def count_loader_files():
    try:
        return len(list_files("/loader"))
    except:
        return 0

# =========================
# ENDPOINT PRINCIPAL
# =========================

@app.route("/validate", methods=["POST"])
def validate():

    data = request.json or {}

    # =========================
    # KEEP ALIVE
    # =========================
    if data.get("username") == "PING_KEEPALIVE":
        return jsonify({
            "error": False,
            "status": "SERVER_ALIVE",
            "server_time": datetime.now().isoformat(),
            "uptime": get_uptime(),
            "licenses_total": count_licenses(),
            "loader_files": count_loader_files()
        }), 200
 

    # =========================
    # LOGIN NORMAL
    # =========================

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
    except:
        return jsonify({"error": True, "status": "User not found"}), 404

    lic = dict(
        line.split("=", 1)
        for line in content.split("\n")
        if "=" in line
    )

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
        for key, value in [
            ("hwid", hwid),
            ("cpu_id", cpu_id),
            ("ram", ram),
            ("mac", mac),
            ("disk", disk),
            ("ip", ip)
        ]:
            if value and not lic.get(key):
                lic[key] = value
                updated = True

        if updated:
            upload_license(
                username,
                "\n".join(f"{k}={v}" for k, v in lic.items())
            )

        for key, value in [
            ("hwid", hwid),
            ("cpu_id", cpu_id),
            ("mac", mac)
        ]:
            if value and lic.get(key) and value != lic.get(key):
                return jsonify({
                    "error": True,
                    "status": f"{key.upper()} mismatch"
                }), 403

    try:
        loader_files = list_files("/loader")
    except:
        loader_files = []

    return jsonify({
        "error": False,
        "status": "Login successful",
        "license": lic,
        "files": loader_files
    }), 200

# =========================
# MAIN
# =========================


if __name__ == "__main__":
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)




