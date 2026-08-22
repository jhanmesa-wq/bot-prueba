import os
import re
import base64
import sqlite3
import tempfile
import requests
from functools import wraps
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CODART_TOKEN = os.getenv("CODART_TOKEN")
API_BASE = "https://api-codart.cgrt.org/api/v1/consultas/fd"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CODART_TOKEN}"
}

# ================== SISTEMA DE CRÉDITOS ==================
DB_PATH = "bot.db"
CREDITOS_INICIALES = 10
COSTOS = {
    "dni": 5,
    "agv": 20,
    "facial": 60
}

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            creditos INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def get_creditos(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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

def set_creditos(user_id: int, nuevo_saldo: int):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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

def con_creditos(costo: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            saldo = get_creditos(user_id)
            if saldo < costo:
                await update.message.reply_text(
                    f"❌ Créditos insuficientes.\nTe quedan: {saldo} CRD\nCosto: {costo} CRD",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER", callback_data="menu")]])
                )
                return
            nuevo_saldo = descontar_creditos(user_id, costo)
            context.user_data['costo_actual'] = costo
            context.user_data['saldo_actual'] = nuevo_saldo
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

def reembolsar(user_id: int, cantidad: int):
    saldo_actual = get_creditos(user_id)
    nuevo_saldo = saldo_actual + cantidad
    set_creditos(user_id, nuevo_saldo)
    return nuevo_saldo

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
    except:
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
    texto = f"""
╔═════════════════════╗
⚜️ SPECTER PERÚ
╚═════════════════════╝

🚀 PLATAFORMA DE CONSULTAS

🏷️ Nombre: ⚜ SPECTER PERÚ ⚜
👤 Usuario: {bot_username}
🛰️ Estado: ONLINE

━━━━━━━━━━━━━━━━━━━━━━
📚 COMANDOS

📝 /register ➜ Registrar cuenta
📖 /cmds ➜ Ver servicios
👤 /me ➜ Ver perfil
🛡️ /staff ➜ Ver staff
💳 /buy ➜ Planes y créditos

━━━━━━━━━━━━━━━━━━━━━━
⚡ Sistema actualizado y centralizado
"""
    await update.message.reply_text(texto, reply_markup=teclado_volver())

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
        await query.edit_message_text(texto_menu_cmds(), reply_markup=teclado_menu_cmds())
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

@con_creditos(costo=COSTOS["dni"])
async def dni_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text("❌ Uso: /dni 12345678", reply_markup=teclado_volver())
        return

    dni = context.args[0].strip()
    if not validar_dni(dni):
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await update.message.reply_text("❌ DNI no válido. Debe tener 8 dígitos.", reply_markup=teclado_volver())
        return

    mensaje_progreso = await update.message.reply_text(f"🔍 Consultando DNI {dni}...")

    try:
        r = requests.get(f"{API_BASE}/dni/{dni}", headers=HEADERS, timeout=15)

        if r.status_code == 401:
            reembolsar(update.effective_user.id, COSTOS["dni"])
            await mensaje_progreso.edit_text("❌ Error: Token CODART no válido.", reply_markup=teclado_volver())
            return
        if r.status_code == 404:
            reembolsar(update.effective_user.id, COSTOS["dni"])
            await mensaje_progreso.edit_text("❌ DNI no encontrado.", reply_markup=teclado_volver())
            return

        j = r.json()
        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["dni"])
            await mensaje_progreso.edit_text(f"❌ Error API: {j}", reply_markup=teclado_volver())
            return

        data = j.get("data", {})
        dni_info = data.get("dni", {})
        nac = data.get("nacimiento", {})
        info = data.get("informacion_general", {})
        dom = data.get("domicilio", {})

        mensaje = f"""
╔════════════════════════╗
  🪪 IDENTIDAD VERIFICADA
  🔐 RENIEC - SPECTER PERÚ
╚════════════════════════╝

┌─ 📄 DOCUMENTO ───────────┐
│ DNI: {clean(dni_info.get('completo'))}
│ Dígito: {clean(dni_info.get('digito_verificador'))} | ✅ VALIDADO
└─────────────────────────┘

┌─ 👤 TITULAR ─────────────┐
│ Nombres: {clean(data.get('nombres'))}
│ Apellidos: {clean(data.get('apellidos'))}
│ Género: {clean(data.get('genero'))}
│ Edad: {clean(nac.get('edad'))} años
│ F. Nac: {clean(nac.get('fecha'))}
│ Lugar: {clean(nac.get('departamento'))} / {clean(nac.get('provincia'))} / {clean(nac.get('distrito'))}
└─────────────────────────┘

┌─ 📂 FILIACIÓN ───────────┐
│ Padre: {clean(info.get('padre'))}
│ Madre: {clean(info.get('madre'))}
│ Estado Civil: {clean(info.get('estado_civil'))}
│ Nivel Educ: {clean(info.get('nivel_educativo'))}
│ Estatura: {clean(info.get('estatura'))} cm
│ Restricción: {clean(info.get('restriccion'))}
└─────────────────────────┘

┌─ 🏠 DOMICILIO ───────────┐
│ {clean(dom.get('direccion'))}
│ {clean(dom.get('departamento'))} / {clean(dom.get('provincia'))} / {clean(dom.get('distrito'))}
└─────────────────────────┘

┌─ 🗓 VIGENCIA ─────────────┐
│ Emisión: {clean(info.get('fecha_emision'))}
│ Caducidad: {clean(info.get('fecha_caducidad'))}
│ Donante: {clean(info.get('donante_organos'))}
└─────────────────────────┘

⚡ SPECTER.PY | Consulta exitosa
"""
        mensaje += footer_creditos(context)

        images = data.get("images", [])
        if images and images[0].get("data_uri"):
            foto = decodificar_imagen(images[0]["data_uri"])
            if foto:
                await update.message.reply_photo(photo=foto, caption=mensaje, reply_markup=teclado_volver())
                return

        await mensaje_progreso.edit_text(mensaje, reply_markup=teclado_volver())

    except Exception as e:
        reembolsar(update.effective_user.id, COSTOS["dni"])
        await mensaje_progreso.edit_text(f"❌ Error: {e}", reply_markup=teclado_volver())

@con_creditos(costo=COSTOS["agv"])
async def agv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text("❌ Uso: /agv 12345678", reply_markup=teclado_volver())
        return

    dni = context.args[0].strip()
    if not validar_dni(dni):
        reembolsar(update.effective_user.id, COSTOS["agv"])
        await update.message.reply_text("❌ DNI no válido. Debe tener 8 dígitos.", reply_markup=teclado_volver())
        return

    mensaje_progreso = await update.message.reply_text(f"🔍 Consultando AGV {dni}...")

    try:
        r = requests.get(f"{API_BASE}/agv/{dni}", headers=HEADERS, timeout=15)
        j = r.json()

        if not j.get("success"):
            reembolsar(update.effective_user.id, COSTOS["agv"])
            await mensaje_progreso.edit_text(f"❌ Error API: {j}", reply_markup=teclado_volver())
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
                return

        await mensaje_progreso.edit_text(mensaje, reply_markup=teclado_volver())

    except Exception as e:
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

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 COMPRAR CRÉDITOS\n\n"
        "Pronto disponible...\n\n"
        "💰 Precios:\n"
        "5 CRD = S/ 5.00\n"
        "20 CRD = S/ 18.00\n"
        "60 CRD = S/ 50.00",
        reply_markup=teclado_volver()
    )

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Cuenta registrada automáticamente.\nTienes 10 CRD de bienvenida!", reply_markup=teclado_volver())

def main():
    init_db()
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

    print("🤖 SPECTER PERÚ — Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
