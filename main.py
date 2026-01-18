from flask import Flask, request, jsonify
import requests
import os
import time
import json
from datetime import datetime

app = Flask(__name__)

ACCESS_TOKEN = None
ACCESS_TOKEN_TIME = None
SERVER_START_TIME = time.time()

REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]
APP_KEY = os.environ["APP_KEY"]
APP_SECRET = os.environ["APP_SECRET"]

RATING_PATH = "/ratings/global.json"


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
            "client_secret": APP_SECRET
        }
    )
    r.raise_for_status()

    ACCESS_TOKEN = r.json()["access_token"]
    ACCESS_TOKEN_TIME = datetime.now()
    return ACCESS_TOKEN


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

    files = []

    for f in r.json().get("entries", []):
        if f.get(".tag") != "file":
            continue

        try:
            link_resp = requests.post(
                "https://api.dropboxapi.com/2/files/get_temporary_link",
                headers=headers,
                json={"path": f.get("path_lower")}
            )
            link_resp.raise_for_status()
            files.append({
                "name": f.get("name"),
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


def load_rating():
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "{RATING_PATH}"}}'
    }

    try:
        r = requests.post(
            "https://content.dropboxapi.com/2/files/download",
            headers=headers
        )
        r.raise_for_status()
        return json.loads(r.text)
    except:
        return {"likes": 0, "dislikes": 0, "votes": {}}


def save_rating(data):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "{RATING_PATH}", "mode": "overwrite"}}',
        "Content-Type": "application/octet-stream"
    }

    requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers=headers,
        data=json.dumps(data, indent=2).encode()
    )


@app.route("/validate", methods=["POST"])
def validate():
    data = request.json or {}

    if data.get("username") == "PING_KEEPALIVE":
        return jsonify({
            "error": False,
            "status": "SERVER_ALIVE",
            "server_time": datetime.now().isoformat(),
            "uptime": get_uptime(),
            "licenses_total": count_licenses(),
            "loader_files": count_loader_files()
        }), 200

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

        for k, v in [
            ("hwid", hwid),
            ("cpu_id", cpu_id),
            ("ram", ram),
            ("mac", mac),
            ("disk", disk),
            ("ip", ip)
        ]:
            if v and not lic.get(k):
                lic[k] = v
                updated = True

        if updated:
            upload_license(username, "\n".join(f"{k}={v}" for k, v in lic.items()))

        for k, v in [("hwid", hwid), ("cpu_id", cpu_id), ("mac", mac)]:
            if v and lic.get(k) and v != lic.get(k):
                return jsonify({"error": True, "status": f"{k.upper()} mismatch"}), 403

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


@app.route("/rate", methods=["POST"])
def rate():
    data = request.json or {}

    username = data.get("username")
    vote = data.get("vote")

    if vote not in ("like", "dislike"):
        return jsonify({"error": True, "status": "Invalid vote"}), 400

    try:
        download_license(username)
    except:
        return jsonify({"error": True, "status": "Invalid user"}), 403

    rating = load_rating()
    prev_vote = rating["votes"].get(username)

    if prev_vote == "like":
        rating["likes"] -= 1
    elif prev_vote == "dislike":
        rating["dislikes"] -= 1

    rating["votes"][username] = vote

    if vote == "like":
        rating["likes"] += 1
    else:
        rating["dislikes"] += 1

    save_rating(rating)

    return jsonify({
        "error": False,
        "likes": rating["likes"],
        "dislikes": rating["dislikes"],
        "your_vote": vote
    })


@app.route("/rating", methods=["GET"])
def get_rating():
    rating = load_rating()
    return jsonify({
        "likes": rating["likes"],
        "dislikes": rating["dislikes"]
    })


if __name__ == "__main__":
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
