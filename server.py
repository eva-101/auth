from flask import Flask, request, jsonify
import requests
from datetime import datetime
import os

app = Flask(__name__)
 
REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]
APP_KEY = os.environ["APP_KEY"]
APP_SECRET = os.environ["APP_SECRET"]

def get_access_token():
    url = "https://api.dropbox.com/oauth2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": APP_KEY,
        "client_secret": APP_SECRET
    }
    r = requests.post(url, data=data)
    r.raise_for_status()
    return r.json()["access_token"]

def download_license(username):
    token = get_access_token()
    url = "https://content.dropboxapi.com/2/files/download"
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt"}}'
    }
    r = requests.post(url, headers=headers)
    r.raise_for_status()
    return r.text

def upload_license(username, content):
    token = get_access_token()
    url = "https://content.dropboxapi.com/2/files/upload"
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt", "mode": "overwrite"}}',
        "Content-Type": "application/octet-stream"
    }
    r = requests.post(url, headers=headers, data=content.encode())
    r.raise_for_status()
    return True

@app.route("/validate", methods=["POST"])
def validate():
    data = request.json
    username = data.get("username")
    password = data.get("password", "")
    hwid = data.get("hwid", "")

    try:
        content = download_license(username)
    except:
        return jsonify({"error": True, "status": "User not found"}), 404

    lic = {}
    for line in content.split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            lic[k.strip()] = v.strip()
 
    if lic.get("pass") and lic["pass"] != password:
        return jsonify({"error": True, "status": "Incorrect password"}), 403
 
    expire_date = datetime.fromisoformat(lic["expires"])
    if datetime.now() > expire_date:
        return jsonify({"error": True, "status": "License expired"}), 403
 
    is_global = lic.get("global", "").lower() == "true"

    # HWID HANDLING FINAL
    if not is_global:  
        if not lic.get("hwid") and hwid:
            lic["hwid"] = hwid
            upload_license(username, "\n".join([f"{k}={v}" for k, v in lic.items()]))

    return jsonify({
        "error": False,
        "status": "Login successful",
        "license": lic
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

