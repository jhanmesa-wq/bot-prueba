import httpx
import json
import datetime
import os
import base64
import io
from io import BytesIO
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
from flask import Flask
from threading import Thread
import asyncio
from telegram import InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton, Update 
from telegram.ext import ContextTypes
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

BTN_VOLVER = InlineKeyboardMarkup([
    [InlineKeyboardButton("🏠 Volver al inicio", callback_data="menu_inicio")]
])


# Forzar la creación de un event loop si no existe en este hilo
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot activo"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ===== CONFIG =====
BOT_TOKEN = os.getenv("8479229761:AAGbmY8k5MSngcGSulo6VZ5rWzrikwgtOUw")
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = [str(os.getenv("ADMIN_ID"))] # Lista para poder agregar varios admins
ARCHIVO_USUARIOS = os.getenv("ARCHIVO_USUARIOS") or "usuarios.json"
BOT_USER = "@specter_Dox44bot"
BOT_NAME = "⚜ SPECTER_PERU⚜"
BASE_URL = "https://api-codart.cgrt.org"

PRECIOS = {
    "dni": 4, "agv": 20, "telpcel": 15, "facial": 30, "ruc": 5, "suel": 5,
    "denuncia": 10, "placa": 12, "nm": 6, "hsoat": 8, "denpla": 30, "dnit": 5, "telp": 15
}

# ===== FUNCIONES BASE =====
def cargar_usuarios():
    try:
        if not os.path.exists(ARCHIVO_USUARIOS):
            with open(ARCHIVO_USUARIOS, "w", encoding="utf-8") as f:
                json.dump({}, f)
        with open(ARCHIVO_USUARIOS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error al cargar usuarios: {e}")
        return {}

def guardar_usuarios(data):
    try:
        with open(ARCHIVO_USUARIOS, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error al guardar usuarios: {e}")

def get_fecha():
    return datetime.datetime.now().strftime("%d/%m/%Y - %I:%M:%S %p")

async def validar_creditos(user_id, comando, usuarios):
    if user_id not in usuarios:
        return False, "❌ No estás registrado. Usa /register"
    
    costo = PRECIOS.get(comando, 0)
    user_creditos = usuarios.get(user_id, {}).get("creditos", 0)
    
    if user_creditos < costo:
        return False, f"❌ No tienes créditos suficientes. Necesitas {costo} y tienes {user_creditos}. Usa /buy"
    
    return True, costo

async def consultar_api_get(url):
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=headers)
            print(f"DEBUG API GET: {url} - Status: {r.status_code}")
            return r.json()
    except Exception as e:
        print(f"DEBUG ERROR API: {e}")
        return {"error": str(e)}

# ===== COMANDOS GENERALES =====
async def agv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    
    ok, res_cred = await validar_creditos(user_id, "agv", usuarios)
    if not ok:
        return await update.message.reply_text(res_cred)

    if len(context.args)!= 1:
        await update.message.reply_text(
            "❌ Uso correcto:\n<code>/agv 12345678</code>",
            parse_mode="HTML"
        )
        return

    dni = context.args[0]
    if not (dni.isdigit() and len(dni) == 8):
        await update.message.reply_text("❌ El DNI debe contener 8 dígitos.")
        return

    m = await update.message.reply_text(f"🔍 Buscando AGV para DNI: <code>{dni}</code>...", parse_mode="HTML")
    url = f"https://api-codart.cgrt.org/api/v1/consultas/fd/agv/{dni}"

    try:
        data = await consultar_api_get(url)

        if not data.get("success"):
            await m.edit_text("❌ No se encontró información para ese DNI.")
            return

        # DESCUENTO DE CRÉDITOS
        usuarios[user_id]["creditos"] -= PRECIOS["agv"]
        usuarios[user_id]["consultas"] = usuarios[user_id].get("consultas", 0) + 1
        guardar_usuarios(usuarios)

        info = data["data"]
        mensaje = (
            "🆔 <b>CONSULTA AGV</b>\n\n"
            f"📄 <b>DNI:</b> <code>{info['dni']}</code>\n"
            f"👤 <b>Nombre:</b> {info['nombres']} {info['apellidos']}\n"
            f"🚻 <b>Género:</b> {info['genero']}\n"
            f"🎂 <b>Edad:</b> {info['edad']} años\n"
            f"📡 <b>Fuente:</b> {data['source']}\n\n"
            f"💰 <b>Créditos restantes:</b> {usuarios[user_id]['creditos']}"
        )

        await m.delete()
        await update.message.reply_text(mensaje, parse_mode="HTML", reply_markup=BTN_VOLVER)

        if info.get("images"):
            for img in info["images"]:
                if img.get("data_uri"):
                    data_uri = img["data_uri"]
                    base64_data = data_uri.split(",")[1]
                    image_bytes = base64.b64decode(base64_data)
                    await update.message.reply_photo(
                        photo=image_bytes, 
                        caption=f"📸 Foto DNI: {dni}",
                    )

    except Exception as e:
        await update.message.reply_text(f"❌ Error:\n<code>{e}</code>", parse_mode="HTML")

async def den(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (Mantenemos tu lógica de denuncias pero verificando créditos)
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    
    # Supongamos que usas el precio de 'denuncia'
    ok, res_cred = await validar_creditos(user_id, "denuncia", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if len(context.args) != 1:
        await update.message.reply_text("❌ Uso correcto:\n<code>/den 12345678</code>", parse_mode="HTML")
        return

    dni = context.args[0]
    url = f"https://api-codart.cgrt.org/api/v1/consultas/fd/den/{dni}"

    try:
        data = await consultar_api_get(url)
        if not data.get("success"):
            await update.message.reply_text("❌ No se encontraron denuncias.")
            return

        usuarios[user_id]["creditos"] -= PRECIOS["denuncia"]
        guardar_usuarios(usuarios)

        info = data["data"]
        mensaje = f"🚨 <b>CONSULTA DE DENUNCIAS</b>\n\n🆔 <b>DNI:</b> <code>{info['consulta']}</code>\n📄 <b>Total:</b> {info['cantidad_denuncias']}\n\n"

        for d in info["denuncias"]:
            mensaje += (
                f"<b>📌 Denuncia #{d['numero']}</b>\n👤 <b>Tipo:</b> {d['tipo']}\n📑 <b>N° Orden:</b> {d['n_orden']}\n"
                f"📅 <b>Fecha Hecho:</b> {d['f_hecho']}\n📋 <b>Condición:</b> {d['condicion']}\n━━━━━━━━━━━━━━\n"
            )

        await update.message.reply_text(mensaje, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    VIDEO_LINK = "https://files.catbox.moe/odq1nv.mp4"
    texto = f"""⚜️ <b>¡BIENVENIDO A DATA PERÚ!</b> ⚜️

━━━━━━━━━━━━━━━━━━

📌 <b>INFORMACIÓN DEL BOT</b>

🏷️ <b>Nombre:</b> {BOT_NAME}
👤 <b>Usuario:</b> {BOT_USER}
🚀 <b>Versión:</b> v2.1 CODART V1

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
    await context.bot.send_video(
        chat_id=update.effective_chat.id,
        video=VIDEO_LINK,
        caption=texto,
        parse_mode='HTML'
    )

async def cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    VIDEO_CMD = "https://files.catbox.moe/m7e3jl.mp4"
    
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("╔═ 🪪 RENIEC ═╗", callback_data="cmd_reniec"), InlineKeyboardButton("╔═ 🏢 RUC ═╗", callback_data="cmd_ruc")],
        [InlineKeyboardButton("╔═ 🚘 VEHÍCULOS ═╗", callback_data="cmd_vehiculos"), InlineKeyboardButton("╔═ 📱 TELÉFONO ═╗", callback_data="cmd_telefono")],
        [InlineKeyboardButton("╔═ ⚖️ DENUNCIAS ═╗", callback_data="cmd_denuncia"), InlineKeyboardButton("╔═ 💰 SUELDO ═╗", callback_data="cmd_sueldo")],
        [InlineKeyboardButton("╔═ 🧬 FACIAL ═╗", callback_data="cmd_facial"), InlineKeyboardButton("╔═ 💎 COMPRAR ═╗", callback_data="cmd_buy")]
    ])

    texto = f"""╔════════════╗\n        ⚜️ 𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦 𝗣𝗘𝗥𝗨 ⚜️\n╚════════════════════╝\n\n🚀 𝗟𝗔 𝗣𝗟𝗔𝗧𝗔𝗙𝗢𝗥𝗠𝗔 #𝟭 𝗗𝗘 𝗖𝗢𝗡𝗦𝗨𝗟𝗧𝗔𝗦\n\n━━━━━━━━━━━━━━━━━━━━━━━\n\n🛰️ 𝗔𝗖𝗖𝗘𝗗𝗘 𝗔 𝗧𝗢𝗗𝗢𝗦 𝗟𝗢𝗦 𝗦𝗘𝗥𝗩𝗜𝗖𝗜𝗢𝗦\n\n💎 Más de 150 servicios disponibles\n⚡ Consultas rápidas y precisas\n🛡️ Plataforma segura y estable\n🚀 Tecnología de última generación\n📡 Actualizaciones constantes\n🎯 Respuesta en pocos segundos\n\n━━━━━━━━━━━━━━━\n\n🔎 𝗖𝗢𝗡𝗘𝗖𝗧𝗔 𝗟𝗔 𝗜𝗡𝗙𝗢𝗥𝗠𝗔𝗖𝗜Ó𝗡\n📂 Descubre relaciones y encuentra\nlos datos que necesitas desde un\nsolo lugar.\n\n━━━━━━━━━━━━━━━\n\n👇 𝗦𝗘𝗟𝗘𝗖𝗜𝗢𝗡𝗔 𝗨𝗡𝗔 𝗖𝗔𝗧𝗘𝗚𝗢𝗥Í𝗔 👇"""

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

    if query.data == "volver_cmds" or query.data == "menu_inicio":
        teclado = InlineKeyboardMarkup([
            [InlineKeyboardButton("╔═ 🪪 RENIEC ═╗", callback_data="cmd_reniec"), InlineKeyboardButton("╔═ 🏢 RUC ═╗", callback_data="cmd_ruc")],
            [InlineKeyboardButton("╔═ 🚘 VEHÍCULOS ═╗", callback_data="cmd_vehiculos"), InlineKeyboardButton("╔═ 📱 TELÉFONO ═╗", callback_data="cmd_telefono")],
            [InlineKeyboardButton("╔═ ⚖️ DENUNCIAS ═╗", callback_data="cmd_denuncia"), InlineKeyboardButton("╔═ 💰 SUELDO ═╗", callback_data="cmd_sueldo")],
            [InlineKeyboardButton("╔═ 🧬 FACIAL ═╗", callback_data="cmd_facial"), InlineKeyboardButton("╔═ 💎 COMPRAR ═╗", callback_data="cmd_buy")]
        ])
        texto = """╔════════════════════╗\n        ⚜️ 𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦 𝗣𝗘𝗥𝗨 ⚜️\n╚══════════════════════╝\n\n🚀 𝗟𝗔 𝗣𝗟𝗔𝗧𝗔𝗙𝗢𝗥𝗠𝗔 #𝟭 𝗗𝗘 𝗖𝗢𝗡𝗦𝗨𝗟𝗧𝗔𝗦\n..."""
        try:
            await query.edit_message_caption(caption=texto, reply_markup=teclado)
        except:
            await query.edit_message_text(texto, reply_markup=teclado)
        return

    # Mantenemos las definiciones de los menús de comandos
    comandos = {
        "cmd_reniec": """❰ #𝗦𝗜𝗦𝗧𝗘𝗠𝗔𝗦_𝗗𝗔𝗧𝗔_𝗣𝗘𝗥𝗨 ❱ ➾ RENIEC\n...\n1. DNI TARJETA (/dnit)\n2. DNI SIMPLE (/dni)...""",
        "cmd_ruc": "Uso: /ruc 20538856674",
        "cmd_vehiculos": "1. /placa\n2. /hsoat\n3. /denpla",
        "cmd_telefono": "1. /telp (DNI)\n2. /telpcel (NUMERO)",
        "cmd_denuncia": "1. /denuncias\n2. /den",
        "cmd_sueldo": "1. /suel",
        "cmd_buy": "💎 PLANES PREMIUM..."
    }

    if query.data in comandos:
        volver = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al inicio", callback_data="volver_cmds")]])
        try:
            await query.edit_message_caption(caption=comandos[query.data], reply_markup=volver)
        except:
            await query.edit_message_text(comandos[query.data], reply_markup=volver)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    if user_id in usuarios: return await update.message.reply_text("Ya estas registrado")
    usuarios[user_id] = {"creditos": 0, "nombre": update.effective_user.first_name, "username": update.effective_user.username, "fecha_registro": get_fecha(), "rol": "PENDIENTE", "plan": "FREE", "consultas": 0}
    guardar_usuarios(usuarios)
    await update.message.reply_text(f"Registro exitoso! Bienvenido {update.effective_user.first_name}")

async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    VIDEO_PERFIL = "https://files.catbox.moe/jwtbu0.mp4"
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    u = usuarios.get(user_id, {})
    if not u: return await update.message.reply_text("No registrado. Usa /register")
    
    texto = f"""[#BOT DATA] ➾ PERFIL DE USUARIO
PERFIL DE ➾ {u.get("nombre", "Usuario")}
[🙎‍♂️] ID ➾ {user_id}
[👨🏻‍💻] USER ➾ @{u.get("username", "")}
[💰] CREDITOS ➾ {u.get('creditos', 0)}
[📊] CONSULTAS ➾ {u.get('consultas', 0)}"""
    
    await context.bot.send_video(chat_id=update.effective_chat.id, video=VIDEO_PERFIL, caption=texto, parse_mode='HTML')

async def denuncias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Lógica de denuncias PDF
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "denuncia", usuarios) # Usamos precio denuncia
    if not ok: return await update.message.reply_text(res_cred)

    if len(context.args) != 1:
        await update.message.reply_text("❌ Uso: /denuncias <DNI>")
        return
    dni = context.args[0]
    url = f"https://api-codart.cgrt.org/api/v1/consultas/fd/denuncias/{dni}"
    try:
        data = await consultar_api_get(url)
        if not data.get("success"):
            await update.message.reply_text("❌ No se encontraron denuncias.")
            return

        usuarios[user_id]["creditos"] -= PRECIOS["denuncia"]
        guardar_usuarios(usuarios)

        info = data["data"]
        for den in info["denuncias"]:
            pdf_b64 = den["data_uri"].split(",")[1]
            archivo = BytesIO(base64.b64decode(pdf_b64))
            archivo.name = den["nombre"]
            await update.message.reply_document(document=archivo, filename=den["nombre"], caption=f"🚨 Denuncia #{den['numero']}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Texto de compra (mismo que pusiste)
    await update.message.reply_text("💎 PLANES PREMIUM... @Sthep_18")

async def staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👑 STAFF OFICIAL... @Sthep_18")

async def quitarcrd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_ID: return await update.message.reply_text("🚫 ACCESO DENEGADO")
    if len(context.args) < 2: return await update.message.reply_text("Uso: /quitarcrd ID CANTIDAD")
    target_id, cantidad = context.args[0], int(context.args[1])
    usuarios = cargar_usuarios()
    if target_id in usuarios:
        usuarios[target_id]["creditos"] = max(0, usuarios[target_id]["creditos"] - cantidad)
        guardar_usuarios(usuarios)
        await update.message.reply_text(f"Créditos quitados. Nuevo saldo: {usuarios[target_id]['creditos']}")

async def addcreditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_ID: return await update.message.reply_text("🚫 ACCESO DENEGADO")
    if len(context.args) < 2: return await update.message.reply_text("Uso: /addcreditos ID CANTIDAD")
    target_id, cantidad = context.args[0], int(context.args[1])
    usuarios = cargar_usuarios()
    if target_id in usuarios:
        usuarios[target_id]["creditos"] += cantidad
        guardar_usuarios(usuarios)
        await update.message.reply_text(f"Créditos agregados. Nuevo saldo: {usuarios[target_id]['creditos']}")

# ===== CORRECCIÓN DE LOS 5 COMANDOS SOLICITADOS =====

async def facial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    
    ok, res_cred = await validar_creditos(user_id, "facial", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    message = update.message
    if not message.photo and not (message.reply_to_message and message.reply_to_message.photo):
        return await message.reply_text("📷 Envía una foto con el comando /facial o responde a una.")

    try:
        photo = message.photo[-1] if message.photo else message.reply_to_message.photo[-1]
        m = await message.reply_text("🧬 Procesando imagen facial...")
        
        tg_file = await context.bot.get_file(photo.file_id)
        img_bytes = await tg_file.download_as_bytearray()

        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        files = {"image_facial": ("img.jpg", bytes(img_bytes), "image/jpeg")}

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{BASE_URL}/api/v1/consultas/fd/facial/top", headers=headers, files=files)
        
        data = r.json()
        if not data.get("success"):
            return await m.edit_text("❌ No se encontraron coincidencias.")

        # DESCUENTO
        usuarios[user_id]["creditos"] -= PRECIOS["facial"]
        usuarios[user_id]["consultas"] += 1
        guardar_usuarios(usuarios)

        info = data["data"]
        res_txt = f"🧬 <b>RESULTADO FACIAL</b>\n\n"
        for i, p in enumerate(info.get("coincidencias", []), 1):
            res_txt += f"👤 <b>#{i}</b>\n🪪 DNI: <code>{p['dni']}</code>\n📛: {p['nombre']}\n🎯: {p['porcentaje']}%\n\n"
        
        res_txt += f"💰 <b>Créditos:</b> {usuarios[user_id]['creditos']}"
        await m.edit_text(res_txt, parse_mode="HTML", reply_markup=BTN_VOLVER)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

async def dnit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "dnit", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if not context.args: return await update.message.reply_text("❌ Uso: /dnit 12345678", parse_mode="HTML")
    dni = context.args[0]
    
    m = await update.message.reply_text(f"🔎 Consultando DNI-T <code>{dni}</code>...", parse_mode="HTML")
    data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/dnit/{dni}")

    if not data.get("success"): return await m.edit_text("❌ No encontrado.")

    usuarios[user_id]["creditos"] -= PRECIOS["dnit"]
    usuarios[user_id]["consultas"] += 1
    guardar_usuarios(usuarios)

    res = data["data"]
    texto = f"💳 <b>DNI-T • SISTEMA</b>\n\n🆔 <b>DNI:</b> <code>{res['dni']['completo']}</code>\n👤 <b>Nombre:</b> {res['nombres']} {res['apellidos']}\n"
    texto += f"📅 <b>Nacimiento:</b> {res['nacimiento']['fecha']}\n🏠 <b>Dirección:</b> {res['domicilio']['direccion']}\n\n💰 <b>Créditos:</b> {usuarios[user_id]['creditos']}"
    
    await m.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    
    if res.get("images"):
        for img in res["images"]:
            b64 = img['data_uri'].split(',')[1]
            await update.message.reply_photo(photo=base64.b64decode(b64))

async def telp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "telp", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if not context.args: return await update.message.reply_text("❌ Uso: /telp 12345678", parse_mode="HTML")
    dni = context.args[0]

    m = await update.message.reply_text(f"📡 Consultando líneas de <code>{dni}</code>...", parse_mode="HTML")
    data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/telp/{dni}")

    if not data.get("success"): return await m.edit_text("❌ Sin resultados.")

    usuarios[user_id]["creditos"] -= PRECIOS["telp"]
    usuarios[user_id]["consultas"] += 1
    guardar_usuarios(usuarios)

    res = data["data"]
    texto = f"📡 <b>LÍNEAS DE {dni}</b>\n\n"
    for i, l in enumerate(res.get("lineas", []), 1):
        texto += f"{i}. 📱 {l['telefono']} | {l['operador']} | {l['empresa']}\n"
    
    texto += f"\n💰 <b>Créditos:</b> {usuarios[user_id]['creditos']}"
    await m.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

async def dni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "dni", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if not context.args: return await update.message.reply_text("❌ Uso: /dni 12345678", parse_mode="HTML")
    dni_val = context.args[0]

    m = await update.message.reply_text(f"🔎 Consultando DNI <code>{dni_val}</code>...", parse_mode="HTML")
    data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/dni/{dni_val}")

    if not data.get("success"): return await m.edit_text("❌ No encontrado.")

    usuarios[user_id]["creditos"] -= PRECIOS["dni"]
    usuarios[user_id]["consultas"] += 1
    guardar_usuarios(usuarios)

    res = data["data"]
    texto = f"🪪 <b>DNI SISTEMA</b>\n\n👤 <b>{res['nombres']} {res['apellidos']}</b>\n🆔 <code>{dni_val}</code>\n"
    texto += f"🏠 {res['domicilio']['direccion']}\n👨 Padre: {res['informacion_general']['padre']}\n👩 Madre: {res['informacion_general']['madre']}\n\n"
    texto += f"💰 <b>Créditos:</b> {usuarios[user_id]['creditos']}"
    await m.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

async def telpcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Mantenemos tu lógica de telpcel pero verificando créditos
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "telpcel", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if not context.args: return await update.message.reply_text("Uso: /telpcel 900000001")
    numero = context.args[0]
    data = await consultar_api_get(f"https://api-codart.cgrt.org/api/v1/consultas/fd/telp/cel/{numero}")

    if not data.get("success"): return await update.message.reply_text("❌ Sin resultados.")

    usuarios[user_id]["creditos"] -= PRECIOS["telpcel"]
    guardar_usuarios(usuarios)

    titulares = data["data"]["titulares"]
    texto = "📡 <b>TELP CEL</b>\n\n"
    for t in titulares:
        texto += f"👤 {t.get('titular')}\n📱 {t.get('telefono')}\n🏢 {t.get('operador')}\n⚡━━━━━⚡\n"
    
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

async def hsoat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Lógica de SOAT con créditos
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "hsoat", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if not context.args: return await update.message.reply_text("Uso: /hsoat ABC123")
    placa = context.args[0].upper()
    data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/hsoat/{placa}")

    if not data.get("success"): return await update.message.reply_text("❌ Placa no encontrada.")

    usuarios[user_id]["creditos"] -= PRECIOS["hsoat"]
    guardar_usuarios(usuarios)
    await update.message.reply_text(f"✅ Historial SOAT enviado. Créditos: {usuarios[user_id]['creditos']}")

async def denpla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "denpla", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if not context.args: return await update.message.reply_text("Uso: /denpla ABC123")
    placa = context.args[0].upper()
    data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/denpla/{placa}")

    if not data.get("success"): return await update.message.reply_text("❌ No encontrado.")

    usuarios[user_id]["creditos"] -= PRECIOS["denpla"]
    guardar_usuarios(usuarios)
    await update.message.reply_text(f"🚨 Denuncias por placa obtenidas. Créditos: {usuarios[user_id]['creditos']}")

async def suel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    usuarios = cargar_usuarios()
    ok, res_cred = await validar_creditos(user_id, "suel", usuarios)
    if not ok: return await update.message.reply_text(res_cred)

    if not context.args: return await update.message.reply_text("Uso: /suel 12345678")
    dni = context.args[0]
    data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/suel/{dni}")

    if not data.get("success"): return await update.message.reply_text("❌ Sin registros.")

    usuarios[user_id]["creditos"] -= PRECIOS["suel"]
    guardar_usuarios(usuarios)
    
    res = data["data"]
    texto = f"💰 <b>SUELDOS</b>\n\n🆔 {res['consulta']}\n"
    for s in res.get("sueldos", []):
        texto += f"🏢 {s['empresa']} | 💰 {s['sueldo']}\n"
    
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)


# ===== MAIN =====
def main():
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cmds", cmds))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("me", me))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("staff", staff))
    application.add_handler(CommandHandler("addcreditos", addcreditos))
    application.add_handler(CommandHandler("quitarcrd", quitarcrd))
    
    # Comandos de Consulta
    application.add_handler(CommandHandler("dni", dni))
    application.add_handler(CommandHandler("dnit", dnit))
    application.add_handler(CommandHandler("telp", telp))
    application.add_handler(CommandHandler("facial", facial))
    application.add_handler(CommandHandler("agv", agv))
    
    # Otros
    application.add_handler(CommandHandler("suel", suel))
    application.add_handler(CommandHandler("hsoat", hsoat))
    application.add_handler(CommandHandler("denpla", denpla))
    application.add_handler(CommandHandler("den", den))
    application.add_handler(CommandHandler("telpcel", telpcel))
    application.add_handler(CommandHandler("denuncias", denuncias))
    
    application.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/facial"), facial))

    print("Bot iniciado v2.1 (Senior Mode)...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()