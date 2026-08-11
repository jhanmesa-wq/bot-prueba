import json
import httpx
import datetime
import os
import base64
import io
import html
import tempfile
from pathlib import Path
import re
from urllib.parse import quote
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
from flask import Flask
from threading import Thread
import asyncio

BTN_VOLVER = InlineKeyboardMarkup([
    [InlineKeyboardButton("🏠 Volver al inicio", callback_data="menu_inicio")]
])



app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot activo"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = [
    admin_id.strip()
    for admin_id in os.getenv("ADMIN_ID", "").split(",")
    if admin_id.strip()
]
ARCHIVO_USUARIOS = os.getenv("ARCHIVO_USUARIOS") or "usuarios.json"
BOT_USER = "@specter_Dox44bot"
BOT_NAME = "⚜ SPECTER_PERU⚜"
BASE_URL = os.getenv("BASE_URL", "https://api-codart.cgrt.org").rstrip("/")
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))
API_CONNECT_TIMEOUT = float(os.getenv("API_CONNECT_TIMEOUT", "10"))
TELEGRAM_TEXT_LIMIT = 4096

# Endpoints conservados con el mismo patrón de tu API actual.
ENDPOINTS = {
    "dni": "/api/v1/consultas/fd/dni/{valor}",
    "dnit": "/api/v1/consultas/fd/dnit/{valor}",
    "agv": "/api/v1/consultas/fd/agv/{valor}",
    "den": "/api/v1/consultas/fd/den/{valor}",
    "denuncias": "/api/v1/consultas/fd/denuncias/{valor}",
    "telp": "/api/v1/consultas/fd/telp/{valor}",
    "telpcel": "/api/v1/consultas/fd/telp/cel/{valor}",
    "hsoat": "/api/v1/consultas/fd/hsoat/{valor}",
    "denpla": "/api/v1/consultas/fd/denpla/{valor}",
    "suel": "/api/v1/consultas/fd/suel/{valor}",
    "placa": "/api/v1/consultas/fd/placa/{valor}",
    "ruc": "/api/v1/consultas/fd/ruc/{valor}",
    "nm": "/api/v1/consultas/fd/nm/{valor}",
}

PRECIOS = {
    "dni": 4,
    "agv": 20,
    "telpcel": 15,
    "facial": 30,
    "ruc": 5,
    "suel": 4,
    "denuncia": 15,
    "den": 15,
    "denuncias": 30,
    "placa": 12,
    "nm": 6,
    "hsoat": 8,
    "denpla": 30,
    "dnit": 5,
    "telp": 15,
}

# ===== FUNCIONES BASE =====
def get_fecha():
    return datetime.datetime.now().strftime("%d/%m/%Y - %I:%M:%S %p")


def cargar_usuarios():
    try:
        with open(ARCHIVO_USUARIOS, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return {}


def guardar_usuarios(data):
    """Guarda usuarios de forma atómica para evitar corromper usuarios.json."""
    archivo = Path(ARCHIVO_USUARIOS)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{archivo.name}.", suffix=".tmp", dir=str(archivo.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, archivo)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except OSError:
            pass


def asegurar_usuario(usuarios, user_id, telegram_user=None):
    """Migra perfiles antiguos y garantiza todas las claves usadas por el bot."""
    if not isinstance(usuarios, dict):
        usuarios = {}

    usuario = usuarios.get(user_id)
    if not isinstance(usuario, dict):
        usuario = {}
        usuarios[user_id] = usuario

    usuario.setdefault("creditos", 0)
    usuario.setdefault("consultas", 0)
    usuario.setdefault("nombre", "Usuario")
    usuario.setdefault("username", "")
    usuario.setdefault("fecha_registro", get_fecha())
    usuario.setdefault("rol", "PENDIENTE")
    usuario.setdefault("plan", "FREE")

    # Normalización para evitar valores inválidos provenientes de JSON antiguo.
    try:
        usuario["creditos"] = max(0, int(usuario.get("creditos", 0)))
    except (TypeError, ValueError):
        usuario["creditos"] = 0
    try:
        usuario["consultas"] = max(0, int(usuario.get("consultas", 0)))
    except (TypeError, ValueError):
        usuario["consultas"] = 0

    if telegram_user is not None:
        if telegram_user.first_name:
            usuario["nombre"] = telegram_user.first_name
        if telegram_user.username:
            usuario["username"] = telegram_user.username

    return usuario


def escapar_html(valor):
    return html.escape(str(valor if valor is not None else "-"), quote=False)


def limitar_texto_telegram(texto, limite=TELEGRAM_TEXT_LIMIT):
    texto = str(texto)
    if len(texto) <= limite:
        return texto
    aviso = "\n\n⚠️ Resultado recortado por el límite de Telegram."
    return texto[:limite - len(aviso)] + aviso


async def editar_mensaje(mensaje, texto, reply_markup=BTN_VOLVER, parse_mode="HTML"):
    """Edita el mismo mensaje de progreso; evita crear mensajes duplicados."""
    texto = limitar_texto_telegram(texto)
    try:
        await mensaje.edit_text(texto, parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except Exception:
        return False


async def responder_error(mensaje, texto, reply_markup=BTN_VOLVER):
    if await editar_mensaje(mensaje, texto, reply_markup=reply_markup):
        return
    # Solo se usa como respaldo si Telegram no permite editar el mensaje original.
    try:
        await mensaje.get_bot().send_message(
            chat_id=mensaje.chat_id,
            text=limitar_texto_telegram(texto),
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception:
        pass


async def validar_creditos(user_id, comando, usuarios):
    if comando not in PRECIOS:
        return False, f"Comando sin precio configurado: {comando}"

    usuario = asegurar_usuario(usuarios, user_id)
    costo = int(PRECIOS[comando])
    if usuario["creditos"] < costo:
        return False, f"❌ No tienes créditos suficientes. Necesitas {costo}. Usa /buy"
    return True, costo


def registrar_consulta(usuarios, user_id, comando):
    usuario = asegurar_usuario(usuarios, user_id)
    costo = int(PRECIOS[comando])
    usuario["creditos"] = max(0, usuario["creditos"] - costo)
    usuario["consultas"] += 1
    guardar_usuarios(usuarios)
    return usuario


async def consultar_api_get(url, timeout=None):
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "DataPeruBot/3.0",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout or API_TIMEOUT, connect=API_CONNECT_TIMEOUT),
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)
            try:
                data = response.json()
            except ValueError:
                data = {"error": f"Respuesta no válida de la API (HTTP {response.status_code})", "raw": response.text[:1000]}
            if response.status_code >= 400:
                return {"error": f"HTTP {response.status_code}", "details": data}
            return data
    except httpx.TimeoutException:
        return {"error": "Tiempo de espera agotado al conectar con la API."}
    except httpx.RequestError as e:
        return {"error": f"Error de conexión: {e}"}
    except Exception as e:
        return {"error": f"Error inesperado: {e}"}


async def consultar_api_post(url, *, files=None, timeout=None):
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
        "User-Agent": "DataPeruBot/3.0",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout or API_TIMEOUT, connect=API_CONNECT_TIMEOUT),
            follow_redirects=True,
        ) as client:
            response = await client.post(url, headers=headers, files=files)
            try:
                data = response.json()
            except ValueError:
                data = {"error": f"Respuesta no válida de la API (HTTP {response.status_code})", "raw": response.text[:1000]}
            if response.status_code >= 400:
                return {"error": f"HTTP {response.status_code}", "details": data}
            return data
    except httpx.TimeoutException:
        return {"error": "Tiempo de espera agotado al conectar con la API."}
    except httpx.RequestError as e:
        return {"error": f"Error de conexión: {e}"}
    except Exception as e:
        return {"error": f"Error inesperado: {e}"}


def url_endpoint(comando, valor):
    return BASE_URL + ENDPOINTS[comando].format(valor=quote(str(valor), safe=""))


def extraer_data_uri(data_uri):
    if not data_uri or "," not in data_uri:
        return None
    try:
        return base64.b64decode(data_uri.split(",", 1)[1], validate=False)
    except Exception:
        return None


def teclado_volver():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Volver al menú", callback_data="volver_cmds")]
    ])

# ===== COMANDOS GENERALES =====
async def agv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, msg = await validar_creditos(user_id, "agv", usuarios)
    if not ok:
        return await update.message.reply_text(msg, reply_markup=BTN_VOLVER)

    if len(context.args) != 1:
        return await update.message.reply_text(
            "❌ Uso correcto:\n<code>/agv 12345678</code>",
            parse_mode="HTML",
            reply_markup=BTN_VOLVER,
        )

    dni = context.args[0].strip()
    if not (dni.isdigit() and len(dni) == 8):
        return await update.message.reply_text("❌ El DNI debe contener 8 dígitos.", reply_markup=BTN_VOLVER)

    progreso = await update.message.reply_text(
        f"🔍 Buscando AGV para DNI: <code>{dni}</code>...",
        parse_mode="HTML",
        reply_markup=BTN_VOLVER,
    )

    try:
        data = await consultar_api_get(url_endpoint("agv", dni))
        if "error" in data:
            return await editar_mensaje(progreso, f"❌ <b>Error API:</b> <code>{escapar_html(data['error'])}</code>")
        if not data.get("success"):
            return await editar_mensaje(progreso, "❌ No se encontró información para ese DNI.")

        info = data.get("data", {})
        registrar_consulta(usuarios, user_id, "agv")

        mensaje = (
            "🆔 <b>CONSULTA AGV</b>\n\n"
            f"📄 <b>DNI:</b> <code>{escapar_html(info.get('dni'))}</code>\n"
            f"👤 <b>Nombre:</b> {escapar_html(info.get('nombres'))} {escapar_html(info.get('apellidos'))}\n"
            f"🚻 <b>Género:</b> {escapar_html(info.get('genero'))}\n"
            f"🎂 <b>Edad:</b> {escapar_html(info.get('edad'))} años\n"
            f"📡 <b>Fuente:</b> {escapar_html(data.get('source'))}\n\n"
            f"💳 <b>Créditos restantes:</b> <code>{usuarios[user_id]['creditos']}</code>"
        )
        await editar_mensaje(progreso, mensaje)

        images = info.get("images") or []
        if images and images[0].get("data_uri"):
            image_bytes = extraer_data_uri(images[0].get("data_uri"))
            if image_bytes:
                await update.message.reply_photo(
                    photo=image_bytes,
                    caption=f"📸 Foto DNI: {dni}",
                    reply_markup=BTN_VOLVER,
                )
    except Exception as e:
        await editar_mensaje(progreso, f"❌ <b>Error:</b> <code>{escapar_html(e)}</code>")

async def den(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, msg = await validar_creditos(user_id, "den", usuarios)
    if not ok:
        return await update.message.reply_text(msg, reply_markup=BTN_VOLVER)

    if len(context.args) != 1:
        return await update.message.reply_text(
            "❌ Uso correcto:\n<code>/den 12345678</code>",
            parse_mode="HTML",
            reply_markup=BTN_VOLVER,
        )

    dni = context.args[0].strip()
    if not (dni.isdigit() and len(dni) == 8):
        return await update.message.reply_text("❌ El DNI debe contener 8 dígitos.", reply_markup=BTN_VOLVER)

    progreso = await update.message.reply_text(
        f"🔎 Consultando denuncias de <code>{dni}</code>...",
        parse_mode="HTML",
        reply_markup=BTN_VOLVER,
    )

    try:
        data = await consultar_api_get(url_endpoint("den", dni))
        if "error" in data:
            return await editar_mensaje(progreso, f"❌ <b>Error API:</b> <code>{escapar_html(data['error'])}</code>")
        if not data.get("success"):
            return await editar_mensaje(progreso, "❌ No se encontraron denuncias.")

        info = data.get("data", {})
        registrar_consulta(usuarios, user_id, "den")

        mensaje = (
            "🚨 <b>CONSULTA DE DENUNCIAS</b>\n\n"
            f"🆔 <b>DNI:</b> <code>{escapar_html(info.get('consulta'))}</code>\n"
            f"📄 <b>Total:</b> {escapar_html(info.get('cantidad_denuncias'))}\n\n"
        )
        for d in info.get("denuncias", []):
            mensaje += (
                f"<b>📌 Denuncia #{escapar_html(d.get('numero'))}</b>\n"
                f"👤 <b>Tipo:</b> {escapar_html(d.get('tipo'))}\n"
                f"📑 <b>N° Orden:</b> {escapar_html(d.get('n_orden'))}\n"
                f"📅 <b>Fecha Hecho:</b> {escapar_html(d.get('f_hecho'))}\n"
                f"🗂 <b>Registro:</b> {escapar_html(d.get('f_registro'))}\n"
                f"📋 <b>Condición:</b> {escapar_html(d.get('condicion'))}\n"
                f"📝 <b>Resumen:</b> {escapar_html(d.get('resumen'))}\n"
                "━━━━━━━━━━━━━━\n"
            )
        mensaje += f"\n💳 <b>Créditos restantes:</b> <code>{usuarios[user_id]['creditos']}</code>"
        await editar_mensaje(progreso, mensaje)
    except Exception as e:
        await editar_mensaje(progreso, f"❌ <b>Error:</b> <code>{escapar_html(e)}</code>")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    VIDEO_LINK = "https://files.catbox.moe/odq1nv.mp4"
    texto = f"""⚜️ <b>¡BIENVENIDO A DATA PERÚ!</b> ⚜️

━━━━━━━━━━━━━━━━━━

📌 <b>INFORMACIÓN DEL BOT</b>

🏷️ <b>Nombre:</b> {BOT_NAME}
👤 <b>Usuario:</b> {BOT_USER}
🚀 <b>Versión:</b> v3.0 CODART V1

━━━━━━━━━━━━━━━━━━

📚 <b>COMANDOS GENERALES</b>

📝 /register ➾ Registrar cuenta
📖 /cmds ➾ Lista de comandos
👤 /me ➾ Ver tu perfil
🛡️ /staff ➾ Ver el staff
💳 /buy ➾ Comprar créditos/días

━━━━━━━━━━━━━━━━━━

⚡ <b>EN CONSTANTE EVOLUCIÓN</b>

Gracias por utilizar <b>DATA PERÚ</b>.
"""
    # Primero manda el video
    await context.bot.send_video(
        chat_id=update.effective_chat.id,
        video=VIDEO_LINK,
        caption=texto,
        parse_mode='HTML'
    )

async def cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    VIDEO_CMD = "https://files.catbox.moe/m7e3jl.mp4"
    
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("╔═ 🪪 RENIEC ═╗", callback_data="cmd_reniec"),
            InlineKeyboardButton("╔═ 🏢 RUC ═╗", callback_data="cmd_ruc")
        ],
        [
            InlineKeyboardButton("╔═ 🚘 VEHÍCULOS ═╗", callback_data="cmd_vehiculos"),
            InlineKeyboardButton("╔═ 📱 TELÉFONO ═╗", callback_data="cmd_telefono")
        ],
        [
            InlineKeyboardButton("╔═ ⚖️ DENUNCIAS ═╗", callback_data="cmd_denuncia"),
            InlineKeyboardButton("╔═ 💰 SUELDO ═╗", callback_data="cmd_sueldo")
        ],
        [
            InlineKeyboardButton("╔═ 🧬 FACIAL ═╗", callback_data="cmd_facial"),
            InlineKeyboardButton("╔═ 💎 COMPRAR ═╗", callback_data="cmd_buy")
        ]
    ])

    texto = f"""╔════════════╗
        ⚜️ 𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦 𝗣𝗘𝗥𝗨 ⚜️
╚════════════════════╝

🚀 𝗟𝗔 𝗣𝗟𝗔𝗧𝗔𝗙𝗢𝗥𝗠𝗔 #𝟭 𝗗𝗘 𝗖𝗢𝗡𝗦𝗨𝗟𝗧𝗔𝗦

━━━━━━━━━━━━━━━━━━━━━━━

🛰️ 𝗔𝗖𝗖𝗘𝗗𝗘 𝗔 𝗧𝗢𝗗𝗢𝗦 𝗟𝗢𝗦 𝗦𝗘𝗥𝗩𝗜𝗖𝗜𝗢𝗦

💎 Más de 150 servicios disponibles
⚡ Consultas rápidas y precisas
🛡️ Plataforma segura y estable
🚀 Tecnología de última generación
📡 Actualizaciones constantes
🎯 Respuesta en pocos segundos

━━━━━━━━━━━━━━━

🔎 𝗖𝗢𝗡𝗘𝗖𝗧𝗔 𝗟𝗔 𝗜𝗡𝗙𝗢𝗥𝗠𝗔𝗖𝗜Ó𝗡
📂 Descubre relaciones y encuentra
los datos que necesitas desde un
solo lugar.

━━━━━━━━━━━━━━━

👇 𝗦𝗘𝗟𝗘𝗖𝗜𝗢𝗡𝗔 𝗨𝗡𝗔 𝗖𝗔𝗧𝗘𝗚𝗢𝗥Í𝗔 👇"""

    await context.bot.send_video(
        chat_id=update.effective_chat.id,
        video=VIDEO_CMD,
        caption=texto,
        reply_markup=teclado,
        parse_mode='HTML'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Botón volver - ARREGLADO INDENTACIÓN Y edit_message_caption para videos
    if query.data == "volver_cmds" or query.data == "menu_inicio":
        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("╔═ 🪪 RENIEC ═╗", callback_data="cmd_reniec"),
                InlineKeyboardButton("╔═ 🏢 RUC ═╗", callback_data="cmd_ruc")
            ],
            [
                InlineKeyboardButton("╔═ 🚘 VEHÍCULOS ═╗", callback_data="cmd_vehiculos"),
                InlineKeyboardButton("╔═ 📱 TELÉFONO ═╗", callback_data="cmd_telefono")
            ],
            [
                InlineKeyboardButton("╔═ ⚖️ DENUNCIAS ═╗", callback_data="cmd_denuncia"),
                InlineKeyboardButton("╔═ 💰 SUELDO ═╗", callback_data="cmd_sueldo")
            ],
            [
                InlineKeyboardButton("╔═ 🧬 FACIAL ═╗", callback_data="cmd_facial"),
                InlineKeyboardButton("╔═ 💎 COMPRAR ═╗", callback_data="cmd_buy")
            ]
        ])

        texto = f"""╔════════════════════╗
        ⚜️ 𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦 𝗣𝗘𝗥𝗨 ⚜️
╚══════════════════════╝

🚀 𝗟𝗔 𝗣𝗟𝗔𝗧𝗔𝗙𝗢𝗥𝗠𝗔 #𝟭 𝗗𝗘 𝗖𝗢𝗡𝗦𝗨𝗟𝗧𝗔𝗦

━━━━━━━━━━━━━━━━━━━━━━━

🛰️ 𝗔𝗖𝗖𝗘𝗗𝗘 𝗔 𝗧𝗢𝗗𝗢𝗦 𝗟𝗢𝗦 𝗦𝗘𝗥𝗩𝗜𝗖𝗜𝗢𝗦
💎 Más de 150 servicios disponibles
⚡ Consultas rápidas y precisas
🛡️ Plataforma segura y estable
🚀 Tecnología de última generación
📡 Actualizaciones constantes
🎯 Respuesta en pocos segundos

━━━━━━━━━━━━━━━━━━━━━━━

🔎 𝗖𝗢𝗡𝗘𝗖𝗧𝗔 𝗟𝗔 𝗜𝗡𝗙𝗢𝗥𝗠𝗔𝗖𝗜Ó𝗡
📂 Descubre relaciones y encuentra
los datos que necesitas desde un
solo lugar.

━━━━━━━━━━━━━━━━━━━━━━━

👇 𝗦𝗘𝗟𝗘𝗖𝗖𝗜𝗢𝗡𝗔 𝗨𝗡𝗔 𝗖𝗔𝗧𝗘𝗚𝗢𝗥Í𝗔 👇"""
        # FIX: si el mensaje original es VIDEO, hay que editar caption, no texto
        try:
            await query.edit_message_caption(caption=texto, reply_markup=teclado)
        except:
            await query.edit_message_text(texto, reply_markup=teclado)
        return

    comandos = {
        "cmd_reniec": """❰ #𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦_𝗗𝗔𝗧𝗔_𝗣𝗘𝗥𝗨 ❱ ➾ RENIEC
✦ ──────────────── ✦
ᴄᴏᴍᴀɴᴅᴏs ᴅɪsᴘᴏɴɪʙʟᴇs ➾ 5
ᴘᴀɢɪɴᴀ ➾ 1/1

1. DNI TARJETA
• ᴇsᴛᴀᴅᴏ ➾ OPERATIVO [✅]
• ᴄᴏᴍᴀɴᴅᴏ ➾ /dnit 44445555
• ᴘʀᴇᴄɪᴏ ➾ 5 ᴄʀᴇ‌ᴅɪᴛᴏs
• ʀᴇsᴜʟᴛᴀᴅᴏ ➾ texto con foto y firma

2. DNI POR NOMBRES
• ᴇsᴛᴀᴅᴏ ➾ OPERATIVO [✅]
• ᴄᴏᴍᴀɴᴅᴏ ➾ /nm juan quispe
• ᴘʀᴇᴄɪᴏ ➾ 6 ᴄʀᴇ‌ᴅɪᴛᴏs
• ʀᴇsᴜʟᴛᴀᴅᴏ ➾ dni por nombre y apellido

3. DNI SIMPLE
• ᴇsᴛᴀᴅᴏ ➾ OPERATIVO [✅]
• ᴄᴏᴍᴀɴᴅᴏ ➾ /dni 44445555
• ᴘʀᴇᴄɪᴏ ➾ 4 ᴄʀᴇ‌ᴅɪᴛᴏs

Página: 1/1""",

        "cmd_ruc": "Uso: /ruc 20538856674",

        "cmd_vehiculos": f"""❰ #𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦_𝗗𝗔𝗧𝗔_𝗣𝗘𝗥𝗨 ❱ ➾ VEHICULARES
✦ ──────────────── ✦

1. PLACA TEXTO
• ᴄᴏᴍᴀɴᴅᴏ ➾ /placa ABC123
• ᴘʀᴇᴄɪᴏ ➾ {PRECIOS['placa']} créditos

2. SOAT VIGENTE
• ᴄᴏᴍᴀɴᴅᴏ ➾ /hsoat ABC123
• ᴘʀᴇᴄɪᴏ ➾ {PRECIOS['hsoat']} créditos

3. DENUNCIAS POR PLACA
• ᴄᴏᴍᴀɴᴅᴏ ➾ /denpla ABC123
• ᴘʀᴇᴄɪᴏ ➾ {PRECIOS['denpla']} créditos""",

        "cmd_telefono": f"""❰ #𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦_𝗗𝗔𝗧𝗔_𝗣𝗘𝗥𝗨 ❱ ➾ TELEFONIA

1. TELX POR DNI
• ᴄᴏᴍᴀɴᴅᴏ ➾ /telp 44445555
• ᴘʀᴇᴄɪᴏ ➾ {PRECIOS['telp']} créditos

2. TELX POR NUMERO
• ᴄᴏᴍᴀɴᴅᴏ ➾ /telpcel 999888777
• ᴘʀᴇᴄɪᴏ ➾ {PRECIOS['telpcel']} créditos""",

        "cmd_denuncia": f"""❰ #𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦_𝗗𝗔𝗧𝗔_𝗣𝗘𝗥𝗨   ❱ ➾ DENUNCIA
✦ ──────────────── ✦
ᴄᴏᴍᴀɴᴅᴏs ᴅɪsᴘᴏɴɪʙʟᴇs ➾ 2
ᴘᴀ‌ɢɪɴᴀ ➾ 1/1

1. DENUNCIAS EN PDF
• ᴇsᴛᴀᴅᴏ ➾ OPERATIVO [✅]
• ᴄᴏᴍᴀɴᴅᴏ ➾ /denuncias 44445555
• ᴘʀᴇᴄɪᴏ ➾ 30 ᴄʀᴇ‌ᴅɪᴛᴏs
• ʀᴇsᴜʟᴛᴀᴅᴏ ➾ Lista de denuncias en PDF

2. DENUNCIAS POR DNI
• ᴇsᴛᴀᴅᴏ ➾ OPERATIVO [✅]
• ᴄᴏᴍᴀɴᴅᴏ ➾ /den 12345678
• ᴘʀᴇᴄɪᴏ ➾ 15 ᴄʀᴇ‌ᴅɪᴛᴏs
• ʀᴇsᴜʟᴛᴀᴅᴏ ➾ Consulta denuncias asociadas a un DNI.

Página: 1/1""",

        "cmd_sueldo": f"""❰ #𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦_𝗗𝗔𝗧𝗔_𝗣𝗘𝗥𝗨 ❱ ➾ SUELDOS
✦ ──────────────── ✦
ᴄᴏᴍᴀɴᴅᴏs ᴅɪsᴘᴏɴɪʙʟᴇs ➾ 1
ᴘᴀ‌ɢɪɴᴀ ➾ 1/1

1. CONSULTA DE SUELDOS
• ᴇsᴛᴀᴅᴏ ➾ OPERATIVO [✅]
• ᴄᴏᴍᴀɴᴅᴏ ➾ /suel 12345678
• ᴘʀᴇᴄɪᴏ ➾ 4 ᴄʀᴇ‌ᴅɪᴛᴏs
• ʀᴇsᴜʟᴛᴀᴅᴏ ➾ Información de sueldos registrados

Página: 1/1""",
        "cmd_buy": f"""╔════════════════════╗
      💎 PLANES PREMIUM 💎
╚════════════════════╝

💰 𝗖𝗥É𝗗𝗜𝗧𝗢𝗦

🥉 100 Créditos ➜ S/ 10
🥈 200 Créditos ➜ S/ 20
🥇 400 Créditos ➜ S/ 30
💠 500 Créditos ➜ S/ 40
🚀 800 Créditos ➜ S/ 50
👑 2,000 Créditos ➜ S/ 100
💎 4,300 Créditos ➜ S/ 200

━━━━━━━━━━━━━━━━━━

♾️ 𝗜𝗟𝗜𝗠𝗜𝗧𝗔𝗗𝗢𝗦

💥 7 DÍAS ➜ S/ 20
⚡ 15 DÍAS ➜ S/ 35
🔱 30 DÍAS ➜ S/ 60
👑 60 DÍAS ➜ S/ 100

━━━━━━━━━━━━━━━━━━

💳 Aceptamos:
🏦 Yape • Plin • Bcp

╭━━━〔 💠 𝗣𝗔𝗚𝗢𝗦 𝗣𝗥𝗘𝗠𝗜𝗨𝗠 💠 〕━━━╮

⚡ Para activar tu compra o recargar
tu cuenta, comunícate con:

👤 ➤ @Xxxxxxx_Gatito_xxxxxxx

✦ Atención rápida
✦ Activación inmediata
✦ Soporte personalizado

╰━━━━━━━━━━━━━━━━━━━━━━╯""" 
    }

    if query.data in comandos:
        volver = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Volver al inicio", callback_data="volver_cmds")]
        ])
        try:
            await query.edit_message_caption(caption=comandos[query.data], reply_markup=volver)
        except:
            await query.edit_message_text(comandos[query.data], reply_markup=volver)

    elif query.data == "cmd_facial":
        volver = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al inicio", callback_data="volver_cmds")]])
        texto_facial = """❰ #𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦_𝗗𝗔𝗧𝗔_𝗣𝗘𝗥𝗨 ❱ ➾ FACIAL
✦ ──────────────── ✦

ᴄᴏᴍᴀɴᴅᴏs ᴅɪsᴘᴏɴɪʙʟᴇs ➾ 1
ᴘᴀ‌ɢɪɴᴀ ➾ 1/1
1. RECONOCIMIENTO FACIAL
• ᴇsᴛᴀᴅᴏ ➾ OPERATIVO [✅]
• ᴄᴏᴍᴀɴᴅᴏ ➾ /facial
• ᴘʀᴇᴄɪᴏ ➾ 30 ᴄʀᴇ‌ᴅɪᴛᴏs
• ʀᴇsᴜʟᴛᴀᴅᴏ ➾ Procesamiento de imagen facial

✦ ──────────────── ✦

Página: 1/1"""
        try:
            await query.edit_message_caption(caption=texto_facial, reply_markup=volver)
        except:
            await query.edit_message_text(texto_facial, reply_markup=volver)

    elif query.data == "cmd_buy":
        volver = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al inicio", callback_data="volver_cmds")]])
        try:
            await query.edit_message_caption(caption=comandos["cmd_buy"], reply_markup=volver)
        except:
            await query.edit_message_text(comandos["cmd_buy"], reply_markup=volver)
async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    if user_id in usuarios:
        asegurar_usuario(usuarios, user_id, update.effective_user)
        guardar_usuarios(usuarios)
        return await update.message.reply_text("✅ Ya estás registrado. Tu perfil fue verificado.")

    usuarios[user_id] = {
        "creditos": 0,
        "consultas": 0,
        "nombre": update.effective_user.first_name or "Usuario",
        "username": update.effective_user.username or "",
        "fecha_registro": get_fecha(),
        "rol": "PENDIENTE",
        "plan": "FREE"
    }
    guardar_usuarios(usuarios)
    await update.message.reply_text(f"✅ Registro exitoso. Bienvenido {escapar_html(update.effective_user.first_name or 'Usuario')}!")

async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    VIDEO_PERFIL = "https://files.catbox.moe/jwtbu0.mp4"
    
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    u = asegurar_usuario(usuarios, user_id, update.effective_user)
    guardar_usuarios(usuarios)
    
    texto = f"""[#BOT DATA] ➾ PERFIL DE USUARIO
PERFIL DE ➾ {u.get("nombre", "Usuario")}
[🙎‍♂️] ID ➾ {user_id}
[👨🏻‍💻] USER ➾ @{u.get("username", "")}
[💰] CREDITOS ➾ {u.get('creditos', 0)}
[📊] CONSULTAS ➾ {u.get('consultas', 0)}"""
    
    await context.bot.send_video(
        chat_id=update.effective_chat.id,
        video=VIDEO_PERFIL,
        caption=texto,
        parse_mode='HTML'
    )


async def denuncias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)
    ok, msg = await validar_creditos(user_id, "denuncias", usuarios)
    if not ok:
        return await update.message.reply_text(msg)

    if len(context.args) != 1:
        await update.message.reply_text("❌ Uso: /denuncias <DNI>")
        return

    dni = context.args[0]

    if not dni.isdigit() or len(dni) != 8:
        await update.message.reply_text("❌ El DNI debe tener 8 dígitos.")
        return

    url = f"https://api-codart.cgrt.org/api/v1/consultas/fd/denuncias/{dni}"

    try:
        data = await consultar_api_get(url)

        if not data.get("success"):
            await update.message.reply_text("❌ No se encontraron denuncias.")
            return

        info = data["data"]
        registrar_consulta(usuarios, user_id, "denuncias")

        await update.message.reply_text(
            f"📂 Se encontraron {info['cantidad_denuncias']} denuncia(s).\n"
            f"Enviando archivos..."
        )

        for den in info["denuncias"]:
            pdf = den["data_uri"].split(",")[1]
            archivo = BytesIO(base64.b64decode(pdf))
            archivo.name = den["nombre"]

            caption = (
                f"🚨 <b>DENUNCIA #{den['numero']}</b>\n"
                f"👤 <b>Tipo:</b> {den['tipo']}\n"
                f"🏢 <b>Comisaría:</b> {den['comisaria']}\n"
                f"📄 <b>Orden:</b> {den['n_orden']}\n"
                f"📅 <b>Hecho:</b> {den['f_hecho']}\n"
                f"📝 <b>Registro:</b> {den['f_registro']}"
            )

            await update.message.reply_document(
                document=archivo,
                filename=den["nombre"],
                caption=caption,
                parse_mode="HTML"
            )

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"""╔════════════════════╗
      💎 PLANES PREMIUM 💎
╚════════════════════╝

💰 𝗖𝗥É𝗗𝗜𝗧𝗢𝗦

🥉 100 Créditos ➜ S/ 10
🥈 200 Créditos ➜ S/ 20
🥇 400 Créditos ➜ S/ 30
💠 500 Créditos ➜ S/ 40
🚀 800 Créditos ➜ S/ 50
👑 2,000 Créditos ➜ S/ 100
💎 4,300 Créditos ➜ S/ 200

━━━━━━━━━━━━━━━━━━

♾️ 𝗜𝗟𝗜𝗠𝗜𝗧𝗔𝗗𝗢𝗦

💥 7 DÍAS ➜ S/ 20
⚡ 15 DÍAS ➜ S/ 35
🔱 30 DÍAS ➜ S/ 60
👑 60 DÍAS ➜ S/ 100

━━━━━━━━━━━━━━━━━━

💳 Aceptamos:
🏦 Yape • Plin • Bcp

╭━━━〔 💠 𝗣𝗔𝗚𝗢𝗦 𝗣𝗥𝗘𝗠𝗜𝗨𝗠 💠 〕━━━╮

⚡ Para activar tu compra o recargar
tu cuenta, comunícate con:

👤 ➤ @Sthep_18

✦ Atención rápida""
✦ Activación inmediata
✦ Soporte personalizado

╰━━━━━━━━━━━━━━━━━━━━━━╯""")

async def staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""╔═════════════════════╗
        👑 𝗦𝗧𝗔𝗙𝗙 𝗢𝗙𝗜𝗖𝗜𝗔𝗟 👑
╚════════════════════╝

🛡️ 𝗔𝗗𝗠𝗜𝗡𝗜𝗦𝗧𝗥𝗔𝗗𝗢𝗥 𝗣𝗥𝗜𝗡𝗖𝗜𝗣𝗔𝗟

👤 Usuario:
➜ @Sthep_18

━━━━━━━━━━━━━━━━━━━━━━

⚡ Servicios disponibles:
• 💳 Venta de créditos
• ♾️ Planes ilimitados
• 🛠️ Soporte técnico
• 📞 Atención personalizada

━━━━━━━━━━━━━━━━━━━━━━

💬 Para compras, soporte o consultas,
contacta directamente al administrador.

🚀 Gracias por confiar en
⚜️ 𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦 𝗗𝗔𝗧𝗔 𝗣𝗘𝗥𝗨 ⚜️""", parse_mode="HTML")

async def quitarcrd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_ID:
        return await update.message.reply_text("""╔══════════════════════╗
        🚫 ACCESO DENEGADO 🚫
╚══════════════════════╝

⚠️ No cuentas con los permisos
necesarios para ejecutar este comando.

🔒 El comando <code>/quitarcrd</code> está
reservado exclusivamente para el
personal autorizado.

━━━━━━━━━━━━━━━━━━━━━━

👑 Si crees que se trata de un error,
contacta al administrador:

➜ @Sthep_18""", parse_mode="HTML")
    if len(context.args) < 2:
        return await update.message.reply_text("Uso: /quitarcrd ID_USUARIO CANTIDAD")
    target_id = context.args[0].strip()
    try:
        cantidad = int(context.args[1])
    except (TypeError, ValueError):
        return await update.message.reply_text("❌ La cantidad debe ser un número entero.")
    if cantidad <= 0:
        return await update.message.reply_text("❌ La cantidad debe ser mayor que 0.")
    usuarios = cargar_usuarios()
    if target_id not in usuarios:
        return await update.message.reply_text(f"El usuario {target_id} no existe en la BD")
    asegurar_usuario(usuarios, target_id)
    saldo_anterior = usuarios[target_id]["creditos"]
    usuarios[target_id]["creditos"] -= cantidad
    if usuarios[target_id]["creditos"] < 0:
        usuarios[target_id]["creditos"] = 0
    guardar_usuarios(usuarios)
    texto = f"""[#BOT DATA] ➾ CREDITOS QUITADOS
[👤] USUARIO ➾ {target_id}
[➖] QUITADOS ➾ {cantidad} Créditos
[💰] SALDO ANTERIOR ➾ {saldo_anterior}
[💰] SALDO ACTUAL ➾ {usuarios[target_id]['creditos']}"""
    await update.message.reply_text(texto)

async def addcreditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_ID:
        return await update.message.reply_text(
            "🚫 <b>ACCESO DENEGADO</b>\n\n"
            "No cuentas con permisos para ejecutar este comando.",
            parse_mode="HTML",
        )

    if len(context.args) != 2:
        return await update.message.reply_text(
            "╔══════════════════════╗\n"
            "      💎 AÑADIR CRÉDITOS 💎\n"
            "╚══════════════════════╝\n\n"
            "📝 <b>Uso:</b> <code>/addcreditos ID CANTIDAD</code>\n"
            "📌 <b>Ejemplo:</b> <code>/addcreditos 123456789 100</code>",
            parse_mode="HTML",
        )

    target_id = context.args[0].strip()
    try:
        cantidad = int(context.args[1])
    except (TypeError, ValueError):
        return await update.message.reply_text("❌ La cantidad debe ser un número entero.")
    if cantidad <= 0:
        return await update.message.reply_text("❌ La cantidad debe ser mayor que 0.")

    usuarios = cargar_usuarios()
    if target_id not in usuarios:
        return await update.message.reply_text(f"❌ El usuario {escapar_html(target_id)} no existe en la base de datos.")

    asegurar_usuario(usuarios, target_id)
    saldo_anterior = usuarios[target_id]["creditos"]
    usuarios[target_id]["creditos"] += cantidad
    guardar_usuarios(usuarios)

    texto = (
        "[#BOT DATA] ➾ CRÉDITOS AGREGADOS\n"
        f"[👤] USUARIO ➾ {escapar_html(target_id)}\n"
        f"[➕] AGREGADOS ➾ {cantidad} Créditos\n"
        f"[💰] SALDO ANTERIOR ➾ {saldo_anterior}\n"
        f"[💰] SALDO ACTUAL ➾ {usuarios[target_id]['creditos']}"
    )
    await update.message.reply_text(texto)

async def facial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, msg = await validar_creditos(user_id, "facial", usuarios)
    if not ok:
        return await update.message.reply_text(msg)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Volver al menú", callback_data="volver_cmds")]
    ])

    message = update.message

    if not message or not message.photo:
        return await message.reply_text(
            """
╔═════════════════════╗
        🧬 SISTEMA FACIAL
╚═════════════════════╝

📷 Envía una fotografía y escribe

<code>/facial</code>

como descripción de la imagen.

━━━━━━━━━━━━━━━━━━━━
⚜️ DATA PERÚ
""",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    try:

        progreso = await message.reply_text(
            """
╔════════════════════════╗
      🧬 ESCÁNER FACIAL
╚════════════════════════╝

🛰️ Conectando al servidor...

📷 Procesando imagen...
🔎 Analizando rostro...
⚙️ Buscando coincidencias...

━━━━━━━━━━━━━━━━━━━━
""",
            reply_markup=keyboard
        )

        photo = message.photo[-1]

        tg_file = await context.bot.get_file(photo.file_id)
        imagen = await tg_file.download_as_bytearray()

        headers = {
            "Authorization": f"Bearer {API_TOKEN}",
            "Accept": "application/json"
        }

        files = {
            "image_facial": (
                "imagen.jpg",
                bytes(imagen),
                "image/jpeg"
            )
        }

        data = await consultar_api_post(
            f"{BASE_URL}/api/v1/consultas/fd/facial/top",
            files=files,
            timeout=60,
        )

        if "error" in data:
            return await editar_mensaje(
                progreso,
                f"❌ <b>Error API:</b> <code>{escapar_html(data['error'])}</code>",
                reply_markup=keyboard,
            )

        if not data.get("success"):
            return await editar_mensaje(
                progreso,
                """
╔═════════════════════╗
      🧬 RESULTADO
╚═════════════════════╝

❌ No se encontraron coincidencias.

━━━━━━━━━━━━━━━━━━━━
""",
                reply_markup=keyboard
            )

        info = data["data"]

        registrar_consulta(usuarios, user_id, "facial")

        texto = f"""
╔════════════════════════╗
      🧬 RESULTADO FACIAL
╚════════════════════════╝

✅ Consulta completada

🔎 Tipo:
<code>{info.get("tipo_resultado")}</code>

👥 Coincidencias:
<code>{info.get("coincidencias_mostradas")}</code>

━━━━━━━━━━━━━━━━━━━━
"""

        for i, persona in enumerate(info.get("coincidencias", []), 1):
            texto += f"""
👤 <b>COINCIDENCIA #{i}</b>

🪪 DNI
<code>{persona.get("dni")}</code>

📛 Nombre
<code>{persona.get("nombre")}</code>

🎯 Similitud
<code>{persona.get("porcentaje")}%</code>

━━━━━━━━━━━━━━━━━━━━
"""

        texto += f"""
💳 Créditos restantes:
<code>{usuarios[user_id]["creditos"]}</code>

⚜️ DATA PERÚ
📡 Powered by CODART X API
"""

        await editar_mensaje(
            progreso,
            texto,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except httpx.RequestError as e:
        await editar_mensaje(
            progreso,
            f"⚠️ Error de conexión\n\n<code>{escapar_html(e)}</code>",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except Exception as e:
        await editar_mensaje(
            progreso,
            f"⚠️ Error inesperado\n\n<code>{escapar_html(e)}</code>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
async def telpcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, msg = await validar_creditos(user_id, "telpcel", usuarios)
    if not ok:
        return await update.message.reply_text(msg)

    if not context.args:
        return await update.message.reply_text(
            "❌ <b>Uso correcto:</b>\n<code>/telpcel 900000001</code>",
            parse_mode="HTML"
        )

    numero = context.args[0]

    if not (numero.isdigit() and len(numero) == 9):
        return await update.message.reply_text(
            "❌ El número debe contener exactamente 9 dígitos."
        )

    try:
        progreso = await update.message.reply_text(f"📡 Consultando número <code>{numero}</code>...", parse_mode="HTML")
        data = await consultar_api_get(url_endpoint("telpcel", numero))

        if not data.get("success"):
            return await editar_mensaje(progreso, "❌ No se encontraron resultados.", reply_markup=BTN_VOLVER)

        titulares = data.get("data", {}).get("titulares", [])
        if not titulares:
            return await editar_mensaje(progreso, "❌ No se encontraron titulares.", reply_markup=BTN_VOLVER)

        registrar_consulta(usuarios, user_id, "telpcel")

        texto = """╔═════════════════════╗
📡 <b>TELP CEL • SISTEMA</b>
╚═════════════════════╝

🟢 <b>ESTADO DEL SISTEMA</b>
➜ ONLINE

⚡━━━━━━━━━━━━━━━━━━━━━━⚡
"""

        for t in titulares:
            texto += f"""
👤 <b>TITULAR</b>
➜ <code>{t.get('titular','-')}</code>

📱 <b>TELÉFONO</b>
➜ <code>{t.get('telefono','-')}</code>

🏢 <b>OPERADOR</b>
➜ <code>{t.get('operador','-')}</code>

🪪 <b>DNI / RUC</b>
➜ <code>{t.get('dni_ruc','-')}</code>

💳 <b>PLAN</b>
➜ <code>{t.get('plan','-')}</code>

📧 <b>CORREO</b>
➜ <code>{t.get('correo','-')}</code>

🏛️ <b>EMPRESA</b>
➜ <code>{t.get('empresa','-')}</code>

⚡━━━━━━━━━━━━━━━━━━━━━━⚡
"""

        texto += """
🚀 <b>Consulta completada correctamente</b>

⚜️ <b>SISTEMAS DATA PERU</b>
📡 Powered by CODART X API V1
"""

        botones = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "VOLVER AL MENU",
                    callback_data="volver_cmds"
                )
            ]
        ])

        await editar_mensaje(
            progreso,
            texto,
            parse_mode="HTML",
            reply_markup=botones
        )

    except Exception as e:
        if "progreso" in locals():
            await editar_mensaje(progreso, f"❌ <b>Error:</b>\n<code>{escapar_html(e)}</code>", reply_markup=BTN_VOLVER)
        else:
            await update.message.reply_text(f"❌ <b>Error:</b>\n<code>{escapar_html(e)}</code>", parse_mode="HTML")

async def telp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, res_cred = await validar_creditos(user_id, "telp", usuarios)
    if not ok:
        return await update.message.reply_text(res_cred)

    if not context.args:
        return await update.message.reply_text("❌ <b>Uso:</b> <code>/telp 12345678</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)

    dni = context.args[0]
    if not (dni.isdigit() and len(dni) == 8):
        return await update.message.reply_text("❌ El DNI debe contener exactamente 8 dígitos.", parse_mode="HTML", reply_markup=BTN_VOLVER)

    m = await update.message.reply_text(f"📡 Consultando líneas de <code>{dni}</code>...", parse_mode="HTML")
    url = f"{BASE_URL}/api/v1/consultas/fd/telp/{dni}"

    try:
        data = await asyncio.wait_for(consultar_api_get(url), timeout=10)
    except asyncio.TimeoutError:
        return await m.edit_text("❌ <b>Timeout:</b> La API tardó más de 10s.", parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        return await m.edit_text(f"❌ <b>Error:</b>\n<code>{e}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)

    if "error" in data:
        return await m.edit_text(f"❌ <b>Error API:</b>\n<code>{data['error']}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)
    if not data.get("success"):
        return await m.edit_text("❌ No se encontraron líneas telefónicas.", parse_mode="HTML", reply_markup=BTN_VOLVER)

    registrar_consulta(usuarios, user_id, "telp")

    resultado = data["data"]
    lineas = resultado.get("lineas", [])

    texto = f"""╔═══════════════════╗
📡 <b>TELP • SISTEMA</b>
╚═══════════════════╝

🟢 <b>ESTADO DEL SISTEMA</b>
➜ ONLINE

🆔 <b>DNI</b>
➜ <code>{dni}</code>

📞 <b>LÍNEAS ENCONTRADAS</b>
➜ <code>{resultado.get('lineas_encontradas', len(lineas))}</code>

⚡━━━━━━━━━━━━━━━━━━━━━━⚡
"""
    for i, linea in enumerate(lineas, 1):
        periodo = linea.get("periodo","-")
        if len(periodo)==6: periodo = f"{periodo[4:]}/{periodo[:4]}"
        texto += f"""
📱 <b>LÍNEA {i}</b>
☎️ <b>NÚMERO</b> ➜ <code>{linea.get('telefono','-')}</code>
📡 <b>OPERADOR</b> ➜ <code>{linea.get('operador','-')}</code>
🏢 <b>EMPRESA</b> ➜ <code>{linea.get('empresa','-')}</code>
📅 <b>PERIODO</b> ➜ <code>{periodo}</code>
⚡━━━━━━━━━━━━━━━━━━━━━━⚡
"""
    texto += f"""
💳 <b>Créditos restantes:</b> <code>{usuarios[user_id]['creditos']}</code>
🚀 <b>Consulta completada correctamente</b>
⚜️ <b>SISTEMAS DATA PERU</b>
"""
    await m.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
async def dni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, res_cred = await validar_creditos(user_id, "dni", usuarios)
    if not ok:
        return await update.message.reply_text(res_cred)

    if not context.args:
        return await update.message.reply_text(
            "❌ <b>Uso correcto:</b>\n<code>/dni 12345678</code>",
            parse_mode="HTML", reply_markup=BTN_VOLVER
        )

    dni_num = context.args[0]
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await update.message.reply_text(
            "❌ El DNI debe contener exactamente 8 dígitos.",
            parse_mode="HTML", reply_markup=BTN_VOLVER
        )

    m = await update.message.reply_text(f"🔎 Consultando DNI <code>{dni_num}</code>...", parse_mode="HTML")
    url = f"{BASE_URL}/api/v1/consultas/fd/dni/{dni_num}"

    try:
        data = await asyncio.wait_for(consultar_api_get(url), timeout=10)
    except asyncio.TimeoutError:
        return await m.edit_text("❌ <b>Timeout:</b> La API tardó más de 10s. Intenta de nuevo.", parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        return await m.edit_text(f"❌ <b>Error de conexión:</b>\n<code>{e}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)

    if "error" in data:
        return await m.edit_text(f"❌ <b>Error API:</b>\n<code>{data['error']}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)

    if not data.get("success"):
        return await m.edit_text(f"❌ {data.get('message','DNI no encontrado')}", parse_mode="HTML", reply_markup=BTN_VOLVER)

    # SOLO AQUÍ SE DESCUENTA - API respondió success:true
    registrar_consulta(usuarios, user_id, "dni")

    res = data.get("data", {})
    d = res.get("dni", {})
    n = res.get("nacimiento", {})
    dom = res.get("domicilio", {})
    info = res.get("informacion_general", {})

    texto = f"""╔═════════════════════╗
🪪 <b>DNI • SISTEMA</b>
╚═════════════════════╝

🟢 <b>ESTADO</b>
➜ ONLINE

🆔 <b>DNI</b>
➜ <code>{d.get('completo','-')}</code>

👤 <b>TITULAR</b>
➜ <code>{res.get('nombres','')} {res.get('apellidos','')}</code>

⚧️ <b>GÉNERO</b>
➜ <code>{res.get('genero','-')}</code>

📅 <b>NACIMIENTO</b>
➜ <code>{n.get('fecha','-')} | {n.get('edad','-')}</code>

🏠 <b>DOMICILIO</b>
➜ <code>{dom.get('direccion','-')} - {dom.get('distrito','-')}</code>

👨 <b>PADRE</b>
➜ <code>{info.get('padre','-')}</code>

👩 <b>MADRE</b>
➜ <code>{info.get('madre','-')}</code>

⚡━━━━━━━━━━━━━━━━━━━━━━⚡
💳 <b>Créditos restantes:</b> <code>{usuarios[user_id]['creditos']}</code>

⚜️ <b>SISTEMAS DATA PERU</b>
📡 Powered by CODART X API V1
"""
    await m.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

async def dnit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, res_cred = await validar_creditos(user_id, "dnit", usuarios)
    if not ok:
        return await update.message.reply_text(res_cred)

    if not context.args:
        return await update.message.reply_text("❌ <b>Uso:</b> <code>/dnit 12345678</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)

    dni_num = context.args[0]
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await update.message.reply_text("❌ El DNI debe contener exactamente 8 dígitos.", parse_mode="HTML", reply_markup=BTN_VOLVER)

    m = await update.message.reply_text(f"🔎 Consultando DNI-T <code>{dni_num}</code>...", parse_mode="HTML")
    url = f"{BASE_URL}/api/v1/consultas/fd/dnit/{dni_num}"

    try:
        data = await asyncio.wait_for(consultar_api_get(url), timeout=10)
    except asyncio.TimeoutError:
        return await m.edit_text("❌ <b>Timeout:</b> La API tardó más de 10s.", parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        return await m.edit_text(f"❌ <b>Error de conexión:</b>\n<code>{e}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)

    if "error" in data:
        return await m.edit_text(f"❌ <b>Error API:</b>\n<code>{data['error']}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)
    if not data.get("success"):
        return await m.edit_text(f"❌ {data.get('message','DNI no encontrado')}", parse_mode="HTML", reply_markup=BTN_VOLVER)

    registrar_consulta(usuarios, user_id, "dnit")

    res = data.get("data", {})
    d = res.get("dni", {}); n = res.get("nacimiento", {}); dom = res.get("domicilio", {})
    info = res.get("informacion_general", {}); images = res.get("images", [])

    texto = f"""╔═════════════════════╗
💳 <b>DNI-T • SISTEMA</b>
╚═════════════════════╝

🟢 <b>ESTADO</b>
➜ ONLINE

🆔 <b>DNI</b>
➜ <code>{d.get('completo','-')}</code>

👤 <b>NOMBRE</b>
➜ <code>{res.get('nombres','')} {res.get('apellidos','')}</code>

📅 <b>NACIMIENTO</b>
➜ <code>{n.get('fecha','-')} | {n.get('edad','-')}</code>

🏠 <b>DIRECCIÓN</b>
➜ <code>{dom.get('direccion','-')}</code>

📚 <b>EDUCACIÓN</b>
➜ <code>{info.get('nivel_educativo','-')}</code>

💍 <b>ESTADO CIVIL</b>
➜ <code>{info.get('estado_civil','-')}</code>

⚡━━━━━━━━━━━━━━━━━━━━━━⚡
💳 <b>Créditos:</b> <code>{usuarios[user_id]['creditos']}</code>

⚜️ <b>SISTEMAS DATA PERU</b>
"""
    await m.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

    for i, img_data in enumerate(images, 1):
        try:
            b64 = img_data.get('data_uri','').split(',')[1]
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=io.BytesIO(base64.b64decode(b64)), caption=f"Foto {i}")
        except:
            pass

async def hsoat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios(); asegurar_usuario(usuarios, user_id, update.effective_user)
    ok, msg = await validar_creditos(user_id, "hsoat", usuarios)
    if not ok: return await update.message.reply_text(msg)
    if not context.args: return await update.message.reply_text("Uso: /hsoat ABC123", reply_markup=BTN_VOLVER)
    placa = context.args[0].upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{5,8}", placa):
        return await update.message.reply_text("❌ Placa inválida.", reply_markup=BTN_VOLVER)
    m = await update.message.reply_text(f"🔎 Consultando HSOAT de {placa}... -{PRECIOS['hsoat']} creditos")
    url = f"{BASE_URL}/api/v1/consultas/fd/hsoat/{placa}"
    data = await consultar_api_get(url)
    if "error" in data: return await m.edit_text(f"Error: {data['error']}")
    if not data.get("success"): return await m.edit_text(f"Error: {data.get('message','Placa no encontrada')}")
    res = data.get("data", {})
    placa_data = res.get("placa"); cantidad = res.get("cantidad_registros"); historial = res.get("historial", [])
    registrar_consulta(usuarios, user_id, "hsoat")
    texto = f"""[#BOT DATA] ➾ HSOAT
[🚗] PLACA ➾ {placa_data}
[📊] REGISTROS ➾ {cantidad}"""
    for i, h in enumerate(historial, 1):
        texto += f"\n\n--- SOAT {i} ---\n[🏢] COMPAÑIA ➾ {h.get('compania')}\n[✅] ESTADO ➾ {h.get('estado')}\n[📄] PÓLIZA ➾ {h.get('poliza')}"
    texto += f"\n\n💰 Creditos: {usuarios[user_id]['creditos']}"
    await m.edit_text(texto)

async def denpla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios(); asegurar_usuario(usuarios, user_id, update.effective_user)
    ok, msg = await validar_creditos(user_id, "denpla", usuarios)
    if not ok: return await update.message.reply_text(msg)
    if not context.args: return await update.message.reply_text("Uso: /denpla ABC123", reply_markup=BTN_VOLVER)
    placa = context.args[0].upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{5,8}", placa):
        return await update.message.reply_text("❌ Placa inválida.", reply_markup=BTN_VOLVER)
    m = await update.message.reply_text(f"🔎 Consultando DENUNCIAS de {placa}... -{PRECIOS['denpla']} creditos")
    url = f"{BASE_URL}/api/v1/consultas/fd/denpla/{placa}"
    data = await consultar_api_get(url)
    if "error" in data: return await m.edit_text(f"Error: {data['error']}")
    if not data.get("success"): return await m.edit_text(f"Error: {data.get('message','Placa no encontrada')}")
    res = data.get("data", {})
    placa_data = res.get("placa"); cantidad = res.get("cantidad_denuncias"); denuncias = res.get("denuncias", [])
    registrar_consulta(usuarios, user_id, "denpla")
    texto = f"""[#BOT DATA] ➾ DENUNCIAS POLICIALES
[🚗] PLACA ➾ {placa_data}
[🚨] TOTAL DENUNCIAS ➾ {cantidad}"""
    for d in denuncias:
        texto += f"\n\n--- DENUNCIA {d.get('numero')} ---\n[📌] TIPO ➾ {d.get('tipo')}\n[🏛️] COMISARIA ➾ {d.get('comisaria')}"
    texto += f"\n\n💰 Creditos: {usuarios[user_id]['creditos']}"
    await m.edit_text(texto)

async def suel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, msg = await validar_creditos(user_id, "suel", usuarios)
    if not ok:
        return await update.message.reply_text(msg)

    if not context.args:
        return await update.message.reply_text("Uso: /suel 12345678", reply_markup=BTN_VOLVER)

    dni_num = context.args[0]
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await update.message.reply_text("❌ El DNI debe contener exactamente 8 dígitos.", reply_markup=BTN_VOLVER)

    m = await update.message.reply_text("💼 Consultando sueldos... -4 créditos")

    url = f"{BASE_URL}/api/v1/consultas/fd/suel/{dni_num}"
    data = await consultar_api_get(url)

    if "error" in data:
        return await m.edit_text(f"❌ Error: {data['error']}")

    if not data.get("success"):
        return await m.edit_text(f"❌ Error: {data.get('message','No se encontraron registros')}")

    res = data.get("data", {})
    sueldos = res.get("sueldos", [])

    if not sueldos:
        return await m.edit_text("❌ No se encontraron registros.")

    registrar_consulta(usuarios, user_id, "suel")

    texto = f"""<b>[#BOT DATA] ➾ CONSULTA SUELDOS</b>

🆔 <b>DNI:</b> {res.get("consulta")}
📋 <b>Total Registros:</b> {res.get("total_registros")}

"""

    for i, s in enumerate(sueldos, start=1):
        texto += f"""
<b>══════════════════════</b>
<b>Registro {i}</b>

🏢 <b>Empresa:</b> {s.get("empresa")}
🪪 <b>RUC:</b> {s.get("ruc")}
📅 <b>Periodo:</b> {s.get("periodo")}
👔 <b>Situación:</b> {s.get("situacion")}
💰 <b>Sueldo:</b> {s.get("sueldo")}
"""

    texto += f"""

══════════════════════
💳 <b>Créditos:</b> {usuarios[user_id]["creditos"]}
"""

    await m.edit_text(texto, parse_mode="HTML")


#... aqui van tus otros comandos: placa, agv, denuncia, nm, telpcel


async def consulta_generica(update: Update, context: ContextTypes.DEFAULT_TYPE, comando, uso, validador=None):
    """Motor común para comandos que comparten el formato GET /consultas/fd/... ."""
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    asegurar_usuario(usuarios, user_id, update.effective_user)

    ok, msg = await validar_creditos(user_id, comando, usuarios)
    if not ok:
        return await update.message.reply_text(msg)

    if validador is None:
        if not context.args:
            return await update.message.reply_text(f"❌ Uso: <code>{uso}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)
        valor = " ".join(context.args).strip()
    else:
        valor = validador(context.args)
        if valor is None:
            return await update.message.reply_text(f"❌ Uso: <code>{uso}</code>", parse_mode="HTML", reply_markup=BTN_VOLVER)

    progreso = await update.message.reply_text(
        f"🔎 Consultando <code>{escapar_html(valor)}</code>...",
        parse_mode="HTML",
        reply_markup=BTN_VOLVER,
    )

    data = await consultar_api_get(url_endpoint(comando, valor))
    if "error" in data:
        return await editar_mensaje(
            progreso,
            f"❌ <b>Error API:</b> <code>{escapar_html(data.get('error'))}</code>",
            reply_markup=BTN_VOLVER,
        )
    if not data.get("success"):
        return await editar_mensaje(
            progreso,
            f"❌ {escapar_html(data.get('message', 'No se encontraron resultados.'))}",
            reply_markup=BTN_VOLVER,
        )

    registrar_consulta(usuarios, user_id, comando)
    resultado = data.get("data", data)

    # Mantiene toda la información recibida, pero sin romper HTML de Telegram.
    try:
        serializado = json.dumps(resultado, ensure_ascii=False, indent=2, default=str)
    except Exception:
        serializado = str(resultado)

    texto = (
        f"╔════════════════════╗\n"
        f"      🔎 {comando.upper()}\n"
        f"╚════════════════════╝\n\n"
        f"✅ Consulta completada\n\n"
        f"<pre>{escapar_html(serializado)}</pre>\n\n"
        f"💳 <b>Créditos restantes:</b> <code>{usuarios[user_id]['creditos']}</code>\n"
        f"📊 <b>Consultas:</b> <code>{usuarios[user_id]['consultas']}</code>"
    )
    return await editar_mensaje(progreso, texto, reply_markup=BTN_VOLVER)


def validar_ruc(args):
    if len(args) != 1:
        return None
    valor = args[0].strip()
    return valor if valor.isdigit() and len(valor) == 11 else None


def validar_placa(args):
    if len(args) != 1:
        return None
    valor = args[0].strip().upper()
    return valor if re.fullmatch(r"[A-Z0-9]{5,8}", valor) else None


def validar_nombres(args):
    if len(args) < 2:
        return None
    return " ".join(args).strip()


async def ruc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await consulta_generica(update, context, "ruc", "/ruc 20538856674", validar_ruc)


async def placa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await consulta_generica(update, context, "placa", "/placa ABC123", validar_placa)


async def nm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await consulta_generica(update, context, "nm", "/nm JUAN QUISPE", validar_nombres)


async def denuncia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Alias compatible con el nombre singular anunciado en PRECIOS.
    return await den(update, context)

# ===== MAIN =====
def main():
    if not BOT_TOKEN:
        raise RuntimeError("Falta BOT_TOKEN en las variables de entorno.")
    if not API_TOKEN:
        print("⚠️ Advertencia: API_TOKEN no está configurado. Las consultas API fallarán.")

    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Botones / menús.
    application.add_handler(CallbackQueryHandler(button_handler))

    # Comandos generales.
    handlers = [
        ("start", start),
        ("cmds", cmds),
        ("register", register),
        ("me", me),
        ("buy", buy),
        ("staff", staff),
        ("addcreditos", addcreditos),
        ("quitarcrd", quitarcrd),
        ("dni", dni),
        ("dnit", dnit),
        ("ruc", ruc),
        ("nm", nm),
        ("agv", agv),
        ("hsoat", hsoat),
        ("placa", placa),
        ("denpla", denpla),
        ("telp", telp),
        ("telpcel", telpcel),
        ("den", den),
        ("denuncia", denuncia),
        ("denuncias", denuncias),
        ("suel", suel),
        ("facial", facial),
    ]

    for nombre, funcion in handlers:
        application.add_handler(CommandHandler(nombre, funcion))

    # /facial también funciona como caption de una foto.
    application.add_handler(
        MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/facial(?:@\w+)?(?:\s|$)"), facial)
    )

    # Errores no controlados: se registran sin tumbar el bot.
    async def error_handler(update, context):
        print(f"[ERROR] {context.error!r}")

    application.add_error_handler(error_handler)

    print("Bot iniciado v3.0 — comandos registrados correctamente.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
