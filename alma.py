#!/usr/bin/env python3
"""
AL.MA — Asistente Local con Memoria y Autonomía  v2.0 (CORREGIDO)
Backend Flask: multi-proveedor IA, memoria SQLite, cola offline, control de dispositivos
Ejecutar: python alma.py
Acceso local:  http://localhost:5000
Acceso móvil:  http://<TU_IP_LOCAL>:5000  (misma red WiFi)
"""

import os, sqlite3, requests, base64, json, webbrowser, subprocess
import platform, socket, time
from urllib.parse import quote_plus
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ═══ DIRECTORIO AUTOMÁTICO ═══════════════════════════════════════════
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

# ─── Detectar carpeta de datos dinámicamente ────────────────────────
def detectar_carpeta_datos() -> str:
    """Busca Dropbox/OneDrive del usuario; si no existe, usa carpeta junto al script."""
    usuario = os.path.expanduser("~")
    candidatos = [
        os.path.join(usuario, "Dropbox", "ALMA"),
        os.path.join(usuario, "OneDrive", "ALMA"),
    ]
    for c in candidatos:
        padre = os.path.dirname(c)
        if os.path.isdir(padre):
            os.makedirs(c, exist_ok=True)
            return c
    # Fallback: carpeta junto al script
    fb = os.path.join(SCRIPT_DIR, "ALMA_data")
    os.makedirs(fb, exist_ok=True)
    return fb

DATA_FOLDER = detectar_carpeta_datos()
DB_NAME     = os.path.join(DATA_FOLDER, "alma_memoria.db")
CONFIG_FILE = os.path.join(DATA_FOLDER, "alma_config.json")
HTML_FILE   = os.path.join(SCRIPT_DIR,  "index.html")
OLLAMA_URL  = "http://localhost:11434/api/chat"

# ═══════════════════════════════════════════════════════════════════
#  CONFIG JSON
# ═══════════════════════════════════════════════════════════════════
def cargar_config() -> dict:
    defaults = {
        "proveedor_ia":   "ollama",
        "modelo_local":   "llama3.2",
        "claude_api_key": "",
        "gemini_api_key": "",
        "openai_api_key": "",
        "puerto":         5000,
        "capturar_pantalla_auto": False
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            defaults.update(json.load(f))
    else:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(defaults, f, indent=2, ensure_ascii=False)
        print(f"[AL.MA] Config creada en: {CONFIG_FILE}")
    return defaults

def guardar_config_file(nuevos: dict):
    cfg = cargar_config()
    cfg.update(nuevos)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg

config = cargar_config()

# ═══════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════
def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def inicializar_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS conocimiento (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clave TEXT UNIQUE, contenido TEXT, fecha TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS historial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rol TEXT, mensaje TEXT, timestamp TEXT, sincronizado INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cola_offline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        peticion TEXT, respuesta TEXT, timestamp TEXT, subido INTEGER DEFAULT 0)''')
    # Parche: columna 'fecha' puede faltar en DB antiguas
    c.execute("PRAGMA table_info(conocimiento)")
    if "fecha" not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE conocimiento ADD COLUMN fecha TEXT")
    semilla = [
        ('emergencias_chile', 'Ambulancia: 131 | Bomberos: 132 | Carabineros: 133 | PDI: 134'),
        ('sistema_productividad', 'Metodo AL.MA: Bloques de 45 min. Revisar reportes en Dropbox.'),
    ]
    for k, v in semilla:
        c.execute("INSERT OR IGNORE INTO conocimiento (clave,contenido,fecha) VALUES (?,?,?)", (k, v, _now()))
    conn.commit()
    conn.close()
    print(f"[AL.MA] Base de datos lista: {DB_NAME}")

def guardar_memoria(clave, contenido):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT OR REPLACE INTO conocimiento (clave,contenido,fecha) VALUES (?,?,?)",
                 (clave.lower().strip()[:80], contenido, _now()))
    conn.commit()
    conn.close()

def buscar_memoria(termino):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT clave,contenido,fecha FROM conocimiento WHERE clave LIKE ? OR contenido LIKE ? ORDER BY fecha DESC LIMIT 6",
              (f"%{termino}%", f"%{termino}%"))
    rows = c.fetchall()
    conn.close()
    return rows

def listar_memoria(limite=60):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT clave,contenido,fecha FROM conocimiento ORDER BY fecha DESC LIMIT ?", (limite,))
    rows = c.fetchall()
    conn.close()
    return rows

def borrar_memoria(clave):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("DELETE FROM conocimiento WHERE clave=?", (clave,))
    conn.commit()
    conn.close()

def guardar_historial(rol, msg):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT INTO historial (rol,mensaje,timestamp) VALUES (?,?,?)", (rol, msg, _now()))
    conn.commit()
    conn.close()

def obtener_historial(limite=40):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT rol,mensaje,timestamp FROM historial ORDER BY id DESC LIMIT ?", (limite,))
    rows = list(reversed(c.fetchall()))
    conn.close()
    return rows

def procesar_cola_offline():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id,peticion,respuesta FROM cola_offline WHERE subido=0")
    pendientes = c.fetchall()
    for row in pendientes:
        guardar_memoria(row[1][:60].lower().replace(" ", "_"), row[2][:600])
        conn.execute("UPDATE cola_offline SET subido=1 WHERE id=?", (row[0],))
    conn.commit()
    conn.close()
    return len(pendientes)

# ═══════════════════════════════════════════════════════════════════
#  CONECTIVIDAD
# ═══════════════════════════════════════════════════════════════════
def hay_internet():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except:
        return False

def get_ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# ═══════════════════════════════════════════════════════════════════
#  BUSQUEDA WEB (DuckDuckGo scraping simple)
# ═══════════════════════════════════════════════════════════════════
def buscar_web(query: str) -> str:
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=6)
        r.raise_for_status()
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            resultados = []
            for res in soup.select('.result__snippet')[:4]:
                texto = res.get_text(strip=True)
                if texto:
                    resultados.append(texto)
            return "\n\n".join(resultados) if resultados else "Sin resultados disponibles."
        except ImportError:
            return f"Instala beautifulsoup4: pip install beautifulsoup4\nURL manual: https://duckduckgo.com/?q={quote_plus(query)}"
    except Exception as e:
        return f"Error al buscar: {e}"

# ═══════════════════════════════════════════════════════════════════
#  PROVEEDORES DE IA
# ═══════════════════════════════════════════════════════════════════
def chat_ollama(mensaje: str, historial_msgs: list, modelo: str) -> str:
    messages = [{"role": r["role"], "content": r["content"]} for r in historial_msgs]
    messages.append({"role": "user", "content": mensaje})
    payload = {"model": modelo, "messages": messages, "stream": False}
    r = requests.post(OLLAMA_URL, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["message"]["content"]

def chat_claude(mensaje: str, historial_msgs: list, api_key: str) -> str:
    if not api_key:
        return "Error: No hay API Key de Claude configurada. Ve a Configuracion y agrega tu clave."
    messages = [{"role": r["role"], "content": r["content"]} for r in historial_msgs]
    messages.append({"role": "user", "content": mensaje})
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": "Eres AL.MA, un asistente personal inteligente. Eres conciso, util y hablas en espanol.",
        "messages": messages
    }
    r = requests.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()["content"][0]["text"]

def chat_openai(mensaje: str, historial_msgs: list, api_key: str) -> str:
    if not api_key:
        return "Error: No hay API Key de OpenAI configurada."
    messages = [{"role": "system", "content": "Eres AL.MA, asistente personal. Responde en espanol."}]
    messages += [{"role": r["role"], "content": r["content"]} for r in historial_msgs]
    messages.append({"role": "user", "content": mensaje})
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "gpt-4o-mini", "messages": messages, "max_tokens": 1024}
    r = requests.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def chat_gemini(mensaje: str, historial_msgs: list, api_key: str) -> str:
    if not api_key:
        return "Error: No hay API Key de Gemini configurada."
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    partes = [{"text": "Eres AL.MA, asistente personal. Responde en espanol.\n\n"}]
    for m in historial_msgs:
        partes.append({"text": f"[{m['role']}]: {m['content']}\n"})
    partes.append({"text": f"[user]: {mensaje}"})
    payload = {"contents": [{"parts": partes}]}
    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def llamar_ia(mensaje: str, contexto_memoria: str = ""):
    """Llama al proveedor configurado. Retorna (respuesta, proveedor_usado)."""
    global config
    config = cargar_config()
    prov   = config.get("proveedor_ia", "ollama")
    modelo = config.get("modelo_local", "llama3.2")

    # Historial reciente (ultimas 20 entradas = 10 turnos)
    historial_raw = obtener_historial(20)
    historial_msgs = []
    for rol, msg, _ in historial_raw:
        role_api = "user" if rol == "user" else "assistant"
        historial_msgs.append({"role": role_api, "content": msg})

    # Inyectar contexto de memoria
    msg_final = mensaje
    if contexto_memoria:
        msg_final = f"[Contexto de memoria relevante]:\n{contexto_memoria}\n\n[Pregunta]: {mensaje}"

    try:
        if prov == "ollama":
            resp = chat_ollama(msg_final, historial_msgs, modelo)
        elif prov == "claude":
            resp = chat_claude(msg_final, historial_msgs, config.get("claude_api_key", ""))
        elif prov == "openai":
            resp = chat_openai(msg_final, historial_msgs, config.get("openai_api_key", ""))
        elif prov == "gemini":
            resp = chat_gemini(msg_final, historial_msgs, config.get("gemini_api_key", ""))
        else:
            resp = "Proveedor no reconocido. Revisa la configuracion."
        return resp, prov
    except Exception as e:
        return f"Error con {prov}: {e}", prov

# ═══════════════════════════════════════════════════════════════════
#  CONTROL DE PC
# ═══════════════════════════════════════════════════════════════════
def ejecutar_control(accion: str, parametro: str = "") -> str:
    sistema = platform.system()
    try:
        if accion == "abrir_url":
            webbrowser.open(parametro or "https://google.com")
            return f"Abriendo {parametro}"
        elif accion == "volumen_subir":
            if sistema == "Windows":
                subprocess.run(["nircmd", "changesysvolume", "5000"], check=False)
            elif sistema == "Darwin":
                subprocess.run(["osascript", "-e", "set volume output volume ((output volume of (get volume settings)) + 10)"])
            return "Volumen subido"
        elif accion == "volumen_bajar":
            if sistema == "Windows":
                subprocess.run(["nircmd", "changesysvolume", "-5000"], check=False)
            elif sistema == "Darwin":
                subprocess.run(["osascript", "-e", "set volume output volume ((output volume of (get volume settings)) - 10)"])
            return "Volumen bajado"
        elif accion == "abrir_app":
            if sistema == "Windows":
                subprocess.Popen(["start", parametro], shell=True)
            elif sistema == "Darwin":
                subprocess.Popen(["open", "-a", parametro])
            else:
                subprocess.Popen([parametro])
            return f"Abriendo {parametro}"
        elif accion == "bloquear_pantalla":
            if sistema == "Windows":
                subprocess.run(["rundll32", "user32.dll,LockWorkStation"])
            elif sistema == "Darwin":
                subprocess.run(["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession", "-suspend"])
            return "Pantalla bloqueada"
        else:
            return f"Accion no reconocida: {accion}"
    except Exception as e:
        return f"Error: {e}"

def tomar_screenshot() -> str:
    try:
        import pyautogui
        from PIL import Image
        from io import BytesIO
        img = pyautogui.screenshot()
        img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        return ""
    except Exception:
        return ""

# ═══════════════════════════════════════════════════════════════════
#  FLASK APP
# ═══════════════════════════════════════════════════════════════════
app = Flask(__name__, static_folder=SCRIPT_DIR)
CORS(app)

@app.route("/")
def index():
    return send_from_directory(SCRIPT_DIR, "index.html")

@app.route("/api/status")
def api_status():
    global config
    config = cargar_config()
    return jsonify({
        "online":    hay_internet(),
        "proveedor": config.get("proveedor_ia", "ollama"),
        "ip_local":  get_ip_local(),
        "version":   "2.0"
    })

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data    = request.json or {}
    mensaje = data.get("mensaje", "").strip()
    if not mensaje:
        return jsonify({"error": "Mensaje vacio"}), 400

    # Buscar memoria relevante
    contexto = ""
    for pal in mensaje.split()[:4]:
        if len(pal) > 3:
            rows = buscar_memoria(pal)
            if rows:
                contexto += "\n".join(f"- {r[0]}: {r[1]}" for r in rows[:2]) + "\n"

    sincronizados = procesar_cola_offline()
    respuesta, proveedor = llamar_ia(mensaje, contexto)

    guardar_historial("user", mensaje)
    guardar_historial("assistant", respuesta)

    return jsonify({
        "respuesta":     respuesta,
        "proveedor":     proveedor,
        "online":        hay_internet(),
        "memoria_usada": bool(contexto),
        "sincronizados": sincronizados,
        "timestamp":     _now()
    })

@app.route("/api/historial")
def api_historial():
    limite = int(request.args.get("limite", 40))
    rows = obtener_historial(limite)
    return jsonify([{"rol": r[0], "mensaje": r[1], "timestamp": r[2]} for r in rows])

@app.route("/api/memoria", methods=["GET"])
def api_memoria_listar():
    rows = listar_memoria(60)
    return jsonify([{"clave": r[0], "contenido": r[1], "fecha": r[2]} for r in rows])

@app.route("/api/memoria/buscar")
def api_memoria_buscar():
    q = request.args.get("q", "")
    rows = buscar_memoria(q)
    return jsonify([{"clave": r[0], "contenido": r[1], "fecha": r[2]} for r in rows])

@app.route("/api/memoria", methods=["POST"])
def api_memoria_guardar():
    data = request.json or {}
    clave = data.get("clave", "").strip()
    contenido = data.get("contenido", "").strip()
    if not clave or not contenido:
        return jsonify({"error": "Clave y contenido requeridos"}), 400
    guardar_memoria(clave, contenido)
    return jsonify({"ok": True})

@app.route("/api/memoria/<path:clave>", methods=["DELETE"])
def api_memoria_borrar(clave):
    borrar_memoria(clave)
    return jsonify({"ok": True})

@app.route("/api/buscar")
def api_buscar():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"resultado": "Consulta vacia"}), 400
    resultado = buscar_web(q)
    return jsonify({"resultado": resultado, "query": q})

@app.route("/api/control", methods=["POST"])
def api_control():
    data     = request.json or {}
    accion   = data.get("accion", "")
    param    = data.get("parametro", "")
    resultado = ejecutar_control(accion, param)
    return jsonify({"resultado": resultado})

@app.route("/api/screenshot")
def api_screenshot():
    img = tomar_screenshot()
    if img:
        return jsonify({"imagen": img})
    return jsonify({"error": "Instala: pip install pyautogui pillow"}), 500

@app.route("/api/config", methods=["GET"])
def api_config_get():
    cfg = cargar_config()
    safe = {k: ("***" if "api_key" in k and v else v) for k, v in cfg.items()}
    return jsonify(safe)

@app.route("/api/config", methods=["POST"])
def api_config_post():
    data = request.json or {}
    cfg = guardar_config_file(data)
    global config
    config = cfg
    return jsonify({"ok": True, "proveedor_ia": cfg.get("proveedor_ia")})

# ═══════════════════════════════════════════════════════════════════
#  ARRANQUE
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 55)
    print("  AL.MA  Asistente Local con Memoria y Autonomia")
    print("  v2.0 - Backend Flask CORREGIDO")
    print("=" * 55)
    inicializar_db()
    puerto    = config.get("puerto", 5000)
    ip_local  = get_ip_local()
    internet  = hay_internet()
    proveedor = config.get("proveedor_ia", "ollama")
    print(f"\n  Carpeta de datos : {DATA_FOLDER}")
    print(f"  Proveedor IA     : {proveedor.upper()}")
    print(f"  Internet         : {'SI' if internet else 'NO'}")
    print(f"\n  Acceso local     : http://localhost:{puerto}")
    print(f"  Acceso red WiFi  : http://{ip_local}:{puerto}")
    print("\n  Abriendo navegador automaticamente...")
    print("  (Cierra esta ventana para detener AL.MA)\n")
    time.sleep(1)
    webbrowser.open(f"http://localhost:{puerto}")
    app.run(host="0.0.0.0", port=puerto, debug=False)
