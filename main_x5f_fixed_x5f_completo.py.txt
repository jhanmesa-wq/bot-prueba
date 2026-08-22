import os
import re
import base64
import sqlite3
import tempfile
import time
import logging
import requests
from functools import wraps
from threading import Thread
from flask import Flask, request, jsonify

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("specter_peru")

KEEP_ALIVE_URL = os.getenv("KEEP_ALIVE_URL", "").strip()
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()
KEEP_ALIVE_INTERVAL = max(60, int(os.getenv("KEEP_ALIVE_INTERVAL", "300")))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CODART_TOKEN = os.getenv("CODART_TOKEN", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Falta la variable de entorno BOT_TOKEN en Render.")
if not CODART_TOKEN:
    raise RuntimeError("Falta la variable de entorno CODART_TOKEN en Render.")
API_BASE = "https://api-codart.cgrt.org/api/v1/consultas/fd"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CODART_TOKEN}"
}

# ================== CONFIGURACIÓN ==================
DB_PATH = "bot.db"
CREDITOS_INICIALES = 10
COSTOS = {
    "dni": 5,
    "agv": 20,
    "facial": 60
}
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

# ================== BASE DE DATOS — AGREGADA COLUMNA CELULAR ==================
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            creditos INTEGER NOT NULL,
            celular TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_creditos(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    cur.execute("SELECT creditos FROM usuarios WHERE user_id =?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO usuarios (user_id, creditos, celular) VALUES (?,?,?)",
                    (user_id, CREDITOS_INICIALES, ""))
        conn.commit()
        conn.close()
        return CREDITOS_INICIALES
    conn.close()
    return row[0]

def set_creditos(user_id: int, nuevo_saldo: int):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET creditos =? WHERE user_id =?", (nuevo_saldo, user_id))
    conn.commit()
    conn.close()

def descontar_creditos(user_id: int, cantidad: int) -> int:
    saldo_actual = get_creditos(user_id)
    nuevo_saldo = saldo_actual - cantidad
    set_creditos(user_id, nuevo_saldo)
    return nuevo_saldo

def agregar_creditos(user_id: int, cantidad: int) -> int:
    saldo_actual = get_creditos(user_id)
    nuevo_saldo = saldo_actual + cantidad
    set_creditos(user_id, nuevo_saldo)
    return nuevo_saldo

def get_celular(user_id: int) -> str:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    cur.execute("SELECT celular FROM usuarios WHERE user_id =?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else "No registrado"

def set_celular(user_id: int, celular: str):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET celular =? WHERE user_id =?", (celular, user_id))
    conn.commit()
    conn.close()

def buscar_por_celular(celular: str):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM usuarios WHERE celular =?", (celular,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

# Decorator para manejar creditos automaticamente
def con_creditos(costo: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            saldo = get_creditos(user_id)

            if saldo < costo:
                await update.message.reply_text(f"❌ Créditos insuficientes. Te quedan: {saldo} CRD")
                return

            nuevo_saldo = descontar_creditos(user_id, costo)
            context.user_data['costo_actual'] = costo
            context.user_data['saldo_actual'] = nuevo_saldo

            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

def footer_creditos(context: ContextTypes.DEFAULT_TYPE) -> str:
    costo = context.user_data.get('costo_actual', 0)
    saldo = context.user_data.get('saldo_actual', 0)
    return f"\n\n━━━━━━━━━━━━━━━━━━━━\n💰 Costo: {costo} CRD | Saldo: {saldo} CRD"

def clean(valor):
    if valor is None:
        return "-"
    return str(valor)

# ================== FLASK — RECARGA AUTOMÁTICA INTEGRADA ==================
app = Flask(__name__)
flask_app = app  # compatibilidad

@flask_app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "SPECTER PERÚ",
        "bot": "telegram"
    }), 200

@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "SPECTER PERÚ"
    }), 200

@flask_app.route("/keep-alive", methods=["GET"])
def keep_alive():
    return jsonify({"status": "alive"}), 200

def enviar_telegram(chat_id: int, texto: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": texto})
    except Exception as e:
        print(f"Error enviando mensaje: {e}")

@flask_app.route("/webhook-pago", methods=["POST"])
def webhook_pago():
    datos = request.get_json()
    if not datos:
        return jsonify({"error": "Sin datos"}), 400

    celular = str(datos.get("celular", "")).strip()
    monto = datos.get("monto", 0)

    if not celular or not monto:
        return jsonify({"error": "Faltan datos: celular y monto"}), 400

    user_id = buscar_por_celular(celular)
    if not user_id:
        print(f"⚠️ Pago recibido — Celular {celular} NO REGISTRADO")
        return jsonify({"status": "usuario_no_registrado"}), 200

    creditos = int(float(monto))
    saldo_nuevo = agregar_creditos(user_id, creditos)

    enviar_telegram(
        user_id,
        f"✅ Pago detectado!\n\n"
        f"💰 Recibido: S/ {monto}\n"
        f"🎁 +{creditos} CRD agregados\n"
        f"💳 Saldo actual: {saldo_nuevo} CRD"
    )

    print(f"✅ Pago procesado — Usuario {user_id} | +{creditos} CRD")
    return jsonify({
        "status": "ok",
        "user_id": user_id,
        "creditos": creditos,
        "saldo_actual": saldo_nuevo
    }), 200

def iniciar_flask(): # DEPRECADO - Ya no se usa en Render, se usa gunicorn
    return
    port = int(os.getenv("PORT", "10000"))
    logger.info("🌐 Flask escuchando en 0.0.0.0:%s", port)
    flask_app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False
    )

def iniciar_keep_alive():
    """
    Mantiene una petición periódica al propio servicio cuando Render
    proporciona RENDER_EXTERNAL_URL o cuando se configura KEEP_ALIVE_URL.
    No reemplaza las health checks de Render ni garantiza que un plan
    gratuito permanezca encendido si la plataforma decide suspenderlo.
    """
    url = KEEP_ALIVE_URL or RENDER_EXTERNAL_URL
    if not url:
        logger.info("ℹ️ Keep-alive externo desactivado: configura KEEP_ALIVE_URL si lo deseas.")
        return

    url = url.rstrip("/") + "/keep-alive"
    logger.info("💓 Keep-alive activo: %s cada %s segundos", url, KEEP_ALIVE_INTERVAL)

    while True:
        try:
            response = requests.get(url, timeout=15)
            logger.info("💓 Keep-alive HTTP %s", response.status_code)
        except Exception as exc:
            logger.warning("⚠️ Keep-alive falló: %s", exc)

        time.sleep(KEEP_ALIVE_INTERVAL)

# ================== FUNCIONES AUXILIARES ==================
def validar_dni(dni: str) -> bool:
    return bool(re.match(r"^\d{8}$", dni))

def decodificar_imagen(data_uri: str):
    try:
        if "," in data_uri:
            _, b64 = data_uri.split(",", 1)
        else:
            b64 = data_uri
        return base64.b64decode(b64)
    except:
        return None

# ================== COMANDOS NUEVOS — /me /staff /micelular ==================
async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    creditos = get_creditos(user_id)
    celular = get_celular(user_id)
    nombre = user.full_name
    username = f"@{user.username}" if user.username else "Sin usuario"

    texto = f"""
👤 INFORMACIÓN DE USUARIO
━━━━━━━━━━━━━━━━━━━━━━━━
🆔 ID: {user_id}
👤 Nombre: {nombre}
🔖 Usuario: {username}
📱 Celular: {celular}
💳 Créditos: {creditos} CRD
━━━━━━━━━━━━━━━━━━━━━━━━
📚 Comandos: /cmds /saldo /buy
"""
    await update.message.reply_text(texto)

async def micelular_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text(
            "📱 Uso: /micelular 987654321\n"
            "Registra tu número para que los pagos por Yape\n"
            "se sumen automáticamente a tus créditos ⚡"
        )
    celular = context.args[0].strip()
    if not re.fullmatch(r"9\d{8}", celular):
        return await update.message.reply_text(
            "❌ Número inválido. Debe empezar con 9 y tener 9 dígitos."
        )
    set_celular(user_id, celular)
    await update.message.reply_text(
        f"✅ Celular {celular} registrado!\n\n"
        "Ahora cuando pagues por Yape a este número,\n"
        "los créditos se sumarán automáticamente ⚡"
    )

async def saldo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    creditos = get_creditos(user_id)
    celular = get_celular(user_id)
    await update.message.reply_text(
        f"💳 Tu saldo: {creditos} CRD\n"
        f"📱 Celular: {celular}"
    )

async def staff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mensaje_usuario = " ".join(context.args) if context.args else ""

    if user_id not in ADMIN_IDS:
        return await update.message.reply_text("❌ No tienes permisos de STAFF.")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(creditos) FROM usuarios")
    total_usuarios, total_creditos = cur.fetchone()
    conn.close()

    texto = f"""
👑 PANEL DE STAFF
━━━━━━━━━━━━━━━━━━━━━━━━
👥 Usuarios registrados: {total_usuarios}
💳 Créditos totales: {total_creditos or 0} CRD
━━━━━━━━━━━━━━━━━━━━━━━━
📝 Mensaje: {mensaje_usuario or "Sin mensaje"}
━━━━━━━━━━━━━━━━━━━━━━━━
Comandos de administración:
/addcreditos <user_id> <cantidad> — Sumar créditos manualmente
"""
    await update.message.reply_text(texto)

async def addcreditos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return await update.message.reply_text("❌ No tienes permisos.")
    if len(context.args) < 2:
        return await update.message.reply_text("Uso: /addcreditos 123456789 50")
    try:
        target_id = int(context.args[0])
        cantidad = int(context.args[1])
    except:
        return await update.message.reply_text("❌ Datos inválidos.")

    saldo_nuevo = agregar_creditos(target_id, cantidad)
    await update.message.reply_text(
        f"✅ Créditos agregados!\n"
        f"🆔 Usuario: {target_id}\n"
        f"➕ +{cantidad} CRD\n"
        f"💳 Saldo actual: {saldo_nuevo} CRD"
    )

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 COMPRAR CRÉDITOS\n\n"
        "1️⃣ Registra tu número: /micelular 9XXXXXXX\n"
        "2️⃣ Paga por Yape a tu número registrado\n"
        "3️⃣ Los créditos se suman SOLOS ⚡\n\n"
        "💡 Tasa: S/ 1 = 1 CRD"
    )

# ================== MENÚ Y COMANDOS ORIGINALES ==================
def texto_menu_principal():
    return """
╔════════════════════╗
🛰️ MENÚ DE SERVICIOS
╚════════════════════╝

🚀 SISTEMA CENTRAL DE
CONSULTAS

💎 Cada servicio muestra su costo.
⚡ Los créditos se descuentan
únicamente cuando la API confirma
una consulta exitosa.
🛡️ Si la API falla o no devuelve
resultados, no se cobra.

━━━━━━━━━━━━━━━━━━━━━━━
👇 SELECCIONA UNA CATEGORÍA
"""

def teclado_menu_principal():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🪪 RENIEC", callback_data="cat_reniec"),
         InlineKeyboardButton("🏢 RUC", callback_data="cat_ruc")],
        [InlineKeyboardButton("🚗 VEHÍCULOS", callback_data="cat_vehiculos"),
         InlineKeyboardButton("📱 TELÉFONO", callback_data="cat_telefono")],
        [InlineKeyboardButton("⚖️ DENUNCIAS", callback_data="cat_denuncias"),
         InlineKeyboardButton("💰 SUELDOS", callback_data="cat_sueldos")],
        [InlineKeyboardButton("🧬 FACIAL", callback_data="cat_facial"),
         InlineKeyboardButton("💎 COMPRAR", callback_data="cat_comprar")],
    ])

async def cmds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        texto_menu_principal(),
        reply_markup=teclado_menu_principal()
    )

async def botones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "menu":
        await query.edit_message_text(
            text=texto_menu_principal(),
            reply_markup=teclado_menu_principal()
        )
        return

    if query.data == "cat_reniec":
        texto = "🪪 *RENIEC*\n\n/dni 12345678 - Ficha completa (5 CRD)\n/agv 12345678 - Datos básicos (20 CRD)"
        teclado = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER", callback_data="menu")]])
    elif query.data == "cat_facial":
        texto = "🧬 *FACIAL*\n\nEnvía una foto con /facial (60 CRD)"
        teclado = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER", callback_data="menu")]])
    else:
        texto = "🔧 En desarrollo..."
        teclado = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER", callback_data="menu")]])

    await query.edit_message_text(text=texto, reply_markup=teclado, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_creditos(update.effective_user.id)
    bot_username = f"@{context.bot.username}"
    saldo = get_creditos(update.effective_user.id)

    texto = f"""
╔══════════════════╗
⚜ SPECTER PERÚ
╚══════════════════╝

🚀 PLATAFORMA DE CONSULTAS

🏷 Nombre: ⚜ SPECTER PERÚ ⚜
👤 Usuario: {bot_username}
🛰 Estado: ONLINE
💳 Tu Saldo: {saldo} CRD

━━━━━━━━━━━━━━━━━━━━━━━━
📚 COMANDOS

📖 /cmds ➜ Ver servicios
👤 /me ➜ Ver mi información
📱 /micelular ➜ Registrar celular
💰 /saldo ➜ Ver créditos
💳 /buy ➜ Comprar créditos
🛡 /staff ➜ Panel de administración
"""

    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 MENÚ", callback_data="menu")]
    ])

    await update.message.reply_text(texto, reply_markup=teclado)

@con_creditos(costo=COSTOS["dni"])
async def dni_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        agregar_creditos(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text("Uso: /dni 12345678")
        return

    dni = context.args[0].strip()
    if not validar_dni(dni):
        agregar_creditos(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text("DNI no válido. Debe tener 8 dígitos.")
        return

    mensaje = await update.message.reply_text("🔍 Consultando DNI...")

    try:
        url = f"{API_BASE}/dni/{dni}"
        r = requests.get(url, headers=HEADERS, timeout=30)

        if r.status_code != 200 or not r.json().get("success"):
            agregar_creditos(update.effective_user.id, COSTOS["dni"])
            await mensaje.edit_text("❌ No se encontraron resultados. Créditos devueltos.")
            return

        data = r.json().get("data", {})
        dni_info = data.get("dni", {})
        nacimiento = data.get("nacimiento", {})
        domicilio = data.get("domicilio", {})

        respuesta = f"""
╔══════════════╗
  🪪 DNI
╚══════════════╝

📋 DNI: {clean(dni_info.get('completo'))}
👤 Nombres: {clean(data.get('nombres'))}
👨‍👩 Apellidos: {clean(data.get('apellidos'))}
⚧ Género: {clean(data.get('genero'))}
🎂 Nacimiento: {clean(nacimiento.get('fecha'))} | {clean(nacimiento.get('edad'))}
📍 Dirección: {clean(domicilio.get('direccion'))}
🏠 Distrito: {clean(domicilio.get('distrito'))}
"""
        respuesta += footer_creditos(context)
        await mensaje.edit_text(respuesta)

        imagenes = data.get("images", [])
        if imagenes:
            foto = decodificar_imagen(imagenes[0].get("data_uri", ""))
            if foto:
                await update.message.reply_photo(foto, caption="📸 Foto RENIEC")

    except Exception as e:
        agregar_creditos(update.effective_user.id, COSTOS["dni"])
        await mensaje.edit_text(f"❌ Error: {str(e)[:200]} — Créditos devueltos.")

@con_creditos(costo=COSTOS["agv"])
async def agv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        agregar_creditos(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text("Uso: /agv 12345678")
        return

    dni = context.args[0].strip()
    if not validar_dni(dni):
        agregar_creditos(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text("DNI no válido. Debe tener 8 dígitos.")
        return

    mensaje = await update.message.reply_text("🔍 Consultando AGV...")

    try:
        url = f"{API_BASE}/agv/{dni}"
        r = requests.get(url, headers=HEADERS, timeout=30)

        if r.status_code != 200 or not r.json().get("success"):
            agregar_creditos(update.effective_user.id, COSTOS["agv"])
            await mensaje.edit_text("❌ No se encontraron resultados. Créditos devueltos.")
            return

        data = r.json().get("data", {})

        respuesta = f"""
╔══════════════╗
  🛰️ AGV
╚══════════════╝

📋 DNI: {clean(data.get('dni'))}
👤 Nombres: {clean(data.get('nombres'))}
👨‍👩 Apellidos: {clean(data.get('apellidos'))}
⚧ Género: {clean(data.get('genero'))}
🎂 Edad: {clean(data.get('edad'))}
"""
        respuesta += footer_creditos(context)
        await mensaje.edit_text(respuesta)

        imagenes = data.get("images", [])
        if imagenes:
            foto = decodificar_imagen(imagenes[0].get("data_uri", ""))
            if foto:
                await update.message.reply_photo(foto, caption="📸 Foto AGV")

    except Exception as e:
        agregar_creditos(update.effective_user.id, COSTOS["agv"])
        await mensaje.edit_text(f"❌ Error: {str(e)[:200]} — Créditos devueltos.")

@con_creditos(costo=COSTOS["facial"])
async def facial_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    photo_file_id = None

    if message.photo:
        photo_file_id = message.photo[-1].file_id
    elif message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not photo_file_id:
        agregar_creditos(update.effective_user.id, COSTOS["facial"])
        await message.reply_text(
            "🧬 *FACIAL TOP*\n\n"
            "Envíame una foto con /facial o responde a una foto con /facial\n"
            f"Costo: {COSTOS['facial']} CRD",
            parse_mode="Markdown"
        )
        return

    await message.reply_text("🔍 Analizando rostro...")

    tmp_path = None
    try:
        tg_file = await context.bot.get_file(photo_file_id)
        tmp_path = os.path.join(tempfile.gettempdir(), f"facial_{update.effective_user.id}.jpg")
        await tg_file.download_to_drive(tmp_path)

        url = f"{API_BASE}/facial/top"
        headers_facial = {
            "Authorization": f"Bearer {CODART_TOKEN}",
            "Accept": "application/json"
        }
        with open(tmp_path, "rb") as f:
            files = {"image_facial": ("facial.jpg", f, "image/jpeg")}
            r = requests.post(url, headers=headers_facial, files=files, timeout=30)

        if r.status_code != 200 or not r.json().get("success"):
            agregar_creditos(update.effective_user.id, COSTOS["facial"])
            await update.message.reply_text("❌ Sin resultados. Créditos devueltos.")
            return

        data = r.json().get("data", {})
        rostros = data.get("rostros", [])

        if not rostros:
            agregar_creditos(update.effective_user.id, COSTOS["facial"])
            await update.message.reply_text(
                "❌ No se detectó ningún rostro. Créditos devueltos."
            )
            return

        mensaje = f"""
╔══════════════╗
  🧬 FACIAL
╚══════════════╝

👤 Rostros: {clean(data.get('total_rostros'))}
🔎 Tipo: {clean(data.get('tipo_resultado'))}

"""
        for rostro in rostros:
            mensaje += f"┌─ ROSTRO #{rostro.get('numero_rostro')} ──────────┐\n"
            for i, coinc in enumerate(rostro.get("coincidencias", []), 1):
                pct = coinc.get('porcentaje', 0)
                emoji = "🟢" if pct >= 90 else "🟡" if pct >= 75 else "🔴"
                mensaje += f"│ {emoji} {i}. {clean(coinc.get('nombre'))}\n"
                mensaje += f"│    DNI: {clean(coinc.get('dni'))} | {pct}%\n"
            mensaje += "└──────────────────────────┘\n"

        mensaje += footer_creditos(context)
        await update.message.reply_text(mensaje)

    except Exception as e:
        agregar_creditos(update.effective_user.id, COSTOS["facial"])
        await update.message.reply_text(f"❌ Error: {e} — Créditos devueltos.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

# ================== BOT APPLICATION - COMPATIBLE CON RENDER ==================
# Inicializar DB al importar (para gunicorn)
init_db()

# Crear Application UNA SOLA VEZ
application = Application.builder().token(BOT_TOKEN).build()

# Registrar todos los handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("cmds", cmds_command))
application.add_handler(CommandHandler("me", me_command))
application.add_handler(CommandHandler("micelular", micelular_command))
application.add_handler(CommandHandler("saldo", saldo_command))
application.add_handler(CommandHandler("buy", buy_command))
application.add_handler(CommandHandler("staff", staff_command))
application.add_handler(CommandHandler("addcreditos", addcreditos_command))
application.add_handler(CommandHandler("dni", dni_command))
application.add_handler(CommandHandler("agv", agv_command))
application.add_handler(CommandHandler("facial", facial_command))
application.add_handler(CallbackQueryHandler(botones_callback))

# ================== WEBHOOKS PARA RENDER ==================
import asyncio

@app.route(f"/telegram/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Recibe updates de Telegram cuando está en modo webhook"""
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        # Procesar update en nuevo loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.process_update(update))
        loop.close()
        return "ok", 200
    except Exception as e:
        logger.exception(f"Error en webhook telegram: {e}")
        return "error", 500

@app.route("/set-webhook", methods=["GET"])
def set_webhook_route():
    """Configura el webhook automáticamente. Visita esta URL 1 vez después del deploy"""
    base_url = (os.getenv("RENDER_EXTERNAL_URL", "") or os.getenv("KEEP_ALIVE_URL", "")).strip().rstrip("/")
    if not base_url:
        return jsonify({"error": "Configura RENDER_EXTERNAL_URL en Render Environment"}), 400
    webhook_url = f"{base_url}/telegram/{BOT_TOKEN}"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Eliminar webhook viejo y poner nuevo
        loop.run_until_complete(application.bot.delete_webhook())
        result = loop.run_until_complete(application.bot.set_webhook(url=webhook_url))
        logger.info(f"Webhook configurado: {webhook_url} -> {result}")
        return jsonify({"webhook_url": webhook_url, "telegram_result": str(result)}), 200
    except Exception as e:
        logger.exception("Error configurando webhook")
        return jsonify({"error": str(e)}), 500
    finally:
        loop.close()

@app.route("/delete-webhook", methods=["GET"])
def delete_webhook_route():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(application.bot.delete_webhook())
        return jsonify({"result": str(result), "message": "Webhook eliminado, ahora puedes usar polling local"}), 200
    finally:
        loop.close()

# ================== INICIAR TODO - SOLO LOCAL ==================
def main():
    """Solo se ejecuta cuando haces python main.py en tu PC. En Render usa gunicorn"""
    logger.info("🤖 Bot SPECTER PERÚ iniciado en modo POLLING LOCAL")
    logger.info("🐍 Python runtime: %s", __import__("sys").version.split()[0])
    # En local, borra webhook y usa polling
    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(application.bot.delete_webhook())
    except Exception:
        pass
    loop.close()
    
    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()

