import os
import re
import base64
import sqlite3
import tempfile
import requests
import logging
import threading
from functools import wraps
from dotenv import load_dotenv
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ================== CONFIGURACIÓN INICIAL PARA RENDER ==================
load_dotenv()

# Logging para que veas errores en Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CODART_TOKEN = os.getenv("CODART_TOKEN")
API_BASE = os.getenv("API_BASE", "https://api-codart.cgrt.org/api/v1/consultas/fd")
PORT = int(os.getenv("PORT", 10000))

# Validación crítica - Si falta token, Render crashea sin logs claros
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN no está definido en Variables de Entorno")
    raise SystemExit("BOT_TOKEN faltante")
if not CODART_TOKEN:
    logger.warning("⚠️ CODART_TOKEN no está definido, las consultas fallarán")

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CODART_TOKEN}"
}

# ================== SERVIDOR WEB PARA RENDER (HEALTH CHECK) ==================
# Render exige que abras un puerto, si no, te da error "Port not open"
app_flask = Flask(__name__)

@app_flask.route('/')
def health():
    return "🤖 SPECTER PERÚ - Bot ONLINE", 200

@app_flask.route('/health')
def health_check():
    return {"status": "ok", "bot": "SPECTER PERÚ"}, 200

def run_flask():
    app_flask.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ================== SISTEMA DE CRÉDITOS (FIX RENDER FS) ==================
# En Render el filesystem es efímero, usamos /tmp si no hay disco persistente
# Si configuras un Disk en Render, pon DB_PATH=/data/bot.db
DB_PATH = os.getenv("DB_PATH", "bot.db")
# Asegurar directorio existe
db_dir = os.path.dirname(os.path.abspath(DB_PATH))
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

CREDITOS_INICIALES = 10
COSTOS = {
    "dni": 5,
    "agv": 20,
    "facial": 60
}

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                user_id INTEGER PRIMARY KEY,
                creditos INTEGER NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"✅ DB inicializada en: {DB_PATH}")
    except Exception as e:
        logger.error(f"❌ Error init_db: {e}")
        raise

def get_creditos(user_id: int) -> int:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT creditos FROM usuarios WHERE user_id =?", (user_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO usuarios (user_id, creditos) VALUES (?,?)", (user_id, CREDITOS_INICIALES))
            conn.commit()
            conn.close()
            return CREDITOS_INICIALES
        conn.close()
        return row[0]
    except Exception as e:
        logger.error(f"Error get_creditos {user_id}: {e}")
        if conn:
            try: conn.close()
            except: pass
        return CREDITOS_INICIALES

def set_creditos(user_id: int, nuevo_saldo: int):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET creditos =? WHERE user_id =?", (nuevo_saldo, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error set_creditos: {e}")
        if conn:
            try: conn.close()
            except: pass

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

def con_creditos(costo: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            try:
                user_id = update.effective_user.id
                saldo = get_creditos(user_id)
                if saldo < costo:
                    # FIX: update.message puede ser None en algunos casos
                    reply_target = update.message or (update.callback_query.message if update.callback_query else None)
                    if reply_target:
                        await reply_target.reply_text(
                            f"❌ Créditos insuficientes.\nTe quedan: {saldo} CRD\nCosto: {costo} CRD",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER", callback_data="menu")]])
                        )
                    return
                nuevo_saldo = descontar_creditos(user_id, costo)
                context.user_data['costo_actual'] = costo
                context.user_data['saldo_actual'] = nuevo_saldo
                return await func(update, context, *args, **kwargs)
            except Exception as e:
                logger.error(f"Error en decorador con_creditos: {e}")
                try:
                    await update.effective_message.reply_text(f"❌ Error interno: {e}", reply_markup=teclado_volver())
                except: pass
        return wrapper
    return decorator

def reembolsar(user_id: int, cantidad: int):
    try:
        saldo_actual = get_creditos(user_id)
        nuevo_saldo = saldo_actual + cantidad
        set_creditos(user_id, nuevo_saldo)
        return nuevo_saldo
    except Exception as e:
        logger.error(f"Error reembolsar: {e}")
        return 0

def footer_creditos(context: ContextTypes.DEFAULT_TYPE) -> str:
    costo = context.user_data.get('costo_actual', 0)
    saldo = context.user_data.get('saldo_actual', 0)
    return f"\n\n━━━━━━━━━━━━━━━━━━━━\n💰 Costo: {costo} CRD | Saldo: {saldo} CRD"

def validar_dni(dni: str) -> bool:
    return bool(re.match(r"^\d{8}$", dni))

def decodificar_imagen(data_uri: str):
    try:
        if "," in data_uri:
            _, b64 = data_uri.split(",", 1)
        else:
            b64 = data_uri
        return base64.b64decode(b64)
    except Exception:
        return None

def clean(v):
    if not v or str(v) == "None" or str(v).strip() == "":
        return "—"
    return str(v)

def teclado_volver():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER", callback_data="menu")]])

# ================== COMANDOS DEL SISTEMA ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_creditos(update.effective_user.id)
    bot_username = f"@{context.bot.username}"
    nombre_bot = "⚜️ SPECTER PERÚ ⚜️"
    user_mention = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

    texto = f"""╔══════════════════════╗
✅ SPECTER PERÚ
╚══════════════════════╝

 BOT DE CONSULTAS

😱 Nombre: ⚜️ {nombre_bot} ⚜️
😀 Usuario: {user_mention}
😈 Estado: ONLINE

━━━━━━━━━━━━━━━━━━━━━━━
😀 COMANDOS

♾️ /register ➜ Registrarte
 🔍 /cmds ➜ Ver servicios
✔️ /me ➜ Ver perfil
 ✅ /staff ➜ fundador 
🟣 /buy ➜ Planes y créditos

━━━━━━━━━━━━━━━
😀 Sistema actualizado y centralizado
"""

    entities = [
        MessageEntity(type="custom_emoji", offset=24, length=1, custom_emoji_id="5431650332419563627"), # ✅
        MessageEntity(type="custom_emoji", offset=83, length=2, custom_emoji_id="5177431372788139022"), # 😱
        MessageEntity(type="custom_emoji", offset=94, length=2, custom_emoji_id="6219727185708582935"), # ⚜️
        MessageEntity(type="custom_emoji", offset=110, length=2, custom_emoji_id="6219727185708582935"), # ⚜️
        MessageEntity(type="custom_emoji", offset=113, length=2, custom_emoji_id="5429128173004529431"), # 😀
        MessageEntity(type="mention", offset=125, length=len(user_mention)), # @usuario
        MessageEntity(type="custom_emoji", offset=156, length=2, custom_emoji_id="5429128173004529431"), # 😈
        MessageEntity(type="custom_emoji", offset=196, length=2, custom_emoji_id="5429128173004529431"), # 😀
        MessageEntity(type="custom_emoji", offset=210, length=2, custom_emoji_id="5431650332419563628"), # ♾️
        MessageEntity(type="custom_emoji", offset=246, length=2, custom_emoji_id="5431650332419563629"), # 🔍
        MessageEntity(type="custom_emoji", offset=276, length=2, custom_emoji_id="5431650332419563630"), # ✔️
        MessageEntity(type="custom_emoji", offset=302, length=2, custom_emoji_id="5431650332419563627"), # ✅
        MessageEntity(type="custom_emoji", offset=334, length=2, custom_emoji_id="5431650332419563631"), # 🟣
        MessageEntity(type="custom_emoji", offset=376, length=2, custom_emoji_id="5429128173004529431"), # 😀
    ]
    
    await update.message.reply_text(texto, entities=entities, reply_markup=teclado_volver())
def texto_menu_cmds():
    return """
╔════════════════════════════╗
📡 MENÚ DE SERVICIOS
╚════════════════════════════╝

🚀 SISTEMA CENTRAL DE CONSULTAS

💎 Cada servicio muestra su costo.
⚡ Los créditos se descuentan
únicamente cuando la API confirma
una consulta exitosa.
🛡️ Si la API falla o no devuelve
resultados, no se cobra.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👇 SELECCIONA UNA CATEGORÍA
"""

def teclado_menu_cmds():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🪪 RENIEC", callback_data="cat_reniec"), InlineKeyboardButton("🏢 RUC", callback_data="cat_ruc")],
        [InlineKeyboardButton("🚗 VEHÍCULOS", callback_data="cat_vehiculos"), InlineKeyboardButton("📱 TELÉFONO", callback_data="cat_telefono")],
        [InlineKeyboardButton("⚖️ DENUNCIAS", callback_data="cat_denuncias"), InlineKeyboardButton("💰 SUELDOS", callback_data="cat_sueldos")],
        [InlineKeyboardButton("🧬 FACIAL", callback_data="cat_facial"), InlineKeyboardButton("💎 COMPRAR", callback_data="cat_comprar")],
    ])

async def cmds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(texto_menu_cmds(), reply_markup=teclado_menu_cmds())

async def botones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "menu":
        await query.message.edit_text(texto_menu_cmds(), reply_markup=teclado_menu_cmds())
    elif query.data == "cat_reniec":
        await query.message.edit_text(
            "🪪 *RENIEC*\n\n"
            f"📌 /dni [numero] - Costo: {COSTOS['dni']} CRD\n"
            f"📌 /agv [dni] - Costo: {COSTOS['agv']} CRD",
            parse_mode="Markdown",
            reply_markup=teclado_volver()
        )
    elif query.data == "cat_facial":
        await query.message.edit_text(
            "🧬 *FACIAL*\n\n"
            f"📌 /facial - Responde a una foto o envía foto con comando\n"
            f"💰 Costo: {COSTOS['facial']} CRD",
            parse_mode="Markdown",
            reply_markup=teclado_volver()
        )
    elif query.data == "cat_comprar":
        await buy_command(update, context, from_callback=True)
    else:
        await query.message.edit_text(
            "🚧 Categoría en desarrollo...",
            reply_markup=teclado_volver()
        )

@con_creditos(costo=COSTOS["dni"])
async def dni_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text(
            f"❌ DNI inválido. Usa: /dni 12345678\n💰 Costo: {COSTOS['dni']} CRD",
            reply_markup=teclado_volver()
        )
        return

    dni = context.args[0]
    mensaje_progreso = await update.message.reply_text("🔍 Consultando DNI...")

    try:
        url = f"{API_BASE}/dni"
        payload = {"dni": dni}
        r = requests.post(url, headers=HEADERS, json=payload, timeout=20)
        j = r.json()

        if r.status_code != 200 or not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["dni"])
            await mensaje_progreso.edit_text("❌ Sin resultados. Créditos devueltos.", reply_markup=teclado_volver())
            return

        data = j.get("data", {})
        mensaje = f"""
╔════════════════════════╗
  🪪 RENIEC - DNI
╚════════════════════════╝

📋 DNI: {clean(data.get('dni'))}
👤 Nombres: {clean(data.get('nombres'))}
👨‍👩 Apellidos: {clean(data.get('apellidoPaterno'))} {clean(data.get('apellidoMaterno'))}
⚡ SPECTER.PY | Consulta exitosa
"""
        mensaje += footer_creditos(context)
        await mensaje_progreso.edit_text(mensaje, reply_markup=teclado_volver())

    except Exception as e:
        logger.error(f"Error dni_command: {e}")
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await mensaje_progreso.edit_text(f"❌ Error: {e}", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["agv"])
async def agv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text(
            f"❌ DNI inválido. Usa: /agv 12345678\n💰 Costo: {COSTOS['agv']} CRD",
            reply_markup=teclado_volver()
        )
        return

    dni = context.args[0]
    mensaje_progreso = await update.message.reply_text("🔍 Consultando AGV...")

    try:
        url = f"{API_BASE}/agv"
        payload = {"dni": dni}
        r = requests.post(url, headers=HEADERS, json=payload, timeout=20)
        j = r.json()

        if r.status_code != 200 or not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["agv"])
            await mensaje_progreso.edit_text("❌ Sin resultados. Créditos devueltos.", reply_markup=teclado_volver())
            return

        data = j.get("data", {})

        mensaje = f"""
╔════════════════════════╗
  🛰️ AGV - DATOS BÁSICOS
╚════════════════════════╝

📋 DNI: {clean(data.get('dni'))}
👤 Nombres: {clean(data.get('nombres'))}
👨‍👩 Apellidos: {clean(data.get('apellidos'))}
⚧ Género: {clean(data.get('genero'))}
🎂 Edad: {clean(data.get('edad'))} años

⚡ SPECTER.PY | Consulta exitosa
"""
        mensaje += footer_creditos(context)

        images = data.get("images", [])
        if images and images[0].get("data_uri"):
            foto = decodificar_imagen(images[0]["data_uri"])
            if foto:
                await update.message.reply_photo(photo=foto, caption=mensaje, reply_markup=teclado_volver())
                try: await mensaje_progreso.delete()
                except: pass
                return

        await mensaje_progreso.edit_text(mensaje, reply_markup=teclado_volver())

    except Exception as e:
        logger.error(f"Error agv_command: {e}")
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await mensaje_progreso.edit_text(f"❌ Error: {e}", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["facial"])
async def facial_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file_id = None
    message = update.message

    if message.photo:
        photo_file_id = message.photo[-1].file_id
    elif message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not photo_file_id:
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await message.reply_text(
            "🧬 *RECONOCIMIENTO FACIAL*\n\n"
            "Envía una foto con /facial o responde a una foto con /facial\n\n"
            "Ej: foto + caption: /facial\n"
            f"💰 Costo: {COSTOS['facial']} CRD",
            parse_mode="Markdown",
            reply_markup=teclado_volver()
        )
        return

    mensaje_progreso = await message.reply_text("🔍 Analizando rostro...")
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
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await mensaje_progreso.edit_text("❌ Sin resultados. Créditos devueltos.", reply_markup=teclado_volver())
            return

        data = r.json().get("data", {})
        rostros = data.get("rostros", [])

        if not rostros:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await mensaje_progreso.edit_text("❌ No se detectó ningún rostro. Créditos devueltos.", reply_markup=teclado_volver())
            return

        mensaje = f"""
╔════════════════════════╗
  🧬 RECONOCIMIENTO FACIAL
╚════════════════════════╝

👤 Total rostros detectados: {clean(data.get('total_rostros'))}
🔎 Tipo: {clean(data.get('tipo_resultado'))}

"""
        for rostro in rostros:
            mensaje += f"┌─ ROSTRO #{rostro.get('numero_rostro')} ───────────────┐\n"
            for i, coinc in enumerate(rostro.get("coincidencias", []), 1):
                pct = coinc.get('porcentaje', 0)
                emoji = "🟢" if pct >= 90 else "🟡" if pct >= 75 else "🔴"
                mensaje += f"│ {emoji} {i}. {clean(coinc.get('nombre'))}\n"
                mensaje += f"│    DNI: {clean(coinc.get('dni'))} | {pct}%\n"
            mensaje += "└──────────────────────────────────────┘\n"

        mensaje += footer_creditos(context)
        await mensaje_progreso.edit_text(mensaje, reply_markup=teclado_volver())

    except Exception as e:
        logger.error(f"Error facial_command: {e}")
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await mensaje_progreso.edit_text(f"❌ Error: {e}", reply_markup=teclado_volver())
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    saldo = get_creditos(user.id)
    texto = f"""
👤 INFORMACIÓN DE USUARIO
━━━━━━━━━━━━━━━━━━━━━━━━
🆔 ID: {user.id}
👤 Nombre: {user.full_name}
🔖 Usuario: @{user.username if user.username else "Sin usuario"}
💳 Créditos: {saldo} CRD
━━━━━━━━━━━━━━━━━━━━━━━━
📚 Comandos: /cmds /buy
"""
    await update.message.reply_text(texto, reply_markup=teclado_volver())

async def staff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛡️ Panel de STAFF - En desarrollo", reply_markup=teclado_volver())

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
    text = (
        "💳 COMPRAR CRÉDITOS\n\n"
        "Pronto disponible...\n\n"
        "💰 Precios:\n"
        "5 CRD = S/ 5.00\n"
        "20 CRD = S/ 18.00\n"
        "60 CRD = S/ 50.00"
    )
    if from_callback and update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=teclado_volver())
    else:
        await update.message.reply_text(text, reply_markup=teclado_volver())

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_creditos(update.effective_user.id)
    await update.message.reply_text("✅ Cuenta registrada automáticamente.\nTienes 10 CRD de bienvenida!", reply_markup=teclado_volver())

def main():
    init_db()
    
    # Iniciar servidor Flask en hilo separado para que Render no falle
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Servidor Flask iniciado en puerto {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cmds", cmds_command))
    app.add_handler(CommandHandler("dni", dni_command))
    app.add_handler(CommandHandler("agv", agv_command))
    app.add_handler(CommandHandler("facial", facial_command))
    app.add_handler(CommandHandler("me", me_command))
    app.add_handler(CommandHandler("staff", staff_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("register", register_command))
    app.add_handler(CallbackQueryHandler(botones_callback))

    logger.info("🤖 SPECTER PERÚ — Bot iniciado correctamente...")
    # run_polling bloqueante, pero Flask ya está corriendo en otro hilo
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
