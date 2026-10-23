import os
import re
import base64
import sqlite3
import tempfile
import requests
import logging
import threading
import html
import asyncio
import urllib.parse
import secrets
import ipaddress
from datetime import datetime
from urllib.parse import quote_plus
from aiohttp import web
from functools import wraps
from dotenv import load_dotenv
from flask import Flask

import telebot
from telebot import types
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    MessageEntity,
)

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CODART_TOKEN = os.getenv("CODART_TOKEN")
API_BASE = os.getenv(
    "API_BASE",
    "https://api-codart.cgrt.org/api/v1/consultas/fd"
).rstrip("/")
IPAPI_TOKEN = os.getenv("IPAPI_TOKEN", "")
COSTO_IP = int(os.getenv("COSTO_IP", "10"))
PORT = int(os.getenv("PORT", 10000))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

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

# Configuración de la base de datos
DB_PATH = os.getenv("DB_PATH", "bot.db")
db_dir = os.path.dirname(os.path.abspath(DB_PATH))

if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)
CREDITOS_INICIALES = 2

COSTOS = {
    "dni": 5,
    "COSTO_IP": 10,
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
    "plat": 6
}

COSTO_IP = COSTOS["COSTO_IP"]

# Estado temporal compatible con TeleBot.
# Se utilizará para reemplazar ctx.user_data en las siguientes partes.
user_data = {}


def init_db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=10
    )
    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                user_id INTEGER PRIMARY KEY,
                creditos INTEGER NOT NULL,
                celular TEXT
            )
        """)

        # Agregar la columna celular si la tabla ya existía
        try:
            cur.execute(
                "ALTER TABLE usuarios ADD COLUMN celular TEXT"
            )
        except sqlite3.OperationalError:
            pass

        conn.commit()
    finally:
        conn.close()


def get_creditos(uid: int):
    try:
        conn = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
            timeout=10
        )
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT creditos FROM usuarios WHERE user_id = ?",
                (uid,)
            )
            row = cur.fetchone()

            if row is None:
                cur.execute("""
                    INSERT INTO usuarios (user_id, creditos, celular)
                    VALUES (?, ?, ?)
                """, (uid, CREDITOS_INICIALES, ""))

                conn.commit()
                return CREDITOS_INICIALES

            return row[0]
        finally:
            conn.close()

    except Exception as e:
        logger.error(
            f"Error obteniendo créditos del usuario {uid}: {e}",
            exc_info=True
        )
        return CREDITOS_INICIALES


def cargar_usuarios():
    """Carga los usuarios registrados en un diccionario compatible con el bot."""
    usuarios = {}

    try:
        conn = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
            timeout=10
        )
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, creditos, celular FROM usuarios"
            )

            for user_id, creditos, celular in cur.fetchall():
                usuarios[str(user_id)] = {
                    "creditos": int(creditos),
                    "celular": celular or ""
                }
        finally:
            conn.close()

    except Exception as e:
        logger.error(
            f"Error cargando usuarios: {e}",
            exc_info=True
        )

    return usuarios


def set_creditos(uid, nuevo):
    """Guarda el saldo del usuario y registra errores sin ocultarlos."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        with conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE usuarios SET creditos = ? WHERE user_id = ?",
                (int(nuevo), int(uid)),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO usuarios (user_id, creditos, celular) VALUES (?, ?, ?)",
                    (int(uid), int(nuevo), ""),
                )
        return True
    except Exception as e:
        logger.error("Error actualizando créditos de %s: %s", uid, e, exc_info=True)
        return False
    finally:
        if conn is not None:
            conn.close()


def sumar_creditos(uid, cantidad):
    """Incremento atómico para evitar perder cambios si hay solicitudes simultáneas."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        with conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO usuarios (user_id, creditos, celular) VALUES (?, ?, ?)",
                (int(uid), CREDITOS_INICIALES, ""),
            )
            cur.execute(
                "UPDATE usuarios SET creditos = creditos + ? WHERE user_id = ?",
                (int(cantidad), int(uid)),
            )
            cur.execute("SELECT creditos FROM usuarios WHERE user_id = ?", (int(uid),))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("No se pudo leer el saldo después de actualizar")
            return int(row[0])
    except Exception as e:
        logger.error("Error sumando créditos de %s: %s", uid, e, exc_info=True)
        raise
    finally:
        if conn is not None:
            conn.close()


def descontar(uid, cant):
    """Descuenta únicamente si el saldo alcanza, en una operación atómica."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        with conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE usuarios SET creditos = creditos - ? WHERE user_id = ? AND creditos >= ?",
                (int(cant), int(uid), int(cant)),
            )
            if cur.rowcount != 1:
                return None
            cur.execute("SELECT creditos FROM usuarios WHERE user_id = ?", (int(uid),))
            row = cur.fetchone()
            return int(row[0]) if row else None
    except Exception as e:
        logger.error("Error descontando créditos de %s: %s", uid, e, exc_info=True)
        raise
    finally:
        if conn is not None:
            conn.close()


def reembolsar(uid, cant):
    try:
        nuevo_saldo = get_creditos(uid) + cant
        set_creditos(uid, nuevo_saldo)
        return nuevo_saldo
    except Exception as e:
        logger.error(f"Error reembolsando créditos: {e}")
        return 0


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
                if nuevo is None:
                    saldo_actual = get_creditos(uid)
                    if target:
                        bot.send_message(
                            target.chat.id,
                            premium(
                                f"⚠️ ACCESO DENEGADO\n\n"
                                f"🆔 ID TELEGRAM: {uid}\n"
                                f"💳 SALDO: {saldo_actual} CRD\n"
                                f"💸 REQUERIDO: {costo} CRD\n\n"
                                f"🔋 /buy para recargar"
                            ),
                            reply_markup=teclado_volver()
                        )
                    return
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
╔════════════╗
║  🪪 RENIEC   ║
╚════════════╝

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
    texto = f"""╔═════════════════════╗
[1] SPECTER PERÚ
╚═════════════════════╝

 BOT DE CONSULTAS

[2] Nombre: SPECTER PERÚ [3]
[4] Usuario: {bot_username}
[5] Estado: ONLINE

━━━━━━━━━━━━━━━━━━━━━━
[6] COMANDOS

[7] /register ➜ Registrarte
[8] /cmdss ➜ Ver servicios
[9] /me ➜ Ver perfil
[12] /staff ➜ fundador
[10] /buy ➜ Planes y créditos

━━━━━━━━━━━━━━━━━━━━━━
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
        """╔═════════════════════╗
  MENÚ COMANDOS [3]
╚═════════════════════╝

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
                icon_custom_emoji_id="4900459325689955284"
            ),
            InlineKeyboardButton(
                "VEHÍCULOS",
                callback_data="cat_placa",
                icon_custom_emoji_id="5379754019297283005"
            )
        ],
        [
            InlineKeyboardButton(
                "FAMILIARES",
                callback_data="cat_familiares",
                icon_custom_emoji_id="5413480005348838600"
            ),
            InlineKeyboardButton(
                "TELÉFONOS",
                callback_data="cat_telcel",
                icon_custom_emoji_id="5362373442541815800"
            )
        ],
        [
            InlineKeyboardButton(
                "FACIAL",
                callback_data="cat_facial",
                icon_custom_emoji_id="5386053082005237605"
            ),
            InlineKeyboardButton(
                "RECARGAR",
                callback_data="cat_comprar",
                icon_custom_emoji_id="5398406198340340300"
            )
        ],
        [
            InlineKeyboardButton(
                "DENUNCIAS",
                callback_data="cat_denuncias",
                icon_custom_emoji_id="5364604735010231300"
            ),
            InlineKeyboardButton(
                "DIRECCIÓN",
                callback_data="cat_localizar",
                icon_custom_emoji_id="5371818010436943405"
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
    func=lambda call: bool(call.data)
    and not call.data.startswith("cmdss_")
    and call.data != "ip_otra"
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
        texto_comprar = """╔═════════════════════╗
[3]  PLANES PREMIUM
╚═════════════════════╝

 CRÉDITOS

🥉 100 créditos ➜ S/ 10
🥈 200 créditos ➜ S/ 20
🥇 400 créditos ➜ S/ 30
💠 500 créditos ➜ S/ 40
🚀 800 créditos ➜ S/ 50
👑 2,000 créditos ➜ S/ 100
💎 4,300 créditos ➜ S/ 200

━━━━━━━━━━━━━━━━━━━━━━
♾️ PLANES ILIMITADOS

💥 7 días ➜ S/ 20
⚡ 15 días ➜ S/ 35
🔱 30 días ➜ S/ 60
👑 60 días ➜ S/ 100

━━━━━━━━━━━━━━━━━━━━━━
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

# Enlaces originales de las imágenes
IMAGENES_CMDSS = {
    "cmdss_bancas": "https://ibb.co/0Vqx4pfM",
    "cmdss_fake": "https://files.catbox.moe/udw7be.jpg",
    "cmdss_whatsapp": "https://ibb.co/5gkqqkhG",
    "cmdss_telegram": "https://files.catbox.moe/ENLACE_TELEGRAM.jpg",
    "cmdss_youtube": "https://files.catbox.moe/ENLACE_YOUTUBE.jpg",
    "cmdss_instagram": "https://files.catbox.moe/ENLACE_INSTAGRAM.jpg",
    "cmdss_facebook": "https://files.catbox.moe/ENLACE_FACEBOOK.jpg",
    "cmdss_bot": "https://files.catbox.moe/ENLACE_BOT.jpg",
    "cmdss_panel": "https://files.catbox.moe/ENLACE_PANEL.jpg",
}

NOMBRES_CMDSS = {
    "cmdss_bancas": "🌐 BANCAS",
    "cmdss_fake": "🎭 BANCAS FAKE",
    "cmdss_whatsapp": "📱 WHATSAPP",
    "cmdss_telegram": "✈️ TELEGRAM",
    "cmdss_youtube": "▶️ YOUTUBE",
    "cmdss_instagram": "📸 INSTAGRAM",
    "cmdss_facebook": "📘 FACEBOOK",
    "cmdss_bot": "🤖 BOT DOXEO",
    "cmdss_panel": "⛓️‍💥 PANELES",
}


def teclado_cmdss():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 BANCAS", callback_data="cmdss_bancas"),
            InlineKeyboardButton("🎭 BANCAS FAKE", callback_data="cmdss_fake"),
        ],
        [
            InlineKeyboardButton("📱 WHATSAPP", callback_data="cmdss_whatsapp"),
            InlineKeyboardButton("✈️ TELEGRAM", callback_data="cmdss_telegram"),
        ],
        [
            InlineKeyboardButton("▶️ YOUTUBE", callback_data="cmdss_youtube"),
            InlineKeyboardButton("📸 INSTAGRAM", callback_data="cmdss_instagram"),
        ],
        [
            InlineKeyboardButton("📘 FACEBOOK", callback_data="cmdss_facebook"),
            InlineKeyboardButton("🤖 BOT DOXEO", callback_data="cmdss_bot"),
        ],
        [
            InlineKeyboardButton("⛓️‍💥 PANELES", callback_data="cmdss_panel"),
        ],
    ])


def teclado_volver_cmdss():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Volver al menú",
                callback_data="cmdss_volver",
            )
        ]
    ])


def texto_menu(user, saldo):
    return f"""
<b>SPECTER DATA</b> [3]

[7] Bienvenido al menú de servicios.

Selecciona una categoría para consultar
su información y disponibilidad.

💳 Saldo: <code>{esc(saldo)} CRD</code>
"""


def cmdss_command(message):
    user = message.from_user
    saldo = get_creditos(user.id)

    bot.send_message(
        chat_id=message.chat.id,
        text=premium(texto_menu(user, saldo)),
        parse_mode="HTML",
        reply_markup=teclado_cmdss(),
    )


def cmdss_callback(call):
    """Maneja todos los botones del menú /cmdss."""
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        logger.debug("No se pudo responder al callback CMDSS", exc_info=True)

    mensaje = call.message
    chat_id = mensaje.chat.id
    user = call.from_user

    opciones = {
        "cmdss_bancas": (
            "🌐 BANCAS",
            "Contamos con diferentes bancas\\nPanda 2\\nLemon 4\\nMoni 2\\nPago efectivo 5",
        ),
        "cmdss_fake": (
            "🎭 BANCAS FAKE",
            "Información de bancas y planes disponible según configuración.",
        ),
        "cmdss_whatsapp": (
            "📱 WHATSAPP",
            "REACCIONES PARA CANALES\\n/reaccion + enlace\\nCosto: 20 CRD\\n\\nPara consultas legítimas, usa únicamente servicios autorizados.",
        ),
        "cmdss_telegram": (
            "✈️ TELEGRAM",
            "Herramientas de administración de Telegram. Usa únicamente funciones autorizadas y respeta las reglas de la plataforma.",
        ),
        "cmdss_youtube": (
            "▶️ YOUTUBE",
            "Servicios relacionados con YouTube. Consulta disponibilidad y condiciones antes de solicitar un servicio.",
        ),
        "cmdss_instagram": (
            "📸 INSTAGRAM",
            "Ayuda sobre privacidad y seguridad de Instagram.",
        ),
        "cmdss_facebook": (
            "📘 FACEBOOK",
            "Ayuda sobre seguridad y recuperación de cuentas de Facebook.",
        ),
        "cmdss_bot": (
            "🤖 BOT",
            "Consulta /cmds para ver los comandos disponibles.",
        ),
        "cmdss_panel": (
            "⛓️‍💥 PANELES",
            "PANELES TIKTOK, YOUTUBE, INSTAGRAM Y FACEBOOK\\n\\nComando: /panel\\nCosto indicado: 80 CRD",
        ),
    }

    try:
        if call.data == "cmdss_volver":
            texto = premium(texto_menu(user, get_creditos(user.id)))
            if mensaje.content_type == "photo":
                bot.delete_message(chat_id, mensaje.message_id)
                bot.send_message(
                    chat_id=chat_id,
                    text=texto,
                    parse_mode="HTML",
                    reply_markup=teclado_cmdss(),
                )
            else:
                bot.edit_message_text(
                    text=texto,
                    chat_id=chat_id,
                    message_id=mensaje.message_id,
                    parse_mode="HTML",
                    reply_markup=teclado_cmdss(),
                )
            return

        seleccion = opciones.get(call.data)
        if seleccion is None:
            logger.warning("Callback CMDSS desconocido: %s", call.data)
            return

        titulo, descripcion = seleccion
        texto = premium(
            f"<b>{titulo}</b>\\n\\n{descripcion}\\n\\n"
            f"💳 Saldo: <code>{get_creditos(user.id)} CRD</code>"
        )
        teclado = teclado_volver_cmdss()

        if mensaje.content_type == "photo":
            bot.edit_message_caption(
                chat_id=chat_id,
                message_id=mensaje.message_id,
                caption=texto,
                parse_mode="HTML",
                reply_markup=teclado,
            )
        else:
            bot.edit_message_text(
                text=texto,
                chat_id=chat_id,
                message_id=mensaje.message_id,
                parse_mode="HTML",
                reply_markup=teclado,
            )

    except Exception as e:
        logger.exception("Error en botón CMDSS (%s): %s", call.data, e)
        try:
            bot.send_message(
                chat_id=chat_id,
                text="❌ No se pudo abrir esta categoría. Intenta nuevamente.",
                reply_markup=teclado_cmdss(),
            )
        except Exception:
            logger.exception("No se pudo enviar el aviso de error CMDSS")


# ============== CONSULTA DE IP ==============

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


def consultar_ip_publica(ip):
    if not IPAPI_TOKEN:
        raise ValueError("Falta configurar IPAPI_TOKEN.")

    url = f"https://api.theipapi.com/v1/ip/{ip}"

    respuesta = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {IPAPI_TOKEN}",
            "Accept": "application/json",
        },
        timeout=15,
    )
    respuesta.raise_for_status()

    datos = respuesta.json()

    if (
        not isinstance(datos, dict)
        or datos.get("status") != "OK"
        or not isinstance(datos.get("body"), dict)
    ):
        raise ValueError("La API no devolvió datos válidos.")

    return datos["body"]


@bot.message_handler(commands=["ip"])
def ip_command(message):
    user = message.from_user

    if not user or not message:
        return

    uid = user.id
    partes = (message.text or "").split(maxsplit=1)

    if len(partes) < 2 or not partes[1].strip():
        bot.reply_to(
            message,
            "🌐 Uso: /ip 8.8.8.8\n"
            f"💰 Costo: {COSTO_IP} crédito por consulta exitosa."
        )
        return

    ip = partes[1].strip()

    try:
        direccion = ipaddress.ip_address(ip)
    except ValueError:
        bot.reply_to(
            message,
            "❌ La dirección IP no es válida."
        )
        return

    if not direccion.is_global:
        bot.reply_to(
            message,
            "⚠️ Introduce una dirección IP pública global."
        )
        return

    saldo = get_creditos(uid)

    if saldo < COSTO_IP:
        bot.reply_to(
            message,
            "❌ No tienes créditos suficientes.\n"
            f"💰 Tu saldo: {saldo}\n"
            f"💳 Costo: {COSTO_IP} crédito."
        )
        return

    estado = bot.reply_to(
        message,
        "🔎 Consultando información pública de la IP...\n"
        "⏳ Espera un momento."
    )

    try:
        datos = consultar_ip_publica(str(direccion))

    except requests.HTTPError as exc:
        codigo = (
            exc.response.status_code
            if exc.response is not None
            else None
        )

        if codigo in (401, 403):
            error = "Token inválido, vencido o sin permisos."
        elif codigo == 429:
            error = "Límite de consultas de la API alcanzado."
        else:
            error = f"Error HTTP de la API ({codigo})."

        bot.edit_message_text(
            f"❌ {error}\n"
            "💰 No se descontaron créditos.",
            chat_id=estado.chat.id,
            message_id=estado.message_id,
        )
        return

    except (requests.RequestException, ValueError):
        bot.edit_message_text(
            "❌ No se pudo consultar la API.\n"
            "💰 No se descontaron créditos.\n"
            "Inténtalo nuevamente más tarde.",
            chat_id=estado.chat.id,
            message_id=estado.message_id,
        )
        return

    except Exception:
        logger.exception("Error inesperado al consultar la IP.")
        bot.edit_message_text(
            "❌ Ocurrió un error inesperado.\n"
            "💰 No se descontaron créditos.",
            chat_id=estado.chat.id,
            message_id=estado.message_id,
        )
        return

    # ==========================================================
    # EXTRAER DATOS
    # ==========================================================
    ubicacion = datos.get("location") or {}
    asn_info = datos.get("asn") or {}
    empresa = datos.get("company") or {}

    ip_resultado = datos.get("ip") or str(direccion)
    pais = ubicacion.get("country") or "No disponible"
    codigo_pais = ubicacion.get("country_code") or "N/D"
    region = ubicacion.get("region") or "No disponible"
    ciudad = ubicacion.get("city") or "No disponible"
    lat = ubicacion.get("latitude")
    lon = ubicacion.get("longitude")
    zona = ubicacion.get("timezone") or "No disponible"

    isp = asn_info.get("org_name") or "No disponible"
    asn = asn_info.get("asn") or "No disponible"
    asn_descripcion = (
        asn_info.get("asn_description") or "No disponible"
    )
    nombre_empresa = empresa.get("name") or "No disponible"
    red = asn_info.get("network") or "No disponible"

    es_vpn = datos.get("is_vpn")
    es_datacenter = datos.get("is_datacenter")
    es_bogon = datos.get("is_bogon")

    def seguro(valor):
        return html.escape(str(valor))

    def estado_si_no(valor):
        if valor is True:
            return "Sí"
        if valor is False:
            return "No"
        return "No disponible"

    # ==========================================================
    # VERIFICAR SALDO Y DESCONTAR
    # ==========================================================
    saldo_actual = get_creditos(uid)

    if saldo_actual < COSTO_IP:
        bot.edit_message_text(
            "❌ Tu saldo cambió y ya no alcanza para esta consulta.\n"
            "💰 No se descontaron créditos.",
            chat_id=estado.chat.id,
            message_id=estado.message_id,
        )
        return

    # Descontar únicamente después de una respuesta válida.
    descontar(uid, COSTO_IP)
    saldo_final = get_creditos(uid)

    # ==========================================================
    # FORMATEAR RESULTADO
    # ==========================================================
    texto = (
        "🌐 <b>SPECTER DATA | IP LOOKUP</b>\n\n"
        f"🖥️ IP: <code>{seguro(ip_resultado)}</code>\n"
        f"🌍 País: {seguro(pais)} ({seguro(codigo_pais)})\n"
        f"📍 Región: {seguro(region)}\n"
        f"🏙️ Ciudad aproximada: {seguro(ciudad)}\n"
        f"🧭 Latitud: {seguro(lat if lat is not None else 'N/D')}\n"
        f"🧭 Longitud: {seguro(lon if lon is not None else 'N/D')}\n"
        f"🕒 Zona horaria: {seguro(zona)}\n\n"
        "📡 <b>RED Y ORGANIZACIÓN</b>\n"
        f"🏢 ISP/Organización: {seguro(isp)}\n"
        f"🔗 ASN: {seguro(asn)}\n"
        f"📋 Descripción ASN: {seguro(asn_descripcion)}\n"
        f"🏬 Empresa: {seguro(nombre_empresa)}\n"
        f"🌐 Red: {seguro(red)}\n\n"
        "🛡️ <b>INDICADORES</b>\n"
        f"🔒 VPN detectada: {estado_si_no(es_vpn)}\n"
        f"🖥️ Centro de datos: {estado_si_no(es_datacenter)}\n"
        f"⚠️ IP bogon: {estado_si_no(es_bogon)}\n\n"
        f"💳 Crédito descontado: {COSTO_IP}\n"
        f"💰 Saldo restante: {saldo_final}\n\n"
        "⚠️ <i>La geolocalización es aproximada; "
        "no identifica una dirección física exacta.</i>"
    )

    botones = None

    if lat is not None and lon is not None:
        try:
            lat_num = float(lat)
            lon_num = float(lon)

            if (
                -90 <= lat_num <= 90
                and -180 <= lon_num <= 180
            ):
                botones = teclado_ip(lat_num, lon_num)

        except (ValueError, TypeError):
            botones = None

    bot.edit_message_text(
        texto,
        chat_id=estado.chat.id,
        message_id=estado.message_id,
        parse_mode="HTML",
        reply_markup=botones,
        disable_web_page_preview=True,
    )


# ==========================================================
# BOTÓN: CONSULTAR OTRA IP
# ==========================================================
@bot.callback_query_handler(func=lambda call: call.data == "ip_otra")
def ip_otra_callback(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    try:
        bot.send_message(
            call.message.chat.id,
            "🌐 Envía la IP pública que deseas consultar.\n"
            f"Ejemplo: <code>/ip 8.8.8.8</code>\n"
            f"💰 Costo: {COSTO_IP} crédito por consulta exitosa.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Error al solicitar otra IP: %s", e)
# ============== CALLBACK CONSULTAR OTRA IP ==============

# Ya está registrado en la parte anterior:
# @bot.callback_query_handler(func=lambda call: call.data == "ip_otra")
# def ip_otra_callback(call):
#     ...


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

        nuevo = sumar_creditos(target, cant)

        bot.reply_to(
            message,
            premium(
                "✅ CRÉDITOS INYECTADOS\n"
                f"👤 USER: <code>{esc(target)}</code>\n"
                f"🆔 TU ID ADMIN: <code>{esc(uid)}</code>\n"
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

# ======================= VOUCHERS ============================

bot.register_message_handler(
    recibir_voucher,
    content_types=["photo"]
)

# ======================= CALLBACKS INLINE ====================

# Registrar primero los callbacks específicos y dejar el general al final.
def _cmdss_callback_handler(call):
    cmdss_callback(call)

def _ip_callback_handler(call):
    ip_callback(call)

def _botones_callback_handler(call):
    botones_callback(call)

bot.register_callback_query_handler(
    _cmdss_callback_handler,
    func=lambda call: bool(call.data) and call.data.startswith("cmdss_"),
)
bot.register_callback_query_handler(
    _ip_callback_handler,
    func=lambda call: call.data == "ip_otra",
)
bot.register_callback_query_handler(
    _botones_callback_handler,
    func=lambda call: bool(call.data)
    and not call.data.startswith("cmdss_")
    and call.data != "ip_otra",
)

# ================================================================
# INICIO DEL BOT
# ================================================================

logger.info("⚜️ SPECTER FUTURISTA ONLINE")
bot.infinity_polling(
    skip_pending=True,
    allowed_updates=["message", "callback_query"]
)

if __name__ == "__main__":
    main()
  
