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

# ================== CONFIG RENDER + LOGS ==================
load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CODART_TOKEN = os.getenv("CODART_TOKEN")
API_BASE = os.getenv("API_BASE", "https://api-codart.cgrt.org/api/v1/consultas/fd").rstrip("/")
PORT = int(os.getenv("PORT", 10000))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN faltante")
    raise SystemExit("BOT_TOKEN faltante")
if not CODART_TOKEN:
    logger.warning("⚠️ CODART_TOKEN faltante")

HEADERS_JSON = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CODART_TOKEN}"
}
HEADERS_FACIAL = {
    "Authorization": f"Bearer {CODART_TOKEN}",
    "Accept": "application/json"
}

# ================== FLASK PARA RENDER ==================
app_flask = Flask(__name__)
@app_flask.route('/')
def health(): return "⚜️ SPECTER OS v2.5 - ONLINE", 200
@app_flask.route('/health')
def health_check(): return {"status": "ok", "system": "SPECTER_PERU_FUTURISTA"}, 200
def run_flask(): app_flask.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ================== DB ==================
DB_PATH = os.getenv("DB_PATH", "bot.db")
db_dir = os.path.dirname(os.path.abspath(DB_PATH))
if db_dir and not os.path.exists(db_dir): os.makedirs(db_dir, exist_ok=True)

CREDITOS_INICIALES = 10
COSTOS = {
    "dni": 5,
    "agv": 20,
    "facial": 60,
    "dnit": 6,
    "telcel": 8
}

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS usuarios (user_id INTEGER PRIMARY KEY, creditos INTEGER NOT NULL)")
    conn.commit(); conn.close()
    logger.info(f"✅ DB en {DB_PATH}")

def get_creditos(user_id: int) -> int:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT creditos FROM usuarios WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO usuarios (user_id, creditos) VALUES (?,?)", (user_id, CREDITOS_INICIALES))
            conn.commit(); conn.close()
            return CREDITOS_INICIALES
        conn.close()
        return row[0]
    except Exception as e:
        logger.error(f"get_creditos {e}")
        if conn:
            try: conn.close()
            except: pass
        return CREDITOS_INICIALES

def set_creditos(user_id: int, nuevo: int):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        cur = conn.cursor()
        cur.execute("UPDATE usuarios SET creditos=? WHERE user_id=?", (nuevo, user_id))
        conn.commit(); conn.close()
    except Exception as e:
        logger.error(f"set_creditos {e}")

def descontar_creditos(uid, cant): 
    saldo = get_creditos(uid)
    nuevo = saldo - cant
    set_creditos(uid, nuevo)
    return nuevo

def reembolsar(uid, cant):
    try:
        nuevo = get_creditos(uid) + cant
        set_creditos(uid, nuevo)
        return nuevo
    except: return 0

def con_creditos(costo: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            try:
                uid = update.effective_user.id
                saldo = get_creditos(uid)
                if saldo < costo:
                    target = update.message or (update.callback_query.message if update.callback_query else None)
                    if target:
                        await target.reply_text(
                            f"⚠️ `ACCESO DENEGADO`\n\n💳 SALDO: {saldo} CRD\n💸 REQUERIDO: {costo} CRD\n\n🔋 Recarga con /buy",
                            parse_mode="Markdown",
                            reply_markup=teclado_volver()
                        )
                    return
                nuevo = descontar_creditos(uid, costo)
                context.user_data['costo_actual'] = costo
                context.user_data['saldo_actual'] = nuevo
                return await func(update, context, *args, **kwargs)
            except Exception as e:
                logger.error(f"decorador {e}", exc_info=True)
                try: await update.effective_message.reply_text(f"⚠️ SYSTEM ERROR: {e}", reply_markup=teclado_volver())
                except: pass
        return wrapper
    return decorator

def footer_creditos(ctx): 
    c = ctx.user_data.get('costo_actual',0)
    s = ctx.user_data.get('saldo_actual',0)
    return f"\n\n▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n💠 COSTO: {c} CRD | 🔋 SALDO: {s} CRD\n⚜️ SPECTER_OS v2.5"

def clean(v): 
    if v is None or str(v).strip() == "" or str(v).lower() == "none": return "—"
    return str(v).strip()
def safe_get(d, *keys):
    cur = d
    for k in keys:
        if isinstance(cur, dict): cur = cur.get(k)
        else: return None
    return cur
def validar_dni(d): return bool(re.match(r"^\d{8}$", d))
def validar_cel(n): return bool(re.match(r"^\d{9}$", n))
def decodificar_imagen(uri):
    try:
        b64 = uri.split(",",1)[1] if "," in uri else uri
        return base64.b64decode(b64)
    except: return None
def teclado_volver(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER AL SISTEMA", callback_data="menu")]])

# ================== UI FUTURISTA ==================
def texto_menu_cmds():
    return (
        "```\n"
        "╔══════════════════════════════╗\n"
        "║  ⚜️ SPECTER OS v2.5 ONLINE   ║\n"
        "║  CENTRAL DE INTELIGENCIA     ║\n"
        "╚══════════════════════════════╝\n"
        "```\n"
        "🧬 *SISTEMA FUTURISTA ACTIVO*\n\n"
        "⚡ *Créditos solo se descuentan si la API responde OK*\n"
        "🛡️ *Si falla, reembolso automático*\n\n"
        "▰▰▰▰▰▰▰ SELECCIONA MÓDULO ▰▰▰▰▰▰▰"
    )

def teclado_menu_cmds():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🪪 DNI CORE [5 CRD]", callback_data="cat_reniec"), InlineKeyboardButton("🧬 DNIT X4 [6 CRD]", callback_data="cat_dnit")],
        [InlineKeyboardButton("🛰️ AGV TRACE [20 CRD]", callback_data="cat_agv"), InlineKeyboardButton("📱 TELCEL OS [8 CRD]", callback_data="cat_telcel")],
        [InlineKeyboardButton("👁️ FACIAL SCAN [60 CRD]", callback_data="cat_facial"), InlineKeyboardButton("💎 RECARGAR", callback_data="cat_comprar")],
    ])

# ================== FORMATTERS FUTURISTAS ==================
def format_dni_futurista(data, ctx):
    dni_obj = data.get("dni", {})
    nac = data.get("nacimiento", {})
    info = data.get("informacion_general", {})
    dom = data.get("domicilio", {})
    ubi = data.get("ubigeos", {})
    
    dni_num = clean(dni_obj.get("completo") or dni_obj.get("numero") or data.get("dni"))
    if isinstance(dni_num, dict): dni_num = clean(dni_num.get("completo"))
    
    txt = f"""
```
╔════════════════════════════════╗
║  🪪 RENIEC CORE // SPECTER OS  ║
╚════════════════════════════════╝
```
🔍 *TARGET:* `{dni_num}`
▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰

👤 *IDENTIDAD*
├─ Nombres: *{clean(data.get('nombres'))}*
├─ Apellidos: *{clean(data.get('apellidos'))}*
├─ Género: `{clean(data.get('genero'))}`
└─ Estado Civil: `{clean(info.get('estado_civil'))}`

🎂 *NACIMIENTO*
├─ Fecha: `{clean(nac.get('fecha'))}`
├─ Edad: `{clean(nac.get('edad'))}`
├─ Dpto: `{clean(nac.get('departamento'))}`
├─ Prov: `{clean(nac.get('provincia'))}`
└─ Dist: `{clean(nac.get('distrito'))}`

📚 *INFO GENERAL*
├─ Nivel Edu: `{clean(info.get('nivel_educativo'))}`
├─ Estatura: `{clean(info.get('estatura'))}`
├─ Donante: `{clean(info.get('donante_organos'))}`
├─ Emisión: `{clean(info.get('fecha_emision'))}`
├─ Caducidad: `{clean(info.get('fecha_caducidad'))}`
├─ Padre: `{clean(info.get('padre'))}`
├─ Madre: `{clean(info.get('madre'))}`
└─ Restricción: `{clean(info.get('restriccion'))}`

🏠 *DOMICILIO*
├─ Dir: `{clean(dom.get('direccion'))}`
├─ Dist: `{clean(dom.get('distrito'))}`
├─ Prov: `{clean(dom.get('provincia'))}`
└─ Dpto: `{clean(dom.get('departamento'))}`

📍 *UBIGEO* → RENIEC: `{clean(ubi.get('reniec'))}` | INEI: `{clean(ubi.get('ine'))}` | SUNAT: `{clean(ubi.get('sunat'))}`
"""
    txt += footer_creditos(ctx)
    return txt

def format_dnit_futurista(data, ctx):
    # Mismo que DNI pero con tag DNIT X4
    base = format_dni_futurista(data, ctx)
    return base.replace("RENIEC CORE", "DNIT X4 // 4 FOTOS").replace("🪪", "🧬")

def format_agv_futurista(data, ctx):
    txt = f"""
```
╔════════════════════════════════╗
║  🛰️ AGV TRACE // SPECTER OS    ║
╚════════════════════════════════╝
```
👁️ *DNI:* `{clean(data.get('dni'))}`
👤 *Nombres:* *{clean(data.get('nombres'))}*
👥 *Apellidos:* *{clean(data.get('apellidos'))}*
⚧ *Género:* `{clean(data.get('genero'))}`
🎂 *Edad:* `{clean(data.get('edad'))}` años

▰▰▰ SCAN COMPLETADO ▰▰▰
"""
    txt += footer_creditos(ctx)
    return txt

def format_telcel_futurista(data, ctx, numero):
    titulares = data.get("titulares", [])
    count = data.get("titulares_encontrados", len(titulares))
    txt = f"""
```
╔════════════════════════════════╗
║  📱 TELCEL OS // SPECTER       ║
╚════════════════════════════════╝
```
📞 *NÚMERO:* `{clean(numero)}`
🔎 *TITULARES ENCONTRADOS:* `{count}`

"""
    for i, t in enumerate(titulares, 1):
        txt += f"""
▰─ TITULAR #{i} ─▰
├─ Nombre: *{clean(t.get('titular'))}*
├─ DNI/RUC: `{clean(t.get('dni_ruc'))}`
├─ Operador: `{clean(t.get('operador'))}`
├─ Empresa: `{clean(t.get('empresa'))}`
├─ Tel: `{clean(t.get('telefono'))}`
├─ Plan: `{clean(t.get('plan'))}`
├─ Periodo: `{clean(t.get('periodo'))}`
├─ Correo: `{clean(t.get('correo'))}`
└─ IP: `{clean(t.get('n_ip'))}`

"""
    txt += footer_creditos(ctx)
    return txt

# ================== COMANDOS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_creditos(update.effective_user.id)
    bot_u = f"@{context.bot.username}"
    texto = (
        "```\n"
        "╔══════════════════════════════╗\n"
        "║  ⚜️ SPECTER PERÚ OS v2.5     ║\n"
        "║  STATUS: ONLINE              ║\n"
        "╚══════════════════════════════╝\n"
        "```\n"
        f"🚀 *PLATAFORMA:* `{bot_u}`\n"
        "🛰️ *CORE:* `CODART_X_API_V1`\n"
        "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
        "📚 *COMANDOS FUTURISTAS*\n"
        "├─ `/register` → Activar sistema\n"
        "├─ `/cmds` → Panel de módulos\n"
        "├─ `/me` → Mi perfil\n"
        "├─ `/dni 12345678` [5 CRD]\n"
        "├─ `/dnit 12345678` [6 CRD]\n"
        "├─ `/agv 12345678` [20 CRD]\n"
        "├─ `/telcel 900000000` [8 CRD]\n"
        "├─ `/facial` [60 CRD]\n"
        "└─ `/buy` → Recargar\n"
    )
    await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_volver())

async def cmds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(texto_menu_cmds(), parse_mode="Markdown", reply_markup=teclado_menu_cmds())

async def botones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "menu":
        await q.message.edit_text(texto_menu_cmds(), parse_mode="Markdown", reply_markup=teclado_menu_cmds())
    elif q.data == "cat_reniec":
        await q.message.edit_text("🪪 *MÓDULO RENIEC CORE*\n\n📌 `/dni 12345678` → Info completa RENIEC\n💰 5 CRD\n\n_Ej: /dni 12345678_", parse_mode="Markdown", reply_markup=teclado_volver())
    elif q.data == "cat_dnit":
        await q.message.edit_text("🧬 *MÓDULO DNIT X4*\n\n📌 `/dnit 12345678` → 4 fotos + info\n💰 6 CRD\n\n_Ej: /dnit 12345678_", parse_mode="Markdown", reply_markup=teclado_volver())
    elif q.data == "cat_agv":
        await q.message.edit_text("🛰️ *MÓDULO AGV TRACE*\n\n📌 `/agv 12345678` → Datos básicos + foto\n💰 20 CRD", parse_mode="Markdown", reply_markup=teclado_volver())
    elif q.data == "cat_telcel":
        await q.message.edit_text("📱 *MÓDULO TELCEL OS*\n\n📌 `/telcel 9XXXXXXXX` → Titular, operador, plan\n💰 8 CRD\n\n_Ej: /telcel 900000000_", parse_mode="Markdown", reply_markup=teclado_volver())
    elif q.data == "cat_facial":
        await q.message.edit_text("👁️ *MÓDULO FACIAL SCAN*\n\n📌 Envía foto con `/facial` o responde a foto\n💰 60 CRD", parse_mode="Markdown", reply_markup=teclado_volver())
    elif q.data == "cat_comprar":
        await buy_command(update, context, from_callback=True)
    else:
        await q.message.edit_text("🚧 Módulo en desarrollo...", reply_markup=teclado_volver())

# ============== CORE REQUEST HANDLER - FIX DEL ERROR JSON ==============
def codart_get(path: str):
    """GET seguro contra error Expecting value"""
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, headers=HEADERS_JSON, timeout=25)
        logger.info(f"GET {url} -> {r.status_code} len={len(r.text)}")
        if not r.text or not r.text.strip():
            return None, f"API vacía Status {r.status_code}"
        try:
            j = r.json()
        except Exception as e:
            logger.error(f"Respuesta no JSON {url}: {r.text[:500]}")
            return None, f"API no JSON Status {r.status_code}: {r.text[:400]}"
        return j, None
    except Exception as e:
        logger.error(f"Request error {url}: {e}", exc_info=True)
        return None, str(e)

# ================== COMANDOS IMPLEMENTADOS ==================
@con_creditos(costo=COSTOS["dni"])
async def dni_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text("⚠️ `FORMATO INVÁLIDO`\n\nUsa: `/dni 12345678`\n8 dígitos obligatorios", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    dni = context.args[0]
    prog = await update.message.reply_text(f"🛰️ `INICIANDO SCAN RENIEC...`\n🎯 TARGET: `{dni}`\n⏳ Conectando a CODART_X...", parse_mode="Markdown")
    j, err = codart_get(f"/dni/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await prog.edit_text(f"❌ `ERROR API`\n{err}\n\n🔋 Créditos devueltos", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await prog.edit_text(f"❌ `SIN RESULTADOS`\n{clean(j.get('message'))}\n🔋 Reembolsado", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    data = j.get("data", {})
    texto = format_dni_futurista(data, context)
    imgs = data.get("images", [])
    if imgs and imgs[0].get("data_uri"):
        foto = decodificar_imagen(imgs[0]["data_uri"])
        if foto:
            await update.message.reply_photo(photo=foto, caption=texto, parse_mode="Markdown", reply_markup=teclado_volver())
            try: await prog.delete()
            except: pass
            return
    await prog.edit_text(texto, parse_mode="Markdown", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["dnit"])
async def dnit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await update.message.reply_text("⚠️ `FORMATO INVÁLIDO`\n\nUsa: `/dnit 12345678`", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    dni = context.args[0]
    prog = await update.message.reply_text(f"🧬 `INICIANDO DNIT X4...`\n🎯 TARGET: `{dni}`\n⏳ Extrayendo 4 fotos...", parse_mode="Markdown")
    j, err = codart_get(f"/dnit/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await prog.edit_text(f"❌ `ERROR API`\n{err}\n🔋 Devuelto", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await prog.edit_text(f"❌ `SIN RESULTADOS`\n{clean(j.get('message'))}", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    data = j.get("data", {})
    texto = format_dnit_futurista(data, context)
    imgs = data.get("images", [])
    # Enviar hasta 4 fotos en grupo si existen
    if imgs:
        fotos_decod = [decodificar_imagen(im.get("data_uri")) for im in imgs if im.get("data_uri")]
        fotos_decod = [f for f in fotos_decod if f]
        if fotos_decod:
            # Primera foto con caption
            await update.message.reply_photo(photo=fotos_decod[0], caption=texto, parse_mode="Markdown", reply_markup=teclado_volver())
            # Resto como fotos adicionales
            for f in fotos_decod[1:4]:
                try: await update.message.reply_photo(photo=f)
                except: pass
            try: await prog.delete()
            except: pass
            return
    await prog.edit_text(texto, parse_mode="Markdown", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["agv"])
async def agv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text("⚠️ `FORMATO INVÁLIDO`\nUsa: `/agv 12345678`", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    dni = context.args[0]
    prog = await update.message.reply_text(f"🛰️ `AGV TRACE INICIADO`\n🎯 `{dni}`", parse_mode="Markdown")
    j, err = codart_get(f"/agv/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await prog.edit_text(f"❌ `ERROR`\n{err}", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await prog.edit_text("❌ `SIN RESULTADOS` - Reembolsado", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    data = j.get("data", {})
    texto = format_agv_futurista(data, context)
    imgs = data.get("images", [])
    if imgs and imgs[0].get("data_uri"):
        foto = decodificar_imagen(imgs[0]["data_uri"])
        if foto:
            await update.message.reply_photo(photo=foto, caption=texto, parse_mode="Markdown", reply_markup=teclado_volver())
            try: await prog.delete()
            except: pass
            return
    await prog.edit_text(texto, parse_mode="Markdown", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["telcel"])
async def telcel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_cel(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await update.message.reply_text("⚠️ `NÚMERO INVÁLIDO`\n\nUsa: `/telcel 900000000`\n9 dígitos, empieza con 9", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    num = context.args[0]
    prog = await update.message.reply_text(f"📡 `TELCEL OS SCANNING...`\n📱 TARGET: `{num}`\n⏳ Rastreando operador...", parse_mode="Markdown")
    j, err = codart_get(f"/telp/cel/{num}")
    # fallback por si usan /telcel endpoint
    if err or not j.get("success"):
        j2, err2 = codart_get(f"/telcel/{num}")
        if j2 and j2.get("success"): j = j2; err = None
    if err:
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await prog.edit_text(f"❌ `ERROR API`\n{err}\n🔋 Devuelto", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await prog.edit_text(f"❌ `SIN TITULAR`\n{clean(j.get('message'))}\n🔋 Reembolsado", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    data = j.get("data", {})
    texto = format_telcel_futurista(data, context, num)
    await prog.edit_text(texto, parse_mode="Markdown", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["facial"])
async def facial_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file_id = None
    msg = update.message
    if msg.photo: photo_file_id = msg.photo[-1].file_id
    elif msg.reply_to_message and msg.reply_to_message.photo: photo_file_id = msg.reply_to_message.photo[-1].file_id
    if not photo_file_id:
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await msg.reply_text("👁️ `FACIAL SCAN`\n\nEnvía una foto con `/facial` o responde a una foto con `/facial`\n\n💰 60 CRD", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    prog = await msg.reply_text("👁️ `FACIAL SCAN INICIADO`\n⏳ Analizando biométrica...", parse_mode="Markdown")
    tmp_path = None
    try:
        tg_file = await context.bot.get_file(photo_file_id)
        tmp_path = os.path.join(tempfile.gettempdir(), f"facial_{update.effective_user.id}.jpg")
        await tg_file.download_to_drive(tmp_path)
        url = f"{API_BASE}/facial/top"
        with open(tmp_path, "rb") as f:
            files = {"image_facial": ("facial.jpg", f, "image/jpeg")}
            r = requests.post(url, headers=HEADERS_FACIAL, files=files, timeout=35)
        if not r.text:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text("❌ `API VACÍA` - Reembolsado", parse_mode="Markdown", reply_markup=teclado_volver())
            return
        try: j = r.json()
        except:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text(f"❌ `NO JSON`\n{r.text[:400]}", parse_mode="Markdown", reply_markup=teclado_volver())
            return
        if r.status_code != 200 or not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text("❌ `SIN COINCIDENCIAS` - Reembolsado", parse_mode="Markdown", reply_markup=teclado_volver())
            return
        data = j.get("data", {}); rostros = data.get("rostros", [])
        if not rostros:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text("❌ `0 ROSTROS DETECTADOS` - Reembolsado", parse_mode="Markdown", reply_markup=teclado_volver())
            return
        txt = f"""
```
╔════════════════════════════════╗
║  👁️ FACIAL SCAN // SPECTER OS  ║
╚════════════════════════════════╝
```
🎯 *TOTAL ROSTROS:* `{clean(data.get('total_rostros'))}`
🧬 *TIPO:* `{clean(data.get('tipo_resultado'))}`

"""
        for rostro in rostros:
            txt += f"▰─ ROSTRO #{rostro.get('numero_rostro')} ─▰\n"
            for i, coinc in enumerate(rostro.get("coincidencias", []), 1):
                pct = coinc.get('porcentaje',0)
                emoji = "🟢" if pct>=90 else "🟡" if pct>=75 else "🔴"
                txt += f"{emoji} {i}. *{clean(coinc.get('nombre'))}*\n   └─ DNI: `{clean(coinc.get('dni'))}` | {pct}%\n"
            txt += "\n"
        txt += footer_creditos(context)
        await prog.edit_text(txt, parse_mode="Markdown", reply_markup=teclado_volver())
    except Exception as e:
        logger.error(f"facial {e}", exc_info=True)
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await prog.edit_text(f"❌ `ERROR`: {e}", parse_mode="Markdown", reply_markup=teclado_volver())
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

# ================== UTILS COMMANDS ==================
async def addcreditos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if ADMIN_ID != 0 and uid != ADMIN_ID:
        await update.message.reply_text("⛔ `ACCESO DENEGADO - SOLO ADMIN`", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    if len(context.args) < 2:
        await update.message.reply_text("⚙️ `USO: /addcreditos <user_id> <cantidad>`\nEj: `/addcreditos 6330231681 100`", parse_mode="Markdown", reply_markup=teclado_volver())
        return
    try:
        target = int(context.args[0]); cant = int(context.args[1])
        nuevo = get_creditos(target) + cant
        set_creditos(target, nuevo)
        await update.message.reply_text(f"✅ `CRÉDITOS INYECTADOS`\n👤 USER: `{target}`\n💳 +{cant} CRD\n🔋 NUEVO SALDO: {nuevo} CRD", parse_mode="Markdown", reply_markup=teclado_volver())
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", reply_markup=teclado_volver())

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user; saldo = get_creditos(u.id)
    txt = f"""
```
╔════════════════════════════════╗
║  👤 USER PROFILE // SPECTER    ║
╚════════════════════════════════╝
```
🆔 *ID:* `{u.id}`
👤 *Nombre:* *{u.full_name}*
🔖 *User:* @{clean(u.username)}
💳 *Créditos:* `{saldo} CRD`
🛰️ *Status:* `ONLINE`
▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰
"""
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=teclado_volver())

async def staff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛡️ `STAFF PANEL // EN DESARROLLO`\n\nPróximamente gestión avanzada...", parse_mode="Markdown", reply_markup=teclado_volver())

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
    txt = (
        "```\n╔════════════════════════════════╗\n║  💎 RECARGA // SPECTER STORE  ║\n╚════════════════════════════════╝\n```\n"
        "💰 *PLANES DISPONIBLES*\n"
        "├─ 5 CRD = S/ 5.00\n"
        "├─ 20 CRD = S/ 18.00\n"
        "├─ 60 CRD = S/ 50.00\n"
        "└─ 150 CRD = S/ 110.00\n\n"
        "📩 Contacta @admin para recargar"
    )
    if from_callback and update.callback_query: await update.callback_query.message.edit_text(txt, parse_mode="Markdown", reply_markup=teclado_volver())
    else: await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=teclado_volver())

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_creditos(update.effective_user.id)
    await update.message.reply_text("✅ `SISTEMA ACTIVADO`\n\n🧬 Bienvenido a SPECTER OS v2.5\n💳 Has recibido 10 CRD de bienvenida", parse_mode="Markdown", reply_markup=teclado_volver())

def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"🌐 Flask en {PORT}")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cmds", cmds_command))
    app.add_handler(CommandHandler("dni", dni_command))
    app.add_handler(CommandHandler("dnit", dnit_command))
    app.add_handler(CommandHandler("agv", agv_command))
    app.add_handler(CommandHandler("telcel", telcel_command))
    app.add_handler(CommandHandler("telp", telcel_command))
    app.add_handler(CommandHandler("facial", facial_command))
    app.add_handler(CommandHandler("me", me_command))
    app.add_handler(CommandHandler("staff", staff_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("register", register_command))
    app.add_handler(CommandHandler("addcreditos", addcreditos_command))
    app.add_handler(CallbackQueryHandler(botones_callback))
    logger.info("⚜️ SPECTER PERÚ FUTURISTA - ONLINE")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
