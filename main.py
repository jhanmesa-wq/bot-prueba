# -*- coding: utf-8 -*-
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
from datetime import datetime
from urllib.parse import quote_plus
from aiohttp import web
from functools import wraps
from dotenv import load_dotenv
from flask import Flask
from telegram import Update, InputMediaPhoto, MessageEntity, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CODART_TOKEN = os.getenv("CODART_TOKEN")
API_BASE = os.getenv("API_BASE", "https://api-codart.cgrt.org/api/v1/consultas/fd").rstrip("/")
PORT = int(os.getenv("PORT", 10000))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
link_foto = "https://files.catbox.moe/0y85js.jpg"
PAYMENT_WEB_URL = "t.me/zxxxxx_michi_xxxxxx"
ADMIN_PAYMENT_ID = 6330231681
DATOS_PAGO = {
    "yape_numero": "925805734",      # Tu número Yape
    "cci": "92200200000387413218", # Tu CCI
    "titular": "Christian Gustavo Ramos Gonzales",
    "qr_url": "https://files.catbox.moe/1037r1.jpg"
}
if not BOT_TOKEN: raise SystemExit("BOT_TOKEN faltante")

HEADERS_JSON = {"Content-Type": "application/json", "Authorization": f"Bearer {CODART_TOKEN}"}
HEADERS_FACIAL = {"Authorization": f"Bearer {CODART_TOKEN}", "Accept": "application/json"}

app_flask = Flask(__name__)
@app_flask.route('/')
def health(): return "⚜️ SPECTER OS v2.5 - ONLINE", 200
@app_flask.route('/health')
def health_check(): return {"status": "ok"}, 200
def run_flask(): app_flask.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

DB_PATH = os.getenv("DB_PATH", "bot.db")
db_dir = os.path.dirname(os.path.abspath(DB_PATH))
if db_dir and not os.path.exists(db_dir): os.makedirs(db_dir, exist_ok=True)

CREDITOS_INICIALES = 2
COSTOS = {"dni":5,"dnit":6,"dnivel":10,"dniv":10,"nm":5,"agv":10,"telcel":8,"facial":60,"dir":6,"suel":6,"den":20,"denuncias":60,"pla":4,"rqh":40,"denpla":40,"hsoat":10,"revtec":10,"plat":6}
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            creditos INTEGER NOT NULL,
            celular TEXT
        )
    """)
    # ✅ Si la tabla ya existía, agregamos la columna celular por si falta
    try:
        cur.execute("ALTER TABLE usuarios ADD COLUMN celular TEXT")
    except:
        pass  # Ya existe, no pasa nada
    conn.commit()
    conn.close()
def get_creditos(uid:int):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT creditos FROM usuarios WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO usuarios (user_id, creditos, celular) VALUES (?,?,?)", (uid, CREDITOS_INICIALES, ""))
            conn.commit(); conn.close()
            return CREDITOS_INICIALES
        conn.close(); return row[0]
    except: return CREDITOS_INICIALES
        
def cargar_usuarios():
    """Carga los usuarios registrados en un diccionario compatible con el bot."""
    usuarios = {}
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT user_id, creditos, celular FROM usuarios")
        for user_id, creditos, celular in cur.fetchall():
            usuarios[str(user_id)] = {
                "creditos": int(creditos),
                "celular": celular or ""
            }
        conn.close()
    except Exception as e:
        logger.error(f"Error cargando usuarios: {e}", exc_info=True)
    return usuarios


def set_creditos(uid,nuevo):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET creditos=? WHERE user_id=?", (nuevo, uid))
        conn.commit(); conn.close()
    except Exception as e: logger.error(e)

def descontar(uid,cant):
    s=get_creditos(uid); n=s-cant; set_creditos(uid,n); return n
def reembolsar(uid,cant):
    try: n=get_creditos(uid)+cant; set_creditos(uid,n); return n
    except: return 0

# ESCAPE PARA HTML - FIX DEL ERROR DE TU FOTO
def esc(t):
    if t is None: return "—"
    return html.escape(str(t).strip(), quote=False)

def clean(v): 
    if v is None or str(v).strip()=="" or str(v).lower()=="none": return "—"
    return str(v).strip()

def validar_dni(d): return bool(re.match(r"^\d{8}$", d))
def validar_cel(n): return bool(re.match(r"^\d{9}$", n))
def validar_placa(p): return bool(re.match(r"^[A-Z0-9]{6,7}$", p.upper()))
def decodificar_imagen(uri):
    try:
        b64 = uri.split(",",1)[1] if "," in uri else uri
        return base64.b64decode(b64)
    except: return None

def teclado_volver(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER AL SISTEMA", callback_data="teclado_volver")]])
def footer_creditos(ctx):
    c=ctx.user_data.get('costo_actual',0); s=ctx.user_data.get('saldo_actual',0)
    return f"\n\n▰▰▰▰▰▰▰▰▰▰▰▰\n💠 COSTO: {c} CRD | 🔋 SALDO: {s} CRD\n⚜️ SPECTER_OS v2.5"

def con_creditos(costo:int):
    def decorator(func):
        @wraps(func)
        async def wrapper(update:Update, context:ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            try:
                uid=update.effective_user.id; saldo=get_creditos(uid)
                if saldo < costo:
                    target = update.message or (update.callback_query.message if update.callback_query else None)
                    if target:
                        await target.reply_text(premium(f"⚠️ ACCESO DENEGADO\n\n💳 SALDO: {saldo} CRD\n💸 REQUERIDO: {costo} CRD\n\n🔋 /buy para recargar"), reply_markup=teclado_volver())
                    return
                nuevo=descontar(uid,costo)
                context.user_data['costo_actual']=costo; context.user_data['saldo_actual']=nuevo
                return await func(update,context,*args,**kwargs)
            except Exception as e:
                logger.error(f"decorador {e}", exc_info=True)
                try: await update.effective_message.reply_text(premium(f"⚠️ SYSTEM ERROR: {esc(str(e))}"), reply_markup=teclado_volver())
                except: pass
        return wrapper
    return decorator

# ============== UI FUTURISTA EN HTML ==============


def format_dni_futurista(data, ctx):
    dni_obj=data.get("dni",{}); nac=data.get("nacimiento",{}); info=data.get("informacion_general",{}); dom=data.get("domicilio",{}); ubi=data.get("ubigeos",{})
    dni_num = dni_obj.get("completo") or dni_obj.get("numero") or data.get("dni") or "—"
    if isinstance(dni_num, dict): dni_num = dni_num.get("completo","—")
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
    txt+=footer_creditos(ctx)
    return txt

def format_dnit_futurista(data, ctx):
    txt=format_dni_futurista(data,ctx)
    return txt.replace("RENIEC CORE v2.5","DNIT X4 // 4 FOTOS")

def format_agv_futurista(data, ctx):
    txt=f"""
╔════════════╗
║  🛰️ AGV TRACE║
╚════════════╝

👁️ DNI: <code>{esc(data.get('dni'))}</code>
👤 Nombres: <b>{esc(data.get('nombres'))}</b>
👥 Apellidos: <b>{esc(data.get('apellidos'))}</b>
⚧ Género: <code>{esc(data.get('genero'))}</code>
🎂 Edad: <code>{esc(data.get('edad'))}</code> años

▰▰ SCAN COMPLETADO ▰▰
"""
    txt+=footer_creditos(ctx)
    return txt

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
    txt=f"""
╔════════════╗
║  🪪 {comando:^8} ║
╚════════════╝

[3] ⚡ SISTEMA NACIONAL DE IDENTIDAD ⚡

🎯 TARGET: <code>{esc(data.get("dni"))}</code>
▰▰▰▰▰▰▰▰▰▰▰▰

👤 <b>IDENTIDAD</b>
├─ Nombres: <b>{esc(data.get("nombres"))}</b>
├─ Apellidos: <b>{esc(data.get("apellidos"))}</b>
└─ Género: <code>{esc(data.get("genero"))}</code>

🎂 <b>INFORMACIÓN</b>
└─ Edad: <code>{esc(data.get("edad"))}</code> años

▰▰ SCAN COMPLETADO ▰▰
"""
    txt+=footer_creditos(ctx)
    return txt


def format_telcel_futurista(data, ctx, numero):
    titulares=data.get("titulares",[]); count=data.get("titulares_encontrados",len(titulares))
    txt=f"""
╔════════════╗
║  📱 TELCEL   ║
╚════════════╝

📞 NÚMERO: <code>{esc(numero)}</code>
🔎 TITULARES: <code>{esc(count)}</code>

"""
    for i,t in enumerate(titulares,1):
        txt+=f"""
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
    txt+=footer_creditos(ctx)
    return txt

# ============== REQUEST HANDLER ==============
def codart_get(path:str):
    url=f"{API_BASE}{path}"
    try:
        r=requests.get(url, headers=HEADERS_JSON, timeout=25)
        logger.info(f"GET {url} -> {r.status_code}")
        if not r.text or not r.text.strip():
            return None, f"API vacía Status {r.status_code}"
        try: j=r.json()
        except: return None, f"API no JSON: {r.text[:400]}"
        return j, None
    except Exception as e:
        return None, str(e)

# ============== COMANDOS ==============
# 💰 PLANES — MISMOS QUE TUS PLANES
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
    """
    Genera un número de pedido diferente en cada solicitud.
    """

    fecha = datetime.now().strftime("%Y%m%d%H%M%S")
    aleatorio = secrets.token_hex(4).upper()

    return f"{fecha}-{aleatorio}"


def generar_orden():
    """
    Genera una orden independiente del número de pedido.
    """

    fecha = datetime.now().strftime("%Y%m%d")
    aleatorio = secrets.token_hex(3).upper()

    return f"ORD-{fecha}-{aleatorio}"


# ============================================================
# COMANDO /PAGAR
# ============================================================

async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        usuario = update.effective_user

        # ----------------------------------------------------
        # MONTO
        # ----------------------------------------------------
        if len(context.args) >= 1:
            total = context.args[0]
            try:
                total_num = float(total)
                if total_num <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                await update.message.reply_text(
                    premium(
                        "[3] <b>MONTO INVÁLIDO</b>\n\n"
                        "❌ El monto indicado no es válido.\n\n"
                        "Ejemplo:\n"
                        "<code>/pagar 20</code>"
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
        # LINK DE PAGO
        # ----------------------------------------------------
        link_pago = (
            f"{PAYMENT_WEB_URL}"
            f"?pedido={pedido}"
            f"&orden={orden}"
            f"&monto={total}"
            f"&usuario={usuario.id}"
        )

        # ----------------------------------------------------
        # TEXTO
        # ----------------------------------------------------
        texto = f"""[3] <b>💳 PAGO DE SERVICIO</b>

🛒 <b>Servicio:</b> Créditos
💰 <b>Total a pagar:</b> S/ {total}

🧾 <b>N° Pedido:</b>
<code>#{pedido}</code>

📦 <b>N° Orden:</b>
<code>{orden}</code>

➡️ <b>CCI:</b>
<code>92200200000387413218</code>

➡️ <b>BANCO:</b>
DALE

⚠️ <b>NOTA:</b>
Adjuntar comprobante de pago.

📸 <b>ATENCIÓN:</b>
Envía la foto del voucher aquí mismo 👇

━━━━━━━━━━━━━━━━━━━━

 PAGA CON [36][37][38][39][40]."""

        # ----------------------------------------------------
        # BOTONES - ESTO ERA LO QUE TE FALTABA
        # ----------------------------------------------------
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "ADMINISTRADOR 📡",
                        url=link_pago
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 VOLVER",
                        callback_data="volver_teclado"
                    )
                ]
            ]
        )

        await update.message.reply_text(
            premium(texto),
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.exception(f"ERROR EN /pagar: {e}")
        await update.message.reply_text(
            premium(f"❌ <b>ERROR EN PAGAR</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
)

        # ----------------------------------------------------
        # ENVIAR PAGO
        # ----------------------------------------------------

        await update.message.reply_photo(
            photo=link_foto,
            caption=premium(texto),
            parse_mode="HTML",
            reply_markup=teclado_pago
        )

        # ----------------------------------------------------
        # GUARDAR DATOS DEL PEDIDO EN EL CONTEXTO
        # ----------------------------------------------------

        context.user_data["pedido_pago"] = pedido
        context.user_data["orden_pago"] = orden
        context.user_data["monto_pago"] = str(total)

    except Exception as e:

        logger.exception(f"ERROR EN /PAGAR: {e}")

        await update.message.reply_text(
            premium(
                "[3] <b>ERROR EN EL SISTEMA DE PAGO</b>\n\n"
                f"❌ <code>{esc(str(e))}</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )


# ============================================================
# RECIBIR VOUCHER
# ============================================================

async def recibir_voucher(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        usuario = update.effective_user
        mensaje = update.message

        # ----------------------------------------------------
        # VERIFICAR QUE SEA UNA FOTO
        # ----------------------------------------------------

        if not mensaje.photo:
            return

        # ----------------------------------------------------
        # DATOS DEL PEDIDO
        # ----------------------------------------------------

        pedido = context.user_data.get(
            "pedido_pago",
            "NO DISPONIBLE"
        )

        orden = context.user_data.get(
            "orden_pago",
            "NO DISPONIBLE"
        )

        monto = context.user_data.get(
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
        # OBTENER LA FOTO CON MAYOR CALIDAD
        # ----------------------------------------------------

        foto = mensaje.photo[-1]

        # ----------------------------------------------------
        # ENVIAR VOUCHER AL ADMIN
        # ----------------------------------------------------

        await context.bot.send_photo(
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

        await mensaje.reply_text(
            premium(texto_usuario),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:

        logger.exception(
            f"ERROR RECIBIENDO VOUCHER: {e}"
        )

        await update.message.reply_text(
            premium(
                "[3] <b>ERROR</b>\n\n"
                "❌ No se pudo enviar el comprobante.\n"
                "Inténtalo nuevamente."
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

async def micelular_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text(
            premium("📱 Uso: /micelular 987654321\n"
            "Registra tu número para que los pagos por Yape\n"
            "se sumen automáticamente a tus créditos ⚡"),
            reply_markup=teclado_volver()
        )
    celular = context.args[0].strip()
    if not re.fullmatch(r"9\d{8}", celular):
        return await update.message.reply_text(
            premium("❌ Número inválido. Debe empezar con 9 y tener 9 dígitos."),
            reply_markup=teclado_volver()
        )
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET celular =? WHERE user_id =?", (celular, user_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        premium(f"✅ Celular {celular} registrado!\n\n"
        "Ahora cuando pagues por Yape a este número,\n"
        "los créditos se sumarán automáticamente ⚡"),
        reply_markup=teclado_volver()
    )

# ============== STICKERS PREMIUM GLOBALES ==============
# USO EN TODO EL BOT:
#   [1] SPECTER PERÚ
#   [2] COMANDOS
#   [3] ESTADO
#   [4] USUARIO
#
# También se acepta el formato antiguo [E1], [E2], etc.
#
# EDITA LOS STICKERS DESDE ESTE ÚNICO LUGAR.
PREMIUM_STICKERS = {
    "1": "5431650332419563627", #verificado negro
    "2": "6219810752887262728", #austronauta
    "3": "6298670698948724690", #verificado rojo 
    "4": "5098585844931888090", #free verde
    "5": "5260553279321944543", #haker
    "6": "5098578393163629920", #minsa
    "7": "5429381339851796035", #buena verde 
    "8": "5179570356695860413", #reniec
    "9": "5177431372788139022", #dni
    "10": "5098536693326152842", #tarjeta plateada
    "11": "5260463209562776385", #bolita verde
    "12": "5096114086958072826", #bandera corona
    "13": "6255716507683129387",#huella digital 
    "14": "5895628223407984747", #verificado azul pequeño
    "15": "4900384661978481721",#sunarp
    "16": "4899924254369252443", #poner judicial 
    "17": "4900307150703690607", #policia
    "18": "4900462624224838579", #impe
    "19": "5033168893103834884", #cusdrados
    "20": "5044554872880367239", #+cuadros 
    "21": "5050820134948570464", #cuadro
    "22": "5005992156825912437",#sucame
    "23": "4907189552327689109", #gobierno del peru 
    "24": "5352625743081775722", #rojo verde
    "25": "5350427505805238170", #tres bolitas asules
    "26": "5895714560840568825", #bolita roja x
    "27": "5213285132709929474", #alerta policía 
    "28": "5269744182917866822", #triaungulo alerta
    "29": "5895564043711680203", #direccion azul
    "30": "6233101473350158283", #mapa dirección 
    "32": "5098160935227360207", #claro
    "33": "5098576194140374149", #entel
    "34": "5098483968307626935", #movistar
    "35": "5096117497162105860", #bitel
    "36": "5098178316960007197", #yape 
    "37": "5098083037405512557", #bcp
    "38": "5095905836878791606", #bbva
    "39": "5098117878180218353", #interbanc
    "40": "5098171771429848438", #plin
    "41": "5474610502279637013", #rayo
    "42": "5188455471505221070", #persona chocando su cabeza 
    "43": "4933732338547820655", #arbol genealógico 
    "44": "4934128274402969696", #ositel
    "45": "5098111813686395869", #lupa
    "46": "5096029016540841163", #bandera diablo
    "47": "5332600543963522398", #billetera
    "48": "6296218646284863141", #telegram
    "49": "5039547615323096064", #100 soles
    "50": "5039692531814630487", #50 soles
    "51": "5042037214591060181", #10 soles
    "52": "5039983009042794161", #20 soles
    
} 

def premium(texto):
    """
    Reemplaza [1], [2], [3]... por emojis premium de Telegram.

    Puedes escribir los marcadores directamente dentro de cualquier
    texto del bot. Ejemplo:

        texto = "[2] SPECTER PERÚ"
        await update.message.reply_text(premium(texto), parse_mode="HTML")

    También mantiene compatibilidad con [E1], [E2]...
    """
    if texto is None:
        return texto

    texto = str(texto)

    def reemplazar(match):
        numero = match.group(1)
        custom_id = PREMIUM_STICKERS.get(numero)
        if not custom_id:
            return match.group(0)
        return f'<tg-emoji emoji-id="{custom_id}">🔹</tg-emoji>'

    texto = re.sub(r"\[(?:E)?(\d+)\]", reemplazar, texto)
    return texto


def premium_global(texto):
    """
    Procesador GLOBAL de stickers premium.

    Cualquier texto que pase por los métodos de Telegram que han sido
    parcheados abajo puede contener directamente:

        [1] [2] [3] [4] ... [12]

    También acepta el formato anterior:
        [E1] [E2] ... [E12]

    Los marcadores se transforman en <tg-emoji> y se envían usando
    parse_mode=HTML.
    """
    if texto is None:
        return texto

    # No convertir objetos que no sean texto.
    if not isinstance(texto, str):
        return texto

    def repl(match):
        numero = match.group(1) or match.group(2)
        custom_id = PREMIUM_STICKERS.get(numero)
        if not custom_id:
            return match.group(0)
        return f'<tg-emoji emoji-id="{custom_id}">🔹</tg-emoji>'

    # Un solo regex para [3] y [E3].
    return re.sub(r'\[(\d+)\]|\[E(\d+)\]', repl, texto)


def _patch_premium_method(cls, method_name):
    """
    Parchea el método async de Telegram para que los textos/captions
    enviados desde cualquier parte del código reconozcan [1], [2], etc.

    El parche solo agrega parse_mode=HTML cuando hay marcadores premium.
    Si el mensaje ya usa parse_mode explícito, se respeta.
    """
    original = getattr(cls, method_name, None)
    if original is None:
        return

    @wraps(original)
    async def wrapped(self, *args, **kwargs):
        changed = False

        # Procesar argumentos posicionales cuando el método utiliza texto
        # como primer/segundo argumento. Para mantener compatibilidad,
        # se priorizan los nombres de parámetros conocidos abajo.
        for key in ("text", "caption"):
            if key in kwargs and isinstance(kwargs[key], str):
                nuevo = premium_global(kwargs[key])
                if nuevo != kwargs[key]:
                    kwargs[key] = nuevo
                    changed = True

        # Métodos como reply_text/edit_text suelen recibir texto como
        # segundo argumento posicional después de self.
        if not changed and args:
            args = list(args)
            for i, value in enumerate(args):
                if isinstance(value, str) and ("[" in value):
                    nuevo = premium_global(value)
                    if nuevo != value:
                        args[i] = nuevo
                        changed = True
                        break
            args = tuple(args)

        if changed and "parse_mode" not in kwargs:
            kwargs["parse_mode"] = "HTML"

        return await original(self, *args, **kwargs)

    setattr(cls, method_name, wrapped)


def instalar_stickers_premium_globales():
    """
    Instala el sistema automático una sola vez.

    Desde este punto, los métodos más utilizados por el bot procesan
    automáticamente [1], [2], [3]... sin tener que llamar premium()
    manualmente en cada comando.
    """
    metodos = (
        "send_message",
        "reply_text",
        "edit_message_text",
        "edit_text",
        "send_photo",
        "reply_photo",
        "send_video",
        "reply_video",
    )

    for metodo in metodos:
        _patch_premium_method(type(Update), metodo) if hasattr(type(Update), metodo) else None

    # python-telegram-bot implementa estos métodos en Message/Bot.
    try:
        from telegram import Message, Bot
        for metodo in metodos:
            if hasattr(Message, metodo):
                _patch_premium_method(Message, metodo)
            if hasattr(Bot, metodo):
                _patch_premium_method(Bot, metodo)
    except Exception as e:
        logger.warning(f"No se pudo instalar parche premium global: {e}")
        

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
[8] /cmds ➜ Ver servicios
[9] /me ➜ Ver perfil
[12] /staff ➜ fundador
[10] /buy ➜ Planes y créditos
[13] /otros ➜ Servicios secundarios 

━━━━━━━━━━━━━━━━━━━━━━
[11] Sistema actualizado y centralizado"""

    return premium(texto)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_creditos(update.effective_user.id)

    bot_username = f"@{context.bot.username}"
    texto = crear_mensaje_premium(bot_username)

    video_url = "https://files.catbox.moe/mfy472.mp4"

    # MANDA VIDEO + TEXTO JUNTOS
    # Los stickers premium se procesan mediante HTML.
    await context.bot.send_video(
        chat_id=update.effective_chat.id,
        video=video_url,
        caption=premium(texto),
        parse_mode="HTML"
    )

def texto_menu_cmds():
    return (
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
                "🧩 RENIEC",
                callback_data="cat_reniec"
            ),
            InlineKeyboardButton(
                "🚙 VEHÍCULOS",
                callback_data="cat_placa"
            )
        ],
        [
            InlineKeyboardButton(
                "🛰️ FAMILIARES",
                callback_data="cat_familiares"
            ),
            InlineKeyboardButton(
                "📱 TELÉFONOS",
                callback_data="cat_telcel"
            )
        ],
        [
            InlineKeyboardButton(
                "🧬 FACIAL",
                callback_data="cat_facial"
            ),
            InlineKeyboardButton(
                "💎 RECARGAR",
                callback_data="cat_comprar"
            )
        ],
        [
            InlineKeyboardButton(
                "🛑 DENUNCIAS",
                callback_data="cat_denuncias"
            ),
            InlineKeyboardButton(
                "📡 DIRECCIÓN",
                callback_data="cat_localizar"
            )
        ],
    ])


async def cmds_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        text=premium(texto_menu_cmds()),
        parse_mode="HTML",
        reply_markup=teclado_menu_cmds()
    )


async def botones_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    q = update.callback_query

    if not q:
        return

    try:
        await q.answer()
    except Exception:
        pass

    # ==========================================================
    # VOLVER AL MENÚ PRINCIPAL
    # ==========================================================
    if q.data == "teclado_volver":
        try:
            await q.edit_message_text(
                text=premium(texto_menu_cmds()),
                parse_mode="HTML",
                reply_markup=teclado_menu_cmds()
            )
        except Exception as e:
            logger.exception(
                f"ERROR BOTÓN VOLVER: {e}"
            )

        return

    # ==========================================================
    # BOTÓN MENU
    # ==========================================================
    if q.data == "menu":
        try:
            await q.edit_message_text(
                text=premium(texto_menu_cmds()),
                parse_mode="HTML",
                reply_markup=teclado_menu_cmds()
            )
        except Exception as e:
            logger.exception(
                f"ERROR BOTÓN MENU: {e}"
            )

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

        await q.edit_message_text(
            text=premium(texto_localizar),
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

        await q.edit_message_text(
            text=premium(texto_denuncias),
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
            await q.edit_message_text(
                text=premium(texto_reniec),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )

        except Exception as e:
            logger.exception(
                f"ERROR BOTÓN RENIEC: {e}"
            )

            try:
                await q.message.reply_text(
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
                    f"ERROR RESPUESTA ALTERNATIVA RENIEC: {e2}"
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

        await q.edit_message_text(
            text=premium(texto_placa),
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

        await q.edit_message_text(
            text=premium(texto_familiares),
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

        await q.edit_message_text(
            text=premium(texto_telcel),
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

        await q.edit_message_text(
            text=premium(texto_comprar),
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

        await q.edit_message_text(
            text=premium(texto_facial),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    # ==========================================================
    # CALLBACK DESCONOCIDO
    # ==========================================================
    else:
        logger.warning(
            f"CALLBACK NO RECONOCIDO: {q.data}"
        )


        
@con_creditos(costo=COSTOS["dnivel"])
async def dnivel_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dnivel"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dnivel 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni=context.args[0]
    prog=await update.message.reply_text(
        premium(f"🛰️ INICIANDO DNIVEL...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando..."),
        parse_mode="HTML"
    )

    j,err=codart_get(f"/dnivel/{dni}")

    if err:
        reembolsar(update.effective_user.id, COSTOS["dnivel"])
        await prog.edit_text(
            premium(f"❌ ERROR API\n{esc(err)}\n🔋 CRÉDITOS DEVUELTOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dnivel"])
        await prog.edit_text(
            premium(f"❌ SIN RESULTADOS\n{esc((j or {}).get('message', 'No se encontraron datos'))}\n🔋 CRÉDITOS REEMBOLSADOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data=j.get("data") or {}
    if not data:
        reembolsar(update.effective_user.id, COSTOS["dnivel"])
        await prog.edit_text(
            premium("❌ RESPUESTA SIN DATOS\n🔋 CRÉDITOS REEMBOLSADOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    texto=format_dnivel_futurista(data, context, "DNIVEL")
    imgs=data.get("images") or []
    fotos_decod=[
        decodificar_imagen(im.get("data_uri"))
        for im in imgs
        if isinstance(im, dict) and im.get("data_uri")
    ]
    fotos_decod=[f for f in fotos_decod if f]

    if fotos_decod:
        await update.message.reply_photo(
            photo=fotos_decod[0],
            caption=premium(texto),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        for f in fotos_decod[1:]:
            try:
                await update.message.reply_photo(photo=f)
            except Exception as e:
                logger.warning(f"dnivel segunda imagen: {e}")
        try:
            await prog.delete()
        except Exception:
            pass
        return

    await prog.edit_text(
        premium(texto),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


# ← Función premium asumiendo que ya la tienes definida
def premium(texto: str) -> str:
    return texto  # Mantén tu implementación original

async def otros_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("💵 Billetes 5G", callback_data="otros_billetes"),
            InlineKeyboardButton("🌐 Crea tu web/bot", callback_data="otros_webbot")
        ],
        [
            InlineKeyboardButton("🏦 Bancas", callback_data="otros_bancas"),
            InlineKeyboardButton("📱 Números Virtuales", callback_data="otros_numeros")
        ],
        [
            InlineKeyboardButton("💻 Página Web", callback_data="otros_pagina")
        ]
    ]

    txt = """
HOLA, AMIGO [3] 

ESTOS SON ALGUNOS DE
NUESTROS SERVICIOS



TENEMOS MUCHAS OPCIONES
PARA TI, ELIGE LA QUE
NECESITAS 




        [1]"""

    await update.message.reply_text(
        premium(txt),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def otros_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "otros_pagina":
        await query.message.edit_text(
            premium("🚧 <b>PROYECTO EN DESARROLLO...</b>\n\n⏳ Estamos trabajando en esto, pronto estará disponible."),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Volver", callback_data="otros_menu")
            ]])
        )

    elif data == "otros_webbot":
        await query.message.edit_text(
            premium("""╔══════════════╗
 SERVICIOS DIGITALES [3]
╚══════════════╝

🌐 CREACIÓN DE PÁGINAS WEB
├─ Diseño profesional y responsivo
├─ Estilo personalizados 
├─ Optimizado para móviles
├─ Integración con botones, enlaces y pagos
└─ Hosting incluido — Solo compártelo

🤖 BOTS DE TELEGRAM A MEDIDA
├─ Comandos personalizados
├─ Sistema de créditos y usuarios
├─ Conexión a APIs 
├─ Recargas automáticas + QR de pago
├─ Menús con botones y navegación completa
└─ Despliegue en  — 24/7 ONLINE

⚡ RÁPIDO • SEGURO • A TU MEDIDA
💬 Cotiza — ¡Respuesta inmediata!
"""),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Contactar", url="t.me/zxxxxx_michi_xxxxxx")],
                [InlineKeyboardButton("🔙 Volver", callback_data="otros_menu")]
            ])
        )

    elif data == "otros_numeros":
        await query.message.edit_text(
            premium(""" ¡Hola! Bienvenido a  specter SMS [3]
 
🤖 El bot más rápido para comprar números virtuales al mejor precio.
📱 Úsalos para activar WhatsApp, Telegram, Facebook y más de 30 servicios.
 
 
⚡ ¿Qué deseas hacer hoy [43]"""),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Contactar", url="t.me/zxxxxx_michi_xxxxxx")],
                [InlineKeyboardButton("🔙 Volver", callback_data="otros_menu")]
            ])
        )

    elif data == "otros_billetes":
        await query.message.edit_text(
            premium("""[47]BILLETES G5 

 [49] BILLETE DE S/.100 ≫ S/. 35
[50] BILLETE DE S/.50 ≫ S/. 22
[52] BILLETE DE S/.20 ≫ S/. 10

[11] COMPRA MÍNIMA DE 5 UNIDADES

[11] ENVIOS A LIMA Y PROVINCIA
[52] 15 UNIDADES MINIMO

[26] NO CONTRA ENTREGA 
[48] @zxxxxx_michi_xxxxxx"""),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Contactar", url="t.me/zxxxxx_michi_xxxxxx")],
                [InlineKeyboardButton("🔙 Volver", callback_data="otros_menu")]
            ])
        )

    elif data == "otros_bancas":
    # ──────────────────────────────────────────────────────────────
    # Opción 1: Enviar foto nueva + texto (recomendado)
    # ──────────────────────────────────────────────────────────────
    await query.message.reply_photo(
        photo="https://files.catbox.moe/ksykip.jpg",  # 📷 Pon tu link de foto aquí
        caption=premium(""" SIMULADORES DE BANCA 2026 [3]

[36] Yape SOLO ➡️S/25 
+
[37]BCP|[38] BBVA| [39] Interbank
  [40] PLIN 
50 SOLES

[26]  Requisito Mínimo: CORREO.

ENTRGA 100 % SEGURA [1]"),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Contactar", url="t.me/zxxxxx_michi_xxxxxx")],
            [InlineKeyboardButton("🔙 Volver", callback_data="otros_menu")]
        ])
    )

    elif data == "otros_menu":
        keyboard = [
            [
                InlineKeyboardButton("💵 Billetes 5G", callback_data="otros_billetes"),
                InlineKeyboardButton("🌐 Crea tu web/bot", callback_data="otros_webbot")
            ],
            [
                InlineKeyboardButton("🏦 Bancas fake", callback_data="otros_bancas"),
                InlineKeyboardButton("📱 Números Virtuales", callback_data="otros_numeros")
            ],
            [
                InlineKeyboardButton("💻 Página Web", callback_data="otros_pagina")
            ]
        ]

        txt = """

HOLA, AMIGO [3] 

ESTOS SON ALGUNOS DE
NUESTROS SERVICIOS



TENEMOS MUCHAS OPCIONES
PARA TI, ELIGE LA QUE
NECESITAS 




        [1]
        """

        await query.message.edit_text(
            premium(txt),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
)
            
@con_creditos(COSTOS["denpla"])
async def denpla_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_placa(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["denpla"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/denpla D4G860</code>\n<code>/denpla ABC123</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = context.args[0].upper()
    prog = await update.message.reply_text(
        premium(f"🚨 RASTREANDO DENUNCIAS POR PLACA...\n🎯 PLACA: <code>{esc(placa)}</code>\n⏳ Conectando a CODART..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/denpla/{placa}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["denpla"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API DENPLA</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["denpla"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS DENPLA</b>\n\nPlaca: <code>{esc(placa)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        cantidad = data.get("cantidad_denuncias", 0)
        denuncias = data.get("denuncias", [])
        placa_resp = data.get("placa", placa)

        if cantidad == 0 or not denuncias:
            reembolsar(update.effective_user.id, COSTOS["denpla"])
            await prog.edit_text(
                premium(f"✅ <b>SIN DENUNCIAS - PLACA LIMPIA</b>\n\n🚗 Placa: <code>{esc(placa_resp)}</code>\n🛰️ Limpio en PNP."),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        # Resumen texto
        txt = f"""<b>╔════════════════╗</b>
<b>║ 🚨 DENPLA TRACKER ║</b>
<b>╚════════════════╝</b>

🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>
📊 <b>TOTAL:</b> <code>{esc(cantidad)} DENUNCIAS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source','CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for d in denuncias[:3]: # 3 en texto para no saturar
            txt += f"""
<b>[{esc(d.get('numero'))}] {esc(d.get('tipo','—'))}</b>
🏢 <b>Comisaría:</b> {esc(d.get('comisaria','—'))}
🗂️ <b>N° Orden:</b> <code>{esc(d.get('n_orden','—'))}</code>
📅 <b>F. Hecho:</b> {esc(d.get('f_hecho','—'))}
📝 <b>F. Registro:</b> {esc(d.get('f_registro','—'))}
📄 <b>Archivo:</b> <code>{esc(d.get('nombre','—'))}</code>
"""

        if cantidad > 3:
            txt += f"\n<i>...y {cantidad - 3} más en PDF</i>"

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

        # Enviar PDFs (max 4)
        for d in denuncias[:4]:
            try:
                nombre = d.get('nombre', f"DENPLA-{placa_resp}-{d.get('numero')}.pdf")
                data_uri = d.get('data_uri','')
                if not data_uri or len(data_uri) < 100:
                    continue

                b64_part = data_uri.split(",",1)[1] if "," in data_uri else data_uri
                pdf_bytes = base64.b64decode(b64_part)

                if len(pdf_bytes) < 100:
                    continue

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name

                caption = f"🚨 DENPLA {d.get('numero')} - {esc(d.get('tipo'))}\n🚗 Placa: {placa_resp}\n🏢 {esc(d.get('comisaria'))}\n🗂️ {esc(d.get('n_orden'))}"

                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=open(tmp_path, 'rb'),
                    filename=nombre,
                    caption=premium(caption),
                    parse_mode="HTML"
                )
                os.unlink(tmp_path)

            except Exception as e_pdf:
                logger.error(f"Error PDF DENPLA {d.get('numero')}: {e_pdf}")

    except Exception as e:
        logger.exception(f"ERROR EN /denpla: {e}")
        reembolsar(update.effective_user.id, COSTOS["denpla"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO DENPLA</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
    )

@con_creditos(COSTOS["pla"])
async def pla_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_placa(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["pla"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/pla D5G960</code>\n<code>/pla ABC123</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = context.args[0].upper()
    prog = await update.message.reply_text(
        premium(f"🚗 CONSULTANDO PLACA SUNARP...\n🎯 PLACA: <code>{esc(placa)}</code>\n⏳ Conectando a CODART..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/pla/{placa}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["pla"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API PLA</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["pla"])
            await prog.edit_text(
                premium(f"❌ <b>PLACA NO ENCONTRADA</b>\n\nPlaca: <code>{esc(placa)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        images = data.get("images", [])
        placa_resp = data.get("placa", placa)

        if not images:
            reembolsar(update.effective_user.id, COSTOS["pla"])
            await prog.edit_text(
                premium(f"⚠️ <b>SIN IMÁGENES</b>\n\nPlaca: <code>{esc(placa_resp)}</code>\nLa SUNARP no devolvió fotos."),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        await prog.edit_text(
            premium(f"<b>╔════════════════╗</b>\n<b>║ 🚗 PLA TRACKER ║</b>\n<b>╚════════════════╝</b>\n\n🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>\n🖼️ <b>Imágenes:</b> <code>{len(images)}</code>\n🛰️ <b>SOURCE:</b> <code>{esc(j.get('source','CODART_X_API_V1'))}</code>\n\n⏳ <i>Enviando fotos...</i>" + footer_creditos(context)),
            parse_mode="HTML"
        )

        # Enviar imágenes
        for idx, img_obj in enumerate(images[:5], 1): # Max 5 fotos
            try:
                data_uri = img_obj.get('data_uri','')
                if not data_uri:
                    continue

                img_bytes = decodificar_imagen(data_uri)
                if not img_bytes:
                    try:
                        b64_part = data_uri.split(",",1)[1] if "," in data_uri else data_uri
                        img_bytes = base64.b64decode(b64_part)
                    except:
                        continue

                if not img_bytes or len(img_bytes) < 100:
                    continue

                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                    tmp.write(img_bytes)
                    tmp_path = tmp.name

                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=open(tmp_path, 'rb'),
                    caption=premium(f"🚗 <b>PLACA {esc(placa_resp)}</b> - Foto {idx}/{len(images)}"),
                    parse_mode="HTML"
                )
                os.unlink(tmp_path)

            except Exception as e_img:
                logger.error(f"Error imagen PLA {idx}: {e_img}")

    except Exception as e:
        logger.exception(f"ERROR EN /pla: {e}")
        reembolsar(update.effective_user.id, COSTOS["pla"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO PLA</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
@con_creditos(COSTOS["suel"])
async def suel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["suel"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/suel 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = context.args[0]
    prog = await update.message.reply_text(
        premium(f"💰 RASTREANDO SUELDOS PLANILLA...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando a CODART..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/suel/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["suel"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API SUEL</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["suel"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS SUEL</b>\n\nDNI: <code>{esc(dni)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        total = data.get("total_registros", 0)
        sueldos = data.get("sueldos", [])

        if total == 0 or not sueldos:
            reembolsar(update.effective_user.id, COSTOS["suel"])
            await prog.edit_text(
                premium(f"⚠️ <b>SIN REGISTROS EN PLANILLA</b>\n\nDNI: <code>{esc(dni)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 💰 SUEL TRACKER ║</b>
<b>╚════════════════╝</b>

🆔 <b>DNI:</b> <code>{esc(data.get('consulta', dni))}</code>
📊 <b>TOTAL:</b> <code>{esc(total)} REGISTROS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source','CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for i, s in enumerate(sueldos, 1):
            empresa = esc(s.get('empresa','—'))
            ruc = esc(s.get('ruc','—'))
            periodo = esc(s.get('periodo','—'))
            situ = esc(s.get('situacion','—'))
            sueldo = esc(s.get('sueldo','—'))
            txt += f"\n<b>[{i}] {empresa}</b>\n🏢 <b>RUC:</b> <code>{ruc}</code>\n📅 <b>Periodo:</b> {periodo}\n👔 <b>Situación:</b> {situ}\n💵 <b>Sueldo:</b> <code>{sueldo}</code>\n"

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /suel: {e}")
        reembolsar(update.effective_user.id, COSTOS["suel"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO SUEL</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
                )
@con_creditos(COSTOS["plat"])
async def plat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_placa(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["plat"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/plat D5G960</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = context.args[0].upper()
    prog = await update.message.reply_text(
        premium(f"🚗 CONSULTANDO FICHA TÉCNICA...\n🎯 PLACA: <code>{esc(placa)}</code>\n⏳ Conectando a SUNARP..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/plat/{placa}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["plat"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API PLAT</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["plat"])
            await prog.edit_text(
                premium(f"❌ <b>PLACA NO ENCONTRADA</b>\n\nPlaca: <code>{esc(placa)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        carac = data.get("caracteristicas", {})
        extra = data.get("extra", {})
        props = data.get("propietarios", [])

        txt = f"""<b>╔════════════════╗</b>
<b>║ 🚗 PLAT TRACKER ║</b>
<b>╚════════════════╝</b>

🚗 <b>Placa:</b> <code>{esc(data.get('placa', placa))}</code>
🔢 <b>Serie:</b> <code>{esc(data.get('numero_serie','—'))}</code>
🔧 <b>VIN:</b> <code>{esc(data.get('numero_vin','—'))}</code>
⚙️ <b>Motor:</b> <code>{esc(data.get('numero_motor','—'))}</code>

<b>[1] CARACTERÍSTICAS</b>
🏭 <b>Marca:</b> {esc(carac.get('marca','—'))}
🚙 <b>Modelo:</b> {esc(carac.get('modelo','—'))}
🚦 <b>Estado:</b> {esc(carac.get('estado','—'))}
⛽ <b>Combustible:</b> {esc(carac.get('tipo_combustible','—'))}

<b>[2] DATOS TÉCNICOS</b>
💺 <b>Asientos:</b> {esc(extra.get('asientos','—'))}
👥 <b>Pasajeros:</b> {esc(extra.get('pasajeros','—'))}
⚖️ <b>Peso Bruto:</b> {esc(extra.get('peso_bruto','—'))} TN
⚖️ <b>Peso Neto:</b> {esc(extra.get('peso_neto','—'))} TN

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
<b>[3] PROPIETARIOS ({len(props)})</b>
"""

        for i, p in enumerate(props[:2], 1): # Max 2 propietarios en texto
            txt += f"""
<b>[{i}] {esc(p.get('nombres','—'))}</b>
🆔 <b>DNI/RUC:</b> <code>{esc(p.get('dni') or p.get('le') or '—')}</code>
📄 <b>Partida:</b> <code>{esc(p.get('partida','—'))}</code>
📅 <b>Desde:</b> {esc(p.get('fecha_propietario','—'))}
🏠 <b>Dir:</b> {esc(p.get('direccion','—'))}
"""

        if len(props) > 2:
            txt += f"\n<i>...y {len(props)-2} propietarios más</i>"

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /plat: {e}")
        reembolsar(update.effective_user.id, COSTOS["plat"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO PLAT</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
    )
@con_creditos(COSTOS["denuncias"])
async def denuncias_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["denuncias"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/denuncias 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = context.args[0]
    prog = await update.message.reply_text(
        premium(f"🚨 DESCARGANDO DENUNCIAS COMPLETAS...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Esto puede tardar 20-40s (PDFs)..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/denuncias/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["denuncias"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API DENUNCIAS</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["denuncias"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS</b>\n\nDNI: <code>{esc(dni)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        cantidad = data.get("cantidad_denuncias", 0)
        denuncias = data.get("denuncias", [])

        if cantidad == 0 or not denuncias:
            reembolsar(update.effective_user.id, COSTOS["denuncias"])
            await prog.edit_text(
                premium(f"✅ <b>SIN DENUNCIAS - LIMPIO</b>\n\nDNI: <code>{esc(dni)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        # Mensaje resumen
        await prog.edit_text(
            premium(f"""<b>╔════════════════╗</b>
<b>║ 🚨 DENUNCIAS PDF ║</b>
<b>╚════════════════╝</b>

🆔 <b>DNI:</b> <code>{esc(data.get('consulta', dni))}</code>
📊 <b>TOTAL:</b> <code>{cantidad} PDFS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source'))}</code>

⏳ <b>Enviando {min(cantidad, 4)} archivos...</b>
<i>Telegram limita a 4-5 PDFs por consulta para no spammear</i>""" + footer_creditos(context)),
            parse_mode="HTML"
        )

        # Enviar PDFs (limitamos a 4 por mensaje para no ban)
        enviados = 0
        for d in denuncias[:4]:
            try:
                nombre = d.get('nombre', f"DENUNCIA-{dni}-{d.get('numero')}.pdf")
                data_uri = d.get('data_uri','')
                tipo = esc(d.get('tipo','—'))
                comisaria = esc(d.get('comisaria','—'))
                n_orden = esc(d.get('n_orden','—'))
                f_hecho = esc(d.get('f_hecho','—'))

                # Decodificar base64
                pdf_bytes = decodificar_imagen(data_uri) # tu función ya sirve para pdf también
                if not pdf_bytes:
                    # Intentar decodificar manual si viene con data:application/pdf;base64,
                    try:
                        b64_part = data_uri.split(",",1)[1] if "," in data_uri else data_uri
                        pdf_bytes = base64.b64decode(b64_part)
                    except:
                        pdf_bytes = None

                if not pdf_bytes or len(pdf_bytes) < 100:
                    await update.message.reply_text(
                        premium(f"⚠️ <b>PDF {d.get('numero')} VACÍO</b>\nTipo: {tipo}\nOrden: <code>{n_orden}</code>"),
                        parse_mode="HTML"
                    )
                    continue

                # Guardar temporal y enviar
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name

                caption = f"🚨 DENUNCIA {d.get('numero')} - {tipo}\n🏢 {comisaria}\n🗂️ Orden: {n_orden}\n📅 Hecho: {f_hecho}\nDNI: {dni}"

                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=open(tmp_path, 'rb'),
                    filename=nombre,
                    caption=premium(caption),
                    parse_mode="HTML"
                )
                enviados += 1
                os.unlink(tmp_path)

            except Exception as e_pdf:
                logger.error(f"Error enviando PDF {d.get('numero')}: {e_pdf}")

        if cantidad > 4:
            await update.message.reply_text(
                premium(f"⚠️ <b>NOTA:</b> Se encontraron {cantidad} denuncias pero Telegram solo permite enviar 4 por consulta para evitar spam.\n\nSi necesitas las demás, vuelve a consultar."),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )

    except Exception as e:
        logger.exception(f"ERROR EN /denuncias: {e}")
        reembolsar(update.effective_user.id, COSTOS["denuncias"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO DENUNCIAS</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
    )
@con_creditos(COSTOS["rqh"])
async def rqh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["rqh"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/rqh 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = context.args[0]
    prog = await update.message.reply_text(
        premium(f"🚨 CONSULTANDO RQ - REQUISITORIAS...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando a PODER JUDICIAL..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/rqh/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["rqh"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API RQH</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["rqh"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS RQH</b>\n\nDNI: <code>{esc(dni)}</code>"),
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
            reembolsar(update.effective_user.id, COSTOS["rqh"])
            await prog.edit_text(
                premium(f"✅ <b>SIN REQUISITORIAS - LIMPIO</b>\n\n🆔 DNI: <code>{esc(dni)}</code>\n👤 {esc(datos.get('nombres','—'))}\n🛰️ No registra RQ en sistema."),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        # Texto principal
        txt = f"""<b>╔════════════════╗</b>
<b>║ 🚨 RQH - RQ TRACKER ║</b>
<b>╚════════════════╝</b>

<b>[1] DATOS PERSONALES</b>
🆔 <b>DNI:</b> <code>{esc(datos.get('dni', dni))}</code>
👤 <b>Nombres:</b> {esc(datos.get('nombres','—'))}
⚧️ <b>Sexo:</b> {esc(datos.get('sexo','—'))} | 🎂 <b>Edad:</b> {esc(datos.get('edad','—'))}
📅 <b>F. Nac:</b> {esc(datos.get('fecha_nacimiento','—'))}
💍 <b>E. Civil:</b> {esc(datos.get('estado_civil','—'))}
📏 <b>Estatura:</b> {esc(datos.get('estatura','—'))}
💼 <b>Ocupación:</b> {esc(datos.get('ocupacion','—'))}
🏠 <b>Dirección:</b> {esc(datos.get('direccion','—'))}
📍 <b>Distrito:</b> {esc(datos.get('distrito','—'))}

<b>[2] RESUMEN RQ</b>
📊 <b>Total:</b> <code>{esc(resumen.get('total', cantidad))}</code> | 🔴 <b>Activas:</b> <code>{esc(resumen.get('activas',0))}</code> | 🟢 <b>Inactivas:</b> <code>{esc(resumen.get('inactivas',0))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
<b>[3] DETALLE REQUISITORIAS</b>
"""

        for det in detalles[:3]: # Max 3 en texto para no exceder límite
            estado = esc(det.get('estado','—'))
            icon = "🔴 ACTIVA" if estado == "ACTIVA" else "⚪ INACTIVA"
            txt += f"""
{icon} <b>[{esc(det.get('numero'))}] {esc(det.get('tipo','—'))}</b>
⚖️ <b>Motivo:</b> {esc(det.get('motivo','—'))}
📕 <b>Delito:</b> {esc(det.get('delito','—'))}
📁 <b>Exp:</b> <code>{esc(det.get('exp','—'))}</code> | <b>NRQ:</b> <code>{esc(det.get('nrq','—'))}</code>
📅 <b>Inicio:</b> {esc(det.get('inicio','—'))} | <b>Vence:</b> {esc(det.get('vence','—'))}
🏛️ <b>Dependencia:</b> {esc(det.get('dependencia','—'))}
📍 <b>Distrito Jud:</b> {esc(det.get('distrito','—'))}
👤 <b>Agraviado:</b> {esc(det.get('agraviada_o','—'))}
"""

        if cantidad > 3:
            txt += f"\n<i>...y {cantidad - 3} requisitorias más en PDF</i>"

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

        # Enviar PDFs si hay
        if docs:
            for doc in docs[:3]:
                try:
                    nombre = doc.get('nombre', f"RQH-{dni}-{doc.get('numero')}.pdf")
                    data_uri = doc.get('data_uri','')
                    if not data_uri or len(data_uri) < 100:
                        continue

                    b64_part = data_uri.split(",",1)[1] if "," in data_uri else data_uri
                    pdf_bytes = base64.b64decode(b64_part)

                    if len(pdf_bytes) < 100:
                        continue

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(pdf_bytes)
                        tmp_path = tmp.name

                    caption = f"🚨 RQ {doc.get('numero')} - DNI {dni}\n📄 {nombre}"

                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=open(tmp_path, 'rb'),
                        filename=nombre,
                        caption=premium(caption),
                        parse_mode="HTML"
                    )
                    os.unlink(tmp_path)
                except Exception as e_pdf:
                    logger.error(f"Error PDF RQH: {e_pdf}")

    except Exception as e:
        logger.exception(f"ERROR EN /rqh: {e}")
        reembolsar(update.effective_user.id, COSTOS["rqh"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO RQH</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
    )
@con_creditos(COSTOS["dir"])
async def dir_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dir"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dir 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = context.args[0]
    prog = await update.message.reply_text(
        premium(f"🛰️ RASTREANDO DIRECCIONES...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando a CODART..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/dir/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["dir"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API DIR</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["dir"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS DIR</b>\n\nDNI: <code>{esc(dni)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        total = data.get("total_registros", 0)
        direcciones = data.get("direcciones", [])

        if total == 0 or not direcciones:
            reembolsar(update.effective_user.id, COSTOS["dir"])
            await prog.edit_text(
                premium(f"⚠️ <b>SIN DIRECCIONES REGISTRADAS</b>\n\nDNI: <code>{esc(dni)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 📍 DIR TRACKER ║</b>
<b>╚════════════════╝</b>

🆔 <b>DNI:</b> <code>{esc(data.get('consulta', dni))}</code>
📊 <b>TOTAL:</b> <code>{esc(total)} REGISTROS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source','CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for i, d in enumerate(direcciones, 1):
            ubic = esc(d.get('ubicacion','—'))
            dire = esc(d.get('direccion','—'))
            fuente = esc(d.get('fuente','—'))
            txt += f"\n<b>[{i}] {fuente}</b>\n📍 <b>Ubicación:</b> {ubic}\n🏠 <b>Dirección:</b> {dire}\n"

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /dir: {e}")
        reembolsar(update.effective_user.id, COSTOS["dir"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO DIR</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
    )
@con_creditos(costo=COSTOS["dniv"])
async def dniv_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dniv"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dniv 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni=context.args[0]
    prog=await update.message.reply_text(
        premium(f"🛰️ INICIANDO DNIV...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando..."),
        parse_mode="HTML"
    )

    j,err=codart_get(f"/dniv/{dni}")

    if err:
        reembolsar(update.effective_user.id, COSTOS["dniv"])
        await prog.edit_text(
            premium(f"❌ ERROR API\n{esc(err)}\n🔋 CRÉDITOS DEVUELTOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dniv"])
        await prog.edit_text(
            premium(f"❌ SIN RESULTADOS\n{esc((j or {}).get('message', 'No se encontraron datos'))}\n🔋 CRÉDITOS REEMBOLSADOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data=j.get("data") or {}
    if not data:
        reembolsar(update.effective_user.id, COSTOS["dniv"])
        await prog.edit_text(
            premium("❌ RESPUESTA SIN DATOS\n🔋 CRÉDITOS REEMBOLSADOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    texto=format_dnivel_futurista(data, context, "DNIV")
    imgs=data.get("images") or []
    fotos_decod=[
        decodificar_imagen(im.get("data_uri"))
        for im in imgs
        if isinstance(im, dict) and im.get("data_uri")
    ]
    fotos_decod=[f for f in fotos_decod if f]

    if fotos_decod:
        await update.message.reply_photo(
            photo=fotos_decod[0],
            caption=premium(texto),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        for f in fotos_decod[1:]:
            try:
                await update.message.reply_photo(photo=f)
            except Exception as e:
                logger.warning(f"dniv segunda imagen: {e}")
        try:
            await prog.delete()
        except Exception:
            pass
        return

    await prog.edit_text(
        premium(texto),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )


@con_creditos(costo=COSTOS["dni"])
async def dni_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text(premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dni 12345678</code>"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    dni=context.args[0]
    prog=await update.message.reply_text(premium(f"🛰️ INICIANDO SCAN RENIEC...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando..."), parse_mode="HTML")
    j,err=codart_get(f"/dni/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await prog.edit_text(premium(f"❌ ERROR API\n{esc(err)}\n🔋 Devuelto"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await prog.edit_text(premium(f"❌ SIN RESULTADOS\n{esc(j.get('message'))}\n🔋 Reembolsado"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    data=j.get("data",{}); texto=format_dni_futurista(data, context)
    imgs=data.get("images",[])
    if imgs and imgs[0].get("data_uri"):
        foto=decodificar_imagen(imgs[0]["data_uri"])
        if foto:
            await update.message.reply_photo(photo=foto, caption=premium(texto), parse_mode="HTML", reply_markup=teclado_volver())
            try: await prog.delete()
            except: pass
            return
    await prog.edit_text(premium(texto), parse_mode="HTML", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["dnit"])
async def dnit_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await update.message.reply_text(premium("⚠️ Usa: <code>/dnit 12345678</code>"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    dni=context.args[0]
    prog=await update.message.reply_text(premium(f"🧬 INICIANDO DNIT X4...\n🎯 TARGET: <code>{esc(dni)}</code>"), parse_mode="HTML")
    j,err=codart_get(f"/dnit/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await prog.edit_text(premium(f"❌ ERROR\n{esc(err)}"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await prog.edit_text(premium(f"❌ SIN RESULTADOS\n{esc(j.get('message'))}"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    data=j.get("data",{}); texto=format_dnit_futurista(data, context)
    imgs=data.get("images",[])
    fotos_decod=[decodificar_imagen(im.get("data_uri")) for im in imgs if im.get("data_uri")]
    fotos_decod=[f for f in fotos_decod if f]
    if fotos_decod:
        await update.message.reply_photo(photo=fotos_decod[0], caption=premium(texto), parse_mode="HTML", reply_markup=teclado_volver())
        for f in fotos_decod[1:4]:
            try: await update.message.reply_photo(photo=f)
            except: pass
        try: await prog.delete()
        except: pass
        return
    await prog.edit_text(premium(texto), parse_mode="HTML", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["nm"])
async def nm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 3:
        reembolsar(update.effective_user.id, COSTOS["nm"])
        await update.message.reply_text(
            premium(
                "⚠️ <b>FORMATO INVÁLIDO</b>\n\n"
                "Usa: <code>/nm NOMBRE APELLIDO1 APELLIDO2</code>"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    n1, ap1, ap2 = [x.strip() for x in context.args]
    if not all(re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", x) for x in (n1, ap1, ap2)):
        reembolsar(update.effective_user.id, COSTOS["nm"])
        await update.message.reply_text(
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
    prog = await update.message.reply_text(
        premium(
            f"🔎 <b>BUSCANDO POR NOMBRE...</b>\n\n"
            f"🎯 CONSULTA: <code>{esc(consulta)}</code>\n"
            "⏳ Conectando..."
        ),
        parse_mode="HTML"
    )

    path = f"/nm?n1={quote_plus(n1)}&ap1={quote_plus(ap1)}&ap2={quote_plus(ap2)}"
    j, err = codart_get(path)

    if err:
        reembolsar(update.effective_user.id, COSTOS["nm"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API</b>\n{esc(err)}\n🔋 CRÉDITOS DEVUELTOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    if not j or not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["nm"])
        await prog.edit_text(
            premium(
                f"❌ <b>SIN RESULTADOS</b>\n"
                f"{esc((j or {}).get('message', 'No se encontraron datos'))}\n"
                "🔋 CRÉDITOS REEMBOLSADOS"
            ),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data = j.get("data") or {}
    resultados = data.get("resultados") or []
    if not resultados:
        reembolsar(update.effective_user.id, COSTOS["nm"])
        await prog.edit_text(
            premium("❌ <b>NO SE ENCONTRARON RESULTADOS</b>\n🔋 CRÉDITOS REEMBOLSADOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    texto = format_nm_futurista(data, context, consulta)
    await prog.edit_text(
        premium(texto),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )

@con_creditos(COSTOS["den"])
async def den_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["den"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/den 12345678</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    dni = context.args[0]
    prog = await update.message.reply_text(
        premium(f"🚨 RASTREANDO DENUNCIAS PNP...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando a CODART..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/den/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["den"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API DEN</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["den"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS DEN</b>\n\nDNI: <code>{esc(dni)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        cantidad = data.get("cantidad_denuncias", 0)
        denuncias = data.get("denuncias", [])

        if cantidad == 0 or not denuncias:
            reembolsar(update.effective_user.id, COSTOS["den"])
            await prog.edit_text(
                premium(f"✅ <b>SIN DENUNCIAS REGISTRADAS</b>\n\nDNI: <code>{esc(dni)}</code>\n🛰️ Limpio."),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        # Header
        txt = f"""<b>╔════════════════╗</b>
<b>║ 🚨 DEN TRACKER ║</b>
<b>╚════════════════╝</b>

🆔 <b>DNI:</b> <code>{esc(data.get('consulta', dni))}</code>
📊 <b>TOTAL:</b> <code>{esc(cantidad)} DENUNCIAS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source','CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for d in denuncias[:4]: # Limitamos a 4 para no exceder límite de Telegram
            numero = esc(d.get('numero','—'))
            tipo = esc(d.get('tipo','—'))
            comisaria = esc(d.get('comisaria','—'))
            n_orden = esc(d.get('n_orden','—'))
            f_hecho = esc(d.get('f_hecho','—'))
            f_reg = esc(d.get('f_registro','—'))
            cond = esc(d.get('condicion','—'))
            interv = esc(d.get('intervencion','—'))
            resumen_raw = d.get('resumen','—') or '—'
            # Recortamos resumen a 250 chars para no explotar el mensaje
            resumen = esc(resumen_raw[:300] + ('...' if len(resumen_raw) > 300 else ''))

            # Emoji por tipo
            icon = "🔴" if tipo.upper() in ["DENUNCIADO","AGRESOR"] else "🟢" if tipo.upper() in ["AGRAVIADO","DENUNCIANTE"] else "🟡"

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
            txt += f"\n\n⚠️ <i>...y {cantidad - 4} denuncias más. Mostrando 4/{cantidad}</i>"

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /den: {e}")
        reembolsar(update.effective_user.id, COSTOS["den"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO DEN</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
@con_creditos(costo=COSTOS["agv"])
async def agv_command(update:Update, context:ContextTypes.DEFAULT_TYPE):

    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text(premium("⚠️ Usa: <code>/ag 12345678</code>"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    dni=context.args[0]
    prog=await update.message.reply_text(premium(f"🛰️ AGV TRACE...\n🎯 <code>{esc(dni)}</code>"), parse_mode="HTML")
    j,err=codart_get(f"/agv/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await prog.edit_text(premium(f"❌ ERROR\n{esc(err)}"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j or not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await prog.edit_text(
            premium(f"❌ SIN RESULTADOS\n{esc((j or {}).get('message', 'No se encontraron relaciones'))}\n🔋 CRÉDITOS REEMBOLSADOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    data=j.get("data") or {}
    relaciones=data.get("relaciones") or []
    if not relaciones:
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await prog.edit_text(
            premium("❌ NO SE ENCONTRARON RELACIONES\n🔋 CRÉDITOS REEMBOLSADOS"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    texto=format_ag_futurista(data, context)
    await prog.edit_text(
        premium(texto),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )

@con_creditos(costo=COSTOS["telcel"])
async def telcel_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_cel(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await update.message.reply_text(premium("⚠️ NÚMERO INVÁLIDO\nUsa: <code>/telcel 900000000</code>\n9 dígitos"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    num=context.args[0]
    prog=await update.message.reply_text(premium(f"📡 TELCEL OS SCANNING...\n📱 TARGET: <code>{esc(num)}</code>"), parse_mode="HTML")
    j,err=codart_get(f"/telp/cel/{num}")
    if err or (j and not j.get("success")):
        j2,err2=codart_get(f"/telcel/{num}")
        if j2 and j2.get("success"): j=j2; err=None
    if err:
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await prog.edit_text(premium(f"❌ ERROR API\n{esc(err)}\n🔋 Devuelto"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await prog.edit_text(premium(f"❌ SIN TITULAR\n{esc(j.get('message'))}\n🔋 Reembolsado"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    data=j.get("data",{}); texto=format_telcel_futurista(data, context, num)
    await prog.edit_text(premium(texto), parse_mode="HTML", reply_markup=teclado_volver())

@con_creditos(COSTOS["hsoat"])
async def hsoat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_placa(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["hsoat"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/hsoat D5G960</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = context.args[0].upper()
    prog = await update.message.reply_text(
        premium(f"🛡️ CONSULTANDO HISTORIAL SOAT...\n🎯 PLACA: <code>{esc(placa)}</code>\n⏳ Conectando a CODART..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/hsoat/{placa}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["hsoat"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API HSOAT</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["hsoat"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS HSOAT</b>\n\nPlaca: <code>{esc(placa)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        cantidad = data.get("cantidad_registros", 0)
        historial = data.get("historial", [])
        placa_resp = data.get("placa", placa)

        if cantidad == 0 or not historial:
            reembolsar(update.effective_user.id, COSTOS["hsoat"])
            await prog.edit_text(
                premium(f"⚠️ <b>SIN HISTORIAL SOAT</b>\n\n🚗 Placa: <code>{esc(placa_resp)}</code>\nNo registra SOAT."),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 🛡️ HSOAT TRACKER ║</b>
<b>╚════════════════╝</b>

🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>
📊 <b>Total:</b> <code>{cantidad} REGISTROS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source','CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""
        for i, h in enumerate(historial[:4], 1):
            estado = esc(h.get('estado','—'))
            icon = "🟢" if estado.upper() == "VIGENTE" else "🔴"
            txt += f"""
{icon} <b>[{i}] {estado}</b>
🏢 <b>Compañía:</b> {esc(h.get('compania','—'))}
📜 <b>Póliza:</b> <code>{esc(h.get('poliza','—'))}</code>
📄 <b>Tipo:</b> {esc(h.get('tipo_certificado','—'))}
🚗 <b>Uso:</b> {esc(h.get('uso','—'))} | <b>Clase:</b> {esc(h.get('clase','—'))}
📅 <b>Inicio:</b> {esc(h.get('fecha_inicio','—'))} | <b>Fin:</b> {esc(h.get('fecha_fin','—'))}
🚓 <b>Control:</b> {esc(h.get('control_policial','—'))}
"""

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /hsoat: {e}")
        reembolsar(update.effective_user.id, COSTOS["hsoat"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO HSOAT</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
)
@con_creditos(COSTOS["revtec"])
async def revtec_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_placa(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["revtec"])
        await update.message.reply_text(
            premium("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/revtec ABC123</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    placa = context.args[0].upper()
    prog = await update.message.reply_text(
        premium(f"🔧 CONSULTANDO REVISIÓN TÉCNICA...\n🎯 PLACA: <code>{esc(placa)}</code>\n⏳ Conectando a MTC..."),
        parse_mode="HTML"
    )

    j, err = codart_get(f"/revtec/{placa}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["revtec"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR API REVTEC</b>\n\n<code>{esc(err)}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
        return

    try:
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["revtec"])
            await prog.edit_text(
                premium(f"❌ <b>SIN RESULTADOS REVTEC</b>\n\nPlaca: <code>{esc(placa)}</code>"),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        data = j.get("data", {})
        total = data.get("total_resultados", 0)
        registros = data.get("registros", [])
        placa_resp = data.get("placa", placa)

        if total == 0 or not registros:
            reembolsar(update.effective_user.id, COSTOS["revtec"])
            await prog.edit_text(
                premium(f"⚠️ <b>SIN REGISTROS DE REV. TÉCNICA</b>\n\n🚗 Placa: <code>{esc(placa_resp)}</code>\nNo tiene historial en sistema."),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
            return

        txt = f"""<b>╔════════════════╗</b>
<b>║ 🔧 REVTEC TRACKER ║</b>
<b>╚════════════════╝</b>

🚗 <b>Placa:</b> <code>{esc(placa_resp)}</code>
📊 <b>Total:</b> <code>{esc(total)} REGISTROS</code>
🛰️ <b>SOURCE:</b> <code>{esc(j.get('source','CODART_X_API_V1'))}</code>

<b>━━━━━━━━━━━━━━━━━━━━━━</b>
"""

        for i, r in enumerate(registros[:3], 1): # Max 3 registros
            estado = esc(r.get('estado','—'))
            resultado = esc(r.get('resultado','—'))
            icon = "🟢" if estado.upper() == "VIGENTE" and resultado.upper() == "APROBADO" else "🔴" if resultado.upper()!= "APROBADO" else "🟡"

            txt += f"""
{icon} <b>[{i}] {estado} - {resultado}</b>
📜 <b>Certificado:</b> <code>{esc(r.get('certificado','—'))}</code>
🏢 <b>Entidad:</b> {esc(r.get('entidad','—'))}
📍 <b>Dirección:</b> {esc(r.get('direccion','—'))}
📅 <b>Inspección:</b> {esc(r.get('fecha_inspeccion','—'))}
⏰ <b>Vence:</b> {esc(r.get('fecha_vencimiento','—'))}
🚚 <b>Servicio:</b> {esc(r.get('servicio','—'))}
📝 <b>Obs:</b> {esc(r.get('observaciones','—'))}
"""

        if total > 3:
            txt += f"\n<i>...y {total - 3} registros más en historial</i>"

        await prog.edit_text(
            premium(txt + footer_creditos(context)),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /revtec: {e}")
        reembolsar(update.effective_user.id, COSTOS["revtec"])
        await prog.edit_text(
            premium(f"❌ <b>ERROR INTERNO REVTEC</b>\n\n<code>{esc(str(e))}</code>"),
            parse_mode="HTML",
            reply_markup=teclado_volver()
    )
@con_creditos(costo=COSTOS["facial"])
async def facial_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    photo_file_id=None; msg=update.message
    if msg.photo: photo_file_id=msg.photo[-1].file_id
    elif msg.reply_to_message and msg.reply_to_message.photo: photo_file_id=msg.reply_to_message.photo[-1].file_id
    if not photo_file_id:
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await msg.reply_text(premium("👁️ FACIAL SCAN\nEnvía foto con <code>/facial</code> o responde a foto"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    prog=await msg.reply_text(premium("👁️ FACIAL SCAN INICIADO\n⏳ Analizando..."), parse_mode="HTML")
    tmp_path=None
    try:
        tg_file=await context.bot.get_file(photo_file_id)
        tmp_path=os.path.join(tempfile.gettempdir(), f"facial_{update.effective_user.id}.jpg")
        await tg_file.download_to_drive(tmp_path)
        url=f"{API_BASE}/facial/top"
        with open(tmp_path,"rb") as f:
            files={"image_facial":("facial.jpg",f,"image/jpeg")}
            r=requests.post(url, headers=HEADERS_FACIAL, files=files, timeout=35)
        if not r.text:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text(premium("❌ API VACÍA"), parse_mode="HTML", reply_markup=teclado_volver())
            return
        try: j=r.json()
        except:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text(premium(f"❌ NO JSON: {esc(r.text[:300])}"), parse_mode="HTML", reply_markup=teclado_volver())
            return
        if r.status_code!=200 or not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text(premium("❌ SIN COINCIDENCIAS - Reembolsado"), parse_mode="HTML", reply_markup=teclado_volver())
            return
        data=j.get("data",{}); rostros=data.get("rostros",[])
        if not rostros:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text(premium("❌ 0 ROSTROS - Reembolsado"), parse_mode="HTML", reply_markup=teclado_volver())
            return
        txt=f"<b>╔═════════════════╗</b>\n<b>║  👁️ FACIAL SCAN         ║</b>\n<b>╚════════════════════════╝</b>\n\n🎯 TOTAL ROSTROS: <code>{esc(data.get('total_rostros'))}</code>\n🧬 TIPO: <code>{esc(data.get('tipo_resultado'))}</code>\n\n"
        for rostro in rostros:
            txt+=f"▰─ ROSTRO #{esc(rostro.get('numero_rostro'))} ─▰\n"
            for i,coinc in enumerate(rostro.get("coincidencias",[]),1):
                pct=coinc.get('porcentaje',0); emoji="🟢" if pct>=90 else "🟡" if pct>=75 else "🔴"
                txt+=f"{emoji} {i}. <b>{esc(coinc.get('nombre'))}</b>\n   └─ DNI: <code>{esc(coinc.get('dni'))}</code> | {esc(pct)}%\n"
            txt+="\n"
        txt+=footer_creditos(context)
        await prog.edit_text(premium(txt), parse_mode="HTML", reply_markup=teclado_volver())
    except Exception as e:
        logger.error(f"facial {e}", exc_info=True)
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await prog.edit_text(premium(f"❌ ERROR: {esc(str(e))}"), parse_mode="HTML", reply_markup=teclado_volver())
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

async def addcreditos_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if ADMIN_ID!=0 and uid!=ADMIN_ID:
        await update.message.reply_text(premium("⛔ ACCESO DENEGADO - SOLO ADMIN"), reply_markup=teclado_volver())
        return
    if len(context.args)<2:
        await update.message.reply_text(premium("⚙️ USO: <code>/addcreditos &lt;user_id&gt; &lt;cantidad&gt;</code>\nEj: <code>/addcreditos 6330231681 100</code>"), parse_mode="HTML", reply_markup=teclado_volver())
        return
    try:
        target=int(context.args[0]); cant=int(context.args[1])
        nuevo=get_creditos(target)+cant; set_creditos(target,nuevo)
        await update.message.reply_text(premium(f"✅ CRÉDITOS INYECTADOS\n👤 USER: <code>{esc(target)}</code>\n💳 +{esc(cant)} CRD\n🔋 SALDO: {esc(nuevo)} CRD"), parse_mode="HTML", reply_markup=teclado_volver())
    except Exception as e:
        await update.message.reply_text(premium(f"❌ {esc(str(e))}"), reply_markup=teclado_volver())
async def staff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text(
            premium(texto_staff),
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )

    except Exception as e:
        logger.exception(f"ERROR EN /staff: {e}")

        try:
            await update.message.reply_text(
                premium(
                    "❌ <b>ERROR AL MOSTRAR STAFF</b>\n\n"
                    f"<code>{esc(str(e))}</code>"
                ),
                parse_mode="HTML",
                reply_markup=teclado_volver()
            )
        except Exception:
            pass

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
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
<code>{esc(u.full_name)}</code>

[1] USUARIO:
<code>{username}</code>

[10] SALDO:
<code>{esc(saldo)} CRD</code>

[41] ESTADO: [11] ONLINE

━━━━━━━━━━━━━━━━━━━━
 SPECTER OS v2.5 [3]
[25] Sistema operativo activo
"""

    await update.message.reply_text(
        text=premium(txt),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )
    
async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Cargar usuarios registrados
    usuarios = cargar_usuarios()

    # Verificar si el usuario ya está registrado
    if str(user_id) in usuarios or user_id in usuarios:
        await update.message.reply_text(
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

    # Registrar usuario nuevo
    get_creditos(user_id)

    await update.message.reply_text(
        premium(
            "[11] <b>SISTEMA ACTIVADO</b>\n\n"
            "[3] Bienvenido a <b>SPECTER OS v2.5</b>\n"
            "💳 <b>10 CRD</b> de bienvenida\n\n"
            "✅ Registro completado correctamente."
        ),
        parse_mode="HTML",
        reply_markup=teclado_volver()
    )
async def buy_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    from_callback=False
):
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

    if from_callback and update.callback_query:
        await update.callback_query.message.edit_text(
            text=texto,
            parse_mode="HTML",
            reply_markup=teclado_volver()
        )
    else:
        await update.message.reply_text(
            text=texto,
            parse_mode="HTML",
            reply_markup=teclado_volver()
    )
def main():
    init_db()

    # ================================================================
    # STICKERS PREMIUM GLOBALES
    # Desde aquí [1], [2], [3]... funcionan automáticamente en los
    # textos enviados/editados por el bot, sin llamar premium() manualmente.
    # ================================================================
    instalar_stickers_premium_globales()

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask {PORT}")
    app=Application.builder().token(BOT_TOKEN).build()
    # ========================== COMANDOS ==========================
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", register_command))
    app.add_handler(CommandHandler("cmds", cmds_command))
    app.add_handler(CommandHandler("me", me_command))
    app.add_handler(CommandHandler("staff", staff_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("micelular", micelular_command))
    app.add_handler(CommandHandler("pagar", pagar))
    
    # ===================== CONSULTAS RENIEC ======================
    app.add_handler(CommandHandler("dni", dni_command))
    app.add_handler(CommandHandler("dnit", dnit_command))
    app.add_handler(CommandHandler("dnivel", dnivel_command))
    app.add_handler(CommandHandler("dniv", dniv_command))
    app.add_handler(CommandHandler("dir", dir_command))
    app.add_handler(CommandHandler("nm", nm_command))
    app.add_handler(CommandHandler("suel", suel_command))
    app.add_handler(CommandHandler("den", den_command))
    app.add_handler(CommandHandler("denuncias", denuncias_command))
    app.add_handler(CommandHandler("rqh", rqh_command))
    app.add_handler(CommandHandler("pla", pla_command))
    app.add_handler(CommandHandler("denpla", denpla_command))
    app.add_handler(CommandHandler("plat", plat_command))
    app.add_handler(CommandHandler("revtec", revtec_command))
    app.add_handler(CommandHandler("dir", dir_command))
    app.add_handler(CommandHandler("hsoat", hsoat_command))

    # ===================== CONSULTAS FAMILIA =====================
    app.add_handler(CommandHandler("ag", agv_command))
    app.add_handler(CommandHandler("agv", agv_command))
    app.add_handler(CommandHandler("otros", otros_command))
    app.add_handler(CallbackQueryHandler(otros_callback, pattern="^otros_"))


    # ===================== TELEFONÍA / FACIAL ====================
    app.add_handler(CommandHandler("telcel", telcel_command))
    app.add_handler(CommandHandler("facial", facial_command))
    app.add_handler(
    MessageHandler(
        filters.PHOTO,
        recibir_voucher
    )
    )
    # ======================= ADMINISTRACIÓN ======================
    app.add_handler(CommandHandler("addcreditos", addcreditos_command))

    # ====================== BOTONES INLINE =======================
    app.add_handler(CallbackQueryHandler(botones_callback))
    logger.info("⚜️ SPECTER FUTURISTA ONLINE")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__=="__main__": main()
