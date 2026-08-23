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
from aiohttp import web
from functools import wraps
from dotenv import load_dotenv
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CODART_TOKEN = os.getenv("CODART_TOKEN")
API_BASE = os.getenv("API_BASE", "https://api-codart.cgrt.org/api/v1/consultas/fd").rstrip("/")
PORT = int(os.getenv("PORT", 10000))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
link_foto = "https://files.catbox.moe/0y85js.jpg"

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

CREDITOS_INICIALES = 10
COSTOS = {"dni":5,"agv":20,"facial":60,"dnit":6,"telcel":8}
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
def decodificar_imagen(uri):
    try:
        b64 = uri.split(",",1)[1] if "," in uri else uri
        return base64.b64decode(b64)
    except: return None

def teclado_volver(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER AL SISTEMA", callback_data="menu")]])
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
                        await target.reply_text(f"⚠️ ACCESO DENEGADO\n\n💳 SALDO: {saldo} CRD\n💸 REQUERIDO: {costo} CRD\n\n🔋 /buy para recargar", reply_markup=teclado_volver())
                    return
                nuevo=descontar(uid,costo)
                context.user_data['costo_actual']=costo; context.user_data['saldo_actual']=nuevo
                return await func(update,context,*args,**kwargs)
            except Exception as e:
                logger.error(f"decorador {e}", exc_info=True)
                try: await update.effective_message.reply_text(f"⚠️ SYSTEM ERROR: {esc(str(e))}", reply_markup=teclado_volver())
                except: pass
        return wrapper
    return decorator

# ============== UI FUTURISTA EN HTML ==============
def texto_menu_cmds():
    return (
        """╔═════════════════════╗
🛰️ MENÚ DE SERVICIOS
╚═════════════════════╝

🚀 SISTEMA CENTRAL DE CONSULTAS

💎 Selecciona una categoría.
⚡ Todos los servicios muestran su costo.
🛡️ El cobro se realiza solamente tras una respuesta exitosa.
 ▰▰▰ SELECCIONA MÓDULO ▰▰▰"""
    )
def teclado_menu_cmds():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🪪 RENIEC", callback_data="cat_reniec"), InlineKeyboardButton("🚙 VEHÍCULOS", callback_data="cat_placa")],
        [InlineKeyboardButton("🛰️ FAMILIARES", callback_data="cat_familiares"), InlineKeyboardButton("📱 TELÉFONOS", callback_data="cat_telcel")],
        [InlineKeyboardButton("🧬 FACIAL", callback_data="cat_facial"), InlineKeyboardButton("💎 RECARGAR", callback_data="cat_comprar")],
    ])

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


# ================== WEBHOOK DE PAGOS SIN FLASK ==================
async def webhook_pago(request):
    try:
        datos = await request.json()
    except:
        return web.json_response({"error": "Sin datos"}, status=400)

    celular = str(datos.get("celular", "")).strip()
    monto = datos.get("monto", 0)

    if not celular or not monto:
        return web.json_response({"error": "Faltan datos: celular y monto"}, status=400)

    # Buscar usuario por celular
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM usuarios WHERE celular =?", (celular,))
    row = cur.fetchone()
    conn.close()

    if not row:
        print(f"⚠️ Pago recibido — Celular {celular} NO REGISTRADO")
        return web.json_response({"status": "usuario_no_registrado"}, status=200)

    user_id = row[0]
    creditos = int(float(monto))
    saldo_nuevo = agregar_creditos(user_id, creditos)

    # Enviar notificación al usuario
    from telegram import Bot
    bot = Bot(token=BOT_TOKEN)
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ Pago detectado!\n\n"
                 f"💰 Recibido: S/ {monto}\n"
                 f"🎁 +{creditos} CRD agregados\n"
                 f"💳 Saldo actual: {saldo_nuevo} CRD"
        )
    except Exception as e:
        print(f"⚠️ No se pudo enviar mensaje: {e}")

    print(f"✅ Pago procesado — Usuario {user_id} | +{creditos} CRD")
    return web.json_response({
        "status": "ok",
        "user_id": user_id,
        "creditos": creditos,
        "saldo_actual": saldo_nuevo
    }, status=200)

async def iniciar_webhook():
    app = web.Application()
    app.router.add_post("/webhook-pago", webhook_pago)
    port = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Webhook de pagos activo en el puerto {port}")
    return runner

# ================== FIN WEBHOOK DE PAGOS =================

async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Uso: /pagar 300 6512
        # Si no pone nada, usa 300 y 6512 por defecto
        if len(context.args) >= 2:
            total = context.args[0]
            pedido = context.args[1]
        else:
            total = "300"
            pedido = "6512"

        texto = f"""💳 PAGO DE SERVICIO 💳

🛒 Servicio: créditos 
💰 Total a pagar: 200
🧾 N° Pedido: #{pedido}

➡️CCI: 92200200000387413218
➡️BANCO: DALE
⚠️NOTA: Adjuntar comprobante de pago⚠️

📸 ATENCION: ENVIA LA FOTO DEL VOUCHER AQUI MISMO 👇"""

        # ESTA ES LA LÍNEA CLAVE - manda foto por link + texto
        await update.message.reply_photo(photo=link_foto, caption=texto)

    except Exception as e:
        await update.message.reply_text(f"Error: {e}\nUso: /pagar <monto> <pedido> Ej: /pagar 300 6512")


async def micelular_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text(
            "📱 Uso: /micelular 987654321\n"
            "Registra tu número para que los pagos por Yape\n"
            "se sumen automáticamente a tus créditos ⚡",
            reply_markup=teclado_volver()
        )
    celular = context.args[0].strip()
    if not re.fullmatch(r"9\d{8}", celular):
        return await update.message.reply_text(
            "❌ Número inválido. Debe empezar con 9 y tener 9 dígitos.",
            reply_markup=teclado_volver()
        )
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET celular =? WHERE user_id =?", (celular, user_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"✅ Celular {celular} registrado!\n\n"
        "Ahora cuando pagues por Yape a este número,\n"
        "los créditos se sumarán automáticamente ⚡",
        reply_markup=teclado_volver()
    )
    
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

async def cmds_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(texto_menu_cmds(), parse_mode="HTML", reply_markup=teclado_menu_cmds())
async def botones_callback(update:Update, context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    
    if q.data=="menu":
        await q.edit_message_text(texto_menu_cmds(), parse_mode="HTML", reply_markup=teclado_menu_cmds())
    elif q.data=="cat_reniec":
        await q.edit_message_text("""
╔════════════╗
 🪪  RENIEC
╚════════════╝

⚡ SISTEMA NACIONAL DE IDENTIDAD ⚡
————————

[01] /dni 12345678
     ↳ FOTO +INFO
     ↳ COSTO: 4 CRD [█████░░░░░]

[02] /dnit 12345678
     ↳ 4 FOTOS + INFORMACIÓN AMPLIADA
     ↳ COSTO: 5 CRD [██████░░░░]

————————
🛡️ Consulta segura | Respuesta < 3s
⚠️ Los créditos solo se descuentan si hay resultado
""", parse_mode="HTML", reply_markup=teclado_volver())

    elif q.data=="cat_placa":
        await q.edit_message_text("""
╔═════════╗
 🧬 PLACA
╚═════════╝

⚡ BUSCAR POR PLACA⚡
————————————————

[01] /dnit 12345678
     ↳ 4
     ↳ COSTO: 6 CRD [██████░░░░]

————————
🛡️ Base SOAT 2026
""", parse_mode="HTML", reply_markup=teclado_volver())

    elif q.data=="cat_familiares":
        await q.edit_message_text("""
╔════════════╗
 🗯️ FAMILIARES  
╚════════════╝

⚡ SISTEMA DE FAMILIARES ⚡
————————

[01] /agv 12345678
     ↳ ÁRBOL GENEALÓGICO FOTO
     ↳ COSTO: 20 CRD [████████░░]

————————
🛡️ Datos en tiempo real
""", parse_mode="HTML", reply_markup=teclado_volver())

    elif q.data=="cat_telcel":
        await q.edit_message_text("""
╔════════════╗
 📱 TELÉFONOS 
╚════════════╝

⚡ SISTEMA DE TELEFONÍA MÓVIL ⚡
————————

[01] /telcel 9XXXXXXXX
     ↳ TITULAR + OPERADOR
     ↳ COSTO: 20 CRD [████░░░░░░]
[02] /telp 9XXXXXXXX
     ↳ NÚMEROS × DNI
     ↳ COSTO: 20 CRD [████░░░░░░]
     

————————————————""", parse_mode="HTML", reply_markup=teclado_volver())

    elif q.data=="cat_facial":
        await q.edit_message_text("""
╔════════════╗
 👁️  FACIAL 
╚════════════╝

⚡ RECONOCIMIENTO BIOMÉTRICO AI ⚡
————————

[01] Enviar foto con /facial
     ↳ MATCH 1:1 CON BASE RENIEC
     ↳ COSTO: 60 CRD [██████████]

————————
🛡️ IA 99.8% precisión
""", parse_mode="HTML", reply_markup=teclado_volver())

    elif q.data=="cat_comprar":
        await q.edit_message_text("""╔═════════════════════╗
💎 PLANES PREMIUM
╚═════════════════════╝

💰 CRÉDITOS

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
💳 PAGOS: Yape • Plin • BCP
👤 CONTACTO: @Sthep_18

⚡ Atención rápida
🛡️ Activación mediante administración""", parse_mode="HTML", reply_markup=teclado_volver())
    
@con_creditos(costo=COSTOS["dni"])
async def dni_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text("⚠️ FORMATO INVÁLIDO\n\nUsa: <code>/dni 12345678</code>", parse_mode="HTML", reply_markup=teclado_volver())
        return
    dni=context.args[0]
    prog=await update.message.reply_text(f"🛰️ INICIANDO SCAN RENIEC...\n🎯 TARGET: <code>{esc(dni)}</code>\n⏳ Conectando...", parse_mode="HTML")
    j,err=codart_get(f"/dni/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await prog.edit_text(f"❌ ERROR API\n{esc(err)}\n🔋 Devuelto", parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await prog.edit_text(f"❌ SIN RESULTADOS\n{esc(j.get('message'))}\n🔋 Reembolsado", parse_mode="HTML", reply_markup=teclado_volver())
        return
    data=j.get("data",{}); texto=format_dni_futurista(data, context)
    imgs=data.get("images",[])
    if imgs and imgs[0].get("data_uri"):
        foto=decodificar_imagen(imgs[0]["data_uri"])
        if foto:
            await update.message.reply_photo(photo=foto, caption=texto, parse_mode="HTML", reply_markup=teclado_volver())
            try: await prog.delete()
            except: pass
            return
    await prog.edit_text(texto, parse_mode="HTML", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["dnit"])
async def dnit_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await update.message.reply_text("⚠️ Usa: <code>/dnit 12345678</code>", parse_mode="HTML", reply_markup=teclado_volver())
        return
    dni=context.args[0]
    prog=await update.message.reply_text(f"🧬 INICIANDO DNIT X4...\n🎯 TARGET: <code>{esc(dni)}</code>", parse_mode="HTML")
    j,err=codart_get(f"/dnit/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await prog.edit_text(f"❌ ERROR\n{esc(err)}", parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["dnit"])
        await prog.edit_text(f"❌ SIN RESULTADOS\n{esc(j.get('message'))}", parse_mode="HTML", reply_markup=teclado_volver())
        return
    data=j.get("data",{}); texto=format_dnit_futurista(data, context)
    imgs=data.get("images",[])
    fotos_decod=[decodificar_imagen(im.get("data_uri")) for im in imgs if im.get("data_uri")]
    fotos_decod=[f for f in fotos_decod if f]
    if fotos_decod:
        await update.message.reply_photo(photo=fotos_decod[0], caption=texto, parse_mode="HTML", reply_markup=teclado_volver())
        for f in fotos_decod[1:4]:
            try: await update.message.reply_photo(photo=f)
            except: pass
        try: await prog.delete()
        except: pass
        return
    await prog.edit_text(texto, parse_mode="HTML", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["agv"])
async def agv_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_dni(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text("⚠️ Usa: <code>/agv 12345678</code>", parse_mode="HTML", reply_markup=teclado_volver())
        return
    dni=context.args[0]
    prog=await update.message.reply_text(f"🛰️ AGV TRACE...\n🎯 <code>{esc(dni)}</code>", parse_mode="HTML")
    j,err=codart_get(f"/agv/{dni}")
    if err:
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await prog.edit_text(f"❌ ERROR\n{esc(err)}", parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await prog.edit_text("❌ SIN RESULTADOS - Reembolsado", parse_mode="HTML", reply_markup=teclado_volver())
        return
    data=j.get("data",{}); texto=format_agv_futurista(data, context)
    imgs=data.get("images",[])
    if imgs and imgs[0].get("data_uri"):
        foto=decodificar_imagen(imgs[0]["data_uri"])
        if foto:
            await update.message.reply_photo(photo=foto, caption=texto, parse_mode="HTML", reply_markup=teclado_volver())
            try: await prog.delete()
            except: pass
            return
    await prog.edit_text(texto, parse_mode="HTML", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["telcel"])
async def telcel_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not context.args or not validar_cel(context.args[0]):
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await update.message.reply_text("⚠️ NÚMERO INVÁLIDO\nUsa: <code>/telcel 900000000</code>\n9 dígitos", parse_mode="HTML", reply_markup=teclado_volver())
        return
    num=context.args[0]
    prog=await update.message.reply_text(f"📡 TELCEL OS SCANNING...\n📱 TARGET: <code>{esc(num)}</code>", parse_mode="HTML")
    j,err=codart_get(f"/telp/cel/{num}")
    if err or (j and not j.get("success")):
        j2,err2=codart_get(f"/telcel/{num}")
        if j2 and j2.get("success"): j=j2; err=None
    if err:
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await prog.edit_text(f"❌ ERROR API\n{esc(err)}\n🔋 Devuelto", parse_mode="HTML", reply_markup=teclado_volver())
        return
    if not j.get("success"):
        reembolsar(update.effective_user.id, COSTOS["telcel"])
        await prog.edit_text(f"❌ SIN TITULAR\n{esc(j.get('message'))}\n🔋 Reembolsado", parse_mode="HTML", reply_markup=teclado_volver())
        return
    data=j.get("data",{}); texto=format_telcel_futurista(data, context, num)
    await prog.edit_text(texto, parse_mode="HTML", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["facial"])
async def facial_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    photo_file_id=None; msg=update.message
    if msg.photo: photo_file_id=msg.photo[-1].file_id
    elif msg.reply_to_message and msg.reply_to_message.photo: photo_file_id=msg.reply_to_message.photo[-1].file_id
    if not photo_file_id:
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await msg.reply_text("👁️ FACIAL SCAN\nEnvía foto con <code>/facial</code> o responde a foto", parse_mode="HTML", reply_markup=teclado_volver())
        return
    prog=await msg.reply_text("👁️ FACIAL SCAN INICIADO\n⏳ Analizando...", parse_mode="HTML")
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
            await prog.edit_text("❌ API VACÍA", parse_mode="HTML", reply_markup=teclado_volver())
            return
        try: j=r.json()
        except:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text(f"❌ NO JSON: {esc(r.text[:300])}", parse_mode="HTML", reply_markup=teclado_volver())
            return
        if r.status_code!=200 or not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text("❌ SIN COINCIDENCIAS - Reembolsado", parse_mode="HTML", reply_markup=teclado_volver())
            return
        data=j.get("data",{}); rostros=data.get("rostros",[])
        if not rostros:
            reembolsar(update.effective_user.id, COSTOS["facial"])
            await prog.edit_text("❌ 0 ROSTROS - Reembolsado", parse_mode="HTML", reply_markup=teclado_volver())
            return
        txt=f"<b>╔═════════════════╗</b>\n<b>║  👁️ FACIAL SCAN         ║</b>\n<b>╚════════════════════════╝</b>\n\n🎯 TOTAL ROSTROS: <code>{esc(data.get('total_rostros'))}</code>\n🧬 TIPO: <code>{esc(data.get('tipo_resultado'))}</code>\n\n"
        for rostro in rostros:
            txt+=f"▰─ ROSTRO #{esc(rostro.get('numero_rostro'))} ─▰\n"
            for i,coinc in enumerate(rostro.get("coincidencias",[]),1):
                pct=coinc.get('porcentaje',0); emoji="🟢" if pct>=90 else "🟡" if pct>=75 else "🔴"
                txt+=f"{emoji} {i}. <b>{esc(coinc.get('nombre'))}</b>\n   └─ DNI: <code>{esc(coinc.get('dni'))}</code> | {esc(pct)}%\n"
            txt+="\n"
        txt+=footer_creditos(context)
        await prog.edit_text(txt, parse_mode="HTML", reply_markup=teclado_volver())
    except Exception as e:
        logger.error(f"facial {e}", exc_info=True)
        reembolsar(update.effective_user.id, COSTOS["facial"])
        await prog.edit_text(f"❌ ERROR: {esc(str(e))}", parse_mode="HTML", reply_markup=teclado_volver())
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

async def addcreditos_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if ADMIN_ID!=0 and uid!=ADMIN_ID:
        await update.message.reply_text("⛔ ACCESO DENEGADO - SOLO ADMIN", reply_markup=teclado_volver())
        return
    if len(context.args)<2:
        await update.message.reply_text("⚙️ USO: <code>/addcreditos &lt;user_id&gt; &lt;cantidad&gt;</code>\nEj: <code>/addcreditos 6330231681 100</code>", parse_mode="HTML", reply_markup=teclado_volver())
        return
    try:
        target=int(context.args[0]); cant=int(context.args[1])
        nuevo=get_creditos(target)+cant; set_creditos(target,nuevo)
        await update.message.reply_text(f"✅ CRÉDITOS INYECTADOS\n👤 USER: <code>{esc(target)}</code>\n💳 +{esc(cant)} CRD\n🔋 SALDO: {esc(nuevo)} CRD", parse_mode="HTML", reply_markup=teclado_volver())
    except Exception as e:
        await update.message.reply_text(f"❌ {esc(str(e))}", reply_markup=teclado_volver())

async def me_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; saldo=get_creditos(u.id)
    txt=f"<b>╔════════════════╗</b>\n<b>║  👤 USER PROFILE       ║</b>\n<b>╚════════════════════════╝</b>\n\n🆔 ID: <code>{esc(u.id)}</code>\n👤 Nombre: <b>{esc(u.full_name)}</b>\n🔖 User: @{esc(u.username)}\n💳 Créditos: <code>{esc(saldo)} CRD</code>\n🛰️ Status: ONLINE"
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=teclado_volver())

async def staff_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛡️ <b>STAFF PANEL // EN DESARROLLO</b>", parse_mode="HTML", reply_markup=teclado_volver())
async def buy_command(update:Update, context:ContextTypes.DEFAULT_TYPE, from_callback=False):
    txt="💎 <b>RECARGA // SPECTER STORE</b>\n\n💰 PLANES\n├─ 5 CRD = S/ 5.00\n├─ 20 CRD = S/ 18.00\n├─ 60 CRD = S/ 50.00\n└─ 150 CRD = S/ 110.00\n\n📩 Contacta @admin"
    if from_callback and update.callback_query: await update.callback_query.message.edit_text(txt, parse_mode="HTML", reply_markup=teclado_volver())
    else: await update.message.reply_text(txt, parse_mode="HTML", reply_markup=teclado_volver())
async def register_command(update:Update, context:ContextTypes.DEFAULT_TYPE):
    get_creditos(update.effective_user.id)
    await update.message.reply_text("✅ <b>SISTEMA ACTIVADO</b>\n\n🧬 Bienvenido a SPECTER OS v2.5\n💳 10 CRD de bienvenida", parse_mode="HTML", reply_markup=teclado_volver())

def main():
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask {PORT}")
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("cmds",cmds_command))
    app.add_handler(CommandHandler("dni",dni_command))
    app.add_handler(CommandHandler("dnit",dnit_command))
    app.add_handler(CommandHandler("agv",agv_command))
    app.add_handler(CommandHandler("telcel",telcel_command))
    app.add_handler(CommandHandler("telp",telcel_command))
    app.add_handler(CommandHandler("facial",facial_command))
    app.add_handler(CommandHandler("me",me_command))
    app.add_handler(CommandHandler("staff",staff_command))
    app.add_handler(CommandHandler("buy",buy_command))
    app.add_handler(CommandHandler("register",register_command))
    app.add_handler(CommandHandler("pagar", pagar))
    app.add_handler(CommandHandler("addcreditos",addcreditos_command))
    app.add_handler(CallbackQueryHandler(botones_callback))
    logger.info("⚜️ SPECTER FUTURISTA ONLINE")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__=="__main__": main()
