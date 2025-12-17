from flask import Flask, request, jsonify
import requests, os
from datetime import datetime

app = Flask(__name__)
ACCESS_TOKEN = None
ACCESS_TOKEN_TIME = None

REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]
APP_KEY = os.environ["APP_KEY"]
APP_SECRET = os.environ["APP_SECRET"]

def get_access_token():
    global ACCESS_TOKEN, ACCESS_TOKEN_TIME

    if ACCESS_TOKEN and (datetime.now() - ACCESS_TOKEN_TIME).seconds < 14400:
        return ACCESS_TOKEN

    r = requests.post("https://api.dropbox.com/oauth2/token", data={
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": APP_KEY,
        "client_secret": APP_SECRET
    })
    r.raise_for_status()

    ACCESS_TOKEN = r.json()["access_token"]
    ACCESS_TOKEN_TIME = datetime.now()
    return ACCESS_TOKEN


#def get_access_token():
#    url = "https://api.dropbox.com/oauth2/token"
 #   data = {
 #       "grant_type": "refresh_token",
 #       "refresh_token": REFRESH_TOKEN,
#        "client_id": APP_KEY,
#        "client_secret": APP_SECRET
 #   }
 #   r = requests.post(url, data=data)
 #   r.raise_for_status()
 #   return r.json()["access_token"]

def download_license(username):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt"}}'
    }
    r = requests.post("https://content.dropboxapi.com/2/files/download", headers=headers)
    r.raise_for_status()
    return r.text
    
def test_root():
    print("=== Probando list_files('/loader') ===")
    try:
        urls = list_files("/loader")
        if not urls:
            print("No files found in /loader")
        else:
            for u in urls:
                print(u)
    except Exception as e:
        print("Error al listar archivos:", e)


def upload_license(username, content):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "/licenses/{username}.txt", "mode": "overwrite"}}',
        "Content-Type": "application/octet-stream"
    }
    r = requests.post("https://content.dropboxapi.com/2/files/upload", headers=headers, data=content.encode())
    r.raise_for_status()
    return True

 
def list_files(folder_path="/loader"):
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # 1️⃣ Listar archivos
    r = requests.post(
        "https://api.dropboxapi.com/2/files/list_folder",
        headers=headers,
        json={
            "path": folder_path,
            "recursive": True,
            "include_deleted": False
        }
    )
    r.raise_for_status()

    entries = r.json().get("entries", [])
    print("DEBUG - entries:", entries)

    urls = []

    for f in entries:
        if f.get(".tag") != "file":
            continue

        path = f.get("path_lower")
        if not path:
            continue

        try:
            # 2️⃣ Obtener link temporal (NO sharing)
            link_resp = requests.post(
                "https://api.dropboxapi.com/2/files/get_temporary_link",
                headers=headers,
                json={"path": path}
            )
            link_resp.raise_for_status()

            url = link_resp.json().get("link")
            if url:
                urls.append(url)

        except Exception as e:
            print(f"No se pudo generar temp link para {f.get('name')}: {e}")

    return urls

 
@app.route("/validate", methods=["POST"])
def validate():

    data = request.json

    if data.get("username") == "PING_KEEPALIVE":
        return jsonify({
            "error": False,
            "status": "PING_OK"
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

    lic = dict(line.split("=", 1) for line in content.split("\n") if "=" in line)

    if lic.get("pass") and lic["pass"] != password:
        return jsonify({"error": True, "status": "Incorrect password"}), 403

    expire_date = datetime.fromisoformat(lic.get("expires", "2100-01-01T00:00:00"))
    if datetime.now() > expire_date:
        return jsonify({"error": True, "status": "License expired"}), 403

    is_global = lic.get("global","false").lower() == "true"

    if not is_global:
        updated = False
        for key, value in [("hwid", hwid), ("cpu_id", cpu_id), ("ram", ram),
                           ("mac", mac), ("disk", disk), ("ip", ip)]:
            if value and not lic.get(key):
                lic[key] = value
                updated = True
        if updated:
            upload_license(username, "\n".join([f"{k}={v}" for k,v in lic.items()]))

        for key, value in [("hwid", hwid), ("cpu_id", cpu_id), ("mac", mac)]:
            if value and lic.get(key) and value != lic.get(key):
                return jsonify({"error": True, "status": f"{key.upper()} mismatch"}), 403

    # -----------------------------
    # OBTENER URLS DE ARCHIVOS DEL LOADER
    # -----------------------------
    try:
        file_urls = list_files("/loader")

    except Exception as e:
        file_urls = []
        print("Error al obtener archivos de Dropbox:", e)

    response = {
        "error": False,
        "status": "Login successful",
        "license": lic,
        "files": file_urls  # <-- URLs de los archivos en loader
    }

    return jsonify(response), 200

if __name__ == "__main__":
    test_root()  # <-- ver qué carpetas ve Dropbox
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host="0.0.0.0", port=port)

