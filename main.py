from flask import Flask, request, jsonify
import requests
import os
import time
from datetime import datetime, timedelta
import ast
import threading
import json

app = Flask(__name__)

access_token = None
access_token_time = None
server_start_time = time.time()

refresh_token = os.environ["REFRESH_TOKEN"]
app_key = os.environ["APP_KEY"]
app_secret = os.environ["APP_SECRET"]

self_base_url = os.environ.get("SELF_BASE_URL", "https://auth-clco.onrender.com")

keepalive_interval = int(os.environ.get("KEEPALIVE_INTERVAL", 60))
keepalive_running = True

username_registry_path = os.environ.get(
    "USERNAME_REGISTRY_PATH", "/accounts/_usernames.json"
)


# ============================
# UTILS GENERALES
# ============================

def get_uptime():
    segundos = int(time.time() - server_start_time)
    horas = segundos // 3600
    minutos = (segundos % 3600) // 60
    segundos %= 60
    return f"{horas}h {minutos}m {segundos}s"


def get_access_token():
    global access_token, access_token_time

    if access_token and (datetime.now() - access_token_time).seconds < 14400:
        return access_token

    r = requests.post(
        "https://api.dropbox.com/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": app_key,
            "client_secret": app_secret,
        },
    )
    r.raise_for_status()

    access_token = r.json()["access_token"]
    access_token_time = datetime.now()
    return access_token


# ============================
# DROPBOX HELPERS (LICENSES / ACCOUNTS)
# ============================

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


def rename_account_file(old_username: str, new_username: str) -> None:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {
        "from_path": f"/accounts/{old_username}.txt",
        "to_path": f"/accounts/{new_username}.txt",
        "autorename": False,
        "allow_shared_folder": False,
        "allow_ownership_transfer": False,
    }
    r = requests.post(
        "https://api.dropboxapi.com/2/files/move_v2",
        headers=headers,
        json=data,
    )
    r.raise_for_status()


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


def download_username_registry() -> dict:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "{username_registry_path}"}}',
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


def upload_username_registry(registry: dict) -> None:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Dropbox-API-Arg": f'{{"path": "{username_registry_path}", "mode": "overwrite"}}',
        "Content-Type": "application/octet-stream",
    }
    content = json.dumps(registry, separators=(",", ":"), ensure_ascii=False)
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers=headers,
        data=content.encode("utf-8"),
    )
    r.raise_for_status()


# ============================
# KEEPALIVE
# ============================

def keepalive_bot():
    global keepalive_running
    url = f"{self_base_url}/validate"

    while keepalive_running:
        try:
            payload = {
                "username": "PING_KEEPALIVE",
                "password": "",
            }
            headers = {"Content-Type": "application/json"}
            requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        except Exception:
            pass
        time.sleep(keepalive_interval)


def start_keepalive_thread():
    t = threading.Thread(target=keepalive_bot, daemon=True)
    t.start()


# ============================
# HELPERS SESIONES (COMUNES)
# ============================

def parse_text_with_sessions(text: str) -> dict:
    data = {}
    roles_dict = {}
    sessions_json = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("roles="):
            try:
                roles_dict = ast.literal_eval(line.split("=", 1)[1])
            except Exception:
                roles_dict = {}
        elif line.lower().startswith("sessions_json="):
            raw = line.split("=", 1)[1].strip()
            try:
                sessions_json = json.loads(raw)
            except Exception:
                sessions_json = {}
        elif "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()

    data["roles"] = roles_dict
    data["sessions_json"] = sessions_json
    return data


def dict_to_text_with_sessions(d: dict) -> str:
    roles = d.get("roles", {})
    sessions_json = d.get("sessions_json", {})

    lines = []
    for k, v in d.items():
        if k in ("roles", "sessions_json"):
            continue
        lines.append(f"{k}={v}")

    lines.append(f"roles={roles}")
    lines.append(
        f"sessions_json={json.dumps(sessions_json, separators=(',', ':'))}"
    )
    return "\n".join(lines)


def parse_sessions(d: dict) -> dict:
    sessions = d.get("sessions_json")
    if isinstance(sessions, dict):
        return sessions
    return {}


def add_session(d: dict, game_name: str, start_iso: str, end_iso: str, seconds: int):
    sessions = parse_sessions(d)
    g = sessions.get(game_name, {
        "total_seconds": 0,
        "total_sessions": 0,
        "last_start": "",
        "last_end": "",
    })

    g["total_seconds"] = int(g.get("total_seconds", 0)) + int(seconds)
    g["total_sessions"] = int(g.get("total_sessions", 0)) + 1
    g["last_start"] = start_iso
    g["last_end"] = end_iso

    sessions[game_name] = g
    d["sessions_json"] = sessions



# ============================
# RUTAS ACCOUNTS
# ============================

@app.route("/update_account", methods=["POST"])
def update_account():
    data = request.json or {}

    current_username = (data.get("current_username") or "").strip()
    new_username = (data.get("new_username") or "").strip()
    new_password = (data.get("new_password") or "").strip()
    new_avatar_url = (data.get("new_avatar_url") or "").strip()

    if not current_username:
        return jsonify(
            {
                "error": True,
                "code": "MISSING_FIELDS",
                "status": "El usuario actual es obligatorio.",
            }
        ), 400

    try:
        content = download_account(current_username)
    except Exception:
        return jsonify(
            {
                "error": True,
                "code": "ACCOUNT_NOT_FOUND",
                "status": "La cuenta no existe.",
            }
        ), 404

    acc = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        acc[k.strip()] = v.strip()

    ahora = datetime.now()

    def parse_dt(valor, default=None):
        if not valor:
            return default
        try:
            return datetime.fromisoformat(valor)
        except Exception:
            return default

    last_username_change_at = parse_dt(acc.get("last_username_change_at"), ahora)
    last_avatar_change_at = parse_dt(acc.get("last_avatar_change_at"), ahora)

    min_delta = timedelta(days=7)

    if "created_at" not in acc:
        acc["created_at"] = ahora.isoformat()
    if not last_username_change_at:
        last_username_change_at = ahora
    if not last_avatar_change_at:
        last_avatar_change_at = ahora

    old_username = acc.get("username", current_username)

    if new_username and new_username != old_username:
        if ahora - last_username_change_at < min_delta:
            restante = min_delta - (ahora - last_username_change_at)
            return jsonify(
                {
                    "error": True,
                    "code": "USERNAME_CHANGE_COOLDOWN",
                    "status": "No puedes cambiar el usuario todavía.",
                    "seconds_remaining": int(restante.total_seconds()),
                }
            ), 403

        registry = download_username_registry()
        if new_username in registry:
            return jsonify(
                {
                    "error": True,
                    "code": "USERNAME_TAKEN",
                    "status": "Este usuario ya está en uso.",
                }
            ), 409

        acc["username"] = new_username
        acc["last_username_change_at"] = ahora.isoformat()

        created = registry.get(old_username, {}).get("created_at", acc["created_at"])
        registry.pop(old_username, None)
        registry[new_username] = {"created_at": created}
        upload_username_registry(registry)

        try:
            rename_account_file(current_username, new_username)
        except Exception as e:
            return jsonify(
                {
                    "error": True,
                    "code": "ACCOUNT_RENAME_ERROR",
                    "status": f"No se pudo renombrar el archivo de cuenta: {e}",
                }
            ), 500

        current_username = new_username

    if new_password:
        if len(new_password) < 4:
            return jsonify(
                {
                    "error": True,
                    "code": "PASSWORD_TOO_SHORT",
                    "status": "La contraseña debe tener al menos 4 caracteres.",
                }
            ), 400
        acc["password"] = new_password

    if new_avatar_url and new_avatar_url != acc.get("avatar_url"):
        if ahora - last_avatar_change_at < min_delta:
            restante = min_delta - (ahora - last_avatar_change_at)
            return jsonify(
                {
                    "error": True,
                    "code": "AVATAR_CHANGE_COOLDOWN",
                    "status": "No puedes cambiar el avatar todavía.",
                    "seconds_remaining": int(restante.total_seconds()),
                }
            ), 403
        acc["avatar_url"] = new_avatar_url
        acc["last_avatar_change_at"] = ahora.isoformat()

    contenido = "\n".join(f"{k}={v}" for k, v in acc.items())
    upload_account(current_username, contenido)

    return jsonify(
        {
            "error": False,
            "status": "Cuenta actualizada.",
            "account": acc,
        }
    ), 200


@app.route("/create_account", methods=["POST"])
def create_account():
    data = request.json or {}

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    avatar_url = (data.get("avatar_url") or "").strip()

    if not username or not password:
        return jsonify(
            {
                "error": True,
                "code": "MISSING_FIELDS",
                "status": "Usuario y contraseña son obligatorios.",
            }
        ), 400

    if len(username) < 3:
        return jsonify(
            {
                "error": True,
                "code": "USERNAME_TOO_SHORT",
                "status": "El usuario debe tener al menos 3 caracteres.",
            }
        ), 400

    if len(password) < 4:
        return jsonify(
            {
                "error": True,
                "code": "PASSWORD_TOO_SHORT",
                "status": "La contraseña debe tener al menos 4 caracteres.",
            }
        ), 400

    registry = download_username_registry()
    if username in registry:
        return jsonify(
            {
                "error": True,
                "code": "USERNAME_TAKEN",
                "status": "Este usuario ya está en uso.",
            }
        ), 409

    try:
        _ = download_account(username)
        return jsonify(
            {
                "error": True,
                "code": "USERNAME_TAKEN",
                "status": "Este usuario ya está en uso.",
            }
        ), 409
    except Exception:
        pass

    ahora = datetime.now().isoformat()
    account_data = {
        "username": username,
        "password": password,
        "avatar_url": avatar_url,
        "created_at": ahora,
        "last_username_change_at": ahora,
        "last_avatar_change_at": ahora,
    }

    contenido = "\n".join(f"{k}={v}" for k, v in account_data.items())
    try:
        upload_account(username, contenido)
    except Exception as e:
        return jsonify(
            {
                "error": True,
                "code": "ACCOUNT_SAVE_ERROR",
                "status": f"Error al guardar la cuenta: {e}",
            }
        ), 500

    registry[username] = {"created_at": account_data["created_at"]}
    try:
        upload_username_registry(registry)
    except Exception:
        pass

    return jsonify(
        {
            "error": False,
            "status": "Cuenta creada correctamente.",
            "account": account_data,
        }
    ), 201


@app.route("/login_account", methods=["POST"])
def login_account():
    data = request.json or {}

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify(
            {
                "error": True,
                "code": "MISSING_FIELDS",
                "status": "Usuario y contraseña son obligatorios.",
            }
        ), 400

    try:
        content = download_account(username)
    except Exception:
        return jsonify(
            {
                "error": True,
                "code": "ACCOUNT_NOT_FOUND",
                "status": "La cuenta no existe.",
            }
        ), 404

    acc = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        acc[k.strip()] = v.strip()

    if acc.get("password") != password:
        return jsonify(
            {
                "error": True,
                "code": "INVALID_PASSWORD",
                "status": "La contraseña es incorrecta.",
            }
        ), 403

    try:
        files = list_files("/elementos")
        games = [f for f in files if f["name"].lower().endswith(".zip")]
    except Exception:
        games = []

    return jsonify(
        {
            "error": False,
            "status": "Inicio de sesión correcto.",
            "account": acc,
            "games": games,
        }
    ), 200


@app.route("/games", methods=["GET"])
def games():
    try:
        files = list_files("/elementos")
    except Exception as e:
        return jsonify({"error": True, "status": str(e), "files": []}), 500

    zip_files = [f for f in files if f["name"].lower().endswith(".zip")]

    return jsonify({"error": False, "status": "ok", "files": zip_files}), 200


# ============================
# VALIDATE (LICENCIAS)
# ============================

@app.route("/validate", methods=["POST"])
def validate():
    data = request.json or {}

    if data.get("username") == "PING_KEEPALIVE":
        return jsonify(
            {
                "error": False,
                "status": "servidor_activo",
                "server_time": datetime.now().isoformat(),
                "uptime": get_uptime(),
                "licenses_total": count_licenses(),
                "loader_files": count_loader_files(),
            }
        ), 200

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
        return jsonify({"error": True, "status": "Usuario no encontrado."}), 404

    lic = parse_text_with_sessions(content)

    if lic.get("pass") and lic["pass"] != password:
        return jsonify({"error": True, "status": "Contraseña incorrecta."}), 403

    expire_date = datetime.fromisoformat(lic.get("expires", "2100-01-01T00:00:00"))
    if datetime.now() > expire_date:
        return jsonify({"error": True, "status": "Licencia expirada."}), 403

    is_global = lic.get("global", "false").lower() == "true"

    if not is_global:
        actualizado = False

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
                actualizado = True

        if actualizado:
            upload_license(username, dict_to_text_with_sessions(lic))

        for k, v in [("hwid", hwid), ("cpu_id", cpu_id), ("mac", mac)]:
            if v and lic.get(k) and v != lic.get(k):
                return jsonify(
                    {"error": True, "status": f"{k.upper()} no coincide."}
                ), 403

    try:
        loader_files = list_files("/loader")
    except Exception:
        loader_files = []

    try:
        game_files = list_files("/elementos")
    except Exception:
        game_files = []

    return jsonify(
        {
            "error": False,
            "status": "Inicio de sesión correcto.",
            "license": lic,
            "files": loader_files,
            "games": game_files,
        }
    ), 200


# ============================
# TRACKING SESIONES - LICENSE
# ============================

@app.route("/start_session_license", methods=["POST"])
def start_session_license():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    game_name = (data.get("game_name") or "").strip()

    if not username or not game_name:
        return jsonify(
            {
                "error": True,
                "status": "username y game_name son obligatorios.",
            }
        ), 400

    try:
        content = download_license(username)
    except Exception:
        return jsonify({"error": True, "status": "Usuario no encontrado."}), 404

    _ = parse_text_with_sessions(content)

    start_time = datetime.utcnow().isoformat()
    return jsonify(
        {
            "error": False,
            "status": "Sesion iniciada (license).",
            "start_time": start_time,
        }
    ), 200


@app.route("/end_session_license", methods=["POST"])
def end_session_license():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    game_name = (data.get("game_name") or "").strip()
    start_time = (data.get("start_time") or "").strip()

    if not username or not game_name or not start_time:
        return jsonify(
            {
                "error": True,
                "status": "username, game_name y start_time son obligatorios.",
            }
        ), 400

    try:
        content = download_license(username)
    except Exception:
        return jsonify({"error": True, "status": "Usuario no encontrado."}), 404

    d = parse_text_with_sessions(content)

    try:
        dt_start = datetime.fromisoformat(start_time)
    except Exception:
        return jsonify({"error": True, "status": "start_time invalido."}), 400

    dt_end = datetime.utcnow()
    seconds = int((dt_end - dt_start).total_seconds())

    add_session(
        d,
        game_name=game_name,
        start_iso=dt_start.isoformat(),
        end_iso=dt_end.isoformat(),
        seconds=seconds,
    )

    upload_license(username, dict_to_text_with_sessions(d))

    return jsonify(
        {
            "error": False,
            "status": "Sesion registrada (license).",
            "seconds": seconds,
            "minutes": round(seconds / 60, 2),
            "hours": round(seconds / 3600, 2),
        }
    ), 200


# ============================
# TRACKING SESIONES - ACCOUNT
# ============================

@app.route("/start_session_account", methods=["POST"])
def start_session_account():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    game_name = (data.get("game_name") or "").strip()

    if not username or not game_name:
        return jsonify(
            {
                "error": True,
                "status": "username y game_name son obligatorios.",
            }
        ), 400

    try:
        content = download_account(username)
    except Exception:
        return jsonify({"error": True, "status": "Cuenta no encontrada."}), 404

    _ = parse_text_with_sessions(content)

    start_time = datetime.utcnow().isoformat()
    return jsonify(
        {
            "error": False,
            "status": "Sesion iniciada (account).",
            "start_time": start_time,
        }
    ), 200


@app.route("/end_session_account", methods=["POST"])
def end_session_account():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    game_name = (data.get("game_name") or "").strip()
    start_time = (data.get("start_time") or "").strip()

    if not username or not game_name or not start_time:
        return jsonify(
            {
                "error": True,
                "status": "username, game_name y start_time son obligatorios.",
            }
        ), 400

    try:
        content = download_account(username)
    except Exception:
        return jsonify({"error": True, "status": "Cuenta no encontrada."}), 404

    d = parse_text_with_sessions(content)

    try:
        dt_start = datetime.fromisoformat(start_time)
    except Exception:
        return jsonify({"error": True, "status": "start_time invalido."}), 400

    dt_end = datetime.utcnow()
    seconds = int((dt_end - dt_start).total_seconds())

    add_session(
        d,
        game_name=game_name,
        start_iso=dt_start.isoformat(),
        end_iso=dt_end.isoformat(),
        seconds=seconds,
    )

    upload_account(username, dict_to_text_with_sessions(d))

    return jsonify(
        {
            "error": False,
            "status": "Sesion registrada (account).",
            "seconds": seconds,
            "minutes": round(seconds / 60, 2),
            "hours": round(seconds / 3600, 2),
        }
    ), 200


# ============================
# GET SESSIONS (LICENSE / ACCOUNT)
# ============================

@app.route("/sessions_license/<username>", methods=["GET"])
def get_sessions_license(username):
    username = (username or "").strip()
    if not username:
        return jsonify({"error": True, "status": "username requerido."}), 400

    try:
        content = download_license(username)
    except Exception:
        return jsonify({"error": True, "status": "Usuario no encontrado."}), 404

    d = parse_text_with_sessions(content)
    sessions = parse_sessions(d)
    return jsonify({"error": False, "status": "ok", "sessions": sessions}), 200


@app.route("/sessions_account/<username>", methods=["GET"])
def get_sessions_account(username):
    username = (username or "").strip()
    if not username:
        return jsonify({"error": True, "status": "username requerido."}), 400

    try:
        content = download_account(username)
    except Exception:
        return jsonify({"error": True, "status": "Cuenta no encontrada."}), 404

    d = parse_text_with_sessions(content)
    sessions = parse_sessions(d)
    return jsonify({"error": False, "status": "ok", "sessions": sessions}), 200


# ============================
# MAIN
# ============================

if __name__ == "__main__":
    import logging

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    start_keepalive_thread()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
