import os
import re
import base64
import tempfile
import requests
import logging
import threading
import html
import asyncio
import urllib.parse
import secrets
import ipaddress
import time
import io
import threading
from datetime import datetime
from urllib.parse import quote_plus
from aiohttp import web
from functools import wraps
from dotenv import load_dotenv
from flask import Flask, request, abort
from werkzeug.middleware.proxy_fix import ProxyFix

import telebot
from telebot import types
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    MessageEntity,
    CopyTextButton
)

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
NUMLOOKUP_API_KEY = os.getenv("NUMLOOKUP_API_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CODART_TOKEN = os.getenv("CODART_TOKEN")
API_BASE = os.getenv(
    "API_BASE",
    "https://api-codart.cgrt.org/api/v1/consultas/fd"
).rstrip("/")
IPAPI_TOKEN = os.getenv("IPAPI_TOKEN", "")
COSTO_IP = int(os.getenv("COSTO_IP", "10"))
PORT = int(os.getenv("PORT", 10000))
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
C4B_ALLOWED_USER_IDS = {
    int(value.strip()) for value in os.getenv("C4B_ALLOWED_USER_IDS", "").split(",")
    if value.strip().isdigit()
}

link_foto = "https://ibb.co/35xtQyVz"
PAYMENT_WEB_URL = "t.me/zxxxxx_michi_xxxxxx"
ADMIN_PAYMENT_ID = 8271623149

DATOS_PAGO = {
    "yape_numero": "",
    "cci": "NO DISPONIBLE POR EL MOMENTO",
    "titular": "fernando marcelo infante villalobos",
    "qr_url": "https://ibb.co/35xtQyVz"
}

COSTO_PANEL = 80

PANEL_URL = os.getenv(
    "PANEL_URL",
    "https://lytzthebest.com"
).strip()

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN faltante")

# Encabezados de las APIs
HEADERS_JSON = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CODART_TOKEN}"
}

HEADERS_FACIAL = {
    "Authorization": f"Bearer {CODART_TOKEN}",
    "Accept": "application/json"
}

# Inicialización de TeleBot
bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=True
)

# Servidor Flask para comprobar el estado del servicio
app_flask = Flask(__name__)
app_flask.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024
app_flask.wsgi_app = ProxyFix(app_flask.wsgi_app, x_for=1, x_proto=1, x_host=1)

@app_flask.route('/')
def health():
    return "⚜️ SPECTER OS v2.5 - ONLINE", 200

@app_flask.route('/health')
def health_check():
    return {"status": "ok"}, 200

def run_flask():
    app_flask.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        use_reloader=False
    )

# ================================================================
# BASE DE DATOS MONGODB - USUARIOS, CRÉDITOS Y TOKENS
# ================================================================
# Render Environment:
# MONGO_URI=mongodb+srv://...
# MONGO_DB_NAME=specter_data
# SQLITE_IMPORT_PATH=/var/data/bot.db  (opcional, para migrar la SQLite anterior)
# La SQLite original NO se elimina ni se modifica durante la importación.

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError

MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "specter_data").strip()
SQLITE_IMPORT_PATH = os.getenv("SQLITE_IMPORT_PATH", "").strip()
CREDITOS_INICIALES = 2

if not MONGO_URI:
    raise SystemExit("MONGO_URI faltante: configura MongoDB Atlas en las variables de Render.")

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=15000,
    connectTimeoutMS=15000,
    socketTimeoutMS=20000,
    retryWrites=True,
)
mongo_client.admin.command("ping")
mongo_db = mongo_client[MONGO_DB_NAME]
usuarios_col = mongo_db["usuarios"]
localizar_tokens_col = mongo_db["localizar_tokens"]
migraciones_col = mongo_db["migraciones"]

# Índices únicos para evitar usuarios o tokens duplicados.
usuarios_col.create_index("user_id", unique=True, name="user_id_unique")
localizar_tokens_col.create_index("token", unique=True, name="token_unique")
localizar_tokens_col.create_index("expires_at", expireAfterSeconds=0, name="tokens_ttl")

_db_lock = threading.RLock()

COSTOS = {
    "dni": 5,
    "c4": 10,
    "c4b": 10,
  "c4i": 10,
  "actnac": 60,
    "COSTO_IP": 10,
    "localizar": 10,
    "reaccion": 20,
    "dnit": 6,
    "dnivel": 10,
    "dniv": 10,
    "nm": 5,
    "agv": 20,
    "telcel": 20,
    "facial": 60,
    "dir": 6,
    "suel": 6,
    "den": 20,
    "denuncias": 60,
    "pla": 4,
    "rqh": 40,
    "denpla": 40,
    "hsoat": 10,
    "revtec": 10,
    "plat": 6,
  "actmat": 60,
  "actdef": 60,
  "telp": 20,
  "sentinel": 60,
  "pro": 10,
  "ruc": 10,
  "rucs": 60,
  "vec": 6,
  "mpla": 60,
  "migra": 60,
  "migrace": 60,

}

COSTO_IP = COSTOS["COSTO_IP"]
COSTO_LOCALIZAR = COSTOS["localizar"]

# Estado temporal compatible con TeleBot.
user_data = {}


def migrar_sqlite_a_mongo():
    """Importa usuarios y tokens de la SQLite antigua una sola vez.
    La base SQLite de origen se conserva intacta.
    """
    if not SQLITE_IMPORT_PATH:
        logger.info("Migración SQLite omitida: SQLITE_IMPORT_PATH no configurada.")
        return
    if not os.path.isfile(SQLITE_IMPORT_PATH):
        logger.warning("No existe SQLITE_IMPORT_PATH=%s; no se importó SQLite.", SQLITE_IMPORT_PATH)
        return

    migration_id = "sqlite_to_mongodb_v1:" + os.path.abspath(SQLITE_IMPORT_PATH)
    if migraciones_col.find_one({"_id": migration_id}):
        logger.info("Esta SQLite ya fue importada anteriormente.")
        return

    import sqlite3
    conn = sqlite3.connect(SQLITE_IMPORT_PATH, timeout=30)
    try:
        tablas = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        usuarios_importados = 0
        tokens_importados = 0

        if "usuarios" in tablas:
            columnas = {row[1] for row in conn.execute("PRAGMA table_info(usuarios)")}
            if {"user_id", "creditos"}.issubset(columnas):
                celular_expr = "celular" if "celular" in columnas else "''"
                filas = conn.execute(
                    f"SELECT user_id, creditos, {celular_expr} FROM usuarios"
                ).fetchall()
                for user_id, creditos, celular in filas:
                    usuarios_col.update_one(
                        {"user_id": int(user_id)},
                        {"$setOnInsert": {
                            "user_id": int(user_id),
                            "creditos": int(creditos or 0),
                            "celular": celular or "",
                            "created_at": int(time.time()),
                        }},
                        upsert=True,
                    )
                    usuarios_importados += 1

        if "localizar_tokens" in tablas:
            filas = conn.execute(
                "SELECT token, user_id, chat_id, created_at, expires_at, used "
                "FROM localizar_tokens"
            ).fetchall()
            for token, user_id, chat_id, created_at, expires_at, used in filas:
                localizar_tokens_col.update_one(
                    {"token": str(token)},
                    {"$setOnInsert": {
                        "token": str(token),
                        "user_id": int(user_id),
                        "chat_id": int(chat_id),
                        "created_at": int(created_at),
                        "expires_at": int(expires_at),
                        "used": int(used),
                    }},
                    upsert=True,
                )
                tokens_importados += 1

        migraciones_col.insert_one({
            "_id": migration_id,
            "source": os.path.abspath(SQLITE_IMPORT_PATH),
            "usuarios_encontrados": usuarios_importados,
            "tokens_encontrados": tokens_importados,
            "completed_at": int(time.time()),
        })
        logger.info(
            "Migración SQLite→MongoDB completada: %s usuarios y %s tokens. "
            "La SQLite original se conservó.",
            usuarios_importados, tokens_importados
        )
    finally:
        conn.close()


def init_db():
    """Comprueba MongoDB e importa la SQLite antigua si se configuró."""
    mongo_client.admin.command("ping")
    migrar_sqlite_a_mongo()
    logger.info("MongoDB inicializado: base=%s", MONGO_DB_NAME)


def get_creditos(uid: int):
    uid = int(uid)
    usuarios_col.update_one(
        {"user_id": uid},
        {"$setOnInsert": {
            "user_id": uid,
            "creditos": CREDITOS_INICIALES,
            "celular": "",
            "created_at": int(time.time()),
        }},
        upsert=True,
    )
    usuario = usuarios_col.find_one({"user_id": uid}, {"creditos": 1})
    if usuario is None:
        raise RuntimeError("No se pudo leer el saldo desde MongoDB.")
    return int(usuario.get("creditos", CREDITOS_INICIALES))


def cargar_usuarios():
    return {
        str(doc["user_id"]): {
            "creditos": int(doc.get("creditos", CREDITOS_INICIALES)),
            "celular": doc.get("celular", "") or "",
        }
        for doc in usuarios_col.find({}, {"user_id": 1, "creditos": 1, "celular": 1})
    }


def set_creditos(uid, nuevo):
    uid, nuevo = int(uid), int(nuevo)
    usuarios_col.update_one(
        {"user_id": uid},
        {"$set": {"creditos": nuevo}, "$setOnInsert": {
            "user_id": uid, "celular": "", "created_at": int(time.time())
        }},
        upsert=True,
    )
    return nuevo


def agregar_creditos(uid, cantidad):
    uid, cantidad = int(uid), int(cantidad)
    if cantidad <= 0:
        raise ValueError("La cantidad de créditos debe ser mayor que cero.")
    doc = usuarios_col.find_one_and_update(
        {"user_id": uid},
        {
            "$inc": {"creditos": cantidad},
            "$setOnInsert": {
                "user_id": uid, "celular": "", "created_at": int(time.time())
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["creditos"])


def descontar(uid, cant):
    uid, cant = int(uid), int(cant)
    if cant < 0:
        raise ValueError("La cantidad a descontar no puede ser negativa.")
    # Crear el usuario si todavía no existe, sin reiniciar saldos existentes.
    usuarios_col.update_one(
        {"user_id": uid},
        {"$setOnInsert": {
            "user_id": uid, "creditos": CREDITOS_INICIALES,
            "celular": "", "created_at": int(time.time())
        }},
        upsert=True,
    )
    doc = usuarios_col.find_one_and_update(
        {"user_id": uid, "creditos": {"$gte": cant}},
        {"$inc": {"creditos": -cant}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise ValueError("Saldo insuficiente para completar el descuento.")
    return int(doc["creditos"])


def reembolsar(uid, cant):
    uid, cant = int(uid), int(cant)
    if cant < 0:
        raise ValueError("La cantidad a reembolsar no puede ser negativa.")
    doc = usuarios_col.find_one_and_update(
        {"user_id": uid},
        {
            "$inc": {"creditos": cant},
            "$setOnInsert": {
                "user_id": uid, "celular": "", "created_at": int(time.time())
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["creditos"])


# ESCAPE PARA HTML
def esc(t):
    if t is None:
        return "—"

    return html.escape(str(t).strip(), quote=False)


def clean(v):
    if (
        v is None
        or str(v).strip() == ""
        or str(v).lower() == "none"
    ):
        return "—"

    return str(v).strip()


def validar_dni(d):
    return bool(re.match(r"^\d{8}$", str(d)))


def validar_cel(n):
    return bool(re.match(r"^\d{9}$", str(n)))


def validar_placa(p):
    return bool(
        re.match(r"^[A-Z0-9]{6,7}$", str(p).upper())
    )


def decodificar_imagen(uri):
    try:
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        return base64.b64decode(b64)
    except Exception:
        return None


def teclado_volver():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ VOLVER AL SISTEMA",
                callback_data="teclado_volver"
            )
        ]
    ])


def footer_creditos(ctx):
    # Acepta tanto el diccionario de TeleBot como un objeto
    # compatible con el antiguo ctx.user_data.
    if isinstance(ctx, dict):
        datos = ctx
    else:
        datos = getattr(ctx, "user_data", {})

    costo = datos.get("costo_actual", 0)
    saldo = datos.get("saldo_actual", 0)

    return (
        f"\n\n▰▰▰▰▰▰▰▰▰▰▰▰\n"
        f"💠 COSTO: {costo} CRD | 🔋 SALDO: {saldo} CRD\n"
        f"⚜️ SPECTER_OS v2.5"
)
def con_creditos(costo: int):
    def decorator(func):
        @wraps(func)
        def wrapper(message_or_call, *args, **kwargs):
            uid = None
            descontado = False

            try:
                # Compatible con mensajes y callback queries de TeleBot
                if isinstance(message_or_call, types.CallbackQuery):
                    uid = message_or_call.from_user.id
                    target = message_or_call.message
                else:
                    uid = message_or_call.from_user.id
                    target = message_or_call

                saldo = get_creditos(uid)

                if saldo < costo:
                    if target:
                        bot.send_message(
                            target.chat.id,
                            premium(
                                f"⚠️ ACCESO DENEGADO\n\n"
                                f"💳 SALDO: {saldo} CRD\n"
                                f"💸 REQUERIDO: {costo} CRD\n\n"
                                f"🔋 /buy para recargar"
                            ),
                            reply_markup=teclado_volver()
                        )
                    return

                nuevo = descontar(uid, costo)
                descontado = True

                # Estado independiente para cada usuario
                datos = user_data.setdefault(uid, {})
                datos["costo_actual"] = costo
                datos["saldo_actual"] = nuevo

                return func(message_or_call, *args, **kwargs)

            except Exception as e:
                logger.error(
                    f"decorador {e}",
                    exc_info=True
                )

                # Reembolsar si hubo un error después del descuento
                if uid is not None and descontado:
                    reembolsar(uid, costo)

                try:
                    if isinstance(message_or_call, types.CallbackQuery):
                        target = message_or_call.message
                    else:
                        target = message_or_call

                    if target:
                        bot.send_message(
                            target.chat.id,
                            premium(
                                f"⚠️ SYSTEM ERROR: {esc(str(e))}"
                            ),
                            reply_markup=teclado_volver()
                        )
                except Exception:
                    pass

        return wrapper
    return decorator

def detectar_pais(numero):
    prefijos = {
        "51": "PE",    # Perú
        "1": "US",     # Estados Unidos
        "34": "ES",    # España
        "52": "MX",    # México
        "54": "AR",    # Argentina
        "55": "BR",    # Brasil
        "56": "CL",    # Chile
        "57": "CO",    # Colombia
        "58": "VE",    # Venezuela
        "593": "EC",   # Ecuador
        "591": "BO",   # Bolivia
        "595": "PY",   # Paraguay
        "598": "UY",   # Uruguay
        "507": "PA",   # Panamá
        "506": "CR",   # Costa Rica
        "502": "GT",   # Guatemala
        "503": "SV",   # El Salvador
        "504": "HN",   # Honduras
        "505": "NI",   # Nicaragua
        "509": "HT",   # Haití
        "880": "BD"    # Bangladés
    }

    for prefijo in sorted(prefijos, key=len, reverse=True):
        if numero.startswith(prefijo):
            return prefijos[prefijo]

    return "PE"


@bot.message_handler(commands=["val"])
def val_command(message):
    try:
        partes = message.text.split()

        if len(partes) < 2:
            bot.reply_to(
                message,
                "📱 <b>SPECTER • NUMBER VALIDATOR</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "⚡ <b>USO DEL COMANDO</b>\n\n"
                "<code>/val 01613950781 BD</code>\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🆓 <i>CONSULTA GRATUITA</i>",
                parse_mode="HTML"
            )
            return

        numero = partes[1].strip().replace("+", "")

        if not numero.isdigit():
            bot.reply_to(
                message,
                "❌ <b>NÚMERO NO VÁLIDO</b>\n"
                "Introduce solamente números y, si corresponde, el prefijo internacional.",
                parse_mode="HTML"
            )
            return

        if len(numero) < 7 or len(numero) > 15:
            bot.reply_to(
                message,
                "❌ El número debe tener entre 7 y 15 dígitos.",
                parse_mode="HTML"
            )
            return

        if not NUMLOOKUP_API_KEY:
            bot.reply_to(
                message,
                "❌ <b>API NO CONFIGURADA</b>\n"
                "Configura NUMLOOKUP_API_KEY en las variables de entorno de Render.",
                parse_mode="HTML"
            )
            return

        country_code = (
            partes[2].upper()
            if len(partes) >= 3
            else detectar_pais(numero)
        )

        estado = bot.reply_to(
            message,
            "🔎 <b>SPECTER • NUMBER VALIDATOR</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ Analizando número...\n"
            "🌐 Conectando con NumLookup API.",
            parse_mode="HTML"
        )

        url = f"https://api.numlookupapi.com/v1/validate/{numero}"

        respuesta = requests.get(
            url,
            params={
                "apikey": NUMLOOKUP_API_KEY,
                "country_code": country_code
            },
            timeout=20
        )

        if respuesta.status_code != 200:
            bot.edit_message_text(
                "❌ <b>ERROR EN LA CONSULTA</b>\n\n"
                f"HTTP: <code>{respuesta.status_code}</code>\n"
                "Verifica tu API key o los límites de consulta.",
                message.chat.id,
                estado.message_id,
                parse_mode="HTML"
            )
            return

        datos = respuesta.json()

        if not isinstance(datos, dict):
            raise ValueError("Respuesta inesperada de NumLookup")

        def obtener(campo):
            valor = datos.get(campo)
            return html.escape(str(valor)) if valor not in (None, "") else "No disponible"

        valido = datos.get("valid")
        resultado = (
            "✅ VÁLIDO" if valido is True
            else "❌ NO VÁLIDO" if valido is False
            else "⚠️ NO DETERMINADO"
        )

        texto = (
            "📱 <b>SPECTER • NUMBER VALIDATOR</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🔢 Número: <code>{obtener('number')}</code>\n"
            f"📌 Estado: <b>{resultado}</b>\n"
            f"🌍 País: {obtener('country_name')}\n"
            f"🏷️ Código: <code>{obtener('country_code')}</code>\n"
            f"📞 Prefijo: {obtener('country_prefix')}\n"
            f"📡 Operador: {obtener('carrier')}\n"
            f"📋 Tipo de línea: {obtener('line_type')}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🆓 <i>CONSULTA GRATUITA • SPECTER</i>"
        )

        bot.edit_message_text(
            texto,
            message.chat.id,
            estado.message_id,
            parse_mode="HTML"
        )

    except requests.exceptions.Timeout:
        bot.reply_to(message, "⏱️ Tiempo de espera agotado. Inténtalo nuevamente.")

    except requests.exceptions.RequestException:
        logging.exception("Error de conexión en /val")
        bot.reply_to(message, "❌ Error de conexión con NumLookup.")

    except Exception:
        logging.exception("Error en comando /val")
        bot.reply_to(message, "❌ Ocurrió un error al procesar la consulta.")
# ==========================================
# /ACTMAT - ACTA MATRIMONIO - 60 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["actmat"])
@con_creditos(costo=60)
def actmat_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        # Formato: /actmat DNI_ESPOSA DNI_ESPOSO FECHA
        if len(args) != 4:
            reembolsar(usuario.id, 60)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - ACTA MATRIMONIO</b>\n\n"
                    "Uso: <code>/actmat DNI_ESPOSA DNI_ESPOSO FECHA</code>\n"
                    "Ej: <code>/actmat 12345678 87654321 10/10/2010</code>\n\n"
                    "Fecha: DD-MM-AAAA o DD/MM/AAAA\n"
                    "💰 Costo: 60 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        _, dni_esposa, dni_esposo, fecha_raw = args

        if not validar_dni(dni_esposa) or not validar_dni(dni_esposo):
            reembolsar(usuario.id, 60)
            bot.reply_to(message, premium("❌ DNI de esposa o esposo inválido (8 dígitos)."), parse_mode="HTML")
            return

        # Validar fecha DD-MM-AAAA o DD/MM/AAAA
        fecha = fecha_raw.replace("/", "-")
        if not re.match(r"^\d{2}-\d{2}-\d{4}$", fecha):
            reembolsar(usuario.id, 60)
            bot.reply_to(message, premium("❌ Fecha inválida. Usa DD-MM-AAAA o DD/MM/AAAA\nEj: 10/10/2010"), parse_mode="HTML")
            return

        progreso = bot.reply_to(
            message,
            premium(
                f"⚜️ <b>SPECTER DATA • ACTA MATRIMONIO</b>\n\n"
                f"👩 Esposa: <code>{dni_esposa}</code>\n"
                f"👨 Esposo: <code>{dni_esposo}</code>\n"
                f"📅 Fecha: {esc(fecha_raw)}\n\n"
                f"🔎 Consultando..."
            ),
            parse_mode="HTML",
        )

        # ENDPOINT ACTMAT CON QUERY PARAMS
        endpoint = f"/actmat?dni_esposa={dni_esposa}&dni_esposo={dni_esposo}&fecha={fecha}"
        resultado, error = codart_get(endpoint)

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>ACTA MATRIMONIO FALLIDA</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 60 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        texto = (
            "⚜️ <b>ACTA DE MATRIMONIO</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"👩 Esposa: <code>{esc(data.get('dni_esposa', dni_esposa))}</code>\n"
            f"👨 Esposo: <code>{esc(data.get('dni_esposo', dni_esposo))}</code>\n"
            f"📅 Fecha: <b>{esc(data.get('fecha_matrimonio', fecha_raw))}</b>\n"
            "━━━━━━━━━━━━━━\n"
            "✅ Consulta completada\n"
            "💰 60 CRD"
        )
        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

        # PDF
        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")
        if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
            try:
                pdf_bytes = base64.b64decode(data_uri.split(",",1)[1], validate=True)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")
                
                nombre = os.path.basename(str(documento.get("nombre", f"ACTA-MATRIMONIO-{dni_esposa}-{dni_esposo}.pdf")))
                nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>ACTA MATRIMONIO {dni_esposa} - {dni_esposo}</b>"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF ACTMAT")
                bot.send_message(message.chat.id, f"⚠️ PDF falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF")

    except Exception as e:
        logger.exception("actmat")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error ACTMAT: {e} - Reembolsado 60")
        except: pass
# ==========================================
# /ACTDEF - ACTA DEFUNCION - 60 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["actdef"])
@con_creditos(costo=60)
def actdef_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 3:
            reembolsar(usuario.id, 60)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - ACTA DEFUNCIÓN</b>\n\n"
                    "Uso: <code>/actdef DNI FECHA</code>\n"
                    "Ej: <code>/actdef 12345678 10/10/2010</code>\n\n"
                    "Fecha: DD-MM-AAAA o DD/MM/AAAA\n"
                    "💰 Costo: 60 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        _, dni, fecha_raw = args

        if not validar_dni(dni):
            reembolsar(usuario.id, 60)
            bot.reply_to(message, premium("❌ DNI inválido (8 dígitos)."), parse_mode="HTML")
            return

        fecha = fecha_raw.replace("/", "-")
        if not re.match(r"^\d{2}-\d{2}-\d{4}$", fecha):
            reembolsar(usuario.id, 60)
            bot.reply_to(message, premium("❌ Fecha inválida. Usa DD-MM-AAAA o DD/MM/AAAA"), parse_mode="HTML")
            return

        progreso = bot.reply_to(
            message,
            premium(
                f"⚜️ <b>SPECTER DATA • ACTA DEFUNCIÓN</b>\n\n"
                f"🪪 DNI: <code>{dni}</code>\n"
                f"📅 Fecha defunción: {esc(fecha_raw)}\n\n"
                f"🔎 Consultando..."
            ),
            parse_mode="HTML",
        )

        # ENDPOINT ACTDEF
        endpoint = f"/actdef?dni={dni}&fecha={fecha}"
        resultado, error = codart_get(endpoint)

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>ACTA DEFUNCIÓN FALLIDA</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 60 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        texto = (
            "⚜️ <b>ACTA DE DEFUNCIÓN</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🪪 DNI: <code>{esc(data.get('dni', dni))}</code>\n"
            f"📅 Fecha: <b>{esc(data.get('fecha_defuncion', fecha_raw))}</b>\n"
            "━━━━━━━━━━━━━━\n"
            "✅ Consulta completada\n"
            "💰 60 CRD"
        )
        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")
        if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
            try:
                pdf_bytes = base64.b64decode(data_uri.split(",",1)[1], validate=True)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")
                
                nombre = os.path.basename(str(documento.get("nombre", f"ACTA-DEFUNCION-{dni}.pdf")))
                nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>ACTA DEFUNCIÓN {dni}</b>"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF ACTDEF")
                bot.send_message(message.chat.id, f"⚠️ PDF falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF")

    except Exception as e:
        logger.exception("actdef")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error ACTDEF: {e} - Reembolsado 60")
        except: pass
# ==========================================
# /TELP - TELEFONOS POR DNI - 20 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["telp"])
@con_creditos(costo=20)
def telp_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_dni(args[1]):
            reembolsar(usuario.id, 20)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - TELP</b>\n\n"
                    "Uso: <code>/telp 12345678</code>\n\n"
                    "💰 Costo: 20 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        dni = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>TELP • LÍNEAS TELEFÓNICAS</b>\n🔎 Consultando {dni}..."),
            parse_mode="HTML",
        )

        resultado, error = codart_get(f"/telp/{dni}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 20)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>TELP FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 20 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        lineas = data.get("lineas") or []
        total = data.get("lineas_encontradas", len(lineas))

        if not lineas:
            texto = (
                f"⚜️ <b>TELP • {dni}</b>\n"
                "━━━━━━━━━━━━━━\n"
                "❌ No se encontraron líneas.\n"
                "💰 20 CRD"
            )
            bot.edit_message_text(
                premium(texto),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        # Armar lista bonita
        lista_texto = ""
        for i, l in enumerate(lineas[:15], 1):  # max 15 para que no se corte por límite telegram
            tel = esc(str(l.get("telefono","")))
            operador = esc(str(l.get("operador","")))
            empresa = esc(str(l.get("empresa","")))
            periodo = esc(str(l.get("periodo","")))
            lista_texto += f"{i}. <code>{tel}</code> - {operador} ({periodo})\n   └ {empresa}\n"

        texto = (
            f"⚜️ <b>TELP • LÍNEAS ENCONTRADAS</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🪪 DNI: <code>{esc(dni)}</code>\n"
            f"📱 Total: <b>{total}</b> líneas\n"
            f"━━━━━━━━━━━━━━\n"
            f"{lista_texto}"
            f"━━━━━━━━━━━━━━\n"
            f"✅ 20 CRD"
        )

        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

    except Exception as e:
        logger.exception("telp")
        try:
            reembolsar(message.from_user.id, 20)
            bot.reply_to(message, f"❌ Error TELP: {e} - Reembolsado 20")
        except: pass
# ==========================================
# /SENTINEL - REPORTE SENTINEL - 60 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["sentinel"])
@con_creditos(costo=60)
def sentinel_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_dni(args[1]):
            reembolsar(usuario.id, 60)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - SENTINEL</b>\n\n"
                    "Uso: <code>/sentinel 12345678</code>\n\n"
                    "💰 Costo: 60 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        dni = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>SENTINEL • REPORTE CREDITICIO</b>\n🔎 Consultando {dni}..."),
            parse_mode="HTML",
        )

        resultado, error = codart_get(f"/sentinel/{dni}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>SENTINEL FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 60 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        texto = (
            "⚜️ <b>SENTINEL • REPORTE</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🪪 DNI: <code>{esc(data.get('documento_consultado', data.get('consulta', dni)))}</code>\n"
            f"🔍 Tipo: {esc(data.get('tipo_consulta','dni'))}\n"
            "━━━━━━━━━━━━━━\n"
            "✅ Reporte generado\n"
            "💰 60 CRD"
        )
        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")
        if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
            try:
                pdf_bytes = base64.b64decode(data_uri.split(",",1)[1], validate=True)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")
                
                nombre = os.path.basename(str(documento.get("nombre", f"REPORTE-SENTINEL-{dni}.pdf")))
                nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>SENTINEL {dni}</b>\n⚜️ Reporte crediticio completo"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF SENTINEL")
                bot.send_message(message.chat.id, f"⚠️ PDF Sentinel falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF")

    except Exception as e:
        logger.exception("sentinel")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error SENTINEL: {e} - Reembolsado 60")
        except: pass
# ==========================================
# /PRO - PROPIEDADES SUNARP - 10 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["pro"])
@con_creditos(costo=10)
def pro_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_dni(args[1]):
            reembolsar(usuario.id, 10)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - PRO</b>\n\n"
                    "Uso: <code>/pro 12345678</code>\n\n"
                    "💰 Costo: 10 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        dni = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>PRO • PROPIEDADES SUNARP</b>\n🔎 Consultando {dni}..."),
            parse_mode="HTML",
        )

        resultado, error = codart_get(f"/pro/{dni}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 10)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>PRO FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 10 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        props = data.get("propiedades") or []
        total = data.get("cantidad_propiedades", len(props))
        consulta = data.get("consulta", dni)

        if not props:
            texto = (
                f"⚜️ <b>PRO • {esc(consulta)}</b>\n"
                "━━━━━━━━━━━━━━\n"
                "❌ Sin propiedades registradas.\n"
                "💰 10 CRD"
            )
            bot.edit_message_text(
                premium(texto),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        # Armar lista
        lista = ""
        for i, p in enumerate(props[:10], 1):  # Telegram limita 4096 chars, cortamos a 10
            registro = esc(str(p.get("registro","") or ""))
            libro = esc(str(p.get("libro","") or ""))
            partida = esc(str(p.get("partida","") or ""))
            oficina = esc(str(p.get("oficina","") or ""))
            estado = esc(str(p.get("estado","") or ""))
            direccion = esc(str(p.get("direccion","") or ""))
            placa = esc(str(p.get("placa","") or ""))

            lista += f"<b>{i}. {registro}</b>\n"
            lista += f"   📖 {libro}\n"
            lista += f"   🔖 Partida: <code>{partida}</code> - {oficina} - {estado}\n"
            if direccion and direccion != "None":
                lista += f"   📍 {direccion}\n"
            if placa and placa != "None":
                lista += f"   🚗 Placa: <code>{placa}</code>\n"
            lista += "\n"

        # Si hay más de 10, avisar
        extra = ""
        if total > 10:
            extra = f"\n... y {total-10} más (total {total})"

        texto = (
            f"⚜️ <b>PRO • PROPIEDADES SUNARP</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🪪 DNI: <code>{esc(consulta)}</code>\n"
            f"🏠 Total: <b>{total}</b> propiedades\n"
            f"━━━━━━━━━━━━━━\n"
            f"{lista}"
            f"{extra}\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ 10 CRD"
        )

        # Telegram max 4096, si se pasa cortar
        if len(texto) > 4000:
            texto = texto[:3900] + "\n... (cortado)"

        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

    except Exception as e:
        logger.exception("pro")
        try:
            reembolsar(message.from_user.id, 10)
            bot.reply_to(message, f"❌ Error PRO: {e} - Reembolsado 10")
        except: pass
# ==========================================
# /RUC - PROPIEDADES POR RUC - 10 CRD - ABIERTO
# ==========================================
def validar_ruc(ruc: str) -> bool:
    return bool(re.match(r"^\d{11}$", ruc or ""))

@bot.message_handler(commands=["ruc"])
@con_creditos(costo=10)
def ruc_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_ruc(args[1]):
            reembolsar(usuario.id, 10)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - RUC</b>\n\n"
                    "Uso: <code>/ruc 20123456789</code>\n"
                    "RUC: 11 dígitos\n"
                    "💰 Costo: 10 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        ruc = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>RUC • PROPIEDADES SUNARP</b>\n🔎 Consultando {ruc}..."),
            parse_mode="HTML",
        )

        # OJO: endpoint es /pro/ruc/{ruc}
        resultado, error = codart_get(f"/pro/ruc/{ruc}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 10)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>RUC FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 10 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        props = data.get("propiedades") or []
        total = data.get("cantidad_propiedades", len(props))
        consulta = data.get("consulta", ruc)

        if not props:
            texto = (
                f"⚜️ <b>RUC • {esc(consulta)}</b>\n"
                "━━━━━━━━━━━━━━\n"
                "❌ Sin propiedades registradas.\n"
                "💰 10 CRD"
            )
            bot.edit_message_text(
                premium(texto),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        lista = ""
        for i, p in enumerate(props[:10], 1):
            registro = esc(str(p.get("registro","") or ""))
            libro = esc(str(p.get("libro","") or ""))
            partida = esc(str(p.get("partida","") or ""))
            oficina = esc(str(p.get("oficina","") or ""))
            estado = esc(str(p.get("estado","") or ""))
            direccion = esc(str(p.get("direccion","") or ""))
            placa = esc(str(p.get("placa","") or ""))

            lista += f"<b>{i}. {registro}</b>\n"
            lista += f"   📖 {libro}\n"
            lista += f"   🔖 Partida: <code>{partida}</code> - {oficina} - {estado}\n"
            if direccion and direccion != "None":
                lista += f"   📍 {direccion}\n"
            if placa and placa != "None":
                lista += f"   🚗 Placa: <code>{placa}</code>\n"
            lista += "\n"

        extra = f"\n... y {total-10} más (total {total})" if total > 10 else ""

        texto = (
            f"⚜️ <b>RUC • PROPIEDADES SUNARP</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🏢 RUC: <code>{esc(consulta)}</code>\n"
            f"🚗 Total: <b>{total}</b> propiedades/vehículos\n"
            f"━━━━━━━━━━━━━━\n"
            f"{lista}"
            f"{extra}\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ 10 CRD"
        )

        if len(texto) > 4000:
            texto = texto[:3900] + "\n... (cortado)"

        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

    except Exception as e:
        logger.exception("ruc")
        try:
            reembolsar(message.from_user.id, 10)
            bot.reply_to(message, f"❌ Error RUC: {e} - Reembolsado 10")
        except: pass
# ==========================================
# /RUCS - REPORTE SENTINEL RUC - 60 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["rucs"])
@con_creditos(costo=60)
def rucs_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_ruc(args[1]):
            reembolsar(usuario.id, 60)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - RUCS</b>\n\n"
                    "Uso: <code>/rucs 20123456789</code>\n"
                    "RUC: 11 dígitos\n"
                    "💰 Costo: 60 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        ruc = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>RUCS • SENTINEL EMPRESAS</b>\n🔎 Consultando RUC {ruc}..."),
            parse_mode="HTML",
        )

        # ENDPOINT: /sentinel/ruc/{ruc}
        resultado, error = codart_get(f"/sentinel/ruc/{ruc}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>RUCS FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 60 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        texto = (
            "⚜️ <b>RUCS • REPORTE SENTINEL RUC</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🏢 RUC: <code>{esc(data.get('documento_consultado', data.get('consulta', ruc)))}</code>\n"
            f"🔍 Tipo: {esc(data.get('tipo_consulta','ruc'))}\n"
            "━━━━━━━━━━━━━━\n"
            "✅ Reporte generado\n"
            "💰 60 CRD"
        )
        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")
        if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
            try:
                pdf_bytes = base64.b64decode(data_uri.split(",",1)[1], validate=True)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")
                
                nombre = os.path.basename(str(documento.get("nombre", f"REPORTE-SENTINEL-{ruc}.pdf")))
                nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>SENTINEL RUC {ruc}</b>\n⚜️ Reporte crediticio empresa"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF RUCS")
                bot.send_message(message.chat.id, f"⚠️ PDF RUCS falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF")

    except Exception as e:
        logger.exception("rucs")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error RUCS: {e} - Reembolsado 60")
        except: pass
# ==========================================
# /VEC - VEHICULOS POR DNI - 6 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["vec"])
@con_creditos(costo=6)
def vec_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_dni(args[1]):
            reembolsar(usuario.id, 6)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - VEC</b>\n\n"
                    "Uso: <code>/vec 12345678</code>\n\n"
                    "💰 Costo: 6 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        dni = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>VEC • VEHÍCULOS</b>\n🔎 Consultando {dni}..."),
            parse_mode="HTML",
        )

        resultado, error = codart_get(f"/vec/{dni}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 6)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>VEC FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 6 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        vehiculos = data.get("vehiculos") or []
        total = data.get("cantidad_vehiculos", len(vehiculos))
        consulta = data.get("consulta", dni)

        if not vehiculos:
            texto = (
                f"⚜️ <b>VEC • {esc(consulta)}</b>\n"
                "━━━━━━━━━━━━━━\n"
                "❌ Sin vehículos registrados.\n"
                "💰 6 CRD"
            )
            bot.edit_message_text(
                premium(texto),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        lista = ""
        for i, v in enumerate(vehiculos, 1):
            placa = esc(str(v.get("placa","")))
            marca = esc(str(v.get("marca","")))
            modelo = esc(str(v.get("modelo","")))
            titular = esc(str(v.get("titular","")))
            anio_fab = esc(str(v.get("ano_fabricacion","")))
            anio_comp = esc(str(v.get("ano_compra","")))

            lista += (
                f"<b>{i}. {placa} - {marca} {modelo}</b>\n"
                f"   👤 {titular}\n"
                f"   📅 Fab: {anio_fab} | Compra: {anio_comp}\n\n"
            )

        texto = (
            f"⚜️ <b>VEC • VEHÍCULOS SUNARP</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🪪 DNI: <code>{esc(consulta)}</code>\n"
            f"🚗 Total: <b>{total}</b> vehículo(s)\n"
            f"━━━━━━━━━━━━━━\n"
            f"{lista}"
            f"━━━━━━━━━━━━━━\n"
            f"✅ 6 CRD"
        )

        if len(texto) > 4000:
            texto = texto[:3900] + "\n... (cortado)"

        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

    except Exception as e:
        logger.exception("vec")
        try:
            reembolsar(message.from_user.id, 6)
            bot.reply_to(message, f"❌ Error VEC: {e} - Reembolsado 6")
        except: pass
# ==========================================
# /MPLA - META PLACA COMPLETA - 60 CRD - ABIERTO
# ==========================================
def validar_placa(placa: str) -> bool:
    return bool(re.match(r"^[A-Z0-9]{6,7}$", (placa or "").upper().strip()))

@bot.message_handler(commands=["mpla"])
@con_creditos(costo=60)
def mpla_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_placa(args[1]):
            reembolsar(usuario.id, 60)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - MPLA</b>\n\n"
                    "Uso: <code>/mpla ABC123</code>\n"
                    "Placa: 6 o 7 caracteres (ABC123 / ABC-123)\n\n"
                    "💰 Costo: 60 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        placa = args[1].upper().strip().replace("-", "")
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>MPLA • META PLACA</b>\n🔎 Consultando {placa}..."),
            parse_mode="HTML",
        )

        resultado, error = codart_get(f"/mpla/{placa}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>MPLA FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 60 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        
        texto = (
            "⚜️ <b>MPLA • REPORTE VEHICULAR</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🚗 Placa: <code>{esc(data.get('placa', placa))}</code>\n"
            f"🏍️ Marca: {esc(str(data.get('marca','')))} {esc(str(data.get('modelo','')))}\n"
            f"📅 Año: {esc(str(data.get('anio','')))} | 🎨 Color: {esc(str(data.get('color','')))}\n"
            f"📋 Clase: {esc(str(data.get('clase','')))}\n"
            "━━━━━━━━━━━━━━\n"
            f"👤 Propietario: {esc(str(data.get('propietario','')))}\n"
            f"🪪 Doc: <code>{esc(str(data.get('documento_propietario','')))}</code>\n"
            "━━━━━━━━━━━━━━\n"
            f"🛡️ SOAT: {esc(str(data.get('soat','')))} - {esc(str(data.get('aseguradora','')))}\n"
            f"📅 Vigencia SOAT: {esc(str(data.get('vigencia_soat','')))}\n"
            f"🔧 Revisión: {esc(str(data.get('revision_tecnica','')))}\n"
            f"📅 Vigencia RTV: {esc(str(data.get('vigencia_revision','')))}\n"
            "━━━━━━━━━━━━━━\n"
            "✅ 60 CRD"
        )

        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")
        if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
            try:
                pdf_bytes = base64.b64decode(data_uri.split(",",1)[1], validate=True)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")
                
                nombre = os.path.basename(str(documento.get("nombre", f"META-PLACA-{placa}.pdf")))
                nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>MPLA {placa}</b>\n⚜️ Reporte vehicular completo"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF MPLA")
                bot.send_message(message.chat.id, f"⚠️ PDF MPLA falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF - datos en texto enviados")

    except Exception as e:
        logger.exception("mpla")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error MPLA: {e} - Reembolsado 60")
        except: pass
# ==========================================
# /MIGRA - REPORTE MIGRACIONES - 60 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["migra"])
@con_creditos(costo=60)
def migra_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_dni(args[1]):
            reembolsar(usuario.id, 60)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - MIGRA</b>\n\n"
                    "Uso: <code>/migra 12345678</code>\n\n"
                    "💰 Costo: 60 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        dni = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>MIGRA • MIGRACIONES</b>\n🔎 Consultando {dni}..."),
            parse_mode="HTML",
        )

        resultado, error = codart_get(f"/migra/{dni}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>MIGRA FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 60 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        texto = (
            "⚜️ <b>MIGRA • REPORTE MIGRACIONES</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🪪 DNI: <code>{esc(data.get('documento_numero', data.get('dni', dni)))}</code>\n"
            f"📋 Tipo Doc: {esc(str(data.get('tipo_documento','')))}\n"
            f"👤 Nombres: {esc(str(data.get('nombres','')))}\n"
            f"👤 Apellidos: {esc(str(data.get('apellidos','')))}\n"
            f"📅 Nacimiento: {esc(str(data.get('fecha_nacimiento','')))}\n"
            f"🌎 Nacionalidad: {esc(str(data.get('nacionalidad','')))}\n"
            f"💼 Profesión: {esc(str(data.get('profesion','')))}\n"
            "━━━━━━━━━━━━━━\n"
            "✅ Reporte generado\n"
            "💰 60 CRD"
        )
        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")
        if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
            try:
                pdf_bytes = base64.b64decode(data_uri.split(",",1)[1], validate=True)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")
                
                nombre = os.path.basename(str(documento.get("nombre", f"MIGRACIONES-{dni}.pdf")))
                nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>MIGRA {dni}</b>\n⚜️ Reporte migraciones"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF MIGRA")
                bot.send_message(message.chat.id, f"⚠️ PDF MIGRA falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF")

    except Exception as e:
        logger.exception("migra")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error MIGRA: {e} - Reembolsado 60")
        except: pass
# ==========================================
# /MIGRACE - MIGRACIONES CE - 60 CRD - ABIERTO
# ==========================================
def validar_ce(ce: str) -> bool:
    return bool(re.match(r"^\d{9}$", ce or ""))

@bot.message_handler(commands=["migrace"])
@con_creditos(costo=60)
def migrace_command(message):
    try:
        usuario = message.from_user
        if not usuario: return

        args = (message.text or "").split()
        if len(args) != 2 or not validar_ce(args[1]):
            reembolsar(usuario.id, 60)
            bot.reply_to(
                message,
                premium(
                    "⚠️ <b>FORMATO INCORRECTO - MIGRACE</b>\n\n"
                    "Uso: <code>/migrace 000000000</code>\n"
                    "CE: 9 dígitos\n\n"
                    "💰 Costo: 60 créditos"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver(),
            )
            return

        ce = args[1]
        progreso = bot.reply_to(
            message,
            premium(f"⚜️ <b>MIGRACE • MIGRACIONES CE</b>\n🔎 Consultando {ce}..."),
            parse_mode="HTML",
        )

        resultado, error = codart_get(f"/migrace/{ce}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            detalle = error or (resultado or {}).get("message", "Error API")
            bot.edit_message_text(
                premium(f"❌ <b>MIGRACE FALLÓ</b>\n\n{esc(str(detalle)[:500])}\n\n💳 Reembolsado 60 CRD"),
                chat_id=progreso.chat.id,
                message_id=progreso.message_id,
                parse_mode="HTML",
            )
            return

        data = resultado.get("data") or {}
        texto = (
            "⚜️ <b>MIGRACE • REPORTE MIGRACIONES CE</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🪪 CE: <code>{esc(data.get('documento_numero', data.get('ce', ce)))}</code>\n"
            f"📋 Tipo: {esc(str(data.get('tipo_documento','Carnet de Extranjería')))}\n"
            f"👤 Nombres: {esc(str(data.get('nombres','')))}\n"
            f"👤 Apellidos: {esc(str(data.get('apellidos','')))}\n"
            f"📅 Nacimiento: {esc(str(data.get('fecha_nacimiento','')))}\n"
            f"🌎 Nacionalidad: {esc(str(data.get('nacionalidad','')))}\n"
            f"🛂 Calidad: {esc(str(data.get('calidad_migratoria','')))}\n"
            f"📍 Domicilio: {esc(str(data.get('domicilio','')))}\n"
            f"🏢 Oficina: {esc(str(data.get('oficina_migratoria','')))}\n"
            "━━━━━━━━━━━━━━\n"
            "✅ Reporte generado\n"
            "💰 60 CRD"
        )
        bot.edit_message_text(
            premium(texto),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )

        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")
        if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
            try:
                pdf_bytes = base64.b64decode(data_uri.split(",",1)[1], validate=True)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")
                
                nombre = os.path.basename(str(documento.get("nombre", f"MIGRACIONES-CE-{ce}.pdf")))
                nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>MIGRACE CE {ce}</b>\n⚜️ Reporte migraciones CE"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF MIGRACE")
                bot.send_message(message.chat.id, f"⚠️ PDF MIGRACE falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF")

    except Exception as e:
        logger.exception("migrace")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error MIGRACE: {e} - Reembolsado 60")
        except: pass
# ==========================================
# /ACTNAC - ACTA NACIMIENTO - 60 CRD - ABIERTO
# ==========================================
@bot.message_handler(commands=["actnac"])
@con_creditos(costo=60)
def actnac_command(message):
    try:
        usuario = message.from_user
        if not usuario: return
        
        args = (message.text or "").split()
        if len(args) != 2 or not validar_dni(args[1]):
            reembolsar(usuario.id, 60)
            bot.reply_to(message, premium("⚠️ Uso: <code>/actnac 12345678</code>\n💰 60 CRD"), parse_mode="HTML")
            return

        dni = args[1]
        progreso = bot.reply_to(message, premium(f"⚜️ <b>ACTA NACIMIENTO</b>\n🔎 Consultando {dni}..."), parse_mode="HTML")

        resultado, error = codart_get(f"/actnac/{dni}")

        if error or not resultado or not resultado.get("success"):
            reembolsar(usuario.id, 60)
            bot.edit_message_text(premium(f"❌ Falló ACTNAC\n{esc(str(error or resultado.get('message'))[:400])}\n💳 Reembolsado 60 CRD"), chat_id=progreso.chat.id, message_id=progreso.message_id, parse_mode="HTML")
            return

        data = resultado.get("data") or {}
        documento = data.get("documento") or {}
        data_uri = documento.get("data_uri", "")

        # Texto OK
        texto = (
            f"⚜️ <b>ACTA DE NACIMIENTO</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🪪 DNI: <code>{esc(data.get('dni', dni))}</code>\n"
            f"👤 {esc(data.get('nombres',''))} {esc(data.get('apellidos',''))}\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ 60 CRD descontados"
        )
        bot.edit_message_text(premium(texto), chat_id=progreso.chat.id, message_id=progreso.message_id, parse_mode="HTML", reply_markup=teclado_volver())

        # ENVIO PDF - SIN CRASHEAR WHATSAPP
        if data_uri.startswith("data:application/pdf;base64,"):
            try:
                b64 = data_uri.split(",",1)[1]
                pdf_bytes = base64.b64decode(b64, validate=True)
                
                # Validación básica
                if len(pdf_bytes) < 100 or not pdf_bytes.startswith(b"%PDF"):
                    raise ValueError("PDF inválido")

                nombre = f"ACTA-NACIMIENTO-{dni}.pdf"
                archivo = io.BytesIO(pdf_bytes)
                archivo.name = nombre

                bot.send_document(
                    message.chat.id,
                    document=archivo,
                    caption=premium(f"📄 <b>ACTA NACIMIENTO {dni}</b>"),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.exception("PDF ACTNAC")
                bot.send_message(message.chat.id, f"⚠️ Acta encontrada pero PDF falló: {e}")
        else:
            bot.send_message(message.chat.id, "⚠️ API respondió sin PDF")

    except Exception as e:
        logger.exception("actnac")
        try:
            reembolsar(message.from_user.id, 60)
            bot.reply_to(message, f"❌ Error: {e} - Reembolsado")
        except: pass
# ==========================================
# COMANDO /C4 - SPECTER DATA
# API CODART - SIN RESTRICCIONES
# ==========================================

@bot.message_handler(commands=["c4"])
@con_creditos(costo=COSTOS["c4"])
def c4_command(message):

    usuario = message.from_user

    if not usuario:
        return

    args = (message.text or "").split()

    if len(args) != 2 or not validar_dni(args[1]):
        reembolsar(usuario.id, COSTOS["c4"])
        bot.reply_to(
            message,
            premium(
                "⚠️ <b>FORMATO INCORRECTO</b>\n\n"
                "Uso: <code>/c4 12345678</code>\n\n"
                "💰 Costo: 10 créditos"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[1]

    if not CODART_TOKEN:
        reembolsar(usuario.id, COSTOS["c4"])
        bot.reply_to(
            message,
            premium("❌ CODART_TOKEN no está configurado."),
            parse_mode="HTML"
        )
        return

    progreso = bot.reply_to(
        message,
        premium(
            "⚜️ <b>SPECTER DATA</b>\n\n"
            "🔎 Procesando consulta C4...\n"
            "⏳ Conectando con CODART API..."
        ),
        parse_mode="HTML"
    )

    # Consulta API
    resultado, error = codart_get(f"/c4a/{dni}")

    if error:
        reembolsar(usuario.id, COSTOS["c4"])
        bot.edit_message_text(
            premium(
                "❌ <b>ERROR DE CONEXIÓN</b>\n\n"
                f"<code>{esc(str(error)[:500])}</code>\n\n"
                "💳 Créditos reembolsados."
            ),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML"
        )
        return

    if not resultado or not resultado.get("success"):
        reembolsar(usuario.id, COSTOS["c4"])
        error_api = (resultado or {}).get("message", "No se pudo completar la consulta.")
        bot.edit_message_text(
            premium(
                "❌ <b>CONSULTA FALLIDA</b>\n\n"
                f"{esc(str(error_api))}\n\n"
                "💳 Créditos reembolsados."
            ),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML"
        )
        return

    data = resultado.get("data") or {}

    texto = (
        "⚜️ <b>SPECTER DATA - C4</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🪪 DNI: <code>{esc(str(data.get('dni', 'N/D')))}</code>\n"
        f"👤 Nombres: <b>{esc(str(data.get('nombres', 'N/D')))}</b>\n"
        f"👤 Apellidos: <b>{esc(str(data.get('apellidos', 'N/D')))}</b>\n"
        f"⚧ Género: {esc(str(data.get('genero', 'N/D')))}\n"
        f"🎂 Edad: {esc(str(data.get('edad', 'N/D')))}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💠 Consulta completada."
    )

    bot.edit_message_text(
        premium(texto),
        chat_id=progreso.chat.id,
        message_id=progreso.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )

    documento = data.get("documento") or {}
    data_uri = documento.get("data_uri", "")

    if data_uri.startswith("data:application/pdf;base64,"):
        try:
            contenido_base64 = data_uri.split(",",1)[1]
            pdf_bytes = base64.b64decode(contenido_base64, validate=True)

            if not pdf_bytes.startswith(b"%PDF"):
                raise ValueError("El contenido recibido no es un PDF válido.")
            if len(pdf_bytes) > 10 * 1024 * 1024:
                raise ValueError("El PDF supera el límite de 10 MB.")

            nombre = os.path.basename(str(documento.get("nombre", "C4.pdf")))
            nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)

            bot.send_document(
                message.chat.id,
                document=io.BytesIO(pdf_bytes),
                visible_file_name=nombre,
                caption=premium("📄 <b>DOCUMENTO C4</b>\n⚜️ SPECTER DATA"),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.exception("Error procesando PDF C4")
            bot.send_message(
                message.chat.id,
                premium(f"⚠️ Consulta completada, pero no se pudo procesar el PDF.\n<code>{esc(str(e)[:300])}</code>"),
                parse_mode="HTML"
            )

# ==========================================
# COMANDO /C4B - FICHA C4 BLANCO - PUBLICO
# ==========================================
@bot.message_handler(commands=["c4b"])
@con_creditos(costo=COSTOS["c4b"])
def c4b_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()
    if len(args) != 2 or not validar_dni(args[1]):
        reembolsar(usuario.id, COSTOS["c4b"])
        bot.reply_to(
            message,
            premium(
                "⚠️ <b>FORMATO INCORRECTO</b>\n\n"
                "Uso: <code>/c4b 12345678</code>\n\n"
                "💰 Costo: 10 créditos"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver(),
        )
        return

    if not CODART_TOKEN:
        reembolsar(usuario.id, COSTOS["c4b"])
        bot.reply_to(
            message,
            premium("❌ CODART_TOKEN no está configurado en Render."),
            parse_mode="HTML",
        )
        return

    dni = args[1]
    progreso = bot.reply_to(
        message,
        premium(
            "⚜️ <b>SPECTER DATA • C4B</b>\n\n"
            "🔎 Procesando ficha C4 blanco...\n"
            "⏳ Conectando con CODART API..."
        ),
        parse_mode="HTML",
    )

    resultado, error = codart_get(f"/c4b/{dni}")

    if error or not isinstance(resultado, dict) or not resultado.get("success"):
        reembolsar(usuario.id, COSTOS["c4b"])
        detalle = error or (resultado or {}).get(
            "message", "La API no pudo completar la consulta."
        )
        bot.edit_message_text(
            premium(
                "❌ <b>CONSULTA C4B FALLIDA</b>\n\n"
                f"{esc(str(detalle)[:500])}\n\n"
                "💳 Se reembolsaron los 10 créditos."
            ),
            chat_id=progreso.chat.id,
            message_id=progreso.message_id,
            parse_mode="HTML",
        )
        return

    # ESTO YA ESTÁ DENTRO BIEN INDENTADO
    data = resultado.get("data") or {}
    texto = (
        "⚜️ <b>SPECTER DATA • C4B</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🪪 DNI: <code>{esc(data.get('dni', dni))}</code>\n"
        f"👤 Nombres: <b>{esc(data.get('nombres'))}</b>\n"
        f"👤 Apellidos: <b>{esc(data.get('apellidos'))}</b>\n"
        f"⚧ Género: {esc(data.get('genero'))}\n"
        f"🎂 Edad: {esc(data.get('edad'))}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💠 Consulta C4B completada.\n"
        "💰 Costo: 10 CRD"
    )
    bot.edit_message_text(
        premium(texto),
        chat_id=progreso.chat.id,
        message_id=progreso.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver(),
    )
    
    # ENVIO PDF IGUAL QUE ANTES...
    documento = data.get("documento") or {}
    data_uri = documento.get("data_uri", "")
    if isinstance(data_uri, str) and data_uri.startswith("data:application/pdf;base64,"):
        try:
            contenido_base64 = data_uri.split(",", 1)[1]
            pdf_bytes = base64.b64decode(contenido_base64, validate=True)
            if not pdf_bytes.startswith(b"%PDF"):
                raise ValueError("El contenido recibido no es un PDF válido.")
            if len(pdf_bytes) > 10 * 1024 * 1024:
                raise ValueError("El PDF supera el límite de 10 MB.")

            nombre = os.path.basename(str(documento.get(
                "nombre", f"FICHA-C4-BLANCO-{dni}.pdf"
            )))
            nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)
            archivo = io.BytesIO(pdf_bytes)
            archivo.name = nombre
            bot.send_document(
                message.chat.id,
                document=archivo,
                caption=premium("📄 <b>FICHA C4B • DOCUMENTO PDF</b>\n⚜️ SPECTER DATA"),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.exception("Error procesando el PDF de C4B")
            bot.send_message(
                message.chat.id,
                premium(
                    "⚠️ La consulta se completó, pero no se pudo enviar el PDF.\n"
                    f"<code>{esc(str(exc)[:250])}</code>"
                ),
                parse_mode="HTML",
            )
    else:
        bot.send_message(
            message.chat.id,
            "⚠️ La API respondió correctamente, pero no incluyó el PDF esperado.",
        )
# ================================================================
# LOCALIZAR - ENLACE TEMPORAL + CONSENTIMIENTO EXPLÍCITO
# Usa la MISMA DB_PATH y el mismo sistema de créditos global.
# La cámara solo se activa tras aceptar y conceder permiso al navegador.
# La foto solo se envía después de una captura y confirmación explícitas.
# ================================================================

LOCALIZAR_TTL = 10 * 60


def crear_token_localizar(user_id: int, chat_id: int) -> str:
    token = secrets.token_urlsafe(32)
    ahora = int(time.time())
    localizar_tokens_col.insert_one({
        "token": token,
        "user_id": int(user_id),
        "chat_id": int(chat_id),
        "created_at": ahora,
        "expires_at": ahora + LOCALIZAR_TTL,
        "used": 0,
    })
    return token


def obtener_token_localizar(token: str):
    row = localizar_tokens_col.find_one(
        {"token": token},
        {"user_id": 1, "chat_id": 1, "expires_at": 1, "used": 1},
    )
    if not row:
        return None
    return (
        int(row["user_id"]),
        int(row["chat_id"]),
        int(row["expires_at"]),
        int(row.get("used", 0)),
    )


def consumir_token_localizar(token: str):
    ahora = int(time.time())
    row = localizar_tokens_col.find_one_and_update(
        {
            "token": token,
            "used": 0,
            "expires_at": {"$gte": ahora},
        },
        {"$set": {"used": 1, "used_at": ahora}},
        return_document=ReturnDocument.BEFORE,
    )
    if not row:
        return None
    return int(row["user_id"]), int(row["chat_id"])


def _localizar_ip_cliente() -> str:
    # ProxyFix toma la IP reenviada por el proxy de Render.
    candidata = (request.remote_addr or "").strip()
    try:
        ip_obj = ipaddress.ip_address(candidata)
        return str(ip_obj) if ip_obj.is_global else "No disponible"
    except ValueError:
        return "No disponible"


@app_flask.route("/localizar/<token>", methods=["GET"])
def pagina_localizar(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{30,100}", token or ""):
        abort(404)

    registro = obtener_token_localizar(token)
    if not registro:
        return "This link is invalid.", 404

    _user_id, _chat_id, expires_at, used = registro
    if used or int(time.time()) > int(expires_at):
        return "This link has expired or has already been used.", 410

    # La información solo se transmite al bot después del consentimiento.
    # La cámara no se inicia hasta que el visitante pulse el botón y el navegador
    # conceda su permiso; la captura requiere otra acción explícita.
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Secure Device Verification</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b1220;color:#eef4ff;font-family:Arial,sans-serif;padding:20px}}
.card{{width:100%;max-width:440px;background:#172338;border:1px solid #293b59;
border-radius:20px;padding:26px;box-shadow:0 18px 55px #0008}}
.logo{{font-size:12px;letter-spacing:2px;color:#8fb5ff;font-weight:bold}}
h1{{font-size:25px;margin:14px 0 10px}}
p{{color:#bdcbe0;line-height:1.55;font-size:14px}}
ul{{padding-left:20px;color:#dce7f7;line-height:1.9;font-size:14px}}
.notice{{font-size:12px;color:#9fb0c9;background:#101a2b;padding:12px;border-radius:10px}}
button{{width:100%;border:0;border-radius:11px;padding:14px;margin-top:12px;
font-size:15px;font-weight:bold;color:white;background:#2563eb;cursor:pointer}}
button.secondary{{background:#34445f}}
button:disabled{{opacity:.55;cursor:not-allowed}}
video,img{{width:100%;border-radius:12px;margin-top:12px;background:#0a1020}}
.hidden{{display:none}}
.status{{font-size:13px;color:#9fc1ff;min-height:20px;margin-top:12px}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">SPECTER • SECURE VERIFICATION</div>
  <h1>Verify your device</h1>
  <p>
    This page can share your public IP address and basic browser/device
    information with the Telegram user who created this link.
    No GPS location is requested.
  </p>
  <ul>
    <li>Public IP address and browser details</li>
    <li>Device and operating-system information reported by your browser</li>
    <li>Battery level, only if your browser supports this feature</li>
    <li>Camera photo only if you separately allow camera access, take a photo,
        and choose to send it</li>
  </ul>
  <div class="notice">
    By continuing, you agree to send the listed technical information.
    Camera access is optional. Your browser will display its own camera
    permission prompt. You can continue without a photo.
  </div>

  <button id="agree">I AGREE &amp; CONTINUE</button>

  <section id="cameraSection" class="hidden">
    <p><b>Optional camera photo</b><br>
    Select “Enable camera” to open the browser's camera permission prompt.
    Your camera preview will be visible before you take a photo.</p>
    <button id="enableCamera" class="secondary">ENABLE CAMERA</button>
    <video id="video" autoplay playsinline muted class="hidden"></video>
    <canvas id="canvas" class="hidden"></canvas>
    <img id="preview" class="hidden" alt="Photo preview">
    <button id="capture" class="hidden">TAKE PHOTO</button>
    <button id="send" class="hidden">SEND REPORT</button>
    <button id="withoutPhoto" class="secondary">CONTINUE WITHOUT PHOTO</button>
  </section>
  <div id="status" class="status" role="status"></div>
</div>

<script>
const token = "{html.escape(token)}";
const agree = document.getElementById("agree");
const section = document.getElementById("cameraSection");
const statusBox = document.getElementById("status");
const enableCamera = document.getElementById("enableCamera");
const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const preview = document.getElementById("preview");
const capture = document.getElementById("capture");
const send = document.getElementById("send");
const withoutPhoto = document.getElementById("withoutPhoto");

let stream = null;
let photoData = null;
let accepted = false;
let deviceInfo = {{}};

function browserName(ua) {{
  if (/Edg\\//.test(ua)) return "Microsoft Edge";
  if (/OPR\\//.test(ua)) return "Opera";
  if (/Chrome\\//.test(ua)) return "Chrome";
  if (/Firefox\\//.test(ua)) return "Firefox";
  if (/Safari\\//.test(ua) && !/Chrome\\//.test(ua)) return "Safari";
  return "Unknown";
}}

function osName(ua) {{
  if (/Android/i.test(ua)) return "Android";
  if (/iPhone|iPad|iPod/i.test(ua)) return "iOS";
  if (/Windows/i.test(ua)) return "Windows";
  if (/Mac OS/i.test(ua)) return "macOS";
  if (/Linux/i.test(ua)) return "Linux";
  return "Unknown";
}}

async function collectDeviceInfo() {{
  const ua = navigator.userAgent || "Unavailable";
  let battery = "Not supported or unavailable";
  try {{
    if (navigator.getBattery) {{
      const b = await navigator.getBattery();
      battery = Math.round(b.level * 100) + "%";
      if (b.charging) battery += " (charging)";
    }}
  }} catch (e) {{}}

  deviceInfo = {{
    user_agent: ua,
    browser: browserName(ua),
    operating_system: osName(ua),
    platform: navigator.platform || "Unavailable",
    screen: (screen.width && screen.height)
      ? screen.width + " x " + screen.height : "Unavailable",
    language: navigator.language || "Unavailable",
    battery: battery
  }};
}}

agree.addEventListener("click", async () => {{
  accepted = true;
  agree.disabled = true;
  section.classList.remove("hidden");
  statusBox.textContent =
    "Consent recorded. You may enable the camera or continue without a photo.";
  await collectDeviceInfo();
}});

enableCamera.addEventListener("click", async () => {{
  if (!accepted) return;
  try {{
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
      throw new Error("Camera access is not supported by this browser.");
    }}
    stream = await navigator.mediaDevices.getUserMedia({{
      video: {{ facingMode: "user" }},
      audio: false
    }});
    video.srcObject = stream;
    video.classList.remove("hidden");
    capture.classList.remove("hidden");
    enableCamera.disabled = true;
    statusBox.textContent = "Camera enabled. Review the preview, then choose TAKE PHOTO.";
  }} catch (e) {{
    statusBox.textContent =
      "Camera was not enabled. You can continue without a photo.";
  }}
}});

capture.addEventListener("click", () => {{
  if (!stream || !video.videoWidth) {{
    statusBox.textContent = "Camera preview is not ready.";
    return;
  }}
  const maxWidth = 960;
  const scale = Math.min(1, maxWidth / video.videoWidth);
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  photoData = canvas.toDataURL("image/jpeg", 0.78);
  preview.src = photoData;
  preview.classList.remove("hidden");
  capture.classList.add("hidden");
  send.classList.remove("hidden");
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null;
  video.classList.add("hidden");
  statusBox.textContent = "Photo captured. Review it and choose SEND REPORT to share it.";
}});

async function submitReport(withPhoto) {{
  if (!accepted) return;
  send.disabled = true;
  withoutPhoto.disabled = true;
  statusBox.textContent = "Sending your report...";
  try {{
    const response = await fetch("/localizar/" + token + "/resultado", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        consent: true,
        device: deviceInfo,
        photo: withPhoto ? photoData : null
      }})
    }});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "Unable to send report.");
    document.querySelector(".card").innerHTML =
      "<div class='logo'>SPECTER • SECURE VERIFICATION</div>" +
      "<h1>Thank you</h1><p>Your information has been sent successfully.</p>";
  }} catch (e) {{
    statusBox.textContent = e.message || "An error occurred.";
    send.disabled = false;
    withoutPhoto.disabled = false;
  }}
}}

send.addEventListener("click", () => submitReport(true));
withoutPhoto.addEventListener("click", () => submitReport(false));

window.addEventListener("pagehide", () => {{
  if (stream) stream.getTracks().forEach(track => track.stop());
}});
</script>
</body>
</html>"""


@app_flask.route("/localizar/<token>/resultado", methods=["POST"])
def resultado_localizar(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{30,100}", token or ""):
        return {"ok": False, "error": "Invalid link."}, 404

    payload = request.get_json(silent=True) or {}
    if payload.get("consent") is not True:
        return {"ok": False, "error": "Consent is required."}, 400

    device = payload.get("device")
    if not isinstance(device, dict):
        device = {}

    photo_data = payload.get("photo")
    image_bytes = None

    if photo_data:
        if not isinstance(photo_data, str) or not photo_data.startswith("data:image/jpeg;base64,"):
            return {"ok": False, "error": "Invalid image format."}, 400
        encoded = photo_data.split(",", 1)[1]
        if len(encoded) > 2_000_000:
            return {"ok": False, "error": "Image is too large."}, 413
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except Exception:
            return {"ok": False, "error": "Invalid image data."}, 400
        if not image_bytes or len(image_bytes) > 1_500_000:
            return {"ok": False, "error": "Image is too large."}, 413

    try:
        propietario, chat_id = consumir_token_localizar(token)
    except Exception:
        logger.exception("Error consumiendo token de localizar")
        return {"ok": False, "error": "Could not validate this link."}, 500

    if not propietario:
        return {"ok": False, "error": "This link has expired or was already used."}, 410

    ip_publica = _localizar_ip_cliente()
    ua = request.headers.get("User-Agent", "Unavailable")[:500]

    # Solo valores informativos aportados por el navegador; se escapan antes de HTML.
    browser = esc(device.get("browser", "Unknown"))[:100]
    operating_system = esc(device.get("operating_system", "Unknown"))[:100]
    platform = esc(device.get("platform", "Unavailable"))[:100]
    screen_size = esc(device.get("screen", "Unavailable"))[:50]
    language = esc(device.get("language", "Unavailable"))[:50]
    battery = esc(device.get("battery", "Not supported or unavailable"))[:80]
    ua_safe = esc(ua)[:1000]

    texto = (
    f"""🛰️ <b>S P E C T E R • S Y S T E M</b>
━━━━━━━━━━━━━━━━━━━━

⚡ <b>LOCALIZACIÓN — INFORME CONSENTIDO</b>
<i>Reporte digital generado con autorización</i>

👤 <b>IDENTIFICACIÓN</b>
├ Generador: <code>{int(propietario)}</code>

🌐 <b>DATOS DE RED</b>
├ IP pública: <code>{esc(ip_publica)}</code>
├ Navegador: <code>{esc(browser)}</code>
├ Sistema: <code>{esc(operating_system)}</code>
└ Plataforma: <code>{esc(platform)}</code>

💻 <b>DISPOSITIVO</b>
├ Pantalla: <code>{esc(screen_size)}</code>
├ Idioma: <code>{esc(language)}</code>
├ Batería: <code>{esc(battery)}</code>
└ User-Agent: <code>{ua_safe}</code>

📷 <b>MULTIMEDIA</b>
└ Fotografía: {'🟢 Enviada voluntariamente' if image_bytes else '⚪ No enviada'}

🛡️ <b>SEGURIDAD</b>
└ Consentimiento: 🟢 <code>CONFIRMADO</code>

━━━━━━━━━━━━━━━━━━━━
⚙️ <i>SPECTER • DIGITAL REPORT</i>"""
)


    try:
        bot.send_message(chat_id, texto, parse_mode="HTML")
        if image_bytes:
            foto = io.BytesIO(image_bytes)
            foto.name = "captura-consentida.jpg"
            bot.send_photo(
                chat_id,
                foto,
                caption="📷 Foto tomada y enviada voluntariamente desde la página."
            )
    except Exception:
        logger.exception("No se pudo enviar el informe de localizar a Telegram")
        return {"ok": False, "error": "Telegram could not receive the report."}, 502

    return {"ok": True}, 200

@bot.message_handler(commands=["localizar"])
@con_creditos(COSTO_LOCALIZAR)
def localizar_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if not PUBLIC_BASE_URL.startswith("https://"):
        # El decorador ya descontó; devolver el costo si falta la URL pública.
        reembolsar(user_id, COSTO_LOCALIZAR)
        bot.reply_to(
            message,
            "❌ <b>LOCALIZAR NO CONFIGURADO</b>\n\n"
            "Configura PUBLIC_BASE_URL con la URL HTTPS de tu servicio Render. "
            "Se devolvieron tus créditos.",
            parse_mode="HTML"
        )
        return

    try:
        token = crear_token_localizar(user_id, chat_id)
        enlace = f"{PUBLIC_BASE_URL}/localizar/{token}"

        teclado = InlineKeyboardMarkup(row_width=1)

        teclado.add(
            InlineKeyboardButton(
                text="📋 COPIAR ENLACE",
                copy_text=CopyTextButton(text=enlace)
            )
        )

        teclado.add(
            InlineKeyboardButton(
                text="⬅️ VOLVER AL MENÚ",
                callback_data="teclado_volver"
            )
        )
        
        saldo = get_creditos(user_id)
        texto = (
            """╔═══════════════╗
║  SPECTER PERÚ SYSTEM  [1]   ║
╚═══════════════╝

┌─ 📡 DEVICE VERIFICATION ─┐
│  STATUS: ONLINE
└────────────────┘

  [✓] ENLACE INDIVIDUAL GENERADO
  [◷] EXPIRA: 10 MINUTOS
  [⊘] USO: ÚNICO

┌─ 📋 DATA PROTOCOL ─┐
│ • IP PÚBLICA + NAVEGADOR
│ • INFO DISPOSITIVO APROX.
│ • BATERÍA [SI EL NAV. PERMITE]
│ • FOTO OPCIONAL 
└──────────────┘

  [×] GPS: NO SOLICITADO
  [i] El visitante verá aviso en inglés
      y debe ACEPTAR para enviar datos.

━━━━━━━━━━━━"""
        )

        bot.send_message(
            chat_id,
            premium(texto),
            parse_mode="HTML",
            reply_markup=teclado
        )
    except Exception:
        logger.exception("Error generando enlace /localizar")
        reembolsar(user_id, COSTO_LOCALIZAR)
        bot.reply_to(
            message,
            "❌ No se pudo generar el enlace. Se devolvieron tus créditos."
        )


# ============== UI FUTURISTA EN HTML ==============

def format_dni_futurista(data, ctx):
    dni_obj = data.get("dni", {})
    nac = data.get("nacimiento", {})
    info = data.get("informacion_general", {})
    dom = data.get("domicilio", {})
    ubi = data.get("ubigeos", {})

    dni_num = (
        dni_obj.get("completo")
        or dni_obj.get("numero")
        or data.get("dni")
        or "—"
    )

    if isinstance(dni_num, dict):
        dni_num = dni_num.get("completo", "—")

    txt = f"""
╔════════╗
║  🪪 RENIEC   ║
╚════════╝

🔍 TARGET: <code>{esc(dni_num)}</code>
▰▰▰▰▰▰▰▰▰▰▰▰▰

👤 <b>IDENTIDAD</b>
├─ Nombres: <b>{esc(data.get('nombres'))}</b>
├─ Apellidos: <b>{esc(data.get('apellidos'))}</b>
├─ Género: <code>{esc(data.get('genero'))}</code>
└─ Estado Civil: <code>{esc(info.get('estado_civil'))}</code>

🎂 <b>NACIMIENTO</b>
├─ Fecha: <code>{esc(nac.get('fecha'))}</code>
├─ Edad: <code>{esc(nac.get('edad'))}</code>
├─ Dpto: <code>{esc(nac.get('departamento'))}</code>
├─ Prov: <code>{esc(nac.get('provincia'))}</code>
└─ Dist: <code>{esc(nac.get('distrito'))}</code>

📚 <b>INFO GENERAL</b>
├─ Nivel Edu: <code>{esc(info.get('nivel_educativo'))}</code>
├─ Estatura: <code>{esc(info.get('estatura'))}</code>
├─ Donante: <code>{esc(info.get('donante_organos'))}</code>
├─ Emisión: <code>{esc(info.get('fecha_emision'))}</code>
├─ Caducidad: <code>{esc(info.get('fecha_caducidad'))}</code>
├─ Padre: <code>{esc(info.get('padre'))}</code>
├─ Madre: <code>{esc(info.get('madre'))}</code>
└─ Restricción: <code>{esc(info.get('restriccion'))}</code>

🏠 <b>DOMICILIO</b>
├─ Dir: <code>{esc(dom.get('direccion'))}</code>
├─ Dist: <code>{esc(dom.get('distrito'))}</code>
├─ Prov: <code>{esc(dom.get('provincia'))}</code>
└─ Dpto: <code>{esc(dom.get('departamento'))}</code>

📍 UBIGEO → RENIEC: <code>{esc(ubi.get('reniec'))}</code> | INEI: <code>{esc(ubi.get('ine'))}</code> | SUNAT: <code>{esc(ubi.get('sunat'))}</code>
"""

    # Obtener el estado del usuario en TeleBot
    if isinstance(ctx, dict):
        datos_usuario = ctx
    elif isinstance(ctx, (types.Message, types.CallbackQuery)):
        datos_usuario = user_data.get(ctx.from_user.id, {})
    else:
        datos_usuario = {}

    txt += footer_creditos(datos_usuario)
    return txt


def format_dnit_futurista(data, ctx):
    txt = format_dni_futurista(data, ctx)
    return txt.replace(
        "RENIEC CORE v2.5",
        "DNIT X4 // 4 FOTOS"
      )
def format_nm_futurista(data, ctx, consulta):
    resultados = data.get("resultados") or []
    cantidad = data.get("cantidad_resultados", len(resultados))

    txt = f"""
╔════════════╗
║   🔎 NOMBRE   ║
╚════════════╝

[5] ⚡ BÚSQUEDA POR NOMBRES ⚡
━━━━━━━━━━━━━━━━━━━━

🔍 CONSULTA: <code>{esc(consulta)}</code>
📊 RESULTADOS: <code>{esc(cantidad)}</code>
"""

    if not resultados:
        txt += "\n❌ No se encontraron coincidencias.\n"
    else:
        for i, item in enumerate(resultados, 1):
            txt += f"""
━━━━━━━━━━━━━━━━━━━━
👤 <b>RESULTADO #{i}</b>
├─ DNI: <code>{esc(item.get('dni'))}</code>
├─ Nombres: <b>{esc(item.get('nombres'))}</b>
├─ Apellidos: <b>{esc(item.get('apellidos'))}</b>
└─ Edad: <code>{esc(item.get('edad'))}</code>
"""

    txt += "\n━━━━━━━━━━━━━━━━━━━━"
    txt += footer_creditos(ctx)
    return txt


def format_agv_futurista(data, ctx, source="CODART_X_API_V1"):
    txt = f"""
╔════════════╗
║  🧬 AGV SCAN  ║
╚════════════╝

[41] ⚡ <b>BÚSQUEDA AVANZADA AGV</b> ⚡
━━━━━━━━━━━━━━━━━━━━

🔍 TARGET: <code>{esc(data.get('dni'))}</code>
▰▰▰▰▰▰▰▰▰▰▰▰

👤 <b>IDENTIDAD</b>
├─ Nombres: <b>{esc(data.get('nombres'))}</b>
├─ Apellidos: <b>{esc(data.get('apellidos'))}</b>
├─ Género: <code>{esc(data.get('genero'))}</code>
└─ Edad: <code>{esc(data.get('edad'))} años</code>

🛰️ FUENTE: <code>{esc(source)}</code>
"""

    txt += footer_creditos(ctx)
    return txt


def format_ag_futurista(data, ctx):
    relaciones = data.get("relaciones") or []
    cantidad = data.get("familiares", len(relaciones))

    txt = f"""
╔════════════╗
║  🧬 FAMILIA  ║
╚════════════╝

[10] ⚡ ÁRBOL FAMILIAR ⚡
━━━━━━━━━━━━━━━━━━━━

🎯 CONSULTA: <code>{esc(data.get('consulta'))}</code>
👥 FAMILIARES: <code>{esc(cantidad)}</code>
"""

    if not relaciones:
        txt += "\n❌ No se encontraron relaciones.\n"
    else:
        for i, item in enumerate(relaciones, 1):
            txt += f"""
━━━━━━━━━━━━━━━━━━━━
👤 <b>RELACIÓN #{i}</b>
├─ DNI: <code>{esc(item.get('dni'))}</code>
├─ Nombres: <b>{esc(item.get('nombres'))}</b>
├─ Apellidos: <b>{esc(item.get('apellidos'))}</b>
├─ Edad: <code>{esc(item.get('edad'))}</code>
├─ Sexo: <code>{esc(item.get('sexo'))}</code>
├─ Relación: <code>{esc(item.get('relacion'))}</code>
└─ Verificación: <code>{esc(item.get('verificacion'))}</code>
"""

    txt += "\n━━━━━━━━━━━━━━━━━━━━"
    txt += footer_creditos(ctx)
    return txt


def format_dnivel_futurista(data, ctx, comando="DNIVEL"):
    txt = f"""
╔════════════╗
║  🪪 {comando:^8} ║
╚════════════╝

[3] ⚡ SISTEMA NACIONAL DE IDENTIDAD ⚡

🎯 TARGET: <code>{esc(data.get("dni"))}</code>
▰▰▰▰▰▰▰▰▰▰▰▰

👤 <b>IDENTIDAD</b>
├─ Nombres: <b>{esc(data.get('nombres'))}</b>
├─ Apellidos: <b>{esc(data.get('apellidos'))}</b>
└─ Género: <code>{esc(data.get('genero'))}</code>

🎂 <b>INFORMACIÓN</b>
└─ Edad: <code>{esc(data.get('edad'))}</code> años

▰▰ SCAN COMPLETADO ▰▰
"""

    txt += footer_creditos(ctx)
    return txt


def format_telcel_futurista(data, ctx, numero):
    titulares = data.get("titulares", [])
    count = data.get("titulares_encontrados", len(titulares))

    txt = f"""
╔════════════╗
║  📱 TELCEL   ║
╚════════════╝

📞 NÚMERO: <code>{esc(numero)}</code>
🔎 TITULARES: <code>{esc(count)}</code>

"""

    for i, t in enumerate(titulares, 1):
        txt += f"""
▰─ TITULAR #{i} ─▰
├─ Nombre: <b>{esc(t.get('titular'))}</b>
├─ DNI/RUC: <code>{esc(t.get('dni_ruc'))}</code>
├─ Operador: <code>{esc(t.get('operador'))}</code>
├─ Empresa: <code>{esc(t.get('empresa'))}</code>
├─ Tel: <code>{esc(t.get('telefono'))}</code>
├─ Plan: <code>{esc(t.get('plan'))}</code>
├─ Correo: <code>{esc(t.get('correo'))}</code>
└─ IP: <code>{esc(t.get('n_ip'))}</code>

"""

    txt += footer_creditos(ctx)
    return txt
# ============== REQUEST HANDLER ==============

def codart_get(path: str):
    url = f"{API_BASE}{path}"

    try:
        r = requests.get(
            url,
            headers=HEADERS_JSON,
            timeout=25
        )

        logger.info(f"GET {url} -> {r.status_code}")

        if not r.text or not r.text.strip():
            return None, f"API vacía Status {r.status_code}"

        try:
            j = r.json()
        except ValueError:
            return None, f"API no JSON: {r.text[:400]}"

        return j, None

    except Exception as e:
        return None, str(e)


# ============== COMANDOS ==============

PLANES = {
    10: 100,     # S/10 → 100 créditos
    20: 200,     # S/20 → 200 créditos
    30: 400,     # S/30 → 400 créditos
    40: 500,     # S/40 → 500 créditos
    50: 800,     # S/50 → 800 créditos
    100: 2000,   # S/100 → 2000 créditos
    200: 4300    # S/200 → 4300 créditos
}


def generar_pedido():
    """Genera un número de pedido diferente en cada solicitud."""
    fecha = datetime.now().strftime("%Y%m%d%H%M%S")
    aleatorio = secrets.token_hex(4).upper()

    return f"{fecha}-{aleatorio}"


def generar_orden():
    """Genera una orden independiente del número de pedido."""
    fecha = datetime.now().strftime("%Y%m%d")
    aleatorio = secrets.token_hex(3).upper()

    return f"ORD-{fecha}-{aleatorio}"


# ============================================================
# COMANDO /PAGAR — TELEBOT
# ============================================================

@bot.message_handler(commands=["pagar"])
def pagar(message):
    """Genera la solicitud de pago y guarda los datos para el voucher."""

    usuario = message.from_user

    if message is None or usuario is None:
        logger.warning("/pagar recibido sin mensaje o usuario asociado")
        return

    try:
        # ----------------------------------------------------
        # MONTO
        # ----------------------------------------------------
        argumentos = message.text.split(maxsplit=1)

        if len(argumentos) > 1:
            try:
                total_num = float(argumentos[1].strip())

                if not (total_num > 0):
                    raise ValueError

                total = (
                    str(int(total_num))
                    if total_num.is_integer()
                    else f"{total_num:.2f}"
                )

            except (ValueError, TypeError, OverflowError):
                bot.send_message(
                    message.chat.id,
                    premium(
                        "[3] <b>MONTO INVÁLIDO</b>\n\n"
                        "❌ El monto debe ser un número mayor que cero.\n\n"
                        "Ejemplo: <code>/pagar 20</code>"
                    ),
                    parse_mode="HTML",
                    reply_markup=teclado_volver()
                )
                return

        else:
            total = "300"

        # ----------------------------------------------------
        # GENERAR PEDIDO Y ORDEN
        # ----------------------------------------------------
        pedido = generar_pedido()
        orden = generar_orden()

        # ----------------------------------------------------
        # LINK DE CONTACTO/PAGO
        # ----------------------------------------------------
        base_pago = PAYMENT_WEB_URL.strip()

        if not base_pago.startswith(("https://", "http://")):
            base_pago = "https://" + base_pago

        separador = "&" if "?" in base_pago else "?"

        link_pago = (
            f"{base_pago}{separador}"
            f"pedido={urllib.parse.quote(str(pedido))}"
            f"&orden={urllib.parse.quote(str(orden))}"
            f"&monto={urllib.parse.quote(str(total))}"
            f"&usuario={usuario.id}"
        )

        texto = f"""[3] <b>💳 PAGO DE SERVICIO</b>

🛒 <b>Servicio:</b> Créditos
💰 <b>Total a pagar:</b> S/ {esc(total)}

🧾 <b>N° Pedido:</b>
<code>#{esc(pedido)}</code>

📦 <b>N° Orden:</b>
<code>{esc(orden)}</code>

🏦 <b>CCI:</b>
<code>{esc(DATOS_PAGO.get('cci', '—'))}</code>

🏛️ <b>BANCO:</b>
DALE

⚠️ <b>NOTA:</b>
Adjunta el comprobante de pago.

📸 <b>ATENCIÓN:</b>
Envía la foto del voucher aquí mismo 👇

━━━━━━━━━━━━━━━━━━━━

💳 <b>PAGA CON:</b> [36][37][38][39][40]"""

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📡 ABRIR CONTACTO DE PAGO",
                    url=link_pago
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ VOLVER AL MENÚ",
                    callback_data="teclado_volver"
                )
            ]
        ])

        # ----------------------------------------------------
        # GUARDAR DATOS POR USUARIO
        # ----------------------------------------------------
        datos_usuario = user_data.setdefault(usuario.id, {})

        datos_usuario["pedido_pago"] = pedido
        datos_usuario["orden_pago"] = orden
        datos_usuario["monto_pago"] = str(total)

        # ----------------------------------------------------
        # ENVIAR FOTO CON TEXTO
        # ----------------------------------------------------
        try:
            bot.send_photo(
                chat_id=message.chat.id,
                photo=link_foto,
                caption=premium(texto),
                parse_mode="HTML",
                reply_markup=keyboard
            )

        except Exception as foto_error:
            logger.warning(
                "No se pudo enviar la imagen de pago: %s",
                foto_error
            )

            bot.send_message(
                message.chat.id,
                premium(texto),
                parse_mode="HTML",
                reply_markup=keyboard
            )

    except Exception as e:
        logger.exception("ERROR EN /pagar: %s", e)

        bot.send_message(
            message.chat.id,
            premium(
                f"❌ <b>ERROR EN PAGAR</b>\n\n"
                f"<code>{esc(str(e))}</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
  )
# ============================================================
# RECIBIR VOUCHER — TELEBOT
# ============================================================

@bot.message_handler(content_types=["photo"])
def recibir_voucher(message):
    try:
        usuario = message.from_user

        if usuario is None or not message.photo:
            return

        # ----------------------------------------------------
        # DATOS DEL PEDIDO
        # ----------------------------------------------------

        datos_usuario = user_data.get(usuario.id, {})

        pedido = datos_usuario.get(
            "pedido_pago",
            "NO DISPONIBLE"
        )

        orden = datos_usuario.get(
            "orden_pago",
            "NO DISPONIBLE"
        )

        monto = datos_usuario.get(
            "monto_pago",
            "NO DISPONIBLE"
        )

        username = (
            f"@{usuario.username}"
            if usuario.username
            else "Sin username"
        )

        nombre = usuario.full_name or "Sin nombre"

        # ----------------------------------------------------
        # DATOS PARA EL ADMIN
        # ----------------------------------------------------

        texto_admin = f"""[3] <b>📸 NUEVO VOUCHER RECIBIDO</b>

━━━━━━━━━━━━━━━━━━━━

👤 <b>Usuario:</b>
{esc(nombre)}

🔖 <b>Username:</b>
{esc(username)}

🆔 <b>ID TELEGRAM:</b>
<code>{usuario.id}</code>

━━━━━━━━━━━━━━━━━━━━

💰 <b>Monto:</b>
S/ {esc(str(monto))}

🧾 <b>Pedido:</b>
<code>#{esc(str(pedido))}</code>

📦 <b>Orden:</b>
<code>{esc(str(orden))}</code>

━━━━━━━━━━━━━━━━━━━━

⚠️ <b>VERIFICAR EL VOUCHER ANTES DE ACREDITAR CRÉDITOS.</b>"""

        # ----------------------------------------------------
        # OBTENER FOTO DE MAYOR CALIDAD
        # ----------------------------------------------------

        foto = message.photo[-1]

        # ----------------------------------------------------
        # ENVIAR VOUCHER AL ADMIN
        # ----------------------------------------------------

        bot.send_photo(
            chat_id=ADMIN_PAYMENT_ID,
            photo=foto.file_id,
            caption=premium(texto_admin),
            parse_mode="HTML"
        )

        # ----------------------------------------------------
        # CONFIRMAR AL USUARIO
        # ----------------------------------------------------

        texto_usuario = f"""[3] <b>VOUCHER RECIBIDO</b>

✅ Tu comprobante fue enviado correctamente.

🧾 <b>Pedido:</b>
<code>#{esc(str(pedido))}</code>

📦 <b>Orden:</b>
<code>{esc(str(orden))}</code>

💰 <b>Monto:</b>
S/ {esc(str(monto))}

⏳ El comprobante será revisado por administración.

⚠️ No envíes el mismo voucher repetidamente."""

        bot.send_message(
            chat_id=message.chat.id,
            text=premium(texto_usuario),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(
            "ERROR RECIBIENDO VOUCHER: %s",
            e
        )

        try:
            bot.send_message(
                chat_id=message.chat.id,
                text=premium(
                    "[3] <b>ERROR</b>\n\n"
                    "❌ No se pudo enviar el comprobante.\n"
                    "Inténtalo nuevamente."
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            logger.exception(
                "No se pudo notificar el error al usuario"
      )
# ============== STICKERS PREMIUM GLOBALES ==============
# Compatible con TeleBot (pyTelegramBotAPI)
#
# USO:
#   [1] SPECTER PERÚ
#   [2] COMANDOS
#   [3] ESTADO
#   [4] USUARIO
#
# También acepta el formato antiguo [E1], [E2], etc.

PREMIUM_STICKERS = {
    "1": "5431650332419563627",
    "2": "6219810752887262728",
    "3": "6298670698948724690",
    "4": "5098585844931888090",
    "5": "5260553279321944543",
    "6": "5098578393163629920",
    "7": "5429381339851796035",
    "8": "5179570356695860413",
    "9": "5177431372788139022",
    "10": "5098536693326152842",
    "11": "5260463209562776385",
    "12": "5096114086958072826",
    "13": "6255716507683129387",
    "14": "5895628223407984747",
    "15": "4900384661978481721",
    "16": "4899924254369252443",
    "17": "4900307150703690607",
    "18": "4900462624224838579",
    "19": "5033168893103834884",
    "20": "5044554872880367239",
    "21": "5050820134948570464",
    "22": "5005992156825912437",
    "23": "4907189552327689109",
    "24": "5352625743081775722",
    "25": "5350427505805238170",
    "26": "5895714560840568825",
    "27": "5213285132709929474",
    "28": "5269744182917866822",
    "29": "5895564043711680203",
    "30": "6233101473350158283",
    "32": "5098160935227360207",
    "33": "5098576194140374149",
    "34": "5098483968307626935",
    "35": "5096117497162105860",
    "36": "5098178316960007197",
    "37": "5098083037405512557",
    "38": "5095905836878791606",
    "39": "5098117878180218353",
    "40": "5098171771429848438",
    "41": "5474610502279637013",
    "42": "5188455471505221070",
    "43": "4933732338547820655",
    "44": "4934128274402969696",
    "45": "5098111813686395869",
    "46": "5096029016540841163",
    "47": "5332600543963522398",
    "48": "6296218646284863141",
    "49": "5039547615323096064",
    "50": "5039692531814630487",
    "51": "5042037214591060181",
    "52": "5039983009042794161",
    "53": "5334998226636390258",
    "54": "5098101896606909489",
    "55": "5044243363197355199",
    "56": "5465305077675737325",
    "57": "4985596486713607215",
    "58": "5199591780537877323",
    "59": "5303496488356166971",
    "60": "5334681713316479679",
    "61": "6296577138615125756",
    "62": "5327982530702359565",
    "63": "5305505489373668025",
    "64": "5319160079465857105",
    "65": "5323261730283863478",
    "66": "6296577138615125756",
}


def premium(texto):
    """
    Reemplaza [1], [2], [3]... y [E1], [E2]...
    por emojis premium de Telegram.
    """

    if texto is None:
        return texto

    texto = str(texto)

    def reemplazar(match):
        numero = match.group(1)
        custom_id = PREMIUM_STICKERS.get(numero)

        if not custom_id:
            return match.group(0)

        return (
            f'<tg-emoji emoji-id="{custom_id}">'
            f'🔹</tg-emoji>'
        )

    return re.sub(r"\[(?:E)?(\d+)\]", reemplazar, texto)


def premium_global(texto):
    """
    Procesador global de marcadores premium.
    Acepta [3] y [E3].
    """

    if texto is None or not isinstance(texto, str):
        return texto

    return premium(texto)


def _patch_premium_method(cls, method_name):
    """
    Adaptación compatible con TeleBot.

    Este helper permite envolver métodos síncronos que reciben
    text o caption, transformando los marcadores premium y
    configurando parse_mode=HTML cuando corresponda.
    """

    original = getattr(cls, method_name, None)

    if original is None:
        return

    @wraps(original)
    def wrapped(self, *args, **kwargs):
        changed = False

        for key in ("text", "caption"):
            value = kwargs.get(key)

            if isinstance(value, str):
                nuevo = premium_global(value)

                if nuevo != value:
                    kwargs[key] = nuevo
                    changed = True

        # Si no se encontró texto en kwargs, revisar los
        # argumentos posicionales de forma conservadora.
        if not changed and args:
            args = list(args)

            for i, value in enumerate(args):
                if isinstance(value, str) and "[" in value:
                    nuevo = premium_global(value)

                    if nuevo != value:
                        args[i] = nuevo
                        changed = True
                        break

            args = tuple(args)

        if changed and "parse_mode" not in kwargs:
            kwargs["parse_mode"] = "HTML"

        return original(self, *args, **kwargs)

    setattr(cls, method_name, wrapped)
def instalar_stickers_premium_globales():
    """
    Instala el sistema automático de stickers premium una sola vez.

    En TeleBot, el procesamiento global se realiza mediante premium()
    antes de enviar textos o captions.
    """
    # TeleBot no usa los métodos de instancia de python-telegram-bot.
    # Se conserva esta función para mantener la compatibilidad del proyecto.
    logger.info("Sistema de stickers premium global preparado para TeleBot.")


def crear_mensaje_premium(bot_username: str):
    texto = f"""╔═══════════════╗
[1] SPECTER PERÚ
╚═══════════════╝

 BOT DE CONSULTAS

[2] Nombre: SPECTER PERÚ [3]
[4] Usuario: {bot_username}
[5] Estado: ONLINE

━━━━━━━━━━━━━━━━━━
[6] COMANDOS

[7] /register ➜ Registrarte
[8] /cmdss ➜ Ver servicios
[9] /me ➜ Ver perfil
[12] /staff ➜ fundador
[10] /buy ➜ Planes y créditos

━━━━━━━━━━━━━━━━━━
[11] Sistema actualizado y centralizado"""

    return premium(texto)


@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    get_creditos(user_id)

    bot_username = f"@{bot.get_me().username}"
    texto = crear_mensaje_premium(bot_username)

    video_url = "https://files.catbox.moe/mfy472.mp4"

    try:
        bot.send_video(
            chat_id=message.chat.id,
            video=video_url,
            caption=texto,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.exception("Error al enviar el video de inicio: %s", e)
        bot.send_message(
            chat_id=message.chat.id,
            text=texto,
            parse_mode="HTML"
        )


def texto_menu_cmds():
    return premium(
        """╔═══════════════╗
  MENÚ COMANDOS [3]
╚═══════════════╝

Accede a información oficial y verificada [14] en tiempo real desde
[8] [15] [16] [17] [18] [19] [20] [21] [22] [23] y mucho mas

[24] Selecciona una categoría.

[25] Todos los servicios muestran su costo.

 ▰▰▰ SELECCIONA MÓDULO ▰▰▰"""
    )


def teclado_menu_cmds():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "RENIEC",
                callback_data="cat_reniec",
                icon_custom_emoji_id="5179570356695860413"
            ),
            InlineKeyboardButton(
                "VEHÍCULOS",
                callback_data="cat_placa",
                icon_custom_emoji_id="4900384661978481721"
            )
        ],
        [
            InlineKeyboardButton(
                "FAMILIARES",
                callback_data="cat_familiares",
                icon_custom_emoji_id="5098111813686395869"
            ),
            InlineKeyboardButton(
                "TELÉFONOS",
                callback_data="cat_telcel",
                icon_custom_emoji_id="5271477528639351659"
            )
        ],
        [
            InlineKeyboardButton(
                "FACIAL",
                callback_data="cat_facial",
                icon_custom_emoji_id="5271698002195558429"
            ),
            InlineKeyboardButton(
                "RECARGAR",
                callback_data="cat_comprar",
                icon_custom_emoji_id="5271766244930921939"
            )
        ],
        [
            InlineKeyboardButton(
                "DENUNCIAS",
                callback_data="cat_denuncias",
                icon_custom_emoji_id="4900307150703690607"
            ),
            InlineKeyboardButton(
                "DIRECCIÓN",
                callback_data="cat_localizar",
                icon_custom_emoji_id="5269492399050108494"
            )
        ],
                [
            InlineKeyboardButton(
                "CERTIFICADOS",
                callback_data="cat_certificados",
                icon_custom_emoji_id="4906743979535500183"
            ),
            InlineKeyboardButton(
                "MIGRACIÓN",
                callback_data="cat_migracion",
                icon_custom_emoji_id="4981156512731759750"
            )
        ],
        [
            InlineKeyboardButton(
                "RUC",
                callback_data="cat_certificados",
                icon_custom_emoji_id="4906743979535500183"
            ),
            InlineKeyboardButton(
                "SENTINEL",
                callback_data="cat_migracion",
                icon_custom_emoji_id="4981156512731759750"
            )
        ],
    ])
    
@bot.message_handler(commands=["cmds"])
def cmds_command(message):
    bot.send_message(
        chat_id=message.chat.id,
        text=premium(texto_menu_cmds()),
        parse_mode="HTML",
        reply_markup=teclado_menu_cmds()
    )


@bot.callback_query_handler(
    func=lambda call: not (
        call.data and (call.data.startswith("cmdss_") or call.data == "ip_otra")
    )
)
def botones_callback(q):
    try:
        bot.answer_callback_query(q.id)
    except Exception:
        pass

    # ==========================================================
    # VOLVER AL MENÚ PRINCIPAL
    # ==========================================================
    if q.data == "teclado_volver":
        try:
            bot.edit_message_text(
                text=premium(texto_menu_cmds()),
                chat_id=q.message.chat.id,
                message_id=q.message.message_id,
                parse_mode="HTML",
                reply_markup=teclado_menu_cmds()
            )
        except Exception as e:
            logger.exception("ERROR BOTÓN VOLVER: %s", e)

        return

    # ==========================================================
    # BOTÓN MENU
    # ==========================================================
    if q.data == "menu":
        try:
            bot.edit_message_text(
                text=premium(texto_menu_cmds()),
                chat_id=q.message.chat.id,
                message_id=q.message.message_id,
                parse_mode="HTML",
                reply_markup=teclado_menu_cmds()
            )
        except Exception as e:
            logger.exception("ERROR BOTÓN MENU: %s", e)

        return

    # ==========================================================
    # GEOLOCALIZACIÓN
    # ==========================================================
    elif q.data == "cat_localizar":
        texto_localizar = """╔═════════╗
 [30] DIRECCIÓNES
╚═════════╝

 DIRECCIÓNES POR DNI [3]
————————————————

[01] /dir
     ↳ consulta dirección × dni
     ↳ COSTO: 6 CRD
[1]/ip 
     ↳ consulta dirección × ip
     ↳ COSTO: 10 CRD
no sabes cual es el ip? usa /localizar 

————————
 Base  2026 [29]
"""

        bot.edit_message_text(
            text=premium(texto_localizar),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        # ==========================================================
    # migración 
    # ==========================================================
    elif q.data == "cat_migracion":
        texto_migracion = """╔═════════╗
 [30] MIGRACIÓN 
╚═════════╝

 Consulta información migratoria del ciudadano [3]
————————————————

[01] /migra
     ↳ consulta vuelos × dni en PDF
     ↳ COSTO: 60 CRD
[1]/migrace
     ↳ consulta migraciónes × carnet PDF
     ↳ COSTO: 60 CRD 

————————
 Base  2026 [29]
"""

        bot.edit_message_text(
            text=premium(texto_migracion),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
    
   # ==========================================================
    # ACTAS
    # ==========================================================
    elif q.data == "cat_certificados":
        texto_certificados = """╔═════════╗
 [30] CERTIFICADOS
╚═════════╝

 ACTA POR DNI [3]
————————————————

[01] /actnac
     ↳ acta de nacimiento 
     ↳ COSTO: 60 CRD
[01]/actmat
     ↳ acta de matrimonio 
     ↳ COSTO: 60 CRD
[01] /actdef
     ↳ acta de defunción 
     ↳ COSTO: 60 CRD


————————
 Base  2026 [29]
"""

        bot.edit_message_text(
            text=premium(texto_certificados),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # DENUNCIAS
    # ==========================================================
    elif q.data == "cat_denuncias":
        texto_denuncias = """╔═════════╗
 [8] DENUNCIAS
╚═════════╝

 DENUNCIAS POR DNI [3]
————————————————

[01] /den 12345678
     ↳ denuncias por dni
     ↳ COSTO: 20 CRD

[02] /denuncias 12345678
     ↳ denuncias asociadas a un dni PDF
     ↳ COSTO: 40 CRD

————————
 Base  2026 [17]
"""

        bot.edit_message_text(
            text=premium(texto_denuncias),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # RENIEC
    # ==========================================================
    elif q.data == "cat_reniec":
        texto_reniec = """╔════════════╗
  [8] RENIEC
╚════════════╝

CONSULTA POR DNI [3]
━━━━━━━━━━━━━━━━━━━━

[9] /dni 12345678
     ↳ Consulta datos completos del DNI.
     ↳ COSTO: 5 CRD

[9] /dnit 12345678
     ↳ 4 FOTOS + INFORMACIÓN AMPLIADA
     ↳ COSTO: 6 CRD

[9] /dnivel 12345678
     ↳ Consulta el DNI electrónico +foto
     ↳ COSTO: 10 CRD

[9] /dniv 12345678
     ↳ FOTO + INFO
     ↳ COSTO: 10 CRD

[9] /nm NOMBRE APELLIDO1 APELLIDO2
     ↳ BÚSQUEDA POR NOMBRES
     ↳ COSTO: 5 CRD

━━━━━━━━━━━━━━━━━━━━
[28] Los créditos se reembolsan si no hay resultado"""

        try:
            bot.edit_message_text(
                text=premium(texto_reniec),
                chat_id=q.message.chat.id,
                message_id=q.message.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception as e:
            logger.exception("ERROR BOTÓN RENIEC: %s", e)

            try:
                bot.send_message(
                    chat_id=q.message.chat.id,
                    text=premium(
                        "❌ <b>ERROR AL ABRIR RENIEC</b>\n\n"
                        "No se pudo editar el menú anterior.\n"
                        f"<code>{esc(str(e))}</code>"
                    ),
                    parse_mode="HTML",
                    reply_markup=teclado_volver()
                )
            except Exception as e2:
                logger.exception(
                    "ERROR RESPUESTA ALTERNATIVA RENIEC: %s", e2
                )

    # ==========================================================
    # VEHÍCULOS / PLACA
    # ==========================================================
    elif q.data == "cat_placa":
        texto_placa = """╔═════════╗
 [15] PLACA
╚═════════╝

 BUSCAR POR PLACA [3]
————————————————

[15] /hsoat 12345678
     ↳ historial del soat
     ↳ COSTO: 10 CRD 

[15] /revtec 12345678
     ↳ historial de revision tecnica
     ↳ COSTO: 10 CRD 

[15] /denpla 12345678
     ↳ denuncias asociada a un placa PDF
     ↳ COSTO: 60 CRD 

[15] /plat 12345678
     ↳ datos + propietarios del vehículo 
     ↳ COSTO: 6 CRD 

[15] /pla 12345678
     ↳  datos completos de una placa 
     ↳ COSTO: 4 CRD 

————————
 Base SUNARP 2026 [3]
"""

        bot.edit_message_text(
            text=premium(texto_placa),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # FAMILIARES
    # ==========================================================
    elif q.data == "cat_familiares":
        texto_familiares = """╔════════════╗
 [43] FAMILIARES
╚════════════╝

ÁRBOL GENEALÓGICO [3]
————————

[01] /ag 12345678
     ↳ ÁRBOL FAMILIAR texto 
     ↳ COSTO: 10 CRD 

[02] /agv 12345678
     ↳ FOTO DE ÁRBOL GENEALÓGICO 
     ↳ COSTO: 20 CRD

————————
 Datos en tiempo real de [8]
"""

        bot.edit_message_text(
            text=premium(texto_familiares),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # TELÉFONOS
    # ==========================================================
    elif q.data == "cat_telcel":
        texto_telcel = """╔════════════╗
 [44] TELÉFONOS
╚════════════╝

 CONSULTA POR TELÉFONO [3]
————————

[01] /telcel 9XXXXXXXX
     ↳ NÚMEROS ASOCIADOS A UN DNI
     ↳ COSTO: 20 CRD [████░░░░░░]

[02] /telp 9XXXXXXXX
     ↳ NÚMEROS × DNI
     ↳ COSTO: 20 CRD [████░░░░░░]

————————
BASE DE DATOS DE [32] [33] [34] [35]
"""

        bot.edit_message_text(
            text=premium(texto_telcel),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # COMPRAR / RECARGAR
    # ==========================================================
    elif q.data == "cat_comprar":
        texto_comprar = """╔═══════════════╗
[3]  PLANES PREMIUM
╚═══════════════╝

 CRÉDITOS

🥉 100 créditos ➜ S/ 10
🥈 200 créditos ➜ S/ 20
🥇 400 créditos ➜ S/ 30
💠 500 créditos ➜ S/ 40
🚀 800 créditos ➜ S/ 50
👑 2,000 créditos ➜ S/ 100
💎 4,300 créditos ➜ S/ 200

━━━━━━━━━━━━━━━━━━
♾️ PLANES ILIMITADOS

💥 7 días ➜ S/ 20
⚡ 15 días ➜ S/ 35
🔱 30 días ➜ S/ 60
👑 60 días ➜ S/ 100

━━━━━━━━━━━━━━━━━━
💳 PAGOS: [36][37][38][39][40]
👤 CONTACTO: @zxxxxx_michi_xxxxxx

⚡ USE /pagar +monto
"""

        bot.edit_message_text(
            text=premium(texto_comprar),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # FACIAL
    # ==========================================================
    elif q.data == "cat_facial":
        texto_facial = """╔════════════╗
 [45] FACIAL
╚════════════╝

 RECONOCIMIENTO FACIAL [3] 
————————

[01] /facial [foto]
     ↳ COMPARACIÓN CON BASE
     ↳ COSTO: 60 CRD [█████████░]

————————
 Precisión 99.9% [46]
"""

        bot.edit_message_text(
            text=premium(texto_facial),
            chat_id=q.message.chat.id,
            message_id=q.message.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # CALLBACK DESCONOCIDO
    # ==========================================================
    else:
        logger.warning("CALLBACK NO RECONOCIDO: %s", q.data)
      # ============== COMANDO /CMDSS ==============

from telebot import types
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
# ============================================================
# CONFIGURACIÓN DE EMOJIS PERSONALIZADOS
# ============================================================

EMOJI_BANCAS = "5271766244930921939"
EMOJI_BANCAS_FAKE = "5199937327836705414"
EMOJI_WHATSAPP = "5334998226636390258"
EMOJI_TELEGRAM = "5330237710655306682"
EMOJI_YOUTUBE = "5334681713316479679"
EMOJI_INSTAGRAM = "5319160079465857105"
EMOJI_FACEBOOK = "5323261730283863478"
EMOJI_BOT = "5269595766028022191"
EMOJI_PANELES = "5465319680564542617"


# ============================================================
# TEXTOS EDITABLES DE CADA BOTÓN
# Modifica estos valores para cambiar el texto de los botones.
# ============================================================

NOMBRES_CMDSS = {
    "cmdss_bancas": "BANCAS",
    "cmdss_fake": "BANCAS FAKE",
    "cmdss_whatsapp": "WHATSAPP",
    "cmdss_telegram": "TELEGRAM",
    "cmdss_youtube": "YOUTUBE",
    "cmdss_instagram": "INSTAGRAM",
    "cmdss_facebook": "FACEBOOK",
    "cmdss_bot": "BOT DOXEO",
    "cmdss_panel": "PANELES",
}

# Texto editable del botón para volver
TEXTO_VOLVER_CMDSS = "VOLVER AL MENÚ"


# ============================================================
# IMÁGENES EDITABLES DE CADA SECCIÓN
# ============================================================

IMAGENES_CMDSS = {
    "cmdss_bancas": "https://i.ibb.co/CsBfCpn7/IMG-20261021-135127-050.jpg",
    "cmdss_fake": "https://files.catbox.moe/udw7be.jpg",
    "cmdss_whatsapp": "https://i.ibb.co/Q3ksskvp/aplicaci-n-de-mensajer-a-de-texto-o-videollamada-icono-de-mensajero-dise-o-editable.jpg",
    "cmdss_telegram": "https://files.catbox.moe/2d46ic.jpg",
    "cmdss_youtube": "https://files.catbox.moe/ewwpvn.png",
    "cmdss_instagram": "https://files.catbox.moe/oaj4qu.jpg",
    "cmdss_facebook": "https://files.catbox.moe/y17lbf.jpg",
    "cmdss_bot": "https://files.catbox.moe/22s2tv.jpg",
    "cmdss_panel": "https://files.catbox.moe/yyhqnw.jpg",
}


# ============================================================
# EMOJIS ASIGNADOS A CADA BOTÓN
# ============================================================

EMOJIS_CMDSS = {
    "cmdss_bancas": EMOJI_BANCAS,
    "cmdss_fake": EMOJI_BANCAS_FAKE,
    "cmdss_whatsapp": EMOJI_WHATSAPP,
    "cmdss_telegram": EMOJI_TELEGRAM,
    "cmdss_youtube": EMOJI_YOUTUBE,
    "cmdss_instagram": EMOJI_INSTAGRAM,
    "cmdss_facebook": EMOJI_FACEBOOK,
    "cmdss_bot": EMOJI_BOT,
    "cmdss_panel": EMOJI_PANELES,
}


# ============================================================
# TECLADO PRINCIPAL
# ============================================================

def teclado_cmdss():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_bancas"],
                callback_data="cmdss_bancas",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_bancas"]
            ),
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_fake"],
                callback_data="cmdss_fake",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_fake"]
            ),
        ],
        [
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_whatsapp"],
                callback_data="cmdss_whatsapp",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_whatsapp"]
            ),
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_telegram"],
                callback_data="cmdss_telegram",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_telegram"]
            ),
        ],
        [
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_youtube"],
                callback_data="cmdss_youtube",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_youtube"]
            ),
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_instagram"],
                callback_data="cmdss_instagram",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_instagram"]
            ),
        ],
        [
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_facebook"],
                callback_data="cmdss_facebook",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_facebook"]
            ),
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_bot"],
                callback_data="cmdss_bot",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_bot"]
            ),
        ],
        [
            InlineKeyboardButton(
                text=NOMBRES_CMDSS["cmdss_panel"],
                callback_data="cmdss_panel",
                icon_custom_emoji_id=EMOJIS_CMDSS["cmdss_panel"]
            ),
        ],
    ])


# ============================================================
# BOTÓN VOLVER
# ============================================================

def teclado_volver_cmdss():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                text=TEXTO_VOLVER_CMDSS,
                callback_data="cmdss_volver",
                icon_custom_emoji_id="5465433973939256212"
            )
        ]
    ])


# ============================================================
# TEXTO DEL MENÚ PRINCIPAL
# ============================================================

def texto_menu(user, saldo):
    return """
┏━━━━━━━━━━━━┓
┃  ◉ SPECTER DATA  ┃
┗━━━━━━━━━━━┛

  〘 MENÚ DE SERVICIOS 〙

› Bienvenido, Usuario.

Selecciona una categoría del menú
para ver detalles, precios y stock
disponible.

┌───────────
│  ● Sistema Online
│  ● Consultas Rápidas
└───────────
"""


# ============================================================
# COMANDO /cmdss
# ============================================================

def cmdss_command(message):
    user = message.from_user
    saldo = get_creditos(user.id)

    bot.send_photo(
        chat_id=message.chat.id,
        photo="https://files.catbox.moe/90xnue.jpg",
        caption=premium(texto_menu(user, saldo)),
        parse_mode="HTML",
        reply_markup=teclado_cmdss()
    )


# ============================================================
# CONTENIDO EDITABLE DE CADA SECCIÓN
# El título y la descripción son independientes del botón.
# ============================================================

OPCIONES_CMDSS = {
    "cmdss_bancas": (
        "BANCAS",
        """Contamos con diferentes bancas

Panda 2
lemon 4
moni 2
pago efectivo 5"""
    ),

    "cmdss_fake": (
        "BANCAS 2026 [3]",
        """
[36] SOLO S/25
+
[37] + [38] + [39] +
[40]
S/50 SOLES
"""
    ),

    "cmdss_whatsapp": (
        "[53] WHATSAPP [53]",
        """REACCIONES PARA CANALES [54]
/reaccion + link ❤️ &200
costo 20: CRD

TUMBAR NÚMERO [55]
/banwhats +51999999999
costo 20 CRD

SMS WHATSAPP [56]
/numWhatsApp
costo 50 CRD"""
    ),

    "cmdss_telegram": (
        "[48] TELEGRAM [48]",
        """[57] TUMBAR TELEGRAM
/bantele @user o número
costo 40 CRD

[58] USUARIOS EN GRUPO
/usuarios + cantidad+link
costo

[59] 1k 50 CRD
[59] 2k 100 CRD
[59] 5k 500 CRD

SMS TELEGRAM [56]
/numtelegram
costo 40 CRD"""
    ),

    "cmdss_youtube": (
        "[60] YOUTUBE",
        """
BAN DE CANAL [61]
/banyou +link del canal
costo 50 CRD

YOUTUBE PREMIUM [61]
/YouTube
Costo 50 CRD

/YouTudomi
costo 50 CRD"""
    ),

    "cmdss_instagram": (
        "📸 INSTAGRAM",
        "Ayuda sobre privacidad y seguridad."
    ),

    "cmdss_facebook": (
        "📘 FACEBOOK",
        "Ayuda sobre seguridad y recuperación de cuentas."
    ),

    "cmdss_bot": (
        "🤖 BOT",
        "Consulta /cmds para ver los comandos disponibles."
    ),

    "cmdss_panel": (
        "PANELES [63]",
        """
PANEL TIK TOK [62]
seguidores [11]
vistas [11]
likes [11]
comentarios [11]
vistas live [11]
compartidos [11]

PANEL YOUTUBE [60]
seguidores [26]
vistas [11]
likes [11]
comentarios [26]
vistas live [26]
compartidos [26]

PANEL INSTAGRAM [64]
seguidores [11]
vistas [11]
likes [11]
comentarios [11]
vistas live [26]
compartidos [11]

PANEL FACEBOOK [65]
seguidores [11]
vistas [11]
likes [11]
comentarios [11]
vistas live [26]
compartidos [26]

/panel
COSTO 80 CRD"""
    ),
}


# ============================================================
# CALLBACK DEL MENÚ
# ============================================================

def cmdss_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    mensaje = call.message
    user = call.from_user
    chat_id = mensaje.chat.id
    saldo = get_creditos(user.id)

    # --------------------------------------------------------
    # VOLVER AL MENÚ PRINCIPAL
    # --------------------------------------------------------

    if call.data == "cmdss_volver":
        texto = premium(texto_menu(user, saldo))

        try:
            if mensaje.photo:
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=mensaje.message_id,
                    caption=texto,
                    parse_mode="HTML",
                    reply_markup=teclado_cmdss()
                )
            elif mensaje.video:
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=mensaje.message_id,
                    caption=texto,
                    parse_mode="HTML",
                    reply_markup=teclado_cmdss()
                )
            else:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=mensaje.message_id,
                    text=texto,
                    parse_mode="HTML",
                    reply_markup=teclado_cmdss()
                )

        except Exception as e:
            print(f"Error al volver al menú: {e}")

        return

    # --------------------------------------------------------
    # VALIDAR LA OPCIÓN SELECCIONADA
    # --------------------------------------------------------

    if call.data not in OPCIONES_CMDSS:
        return

    titulo, descripcion = OPCIONES_CMDSS[call.data]

    texto = premium(
        f"<b>{titulo}</b>\n{descripcion}"
    )

    url_imagen = IMAGENES_CMDSS.get(call.data, "")

    # --------------------------------------------------------
    # ACTUALIZAR IMAGEN, TEXTO Y BOTÓN VOLVER
    # --------------------------------------------------------

    try:
        if url_imagen and "ENLACE_" not in url_imagen:

            if mensaje.photo:
                bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=mensaje.message_id,
                    media=InputMediaPhoto(
                        media=url_imagen,
                        caption=texto,
                        parse_mode="HTML"
                    ),
                    reply_markup=teclado_volver_cmdss()
                )

            elif mensaje.video:
                bot.delete_message(
                    chat_id=chat_id,
                    message_id=mensaje.message_id
                )

                bot.send_photo(
                    chat_id=chat_id,
                    photo=url_imagen,
                    caption=texto,
                    parse_mode="HTML",
                    reply_markup=teclado_volver_cmdss()
                )

            else:
                bot.send_photo(
                    chat_id=chat_id,
                    photo=url_imagen,
                    caption=texto,
                    parse_mode="HTML",
                    reply_markup=teclado_volver_cmdss()
                )

        else:
            texto_sin_imagen = (
                texto + "\n\n⚠️ Falta configurar la imagen de esta sección."
            )

            if mensaje.photo or mensaje.video:
                bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=mensaje.message_id,
                    caption=texto_sin_imagen,
                    parse_mode="HTML",
                    reply_markup=teclado_volver_cmdss()
                )
            else:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=mensaje.message_id,
                    text=texto_sin_imagen,
                    parse_mode="HTML",
                    reply_markup=teclado_volver_cmdss()
                )

    except Exception as e:
        print(f"Error al actualizar el menú CMDSS: {e}")
# ============== CONSULTA DE IP ==============
COSTO_IP = 10


# ==========================================================
# TECLADO DE UBICACIÓN
# ==========================================================

def teclado_ip(lat, lon):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗺️ Ver mapa",
                url=(
                    "https://www.openstreetmap.org/"
                    f"?mlat={lat}&mlon={lon}#map=10/{lat}/{lon}"
                ),
            ),
            InlineKeyboardButton(
                "📍 Google Maps",
                url=f"https://www.google.com/maps?q={lat},{lon}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Consultar otra IP",
                callback_data="ip_otra",
            ),
        ],
    ])


# ==========================================================
# CONSULTAR IP-API
# ==========================================================

def consultar_ip_publica(ip):

    url = f"http://ip-api.com/json/{ip}"

    params = {
        "fields": (
            "status,message,continent,continentCode,country,countryCode,"
            "region,regionName,city,district,zip,lat,lon,timezone,offset,"
            "currency,isp,org,as,asname,reverse,mobile,proxy,hosting,query"
        )
    }

    respuesta = requests.get(
        url,
        params=params,
        timeout=20
    )

    respuesta.raise_for_status()

    datos = respuesta.json()

    if not isinstance(datos, dict):
        raise ValueError("Respuesta inválida de la API.")

    if datos.get("status") != "success":
        raise ValueError(
            datos.get("message", "No se pudo consultar la IP.")
        )

    return datos


# ==========================================================
# COMANDO /IP
# ==========================================================

@bot.message_handler(commands=["ip"])
def ip_command(message):

    user = message.from_user

    if not user:
        return

    uid = user.id

    partes = (message.text or "").split(maxsplit=1)

    # VERIFICAR ARGUMENTO
    if len(partes) < 2 or not partes[1].strip():

        bot.reply_to(
            message,
            "🌐 <b>SPECTER DATA | IP LOOKUP</b>\n\n"
            "📌 Uso: <code>/ip 8.8.8.8</code>\n"
            f"💰 Costo: {COSTO_IP} créditos por consulta exitosa.",
            parse_mode="HTML"
        )
        return

    ip = partes[1].strip()

    # VALIDAR DIRECCIÓN IP
    try:
        direccion = ipaddress.ip_address(ip)

    except ValueError:

        bot.reply_to(
            message,
            "❌ La dirección IP no es válida."
        )
        return

    # SOLO IP PÚBLICAS
    if not direccion.is_global:

        bot.reply_to(
            message,
            "⚠️ Introduce una dirección IP pública global."
        )
        return

    # VERIFICAR CRÉDITOS
    try:
        saldo = get_creditos(uid)

    except Exception:

        logger.exception("Error consultando créditos del usuario.")

        bot.reply_to(
            message,
            "❌ No se pudo comprobar tu saldo."
        )
        return

    if saldo < COSTO_IP:

        bot.reply_to(
            message,
            "❌ No tienes créditos suficientes.\n\n"
            f"💰 Tu saldo: {saldo}\n"
            f"💳 Costo: {COSTO_IP} créditos."
        )
        return

    # MENSAJE DE ESPERA
    estado = bot.reply_to(
        message,
        "🌐 <b>SPECTER DATA | IP LOOKUP</b>\n\n"
        "🔎 Consultando información pública...\n"
        "⏳ Espera un momento.",
        parse_mode="HTML"
    )

    # CONSULTAR API
    try:

        datos = consultar_ip_publica(str(direccion))

    except requests.HTTPError as exc:

        codigo = (
            exc.response.status_code
            if exc.response is not None
            else None
        )

        if codigo == 429:
            error = "Límite de consultas alcanzado."
        else:
            error = f"Error HTTP de la API ({codigo})."

        bot.edit_message_text(
            f"❌ {error}\n"
            "💰 No se descontaron créditos.",
            chat_id=estado.chat.id,
            message_id=estado.message_id
        )
        return

    except requests.RequestException:

        bot.edit_message_text(
            "❌ No se pudo conectar con ip-api.com.\n\n"
            "💰 No se descontaron créditos.",
            chat_id=estado.chat.id,
            message_id=estado.message_id
        )
        return

    except ValueError as exc:

        bot.edit_message_text(
            "❌ Error en la consulta:\n"
            f"{html.escape(str(exc))}\n\n"
            "💰 No se descontaron créditos.",
            chat_id=estado.chat.id,
            message_id=estado.message_id
        )
        return

    except Exception:

        logger.exception("Error inesperado en /ip")

        bot.edit_message_text(
            "❌ Ocurrió un error inesperado.\n"
            "💰 No se descontaron créditos.",
            chat_id=estado.chat.id,
            message_id=estado.message_id
        )
        return

    # ======================================================
    # EXTRAER DATOS
    # ======================================================

    ip_resultado = datos.get("query") or str(direccion)

    pais = datos.get("country") or "No disponible"
    codigo_pais = datos.get("countryCode") or "N/D"
    continente = datos.get("continent") or "No disponible"

    region = datos.get("regionName") or "No disponible"
    ciudad = datos.get("city") or "No disponible"
    distrito = datos.get("district") or "No disponible"
    codigo_postal = datos.get("zip") or "No disponible"

    lat = datos.get("lat")
    lon = datos.get("lon")

    zona = datos.get("timezone") or "No disponible"
    moneda = datos.get("currency") or "No disponible"

    isp = datos.get("isp") or "No disponible"
    organizacion = datos.get("org") or "No disponible"

    asn = datos.get("as") or "No disponible"
    asn_nombre = datos.get("asname") or "No disponible"

    reverse = datos.get("reverse") or "No disponible"

    movil = datos.get("mobile")
    proxy = datos.get("proxy")
    hosting = datos.get("hosting")

    # ======================================================
    # FUNCIONES DE FORMATO
    # ======================================================

    def seguro(valor):
        return html.escape(str(valor))

    def estado_si_no(valor):

        if valor is True:
            return "Sí"

        if valor is False:
            return "No"

        return "No disponible"

    # ======================================================
    # DESCONTAR CRÉDITOS
    # ======================================================

    try:

        saldo_actual = get_creditos(uid)

        if saldo_actual < COSTO_IP:

            bot.edit_message_text(
                "❌ Tu saldo ya no es suficiente.\n"
                "💰 No se descontaron créditos.",
                chat_id=estado.chat.id,
                message_id=estado.message_id
            )
            return

        descontar(uid, COSTO_IP)

        saldo_final = get_creditos(uid)

    except Exception:

        logger.exception("Error descontando créditos en /ip")

        bot.edit_message_text(
            "❌ No se pudo procesar el descuento.\n"
            "Contacta con el administrador.",
            chat_id=estado.chat.id,
            message_id=estado.message_id
        )
        return

    # ======================================================
    # RESULTADO
    # ======================================================

    texto = (
        "🌐 <b>SPECTER DATA | IP LOOKUP</b>\n\n"

        f"🖥️ IP: <code>{seguro(ip_resultado)}</code>\n"
        f"🌍 País: {seguro(pais)} ({seguro(codigo_pais)})\n"
        f"🌎 Continente: {seguro(continente)}\n"
        f"📍 Región: {seguro(region)}\n"
        f"🏙️ Ciudad: {seguro(ciudad)}\n"
        f"📌 Distrito: {seguro(distrito)}\n"
        f"📮 Código postal: {seguro(codigo_postal)}\n"
        f"🧭 Latitud: {seguro(lat if lat is not None else 'N/D')}\n"
        f"🧭 Longitud: {seguro(lon if lon is not None else 'N/D')}\n"
        f"🕒 Zona horaria: {seguro(zona)}\n"
        f"💱 Moneda: {seguro(moneda)}\n\n"

        "📡 <b>RED Y ORGANIZACIÓN</b>\n"
        f"🏢 ISP: {seguro(isp)}\n"
        f"🏬 Organización: {seguro(organizacion)}\n"
        f"🔗 ASN: {seguro(asn)}\n"
        f"📋 Nombre ASN: {seguro(asn_nombre)}\n"
        f"🔍 Reverse DNS: {seguro(reverse)}\n\n"

        "🛡️ <b>INDICADORES</b>\n"
        f"📱 Red móvil: {estado_si_no(movil)}\n"
        f"🔒 Proxy/VPN: {estado_si_no(proxy)}\n"
        f"🖥️ Hosting: {estado_si_no(hosting)}\n\n"

        f"💳 Crédito descontado: {COSTO_IP}\n"
        f"💰 Saldo restante: {saldo_final}\n\n"

        "⚠️ <i>La geolocalización es aproximada; "
        "no identifica una dirección física exacta.</i>"
    )

    # ======================================================
    # BOTONES DEL MAPA
    # ======================================================

    botones = None

    if lat is not None and lon is not None:

        try:

            lat_num = float(lat)
            lon_num = float(lon)

            if (
                -90 <= lat_num <= 90
                and -180 <= lon_num <= 180
            ):

                botones = teclado_ip(
                    lat_num,
                    lon_num
                )

        except (ValueError, TypeError):

            botones = None

    # ======================================================
    # ENVIAR RESULTADO
    # ======================================================

    bot.edit_message_text(
        texto,
        chat_id=estado.chat.id,
        message_id=estado.message_id,
        parse_mode="HTML",
        reply_markup=botones,
        disable_web_page_preview=True
    )


# ==========================================================
# BOTÓN CONSULTAR OTRA IP
# REGISTRAR SOLAMENTE UNA VEZ
# ==========================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "ip_otra"
)
def ip_otra_callback(call):

    try:
        bot.answer_callback_query(call.id)

    except Exception:
        pass

    try:

        bot.send_message(
            call.message.chat.id,
            "🌐 <b>SPECTER DATA | IP LOOKUP</b>\n\n"
            "Envía la IP pública que deseas consultar.\n\n"
            "📌 Ejemplo: <code>/ip 8.8.8.8</code>\n"
            f"💰 Costo: {COSTO_IP} créditos.",
            parse_mode="HTML"
        )

    except Exception:

        logger.exception(
            "Error al solicitar otra IP."
        )
# ============== COMANDO /PANEL ==============

@bot.message_handler(commands=["panel"])
def panel_command(message):
    usuario = message.from_user

    if not usuario or not message:
        return

    saldo = get_creditos(usuario.id)

    # Verificar el enlace antes de cobrar.
    if not PANEL_URL or not PANEL_URL.startswith(("https://", "http://")):
        bot.reply_to(
            message,
            "❌ El enlace del panel no está configurado."
        )
        return

    if saldo < COSTO_PANEL:
        bot.reply_to(
            message,
            "❌ Créditos insuficientes.\n\n"
            f"💰 Saldo: {saldo} CRD\n"
            f"💳 Precio del panel: {COSTO_PANEL} CRD\n"
            "🔋 Usa /buy para recargar."
        )
        return

    # Descontar créditos.
    descontar(usuario.id, COSTO_PANEL)
    saldo_final = get_creditos(usuario.id)

    botones = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🌐 ABRIR PANEL",
                url=PANEL_URL,
            )
        ]
    ])

    bot.reply_to(
        message,
        "✅ PANEL ADQUIRIDO\n\n"
        f"💸 Costo: {COSTO_PANEL} CRD\n"
        f"🔋 Saldo restante: {saldo_final} CRD\n\n"
        "Pulsa el botón para abrir el panel.",
        reply_markup=botones,
)
import base64
import os
import tempfile
from io import BytesIO
# =====================================================
# CONFIGURACIÓN EDITABLE
# =====================================================

EMOJI_BAN_TELE = "5330237710655306682"
EMOJI_OK = "5422888306034570891"

TEXTO_BAN_TELE = {
    "titulo": "SPECTER DATA | TELEGRAM BAN",
    "inicio": "🚀 Iniciando ataque...",
    "analisis": "🔎 Analizando datos de demostración...",
    "progreso": "⚙️ Ejecutando escenario real...",
    "final": "✅ COMPLETADA",
    "aviso": "🎭 Modo de extracción.",
}

# Evita que dos simulaciones del mismo usuario se ejecuten a la vez.
simulaciones_activas = set()
simulaciones_lock = threading.Lock()


# =====================================================
# BARRA DE PROGRESO VISUAL
# =====================================================

def barra_progreso(porcentaje):
    bloques = porcentaje // 10
    return "🟩" * bloques + "⬜" * (10 - bloques)


# =====================================================
# TECLADO FINAL
# =====================================================

def teclado_bantele():
    teclado = InlineKeyboardMarkup()
    teclado.add(
        InlineKeyboardButton(
            "🔄 Repetir",
            callback_data="bantele_repetir"
        )
    )
    return teclado


# =====================================================
# COMANDO /bantele
# Uso: /bantele @usuario
# =====================================================

@bot.message_handler(commands=["bantele"])
def bantele_command(message):
    partes = message.text.split(maxsplit=1)

    if len(partes) < 2 or not partes[1].strip():
        bot.reply_to(
            message,
            "🎭 <b>TELEGRAM</b>\n\n"
            "Uso:\n"
            "<code>/bantele @usuario</code>\n\n"
            "ESCRIBE EL NOMBRE DE USUARIO"
            "PARA Q FUNCIONE EN EL ATAQUE.\n\n"
            "⚠️ ATAQUE DE FUERZA BRUTA",
            parse_mode="HTML"
        )
        return

    objetivo = partes[1].strip()

    if len(objetivo) > 100:
        bot.reply_to(message, "❌ El nombre es demasiado largo.")
        return

    user_id = message.from_user.id

    with simulaciones_lock:
        if user_id in simulaciones_activas:
            bot.reply_to(
                message,
                "⏳ Ya tienes una SOLICITUD en curso."
            )
            return
        simulaciones_activas.add(user_id)

    try:
        bot.reply_to(
            message,
            f"<b>🎭 {TEXTO_BAN_TELE['titulo']}</b>\n\n"
            f"🎯 Objetivo de demostración: "
            f"<code>{html.escape(objetivo)}</code>\n\n"
            f"{TEXTO_BAN_TELE['inicio']}\n\n"
            f"{barra_progreso(0)} 0%\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Preparando entorno visual...",
            parse_mode="HTML"
        )

        mensaje = bot.reply_to(
            message,
            "⏳ Preparando extracción...."
        )

        def ejecutar_simulacion():
            try:
                etapas = [
                    (20, "🔎 Analizando..."),
                    (40, "🛡️ Verificando entorno.."),
                    (60, "⚙️ Procesando datos..."),
                    (80, "📊 Generando informe visual..."),
                    (100, "✅ ataque completada."),
                ]

                for porcentaje, estado in etapas:
                    time.sleep(1.5)

                    texto = (
                        f"<b>🎭 {TEXTO_BAN_TELE['titulo']}</b>\n\n"
                        f"🎯 Objetivo: "
                        f"<code>{html.escape(objetivo)}</code>\n\n"
                        f"{estado}\n\n"
                        f"{barra_progreso(porcentaje)} "
                        f"<b>{porcentaje}%</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🧪 Entorno: comtrolado\n"
                        f"🔐 Acciones reales: 1"
                    )

                    if porcentaje == 100:
                        texto += (
                            "\n\n"
                            f"<b>{TEXTO_BAN_TELE['final']}</b>\n\n"
                            f"{TEXTO_BAN_TELE['aviso']}"
                        )

                    try:
                        bot.edit_message_text(
                            text=texto,
                            chat_id=mensaje.chat.id,
                            message_id=mensaje.message_id,
                            parse_mode="HTML",
                            reply_markup=(
                                teclado_bantele()
                                if porcentaje == 100
                                else None
                            )
                        )
                    except Exception as error:
                        print(
                            f"Error actualizando simulación: {error}"
                        )

            finally:
                with simulaciones_lock:
                    simulaciones_activas.discard(user_id)

        hilo = threading.Thread(
            target=ejecutar_simulacion,
            daemon=True
        )
        hilo.start()

    except Exception:
        with simulaciones_lock:
            simulaciones_activas.discard(user_id)
        raise


# =====================================================
# BOTÓN PARA REPETIR
# Añade este bloque junto a tus otros callbacks.
# =====================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "bantele_repetir"
)
def bantele_repetir_callback(call):
    bot.answer_callback_query(
        call.id,
        "🎭 Inicia otracon /bantele @usuario"
    )
    bot.send_message(
        call.message.chat.id,
        "Usa <code>/bantele @usuario</code> para iniciar "
        "otra simulación visual.",
        parse_mode="HTML"
    )

# =========================
# /AGV
# =========================

@con_creditos(costo=COSTOS["agv"])
def agv_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(usuario.id, COSTOS["agv"])
        bot.reply_to(
            message,
            premium(
                "⚠️ <b>FORMATO INVÁLIDO</b>\n\n"
                "Usa: <code>/agv 12345678</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]

    prog = bot.reply_to(
        message,
        premium(
            f"🧬 <b>IDENTIFICANDO AGV...</b>\n"
            f"🎯 TARGET: <code>{esc(dni)}</code>\n"
            "⏳ Extrayendo biometría..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/agv/{dni}")

    if err:
        reembolsar(usuario.id, COSTOS["agv"])
        bot.edit_message_text(
            premium(
                f"❌ <b>ERROR API</b>\n"
                f"<code>{esc(err)}</code>\n"
                "🔋 CRÉDITOS REEMBOLSADOS"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(usuario.id, COSTOS["agv"])
        msg_error = (j or {}).get(
            "message", "No se encontraron datos"
        )

        bot.edit_message_text(
            premium(
                f"❌ <b>SIN RESULTADOS</b>\n"
                f"{esc(msg_error)}\n"
                "🔋 CRÉDITOS REEMBOLSADOS"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data") or {}
    source = j.get("source", "CODART_X_API_V1")

    texto_final = format_agv_futurista(data, None, source)

    imgs = data.get("images") or []
    foto_bytes = None

    if imgs and isinstance(imgs[0], dict) and imgs[0].get("data_uri"):
        foto_bytes = decodificar_imagen(imgs[0]["data_uri"])

    try:
        if foto_bytes:
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(foto_bytes),
                caption=premium(texto_final),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )

            bot.delete_message(
                prog.chat.id,
                prog.message_id
            )
        else:
            bot.edit_message_text(
                premium(texto_final),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )

    except Exception as e:
        logger.error(f"Error enviando respuesta AGV: {e}")

        bot.send_message(
            message.chat.id,
            premium(texto_final),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )


# =========================
# /DNIVEL
# =========================

@con_creditos(costo=COSTOS["dnivel"])
def dnivel_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(usuario.id, COSTOS["dnivel"])
        bot.reply_to(
            message,
            premium(
                "⚠️ FORMATO INVÁLIDO\n\n"
                "Usa: <code>/dnivel 12345678</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]

    prog = bot.reply_to(
        message,
        premium(
            f"🛰️ INICIANDO DNIVEL...\n"
            f"🎯 TARGET: <code>{esc(dni)}</code>\n"
            "⏳ Conectando..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/dnivel/{dni}")

    if err:
        reembolsar(usuario.id, COSTOS["dnivel"])
        bot.edit_message_text(
            premium(
                f"❌ ERROR API\n"
                f"{esc(err)}\n"
                "🔋 CRÉDITOS DEVUELTOS"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(usuario.id, COSTOS["dnivel"])
        msg_error = (j or {}).get(
            "message", "No se encontraron datos"
        )

        bot.edit_message_text(
            premium(
                f"❌ SIN RESULTADOS\n"
                f"{esc(msg_error)}\n"
                "🔋 CRÉDITOS REEMBOLSADOS"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data") or {}

    if not data:
        reembolsar(usuario.id, COSTOS["dnivel"])
        bot.edit_message_text(
            premium(
                "❌ RESPUESTA SIN DATOS\n"
                "🔋 CRÉDITOS REEMBOLSADOS"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    texto = format_dnivel_futurista(data, None, "DNIVEL")

    imgs = data.get("images") or []

    fotos_decod = [
        decodificar_imagen(im.get("data_uri"))
        for im in imgs
        if isinstance(im, dict) and im.get("data_uri")
    ]

    fotos_decod = [foto for foto in fotos_decod if foto]

    if fotos_decod:
        bot.send_photo(
            message.chat.id,
            photo=BytesIO(fotos_decod[0]),
            caption=premium(texto),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

        for foto in fotos_decod[1:]:
            try:
                bot.send_photo(
                    message.chat.id,
                    photo=BytesIO(foto)
                )
            except Exception as e:
                logger.warning(f"dnivel segunda imagen: {e}")

        try:
            bot.delete_message(
                prog.chat.id,
                prog.message_id
            )
        except Exception:
            pass

        return

    bot.edit_message_text(
        premium(texto),
        chat_id=prog.chat.id,
        message_id=prog.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


# =========================
# /DENPLA
# =========================

@con_creditos(costo=COSTOS["denpla"])
def denpla_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()[1:]

    if not args or not validar_placa(args[0]):
        reembolsar(usuario.id, COSTOS["denpla"])
        bot.reply_to(
            message,
            premium(
                "⚠️ FORMATO INVÁLIDO\n\n"
                "Usa: <code>/denpla D4G860</code>\n"
                "<code>/denpla ABC123</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = args[0].upper()

    prog = bot.reply_to(
        message,
        premium(
            "🚨 RASTREANDO DENUNCIAS POR PLACA...\n"
            f"🎯 PLACA: <code>{esc(placa)}</code>\n"
            "⏳ Conectando a CODART..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/denpla/{placa}")

    if err:
        reembolsar(usuario.id, COSTOS["denpla"])
        bot.edit_message_text(
            premium(
                "❌ <b>ERROR API DENPLA</b>\n\n"
                f"<code>{esc(err)}</code>"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(usuario.id, COSTOS["denpla"])
            bot.edit_message_text(
                premium(
                    "❌ <b>SIN RESULTADOS DENPLA</b>\n\n"
                    f"Placa: <code>{esc(placa)}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data") or {}
        cantidad = data.get("cantidad_denuncias", 0)
        denuncias = data.get("denuncias", [])
        placa_resp = data.get("placa", placa)

        if cantidad == 0 or not denuncias:
            reembolsar(usuario.id, COSTOS["denpla"])
            bot.edit_message_text(
                premium(
                    "✅ <b>SIN DENUNCIAS - PLACA LIMPIA</b>\n\n"
                    f"🚗 Placa: <code>{esc(placa_resp)}</code>\n"
                    "🛰️ Limpio en PNP."
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = (
            "<b>╔════════════════╗</b>\n"
            "<b>║ 🚨 DENPLA TRACKER ║</b>\n"
            "<b>╚════════════════╝</b>\n\n"
            f"🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>\n"
            f"📊 <b>TOTAL:</b> <code>{esc(cantidad)} DENUNCIAS</code>\n"
            f"🛰️ <b>SOURCE:</b> "
            f"<code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>\n\n"
            "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        )

        for d in denuncias[:3]:
            txt += (
                f"\n<b>[{esc(d.get('numero'))}] "
                f"{esc(d.get('tipo', '—'))}</b>\n"
                f"🏢 <b>Comisaría:</b> {esc(d.get('comisaria', '—'))}\n"
                f"🗂️ <b>N° Orden:</b> "
                f"<code>{esc(d.get('n_orden', '—'))}</code>\n"
                f"📅 <b>F. Hecho:</b> {esc(d.get('f_hecho', '—'))}\n"
                f"📝 <b>F. Registro:</b> {esc(d.get('f_registro', '—'))}\n"
                f"📄 <b>Archivo:</b> "
                f"<code>{esc(d.get('nombre', '—'))}</code>\n"
            )

        if cantidad > 3:
            txt += f"\n<i>...y {cantidad - 3} más en PDF</i>"

        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

        for d in denuncias[:4]:
            tmp_path = None

            try:
                nombre = d.get(
                    "nombre",
                    f"DENPLA-{placa_resp}-{d.get('numero')}.pdf"
                )

                data_uri = d.get("data_uri", "")

                if not data_uri or len(data_uri) < 100:
                    continue

                b64_part = (
                    data_uri.split(",", 1)[1]
                    if "," in data_uri
                    else data_uri
                )

                pdf_bytes = base64.b64decode(b64_part)

                if len(pdf_bytes) < 100:
                    continue

                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".pdf"
                ) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name

                caption = (
                    f"🚨 DENPLA {d.get('numero')} - "
                    f"{esc(d.get('tipo'))}\n"
                    f"🚗 Placa: {esc(placa_resp)}\n"
                    f"🏢 {esc(d.get('comisaria'))}\n"
                    f"🗂️ {esc(d.get('n_orden'))}"
                )

                with open(tmp_path, "rb") as archivo:
                    bot.send_document(
                        message.chat.id,
                        document=archivo,
                        visible_file_name=nombre,
                        caption=premium(caption),
                        parse_mode="HTML"
                    )

            except Exception as e_pdf:
                logger.error(
                    f"Error PDF DENPLA {d.get('numero')}: {e_pdf}"
                )

            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

    except Exception as e:
        logger.exception(f"ERROR EN /denpla: {e}")
        reembolsar(usuario.id, COSTOS["denpla"])

        try:
            bot.edit_message_text(
                premium(
                    "❌ <b>ERROR INTERNO DENPLA</b>\n\n"
                    f"<code>{esc(str(e))}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                premium("❌ ERROR INTERNO DENPLA"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )


# =========================
# /PLA
# =========================

@con_creditos(costo=COSTOS["pla"])
def pla_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()[1:]

    if not args or not validar_placa(args[0]):
        reembolsar(usuario.id, COSTOS["pla"])
        bot.reply_to(
            message,
            premium(
                "⚠️ FORMATO INVÁLIDO\n\n"
                "Usa: <code>/pla D5G960</code>\n"
                "<code>/pla ABC123</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = args[0].upper()

    prog = bot.reply_to(
        message,
        premium(
            "🚗 CONSULTANDO PLACA SUNARP...\n"
            f"🎯 PLACA: <code>{esc(placa)}</code>\n"
            "⏳ Conectando a CODART..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/pla/{placa}")

    if err:
        reembolsar(usuario.id, COSTOS["pla"])
        bot.edit_message_text(
            premium(
                "❌ <b>ERROR API PLA</b>\n\n"
                f"<code>{esc(err)}</code>"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(usuario.id, COSTOS["pla"])
            bot.edit_message_text(
                premium(
                    "❌ <b>PLACA NO ENCONTRADA</b>\n\n"
                    f"Placa: <code>{esc(placa)}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data") or {}
        images = data.get("images", [])
        placa_resp = data.get("placa", placa)

        if not images:
            reembolsar(usuario.id, COSTOS["pla"])
            bot.edit_message_text(
                premium(
                    "⚠️ <b>SIN IMÁGENES</b>\n\n"
                    f"Placa: <code>{esc(placa_resp)}</code>\n"
                    "La SUNARP no devolvió fotos."
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        bot.edit_message_text(
            premium(
                "<b>╔════════════════╗</b>\n"
                "<b>║ 🚗 PLA TRACKER ║</b>\n"
                "<b>╚════════════════╝</b>\n\n"
                f"🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>\n"
                f"🖼️ <b>Imágenes:</b> <code>{len(images)}</code>\n"
                f"🛰️ <b>SOURCE:</b> "
                f"<code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>\n\n"
                "⏳ <i>Enviando fotos...</i>"
                + footer_creditos(None)
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML"
        )

        for idx, img_obj in enumerate(images[:5], 1):
            try:
                data_uri = img_obj.get("data_uri", "")

                if not data_uri:
                    continue

                img_bytes = decodificar_imagen(data_uri)

                if not img_bytes:
                    b64_part = (
                        data_uri.split(",", 1)[1]
                        if "," in data_uri
                        else data_uri
                    )
                    img_bytes = base64.b64decode(b64_part)

                if not img_bytes or len(img_bytes) < 100:
                    continue

                bot.send_photo(
                    message.chat.id,
                    photo=BytesIO(img_bytes),
                    caption=premium(
                        f"🚗 <b>PLACA {esc(placa_resp)}</b> - "
                        f"Foto {idx}/{len(images)}"
                    ),
                    parse_mode="HTML"
                )

            except Exception as e_img:
                logger.error(f"Error imagen PLA {idx}: {e_img}")

    except Exception as e:
        logger.exception(f"ERROR EN /pla: {e}")
        reembolsar(usuario.id, COSTOS["pla"])

        try:
            bot.edit_message_text(
                premium(
                    "❌ <b>ERROR INTERNO PLA</b>\n\n"
                    f"<code>{esc(str(e))}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                premium("❌ ERROR INTERNO PLA"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )

import base64
import os
import tempfile
from io import BytesIO


# =========================
# /SUEL
# =========================

@con_creditos(costo=COSTOS["suel"])
def suel_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(usuario.id, COSTOS["suel"])
        bot.reply_to(
            message,
            premium(
                "⚠️ FORMATO INVÁLIDO\n\n"
                "Usa: <code>/suel 12345678</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]

    prog = bot.reply_to(
        message,
        premium(
            "💰 RASTREANDO SUELDOS PLANILLA...\n"
            f"🎯 TARGET: <code>{esc(dni)}</code>\n"
            "⏳ Conectando a CODART..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/suel/{dni}")

    if err:
        reembolsar(usuario.id, COSTOS["suel"])
        bot.edit_message_text(
            premium(
                "❌ <b>ERROR API SUEL</b>\n\n"
                f"<code>{esc(err)}</code>"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(usuario.id, COSTOS["suel"])
            bot.edit_message_text(
                premium(
                    "❌ <b>SIN RESULTADOS SUEL</b>\n\n"
                    f"DNI: <code>{esc(dni)}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data") or {}
        total = data.get("total_registros", 0)
        sueldos = data.get("sueldos", [])

        if total == 0 or not sueldos:
            reembolsar(usuario.id, COSTOS["suel"])
            bot.edit_message_text(
                premium(
                    "⚠️ <b>SIN REGISTROS EN PLANILLA</b>\n\n"
                    f"DNI: <code>{esc(dni)}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = (
            "<b>╔════════════════╗</b>\n"
            "<b>║ 💰 SUEL TRACKER ║</b>\n"
            "<b>╚════════════════╝</b>\n\n"
            f"🆔 <b>DNI:</b> "
            f"<code>{esc(data.get('consulta', dni))}</code>\n"
            f"📊 <b>TOTAL:</b> "
            f"<code>{esc(total)} REGISTROS</code>\n"
            f"🛰️ <b>SOURCE:</b> "
            f"<code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>\n\n"
            "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        )

        for i, s in enumerate(sueldos, 1):
            empresa = esc(s.get("empresa", "—"))
            ruc = esc(s.get("ruc", "—"))
            periodo = esc(s.get("periodo", "—"))
            situ = esc(s.get("situacion", "—"))
            sueldo = esc(s.get("sueldo", "—"))

            txt += (
                f"\n<b>[{i}] {empresa}</b>\n"
                f"🏢 <b>RUC:</b> <code>{ruc}</code>\n"
                f"📅 <b>Periodo:</b> {periodo}\n"
                f"👔 <b>Situación:</b> {situ}\n"
                f"💵 <b>Sueldo:</b> <code>{sueldo}</code>\n"
            )

        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /suel: {e}")
        reembolsar(usuario.id, COSTOS["suel"])

        try:
            bot.edit_message_text(
                premium(
                    "❌ <b>ERROR INTERNO SUEL</b>\n\n"
                    f"<code>{esc(str(e))}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                premium("❌ ERROR INTERNO SUEL"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )


# =========================
# /PLAT
# =========================

@con_creditos(costo=COSTOS["plat"])
def plat_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()[1:]

    if not args or not validar_placa(args[0]):
        reembolsar(usuario.id, COSTOS["plat"])
        bot.reply_to(
            message,
            premium(
                "⚠️ FORMATO INVÁLIDO\n\n"
                "Usa: <code>/plat D5G960</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = args[0].upper()

    prog = bot.reply_to(
        message,
        premium(
            "🚗 CONSULTANDO FICHA TÉCNICA...\n"
            f"🎯 PLACA: <code>{esc(placa)}</code>\n"
            "⏳ Conectando a SUNARP..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/plat/{placa}")

    if err:
        reembolsar(usuario.id, COSTOS["plat"])
        bot.edit_message_text(
            premium(
                "❌ <b>ERROR API PLAT</b>\n\n"
                f"<code>{esc(err)}</code>"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(usuario.id, COSTOS["plat"])
            bot.edit_message_text(
                premium(
                    "❌ <b>PLACA NO ENCONTRADA</b>\n\n"
                    f"Placa: <code>{esc(placa)}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data") or {}
        carac = data.get("caracteristicas") or {}
        extra = data.get("extra") or {}
        props = data.get("propietarios") or []

        txt = (
            "<b>╔════════════════╗</b>\n"
            "<b>║ 🚗 PLAT TRACKER ║</b>\n"
            "<b>╚════════════════╝</b>\n\n"
            f"🚗 <b>Placa:</b> "
            f"<code>{esc(data.get('placa', placa))}</code>\n"
            f"🔢 <b>Serie:</b> "
            f"<code>{esc(data.get('numero_serie', '—'))}</code>\n"
            f"🔧 <b>VIN:</b> "
            f"<code>{esc(data.get('numero_vin', '—'))}</code>\n"
            f"⚙️ <b>Motor:</b> "
            f"<code>{esc(data.get('numero_motor', '—'))}</code>\n\n"
            "<b>[1] CARACTERÍSTICAS</b>\n"
            f"🏭 <b>Marca:</b> {esc(carac.get('marca', '—'))}\n"
            f"🚙 <b>Modelo:</b> {esc(carac.get('modelo', '—'))}\n"
            f"🚦 <b>Estado:</b> {esc(carac.get('estado', '—'))}\n"
            f"⛽ <b>Combustible:</b> "
            f"{esc(carac.get('tipo_combustible', '—'))}\n\n"
            "<b>[2] DATOS TÉCNICOS</b>\n"
            f"💺 <b>Asientos:</b> {esc(extra.get('asientos', '—'))}\n"
            f"👥 <b>Pasajeros:</b> {esc(extra.get('pasajeros', '—'))}\n"
            f"⚖️ <b>Peso Bruto:</b> "
            f"{esc(extra.get('peso_bruto', '—'))} TN\n"
            f"⚖️ <b>Peso Neto:</b> "
            f"{esc(extra.get('peso_neto', '—'))} TN\n\n"
            "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"<b>[3] PROPIETARIOS ({len(props)})</b>\n"
        )

        for i, p in enumerate(props[:2], 1):
            txt += (
                f"\n<b>[{i}] {esc(p.get('nombres', '—'))}</b>\n"
                f"🆔 <b>DNI/RUC:</b> "
                f"<code>{esc(p.get('dni') or p.get('le') or '—')}</code>\n"
                f"📄 <b>Partida:</b> "
                f"<code>{esc(p.get('partida', '—'))}</code>\n"
                f"📅 <b>Desde:</b> "
                f"{esc(p.get('fecha_propietario', '—'))}\n"
                f"🏠 <b>Dir:</b> {esc(p.get('direccion', '—'))}\n"
            )

        if len(props) > 2:
            txt += f"\n<i>...y {len(props) - 2} propietarios más</i>"

        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /plat: {e}")
        reembolsar(usuario.id, COSTOS["plat"])

        try:
            bot.edit_message_text(
                premium(
                    "❌ <b>ERROR INTERNO PLAT</b>\n\n"
                    f"<code>{esc(str(e))}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                premium("❌ ERROR INTERNO PLAT"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )


# =========================
# /DENUNCIAS
# =========================

@con_creditos(costo=COSTOS["denuncias"])
def denuncias_command(message):
    usuario = message.from_user
    if not usuario:
        return

    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(usuario.id, COSTOS["denuncias"])
        bot.reply_to(
            message,
            premium(
                "⚠️ FORMATO INVÁLIDO\n\n"
                "Usa: <code>/denuncias 12345678</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]

    prog = bot.reply_to(
        message,
        premium(
            "🚨 DESCARGANDO DENUNCIAS COMPLETAS...\n"
            f"🎯 TARGET: <code>{esc(dni)}</code>\n"
            "⏳ Esto puede tardar 20-40s (PDFs)..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/denuncias/{dni}")

    if err:
        reembolsar(usuario.id, COSTOS["denuncias"])
        bot.edit_message_text(
            premium(
                "❌ <b>ERROR API DENUNCIAS</b>\n\n"
                f"<code>{esc(err)}</code>"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(usuario.id, COSTOS["denuncias"])
            bot.edit_message_text(
                premium(
                    "❌ <b>SIN RESULTADOS</b>\n\n"
                    f"DNI: <code>{esc(dni)}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data") or {}
        cantidad = data.get("cantidad_denuncias", 0)
        denuncias = data.get("denuncias") or []

        if cantidad == 0 or not denuncias:
            reembolsar(usuario.id, COSTOS["denuncias"])
            bot.edit_message_text(
                premium(
                    "✅ <b>SIN DENUNCIAS - LIMPIO</b>\n\n"
                    f"DNI: <code>{esc(dni)}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        bot.edit_message_text(
            premium(
                "<b>╔════════════════╗</b>\n"
                "<b>║ 🚨 DENUNCIAS PDF ║</b>\n"
                "<b>╚════════════════╝</b>\n\n"
                f"🆔 <b>DNI:</b> "
                f"<code>{esc(data.get('consulta', dni))}</code>\n"
                f"📊 <b>TOTAL:</b> <code>{esc(cantidad)} PDFS</code>\n"
                f"🛰️ <b>SOURCE:</b> "
                f"<code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>\n\n"
                f"⏳ <b>Enviando {min(cantidad, 4)} archivos...</b>\n"
                "<i>Se enviarán como máximo 4 archivos por consulta.</i>"
                + footer_creditos(None)
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML"
        )

        for d in denuncias[:4]:
            tmp_path = None

            try:
                nombre = d.get(
                    "nombre",
                    f"DENUNCIA-{dni}-{d.get('numero')}.pdf"
                )
                data_uri = d.get("data_uri", "")

                tipo = esc(d.get("tipo", "—"))
                comisaria = esc(d.get("comisaria", "—"))
                n_orden = esc(d.get("n_orden", "—"))
                f_hecho = esc(d.get("f_hecho", "—"))

                if not data_uri:
                    continue

                try:
                    if "," in data_uri:
                        b64_part = data_uri.split(",", 1)[1]
                    else:
                        b64_part = data_uri

                    pdf_bytes = base64.b64decode(b64_part)
                except Exception:
                    logger.warning(
                        f"No se pudo decodificar PDF {d.get('numero')}"
                    )
                    continue

                if not pdf_bytes or len(pdf_bytes) < 100:
                    continue

                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".pdf"
                ) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name

                caption = (
                    f"🚨 DENUNCIA {esc(d.get('numero', '—'))} - {tipo}\n"
                    f"🏢 {comisaria}\n"
                    f"🗂️ Orden: {n_orden}\n"
                    f"📅 Hecho: {f_hecho}"
                )

                with open(tmp_path, "rb") as archivo:
                    bot.send_document(
                        message.chat.id,
                        document=archivo,
                        visible_file_name=nombre,
                        caption=premium(caption),
                        parse_mode="HTML"
                    )

            except Exception as e_pdf:
                logger.error(
                    f"Error enviando PDF {d.get('numero')}: {e_pdf}"
                )

            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        if cantidad > 4:
            bot.send_message(
                message.chat.id,
                premium(
                    f"⚠️ <b>NOTA:</b> Se encontraron {esc(cantidad)} "
                    "denuncias, pero se enviaron como máximo 4 archivos "
                    "en esta consulta."
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )

    except Exception as e:
        logger.exception(f"ERROR EN /denuncias: {e}")
        reembolsar(usuario.id, COSTOS["denuncias"])

        try:
            bot.edit_message_text(
                premium(
                    "❌ <b>ERROR INTERNO DENUNCIAS</b>\n\n"
                    f"<code>{esc(str(e))}</code>"
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                premium("❌ ERROR INTERNO DENUNCIAS"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )

@con_creditos(COSTOS["rqh"])
def rqh_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(message.from_user.id, COSTOS["rqh"])
        bot.reply_to(
            message,
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/rqh 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]
    prog = bot.reply_to(
        message,
        premium(
            f"🚨 CONSULTANDO RQ - REQUISITORIAS...\n"
            f"🎯 TARGET: <code>{esc(dni)}</code>\n"
            "⏳ Conectando a PODER JUDICIAL..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/rqh/{dni}")

    if err:
        reembolsar(message.from_user.id, COSTOS["rqh"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR API RQH</b>\n\n<code>{esc(err)}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(message.from_user.id, COSTOS["rqh"])
            bot.edit_message_text(
                premium(f"❌ <b>SIN RESULTADOS RQH</b>\n\nDNI: <code>{esc(dni)}</code>"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        datos = data.get("datos_personales", {})
        resumen = data.get("resumen_requisitorias", {})
        detalles = data.get("detalle", [])
        docs = data.get("documentos", [])
        cantidad = data.get("cantidad_requisitorias", 0)

        if cantidad == 0 and not detalles:
            reembolsar(message.from_user.id, COSTOS["rqh"])
            bot.edit_message_text(
                premium(
                    f"✅ <b>SIN REQUISITORIAS - LIMPIO</b>\n\n"
                    f"🆔 DNI: <code>{esc(dni)}</code>\n"
                    f"👤 {esc(datos.get('nombres', '—'))}\n"
                    "🛰️ No registra RQ en sistema."
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 🚨 RQH - RQ TRACKER ║</b>
<b>╚════════════════╝</b>

<b>[1] DATOS PERSONALES</b>
🆔 <b>DNI:</b> <code>{esc(datos.get('dni', dni))}</code>
👤 <b>Nombres:</b> {esc(datos.get('nombres', '—'))}
⚧️ <b>Sexo:</b> {esc(datos.get('sexo', '—'))} | 🎂 <b>Edad:</b> {esc(datos.get('edad', '—'))}
📅 <b>F. Nac:</b> {esc(datos.get('fecha_nacimiento', '—'))}
💍 <b>E. Civil:</b> {esc(datos.get('estado_civil', '—'))}
📏 <b>Estatura:</b> {esc(datos.get('estatura', '—'))}
💼 <b>Ocupación:</b> {esc(datos.get('ocupacion', '—'))}
🏠 <b>Dirección:</b> {esc(datos.get('direccion', '—'))}
📍 <b>Distrito:</b> {esc(datos.get('distrito', '—'))}

<b>[2] RESUMEN RQ</b>
📊 <b>Total:</b> <code>{esc(resumen.get('total', cantidad))}</code> | 🔴 <b>Activas:</b> <code>{esc(resumen.get('activas', 0))}</code> | 🟢 <b>Inactivas:</b> <code>{esc(resumen.get('inactivas', 0))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
<b>[3] DETALLE REQUISITORIAS</b>
"""

        for det in detalles[:3]:
            estado = esc(det.get("estado", "—"))
            icon = "🔴 ACTIVA" if estado == "ACTIVA" else "⚪ INACTIVA"

            txt += f"""
{icon} <b>[{esc(det.get('numero'))}] {esc(det.get('tipo', '—'))}</b>
⚖️ <b>Motivo:</b> {esc(det.get('motivo', '—'))}
📕 <b>Delito:</b> {esc(det.get('delito', '—'))}
📁 <b>Exp:</b> <code>{esc(det.get('exp', '—'))}</code> | <b>NRQ:</b> <code>{esc(det.get('nrq', '—'))}</code>
📅 <b>Inicio:</b> {esc(det.get('inicio', '—'))} | <b>Vence:</b> {esc(det.get('vence', '—'))}
🏛️ <b>Dependencia:</b> {esc(det.get('dependencia', '—'))}
📍 <b>Distrito Jud:</b> {esc(det.get('distrito', '—'))}
👤 <b>Agraviado:</b> {esc(det.get('agraviada_o', '—'))}
"""

        if cantidad > 3:
            txt += f"\n<i>...y {cantidad - 3} requisitorias más en PDF</i>"

        # Conserva la llamada original al helper de créditos.
        # Si footer_creditos requiere un Context de PTB, habrá que adaptarlo.
        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

        if docs:
            for doc in docs[:3]:
                tmp_path = None

                try:
                    nombre = doc.get(
                        "nombre",
                        f"RQH-{dni}-{doc.get('numero')}.pdf"
                    )
                    data_uri = doc.get("data_uri", "")

                    if not data_uri or len(data_uri) < 100:
                        continue

                    b64_part = (
                        data_uri.split(",", 1)[1]
                        if "," in data_uri
                        else data_uri
                    )
                    pdf_bytes = base64.b64decode(b64_part)

                    if len(pdf_bytes) < 100:
                        continue

                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=".pdf"
                    ) as tmp:
                        tmp.write(pdf_bytes)
                        tmp_path = tmp.name

                    caption = f"🚨 RQ {doc.get('numero')} - DNI {dni}\n📄 {nombre}"

                    with open(tmp_path, "rb") as pdf_file:
                        bot.send_document(
                            chat_id=message.chat.id,
                            document=pdf_file,
                            caption=premium(caption),
                            parse_mode="HTML"
                        )

                except Exception as e_pdf:
                    logger.error(f"Error PDF RQH: {e_pdf}")

                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.unlink(tmp_path)

    except Exception as e:
        logger.exception(f"ERROR EN /rqh: {e}")
        reembolsar(message.from_user.id, COSTOS["rqh"])

        bot.edit_message_text(
            premium(f"❌ <b>ERROR INTERNO RQH</b>\n\n<code>{esc(str(e))}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )


@con_creditos(COSTOS["dir"])
def dir_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(message.from_user.id, COSTOS["dir"])
        bot.reply_to(
            message,
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dir 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]
    prog = bot.reply_to(
        message,
        premium(
            f"🛰️ RASTREANDO DIRECCIONES...\n"
            f"🎯 TARGET: <code>{esc(dni)}</code>\n"
            "⏳ Conectando a CODART..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/dir/{dni}")

    if err:
        reembolsar(message.from_user.id, COSTOS["dir"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR API DIR</b>\n\n<code>{esc(err)}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(message.from_user.id, COSTOS["dir"])
            bot.edit_message_text(
                premium(f"❌ <b>SIN RESULTADOS DIR</b>\n\nDNI: <code>{esc(dni)}</code>"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        total = data.get("total_registros", 0)
        direcciones = data.get("direcciones", [])

        if total == 0 or not direcciones:
            reembolsar(message.from_user.id, COSTOS["dir"])
            bot.edit_message_text(
                premium(f"⚠️ <b>SIN DIRECCIONES REGISTRADAS</b>\n\nDNI: <code>{esc(dni)}</code>"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 📍 DIR TRACKER ║</b>
<b>╚════════════════╝</b>

🆔 <b>DNI:</b> <code>{esc(data.get('consulta', dni))}</code>
📊 <b>TOTAL:</b> <code>{esc(total)} REGISTROS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for i, d in enumerate(direcciones, 1):
            ubic = esc(d.get("ubicacion", "—"))
            dire = esc(d.get("direccion", "—"))
            fuente = esc(d.get("fuente", "—"))

            txt += (
                f"\n<b>[{i}] {fuente}</b>\n"
                f"📍 <b>Ubicación:</b> {ubic}\n"
                f"🏠 <b>Dirección:</b> {dire}\n"
            )

        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /dir: {e}")
        reembolsar(message.from_user.id, COSTOS["dir"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR INTERNO DIR</b>\n\n<code>{esc(str(e))}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )


@con_creditos(costo=COSTOS["dniv"])
def dniv_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(message.from_user.id, COSTOS["dniv"])
        bot.reply_to(
            message,
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dniv 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]
    prog = bot.reply_to(
        message,
        premium(f"🛰️ INICIANDO DNIV...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/dniv/{dni}")

    if err:
        reembolsar(message.from_user.id, COSTOS["dniv"])
        bot.edit_message_text(
            premium(f"❌ ERROR API\n{esc(err)}\n🔋 CRÉDITOS DEVUELTOS"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(message.from_user.id, COSTOS["dniv"])
        bot.edit_message_text(
            premium(
                f"❌ SIN RESULTADOS\n"
                f"{esc((j or {}).get('message', 'No se encontraron datos'))}\n"
                "🔋 CRÉDITOS REEMBOLSADOS"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data") or {}

    if not data:
        reembolsar(message.from_user.id, COSTOS["dniv"])
        bot.edit_message_text(
            premium("❌ RESPUESTA SIN DATOS\n🔋 CRÉDITOS REEMBOLSADOS"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    texto = format_dnivel_futurista(data, None, "DNIV")
    imgs = data.get("images") or []

    fotos_decod = [
        decodificar_imagen(im.get("data_uri"))
        for im in imgs
        if isinstance(im, dict) and im.get("data_uri")
    ]
    fotos_decod = [f for f in fotos_decod if f]

    if fotos_decod:
        bot.send_photo(
            message.chat.id,
            photo=fotos_decod[0],
            caption=premium(texto),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

        for foto in fotos_decod[1:]:
            try:
                bot.send_photo(message.chat.id, photo=foto)
            except Exception as e:
                logger.warning(f"dniv segunda imagen: {e}")

        try:
            bot.delete_message(prog.chat.id, prog.message_id)
        except Exception:
            pass

        return

    bot.edit_message_text(
        premium(texto),
        chat_id=prog.chat.id,
        message_id=prog.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


@con_creditos(costo=COSTOS["dni"])
def dni_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(message.from_user.id, COSTOS["dni"])
        bot.reply_to(
            message,
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dni 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]
    prog = bot.reply_to(
        message,
        premium(f"🛰️ INICIANDO SCAN RENIEC...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/dni/{dni}")

    if err:
        reembolsar(message.from_user.id, COSTOS["dni"])
        bot.edit_message_text(
            premium(f"❌ ERROR API\n{esc(err)}\n🔋 Devuelto"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(message.from_user.id, COSTOS["dni"])
        bot.edit_message_text(
            premium(f"❌ SIN RESULTADOS\n{esc((j or {}).get('message', 'Sin resultados'))}\n🔋 Reembolsado"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data", {})
    texto = format_dni_futurista(data, None)
    imgs = data.get("images", [])

    if imgs and imgs[0].get("data_uri"):
        foto = decodificar_imagen(imgs[0]["data_uri"])

        if foto:
            bot.send_photo(
                message.chat.id,
                photo=foto,
                caption=premium(texto),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            try:
                bot.delete_message(prog.chat.id, prog.message_id)
            except Exception:
                pass
            return

    bot.edit_message_text(
        premium(texto),
        chat_id=prog.chat.id,
        message_id=prog.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


@con_creditos(costo=COSTOS["dnit"])
def dnit_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(message.from_user.id, COSTOS["dnit"])
        bot.reply_to(
            message,
            premium("⚠️ Usa: <code>/dnit 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]
    prog = bot.reply_to(
        message,
        premium(f"🧬 INICIANDO DNIT X4...\n🎯 TARGET: <code>{esc(dni)}</code>"),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/dnit/{dni}")

    if err:
        reembolsar(message.from_user.id, COSTOS["dnit"])
        bot.edit_message_text(
            premium(f"❌ ERROR\n{esc(err)}"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(message.from_user.id, COSTOS["dnit"])
        bot.edit_message_text(
            premium(f"❌ SIN RESULTADOS\n{esc((j or {}).get('message', 'Sin resultados'))}"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data", {})
    texto = format_dnit_futurista(data, None)
    imgs = data.get("images", [])

    fotos_decod = [
        decodificar_imagen(im.get("data_uri"))
        for im in imgs
        if isinstance(im, dict) and im.get("data_uri")
    ]
    fotos_decod = [f for f in fotos_decod if f]

    if fotos_decod:
        bot.send_photo(
            message.chat.id,
            photo=fotos_decod[0],
            caption=premium(texto),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

        for foto in fotos_decod[1:4]:
            try:
                bot.send_photo(message.chat.id, photo=foto)
            except Exception:
                pass

        try:
            bot.delete_message(prog.chat.id, prog.message_id)
        except Exception:
            pass

        return

    bot.edit_message_text(
        premium(texto),
        chat_id=prog.chat.id,
        message_id=prog.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


@con_creditos(costo=COSTOS["nm"])
def nm_command(message):
    args = (message.text or "").split()[1:]

    if len(args) != 3:
        reembolsar(message.from_user.id, COSTOS["nm"])
        bot.reply_to(
            message,
            premium(
                "⚠️ <b>FORMATO INVÁLIDO</b>\n\n"
                "Usa: <code>/nm NOMBRE APELLIDO1 APELLIDO2</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    n1, ap1, ap2 = [x.strip() for x in args]

    if not all(
        re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", x)
        for x in (n1, ap1, ap2)
    ):
        reembolsar(message.from_user.id, COSTOS["nm"])
        bot.reply_to(
            message,
            premium(
                "⚠️ <b>DATOS INVÁLIDOS</b>\n\n"
                "Los 3 segmentos deben contener solamente letras.\n"
                "Ejemplo: <code>/nm JUAN PEREZ GOMEZ</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    consulta = f"{n1} {ap1} {ap2}"

    prog = bot.reply_to(
        message,
        premium(
            f"🔎 <b>BUSCANDO POR NOMBRE...</b>\n\n"
            f"🎯 CONSULTA: <code>{esc(consulta)}</code>\n"
            "⏳ Conectando..."
        ),
        parse_mode="HTML"
    )

    path = (
        f"/nm?n1={quote_plus(n1)}"
        f"&ap1={quote_plus(ap1)}"
        f"&ap2={quote_plus(ap2)}"
    )
    j, err = codart_get(path)

    if err:
        reembolsar(message.from_user.id, COSTOS["nm"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR API</b>\n{esc(err)}\n🔋 CRÉDITOS DEVUELTOS"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(message.from_user.id, COSTOS["nm"])
        bot.edit_message_text(
            premium(
                f"❌ <b>SIN RESULTADOS</b>\n"
                f"{esc((j or {}).get('message', 'No se encontraron datos'))}\n"
                "🔋 CRÉDITOS REEMBOLSADOS"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data") or {}
    resultados = data.get("resultados") or []

    if not resultados:
        reembolsar(message.from_user.id, COSTOS["nm"])
        bot.edit_message_text(
            premium("❌ <b>NO SE ENCONTRARON RESULTADOS</b>\n🔋 CRÉDITOS REEMBOLSADOS"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    texto = format_nm_futurista(data, None, consulta)

    bot.edit_message_text(
        premium(texto),
        chat_id=prog.chat.id,
        message_id=prog.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )
@con_creditos(COSTOS["den"])
def den_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_dni(args[0]):
        reembolsar(message.from_user.id, COSTOS["den"])
        bot.reply_to(
            message,
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/den 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = args[0]
    prog = bot.reply_to(
        message,
        premium(
            f"🚨 RASTREANDO DENUNCIAS PNP...\n"
            f"🎯 TARGET: <code>{esc(dni)}</code>\n"
            "⏳ Conectando a CODART..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/den/{dni}")

    if err:
        reembolsar(message.from_user.id, COSTOS["den"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR API DEN</b>\n\n<code>{esc(err)}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(message.from_user.id, COSTOS["den"])
            bot.edit_message_text(
                premium(f"❌ <b>SIN RESULTADOS DEN</b>\n\nDNI: <code>{esc(dni)}</code>"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        cantidad = data.get("cantidad_denuncias", 0)
        denuncias = data.get("denuncias", [])

        if cantidad == 0 or not denuncias:
            reembolsar(message.from_user.id, COSTOS["den"])
            bot.edit_message_text(
                premium(
                    f"✅ <b>SIN DENUNCIAS REGISTRADAS</b>\n\n"
                    f"DNI: <code>{esc(dni)}</code>\n🛰️ Limpio."
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 🚨 DEN TRACKER ║</b>
<b>╚════════════════╝</b>

🆔 <b>DNI:</b> <code>{esc(data.get('consulta', dni))}</code>
📊 <b>TOTAL:</b> <code>{esc(cantidad)} DENUNCIAS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for d in denuncias[:4]:
            numero = esc(d.get("numero", "—"))
            tipo = esc(d.get("tipo", "—"))
            comisaria = esc(d.get("comisaria", "—"))
            n_orden = esc(d.get("n_orden", "—"))
            f_hecho = esc(d.get("f_hecho", "—"))
            f_reg = esc(d.get("f_registro", "—"))
            cond = esc(d.get("condicion", "—"))
            interv = esc(d.get("intervencion", "—"))

            resumen_raw = d.get("resumen", "—") or "—"
            resumen = esc(
                resumen_raw[:300] + ("..." if len(resumen_raw) > 300 else "")
            )

            tipo_upper = str(d.get("tipo", "")).upper()
            icon = (
                "🔴" if tipo_upper in ["DENUNCIADO", "AGRESOR"]
                else "🟢" if tipo_upper in ["AGRAVIADO", "DENUNCIANTE"]
                else "🟡"
            )

            txt += f"""
{icon} <b>[{numero}] {tipo}</b>
🗂️ <b>N° Orden:</b> <code>{n_orden}</code>
🏢 <b>Comisaría:</b> {comisaria}
📅 <b>F. Hecho:</b> {f_hecho}
📝 <b>F. Registro:</b> {f_reg}
📌 <b>Condición:</b> {cond}
🚓 <b>Intervención:</b> {interv}
📄 <b>Resumen:</b> <i>{resumen}</i>
"""

        if cantidad > 4:
            txt += (
                f"\n\n⚠️ <i>...y {cantidad - 4} denuncias más. "
                f"Mostrando 4/{cantidad}</i>"
            )

        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /den: {e}")
        reembolsar(message.from_user.id, COSTOS["den"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR INTERNO DEN</b>\n\n<code>{esc(str(e))}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )


@con_creditos(costo=COSTOS["telcel"])
def telcel_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_cel(args[0]):
        reembolsar(message.from_user.id, COSTOS["telcel"])
        bot.reply_to(
            message,
            premium(
                "⚠️ NÚMERO INVÁLIDO\n"
                "Usa: <code>/telcel 900000000</code>\n"
                "9 dígitos"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    num = args[0]
    prog = bot.reply_to(
        message,
        premium(
            f"📡 TELCEL OS SCANNING...\n"
            f"📱 TARGET: <code>{esc(num)}</code>"
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/telp/cel/{num}")

    if err or (j and not j.get("success")):
        j2, err2 = codart_get(f"/telcel/{num}")
        if j2 and j2.get("success"):
            j = j2
            err = None
        elif not err:
            err = err2

    if err:
        reembolsar(message.from_user.id, COSTOS["telcel"])
        bot.edit_message_text(
            premium(f"❌ ERROR API\n{esc(err)}\n🔋 Devuelto"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(message.from_user.id, COSTOS["telcel"])
        bot.edit_message_text(
            premium(
                f"❌ SIN TITULAR\n"
                f"{esc((j or {}).get('message', 'Sin resultados'))}\n"
                "🔋 Reembolsado"
            ),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data", {})
    texto = format_telcel_futurista(data, None, num)

    bot.edit_message_text(
        premium(texto),
        chat_id=prog.chat.id,
        message_id=prog.message_id,
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


@con_creditos(COSTOS["hsoat"])
def hsoat_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_placa(args[0]):
        reembolsar(message.from_user.id, COSTOS["hsoat"])
        bot.reply_to(
            message,
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/hsoat D5G960</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = args[0].upper()
    prog = bot.reply_to(
        message,
        premium(
            f"🛡️ CONSULTANDO HISTORIAL SOAT...\n"
            f"🎯 PLACA: <code>{esc(placa)}</code>\n"
            "⏳ Conectando a CODART..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/hsoat/{placa}")

    if err:
        reembolsar(message.from_user.id, COSTOS["hsoat"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR API HSOAT</b>\n\n<code>{esc(err)}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(message.from_user.id, COSTOS["hsoat"])
            bot.edit_message_text(
                premium(f"❌ <b>SIN RESULTADOS HSOAT</b>\n\nPlaca: <code>{esc(placa)}</code>"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        cantidad = data.get("cantidad_registros", 0)
        historial = data.get("historial", [])
        placa_resp = data.get("placa", placa)

        if cantidad == 0 or not historial:
            reembolsar(message.from_user.id, COSTOS["hsoat"])
            bot.edit_message_text(
                premium(
                    f"⚠️ <b>SIN HISTORIAL SOAT</b>\n\n"
                    f"🚗 Placa: <code>{esc(placa_resp)}</code>\n"
                    "No registra SOAT."
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 🛡️ HSOAT TRACKER ║</b>
<b>╚════════════════╝</b>

🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>
📊 <b>Total:</b> <code>{esc(cantidad)} REGISTROS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for i, h in enumerate(historial[:4], 1):
            estado = esc(h.get("estado", "—"))
            icon = "🟢" if estado.upper() == "VIGENTE" else "🔴"

            txt += f"""
{icon} <b>[{i}] {estado}</b>
🏢 <b>Compañía:</b> {esc(h.get('compania', '—'))}
📜 <b>Póliza:</b> <code>{esc(h.get('poliza', '—'))}</code>
📄 <b>Tipo:</b> {esc(h.get('tipo_certificado', '—'))}
🚗 <b>Uso:</b> {esc(h.get('uso', '—'))} | <b>Clase:</b> {esc(h.get('clase', '—'))}
📅 <b>Inicio:</b> {esc(h.get('fecha_inicio', '—'))} | <b>Fin:</b> {esc(h.get('fecha_fin', '—'))}
🚓 <b>Control:</b> {esc(h.get('control_policial', '—'))}
"""

        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /hsoat: {e}")
        reembolsar(message.from_user.id, COSTOS["hsoat"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR INTERNO HSOAT</b>\n\n<code>{esc(str(e))}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )


@con_creditos(COSTOS["revtec"])
def revtec_command(message):
    args = (message.text or "").split()[1:]

    if not args or not validar_placa(args[0]):
        reembolsar(message.from_user.id, COSTOS["revtec"])
        bot.reply_to(
            message,
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/revtec ABC123</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = args[0].upper()
    prog = bot.reply_to(
        message,
        premium(
            f"🔧 CONSULTANDO REVISIÓN TÉCNICA...\n"
            f"🎯 PLACA: <code>{esc(placa)}</code>\n"
            "⏳ Conectando a MTC..."
        ),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/revtec/{placa}")

    if err:
        reembolsar(message.from_user.id, COSTOS["revtec"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR API REVTEC</b>\n\n<code>{esc(err)}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j or not j.get("success"):
            reembolsar(message.from_user.id, COSTOS["revtec"])
            bot.edit_message_text(
                premium(f"❌ <b>SIN RESULTADOS REVTEC</b>\n\nPlaca: <code>{esc(placa)}</code>"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        total = data.get("total_resultados", 0)
        registros = data.get("registros", [])
        placa_resp = data.get("placa", placa)

        if total == 0 or not registros:
            reembolsar(message.from_user.id, COSTOS["revtec"])
            bot.edit_message_text(
                premium(
                    f"⚠️ <b>SIN REGISTROS DE REV. TÉCNICA</b>\n\n"
                    f"🚗 Placa: <code>{esc(placa_resp)}</code>\n"
                    "No tiene historial en sistema."
                ),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 🔧 REVTEC TRACKER ║</b>
<b>╚════════════════╝</b>

🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>
📊 <b>Total:</b> <code>{esc(total)} REGISTROS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source', 'CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for i, r in enumerate(registros[:3], 1):
            estado = esc(r.get("estado", "—"))
            resultado = esc(r.get("resultado", "—"))

            estado_upper = str(r.get("estado", "")).upper()
            resultado_upper = str(r.get("resultado", "")).upper()

            icon = (
                "🟢"
                if estado_upper == "VIGENTE" and resultado_upper == "APROBADO"
                else "🔴"
                if resultado_upper != "APROBADO"
                else "🟡"
            )

            txt += f"""
{icon} <b>[{i}] {estado} - {resultado}</b>
📜 <b>Certificado:</b> <code>{esc(r.get('certificado', '—'))}</code>
🏢 <b>Entidad:</b> {esc(r.get('entidad', '—'))}
📍 <b>Dirección:</b> {esc(r.get('direccion', '—'))}
📅 <b>Inspección:</b> {esc(r.get('fecha_inspeccion', '—'))}
⏰ <b>Vence:</b> {esc(r.get('fecha_vencimiento', '—'))}
🚚 <b>Servicio:</b> {esc(r.get('servicio', '—'))}
📝 <b>Obs:</b> {esc(r.get('observaciones', '—'))}
"""

        if total > 3:
            txt += f"\n<i>...y {total - 3} registros más en historial</i>"

        bot.edit_message_text(
            premium(txt + footer_creditos(None)),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /revtec: {e}")
        reembolsar(message.from_user.id, COSTOS["revtec"])
        bot.edit_message_text(
            premium(f"❌ <b>ERROR INTERNO REVTEC</b>\n\n<code>{esc(str(e))}</code>"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
      )
@con_creditos(costo=COSTOS["facial"])
def facial_command(message):
    photo_file_id = None

    if message.photo:
        photo_file_id = message.photo[-1].file_id
    elif message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo[-1].file_id

    if not photo_file_id:
        reembolsar(message.from_user.id, COSTOS["facial"])
        bot.reply_to(
            message,
            premium(
                "👁️ FACIAL SCAN\n"
                "Envía foto con <code>/facial</code> o responde a una foto"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    prog = bot.reply_to(
        message,
        premium("👁️ FACIAL SCAN INICIADO\n⏳ Analizando..."),
        parse_mode="HTML"
    )

    tmp_path = None

    try:
        tg_file = bot.get_file(photo_file_id)
        file_bytes = bot.download_file(tg_file.file_path)

        tmp_path = os.path.join(
            tempfile.gettempdir(),
            f"facial_{message.from_user.id}.jpg"
        )

        with open(tmp_path, "wb") as archivo:
            archivo.write(file_bytes)

        url = f"{API_BASE}/facial/top"

        with open(tmp_path, "rb") as archivo:
            files = {
                "image_facial": ("facial.jpg", archivo, "image/jpeg")
            }
            r = requests.post(
                url,
                headers=HEADERS_FACIAL,
                files=files,
                timeout=35
            )

        if not r.text:
            reembolsar(message.from_user.id, COSTOS["facial"])
            bot.edit_message_text(
                premium("❌ API VACÍA"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        try:
            j = r.json()
        except ValueError:
            reembolsar(message.from_user.id, COSTOS["facial"])
            bot.edit_message_text(
                premium(f"❌ NO JSON: {esc(r.text[:300])}"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        if r.status_code != 200 or not j.get("success"):
            reembolsar(message.from_user.id, COSTOS["facial"])
            bot.edit_message_text(
                premium("❌ SIN COINCIDENCIAS - Reembolsado"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        rostros = data.get("rostros", [])

        if not rostros:
            reembolsar(message.from_user.id, COSTOS["facial"])
            bot.edit_message_text(
                premium("❌ 0 ROSTROS - Reembolsado"),
                chat_id=prog.chat.id,
                message_id=prog.message_id,
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = (
            "<b>╔═════════════════╗</b>\n"
            "<b>║  👁️ FACIAL SCAN         ║</b>\n"
            "<b>╚════════════════════════╝</b>\n\n"
            f"🎯 TOTAL ROSTROS: <code>{esc(data.get('total_rostros'))}</code>\n"
            f"🧬 TIPO: <code>{esc(data.get('tipo_resultado'))}</code>\n\n"
        )

        for rostro in rostros:
            txt += f"▰─ ROSTRO #{esc(rostro.get('numero_rostro'))} ─▰\n"

            for i, coinc in enumerate(rostro.get("coincidencias", []), 1):
                pct = coinc.get("porcentaje", 0)
                emoji = "🟢" if pct >= 90 else "🟡" if pct >= 75 else "🔴"

                txt += (
                    f"{emoji} {i}. <b>{esc(coinc.get('nombre'))}</b>\n"
                    f"   └─ DNI: <code>{esc(coinc.get('dni'))}</code> "
                    f"| {esc(pct)}%\n"
                )

            txt += "\n"

        # Si footer_creditos depende de ContextTypes de PTB,
        # habrá que adaptar esa función por separado.
        txt += footer_creditos(None)

        bot.edit_message_text(
            premium(txt),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.error(f"facial {e}", exc_info=True)
        reembolsar(message.from_user.id, COSTOS["facial"])

        bot.edit_message_text(
            premium(f"❌ ERROR: {esc(str(e))}"),
            chat_id=prog.chat.id,
            message_id=prog.message_id,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def addcreditos_command(message):
    uid = message.from_user.id

    if ADMIN_ID != 0 and uid != ADMIN_ID:
        bot.reply_to(
            message,
            premium("⛔ ACCESO DENEGADO - SOLO ADMIN"),
            reply_markup=teclado_volver()
        )
        return

    args = (message.text or "").split()[1:]

    if len(args) < 2:
        bot.reply_to(
            message,
            premium(
                "⚙️ USO: <code>/addcreditos &lt;user_id&gt; &lt;cantidad&gt;</code>\n"
                "Ej: <code>/addcreditos 6330231681 100</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        target = int(args[0])
        cant = int(args[1])

        if cant <= 0:
            raise ValueError("La cantidad debe ser mayor que cero.")
        nuevo = agregar_creditos(target, cant)

        bot.reply_to(
            message,
            premium(
                "✅ CRÉDITOS INYECTADOS\n"
                f"👤 USER: <code>{esc(target)}</code>\n"
                f"💳 +{esc(cant)} CRD\n"
                f"🔋 SALDO: {esc(nuevo)} CRD"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        bot.reply_to(
            message,
            premium(f"❌ {esc(str(e))}"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )


def staff_command(message):
    texto_staff = """╔══════════════╗
║   [7] STAFF OFICIAL    ║
╚══════════════╝

[ FUNDADOR ]
😴 @zxxxxx_michi_xxxxxx- Creador & Admin

[ ADMINISTRADORES ]
@zxxxxx_michi_xxxxxx✅ - Soporte 24/7
@zxxxxx_michi_xxxxxx☑️ - Pagos & Créditos

[ MODERADORES ]
⚡ @Mod1 - Soporte
⚡ @Mod2 - Soporte

╔════════════════╗
║  [7] CONTACTO OFICIAL ║
╚════════════════╝

💬 Grupo: t.me/tugrupo
📢 Canal: t.me/tucanal
💌 Soporte: @SoporteBot

<b>[27] OJO:</b>
Solo estos usuarios son staff oficial.
Cuidado con las estafas."""

    try:
        bot.reply_to(
            message,
            premium(texto_staff),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /staff: {e}")

        try:
            bot.reply_to(
                message,
                premium(
                    "❌ <b>ERROR AL MOSTRAR STAFF</b>\n\n"
                    f"<code>{esc(str(e))}</code>"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            pass


def me_command(message):
    u = message.from_user
    saldo = get_creditos(u.id)

    username = f"@{esc(u.username)}" if u.username else "Sin username"

    txt = f"""
╔══════════════╗
║  [9] MI PERFIL  ║
╚══════════════╝

INFORMACIÓN DE USUARIO [3]
━━━━━━━━━━━━━━━━━━━━

[4] ID:
<code>{esc(u.id)}</code>

[12] NOMBRE:
<code>{esc(u.first_name or '')} {esc(u.last_name or '')}</code>

[1] USUARIO:
<code>{username}</code>

[10] SALDO:
<code>{esc(saldo)} CRD</code>

[41] ESTADO: [11] ONLINE

━━━━━━━━━━━━━━━━━━━━
 SPECTER OS v2.5 [3]
[25] Sistema operativo activo
"""

    bot.reply_to(
        message,
        text=premium(txt),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


def register_command(message):
    user_id = message.from_user.id

    usuarios = cargar_usuarios()

    if str(user_id) in usuarios or user_id in usuarios:
        bot.reply_to(
            message,
            premium(
                "[26] <b>YA ESTÁS REGISTRADO</b>\n\n"
                "[28] Tu cuenta ya se encuentra registrada en SPECTER.\n\n"
                "💳 No puedes reclamar nuevamente los <b>10 CRD</b> de bienvenida.\n"
                "DEJE DE INSISTIR EL BOT TAMBIÉN SE CANSA [42]."
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    get_creditos(user_id)

    bot.reply_to(
        message,
        premium(
            "[11] <b>SISTEMA ACTIVADO</b>\n\n"
            "[3] Bienvenido a <b>SPECTER OS v2.5</b>\n"
            "💳 <b>10 CRD</b> de bienvenida\n\n"
            "✅ Registro completado correctamente."
        ),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


# Agrega "reaccion": 5 dentro del diccionario COSTOS.
def reaccion_command(message):
    """[53] Reacciones de WhatsApp."""
    usuario = message.from_user

    if not message or not usuario:
        return

    args = (message.text or "").split()[1:]

    if len(args) != 3:
        bot.reply_to(
            message,
            premium(
                "💗 <b>REACCIONES</b>\n\n"
                "📌 Uso:\n"
                "<code>/reaccion LINK EMOJI CANTIDAD</code>\n\n"
                "📝 Ejemplo:\n"
                "<code>/reaccion https://whatsapp.com/channel/ID/POST ❤️ 400</code>\n\n"
                f"💠 Costo: <b>{COSTOS['reaccion']} CRD</b>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    link, emoji, cantidad_txt = args

    try:
        parsed = urllib.parse.urlparse(link)

        if (
            parsed.scheme != "https"
            or parsed.hostname not in ("whatsapp.com", "www.whatsapp.com")
            or not parsed.path.startswith("/channel/")
        ):
            raise ValueError

    except (ValueError, TypeError):
        bot.reply_to(message, "❌ Enlace de WhatsApp inválido.")
        return

    if not re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", emoji):
        bot.reply_to(message, "❌ Ingresa un emoji válido.")
        return

    try:
        cantidad = int(cantidad_txt)

        if not 1 <= cantidad <= 10000:
            raise ValueError

    except ValueError:
        bot.reply_to(message, "❌ La cantidad debe ser de 1 a 10000.")
        return

    costo = COSTOS["reaccion"]
    saldo = get_creditos(usuario.id)

    if saldo < costo:
        bot.reply_to(
            message,
            f"⚠️ Créditos insuficientes.\n"
            f"💳 Saldo: {saldo} CRD\n"
            f"💸 Costo: {costo} CRD"
        )
        return

    saldo_nuevo = descontar(usuario.id, costo)

    # Se conserva la información equivalente a context.user_data.
    user_data.setdefault(usuario.id, {})
    user_data[usuario.id]["costo_actual"] = costo
    user_data[usuario.id]["saldo_actual"] = saldo_nuevo

    bot.reply_to(
        message,
        premium(
            "💗 <b>REACCION COMPLETADA</b>\n\n"
            f"🔗 Enlace: {esc(link)}\n"
            f"💖 Emoji: {esc(emoji)}\n"
            f"📊 Cantidad simulada: <b>{cantidad}</b>\n\n"
            "🧪 Esta operación es una simulación; no se han enviado reacciones reales.\n\n"
            f"💠 Costo: {costo} CRD\n"
            f"🔋 Saldo restante: {saldo_nuevo} CRD"
        ),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


def buy_command(message, from_callback=False):
    txt = """[3] <b>╔═════════════════════╗
💎 PLANES PREMIUM
╚═════════════════════╝</b>

💰 <b>CRÉDITOS</b>

🥉 100 créditos ➜ <b>S/ 10</b>
🥈 200 créditos ➜ <b>S/ 20</b>
🥇 400 créditos ➜ <b>S/ 30</b>
💠 500 créditos ➜ <b>S/ 40</b>
🚀 800 créditos ➜ <b>S/ 50</b>
👑 2,000 créditos ➜ <b>S/ 100</b>
💎 4,300 créditos ➜ <b>S/ 200</b>

━━━━━━━━━━━━━━━━━━━━━━

♾️ <b>PLANES ILIMITADOS</b>

💥 7 días ➜ <b>S/ 20</b>
⚡ 15 días ➜ <b>S/ 35</b>
🔱 30 días ➜ <b>S/ 60</b>
👑 60 días ➜ <b>S/ 100</b>

━━━━━━━━━━━━━━━━━━━━━━

💳 <b>PAGOS:</b> [36][37][38][39][40]
👤 <b>CONTACTO:</b> @zxxxxx_michi_xxxxxx

⚡ Atención rápida

💡 <b>USA:</b>
<code>/pagar</code> + <b>monto que abonarás</b>

📌 Ejemplo:
<code>/pagar 20</code>

━━━━━━━━━━━━━━━━━━━━━━

[3] <b>SPECTER PERÚ</b>"""

    texto = premium(txt)

    bot.reply_to(
        message,
        texto,
        parse_mode="HTML",
        reply_markup=teclado_volver()
      )

# ================================================================
# STICKERS PREMIUM GLOBALES
# ================================================================
instalar_stickers_premium_globales()

threading.Thread(target=run_flask, daemon=True).start()
logger.info(f"Flask {PORT}")

# ================================================================
# REGISTRO DE COMANDOS
# No se necesita app = Application.builder() en TeleBot.
# ================================================================

bot.register_message_handler(start, commands=["start"])
bot.register_message_handler(register_command, commands=["register"])
bot.register_message_handler(cmds_command, commands=["cmds"])
bot.register_message_handler(cmdss_command, commands=["cmdss"])
bot.register_message_handler(me_command, commands=["me"])
bot.register_message_handler(staff_command, commands=["staff"])
bot.register_message_handler(buy_command, commands=["buy"])
bot.register_message_handler(pagar, commands=["pagar"])

# ===================== CONSULTAS RENIEC ======================

bot.register_message_handler(dni_command, commands=["dni"])
bot.register_message_handler(dnit_command, commands=["dnit"])
bot.register_message_handler(dnivel_command, commands=["dnivel"])
bot.register_message_handler(dniv_command, commands=["dniv"])
bot.register_message_handler(dir_command, commands=["dir"])
bot.register_message_handler(nm_command, commands=["nm"])
bot.register_message_handler(suel_command, commands=["suel"])
bot.register_message_handler(den_command, commands=["den"])
bot.register_message_handler(denuncias_command, commands=["denuncias"])
bot.register_message_handler(rqh_command, commands=["rqh"])
bot.register_message_handler(pla_command, commands=["pla"])
bot.register_message_handler(denpla_command, commands=["denpla"])
bot.register_message_handler(plat_command, commands=["plat"])
bot.register_message_handler(revtec_command, commands=["revtec"])
bot.register_message_handler(hsoat_command, commands=["hsoat"])
bot.register_message_handler(reaccion_command, commands=["reaccion"])

# ===================== CONSULTAS FAMILIA =====================

bot.register_message_handler(agv_command, commands=["ag", "agv"])

# ===================== TELEFONÍA / FACIAL ====================

bot.register_message_handler(telcel_command, commands=["telcel"])
bot.register_message_handler(facial_command, commands=["facial"])

# ======================= ADMINISTRACIÓN ======================

bot.register_message_handler(addcreditos_command, commands=["addcreditos"])
bot.register_message_handler(ip_command, commands=["ip"])
bot.register_message_handler(panel_command, commands=["panel"])
bot.register_message_handler(localizar_command, commands=["localizar"])

# ======================= VOUCHERS ============================

bot.register_message_handler(
    recibir_voucher,
    content_types=["photo"]
)

# ======================= CALLBACKS INLINE ====================

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("cmdss_"))
def _cmdss_callback_handler(call):
    cmdss_callback(call)

@bot.callback_query_handler(func=lambda call: call.data == "ip_otra")
def _ip_callback_handler(call):
    ip_callback(call)

@bot.callback_query_handler(func=lambda call: True)
def _botones_callback_handler(call):
    botones_callback(call)

# ================================================================
# INICIO DEL BOT
# ================================================================

# Inicializar la base de datos GLOBAL antes de aceptar comandos.
init_db()
logger.info("Base de datos MongoDB lista: %s", MONGO_DB_NAME)
bot.infinity_polling(
    skip_pending=True,
    allowed_updates=["message", "callback_query"]
)


