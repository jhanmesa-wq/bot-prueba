import asyncio
import base64
import io
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_TOKEN = os.getenv("API_TOKEN", "")
API_BASE = "https://api-codart.cgrt.org"

def header_futurista(title):
    return f"┏━━━━━━━━━━━━━━┓\n ⚡ <b>SPECTER PERÚ</b> ⚡\n {title}\n┗━━━━━━━━━━━━━━┛\n\n"

def get_back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLVER AL MENÚ", callback_data="back_to_menu")]])

def get_cmds_menu():
    keyboard = [
        [InlineKeyboardButton("🔍 /dni", callback_data="cmd_dni")],
        [InlineKeyboardButton("⬅️ VOLVER AL MENÚ", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def api_get_dni(session, dni):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CODART_TOKEN}"
    }
    url = f"{API_BASE}/dni/{dni}"
    try:
        async with session.get(url, headers=headers) as response:
            return await response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = header_futurista("SISTEMA DE CONSULTAS") + \
           f"Bienvenido <b>{update.effective_user.first_name}</b>\n\n" + \
           "Bot SPECTER PERÚ activado.\nUsa /cmds para ver los comandos"
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=get_back_button())

async def cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = header_futurista("LISTA DE COMANDOS") + \
           "Selecciona un comando para ejecutar:"
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=get_cmds_menu(), parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if data == "back_to_menu":
        text = header_futurista("MENÚ PRINCIPAL") + "Usa /cmds para ver comandos disponibles"
        await query.edit_message_text(text=text, reply_markup=get_back_button(), parse_mode="HTML")
        return

    if data == "cmd_dni":
        text = header_futurista("/dni") + "Envía el comando así:\n<code>/dni 12345678</code>\n\nDebe tener 8 dígitos"
        await query.edit_message_text(text=text, reply_markup=get_back_button(), parse_mode="HTML")
        return

async def dni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not context.args:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=header_futurista("ERROR") + "Uso correcto: <code>/dni 12345678</code>", 
            reply_markup=get_back_button(), 
            parse_mode="HTML"
        )
        return
    
    dni_val = context.args[0]
    
    if not re.match(r"^\d{8}$", dni_val):
        await context.bot.send_message(
            chat_id=chat_id, 
            text=header_futurista("ERROR") + "❌ El DNI debe tener exactamente 8 dígitos", 
            reply_markup=get_back_button(), 
            parse_mode="HTML"
        )
        return
    
    await context.bot.send_message(chat_id=chat_id, text="⏳ Consultando en CODART...")
    
    async with aiohttp.ClientSession() as session:
        res = await api_get_dni(session, dni_val)
    
    if res.get("success"):
        d = res["data"]
        text = header_futurista("RESULTADO DNI")
        text += f"<b>Nombre:</b> {d['nombres']} {d['apellidos']}\n"
        text += f"<b>DNI:</b> {d['dni']['completo']}\n"
        text += f"<b>Género:</b> {d['genero']}\n"
        text += f"<b>Edad:</b> {d['nacimiento']['edad']}\n"
        text += f"<b>Fecha Nac:</b> {d['nacimiento']['fecha']}\n"
        text += f"<b>Dirección:</b> {d['domicilio']['direccion']}\n"
        text += f"<b>Distrito:</b> {d['domicilio']['distrito']}\n"
        text += f"<b>Estado Civil:</b> {d['informacion_general']['estado_civil']}\n"
        text += f"<b>Grado:</b> {d['informacion_general']['nivel_educativo']}"
        
        await context.bot.send_message(
            chat_id=chat_id, 
            text=text, 
            reply_markup=get_back_button(), 
            parse_mode="HTML"
        )
    else:
        error_msg = res.get("error", "Error desconocido")
        if "401" in str(error_msg) or "Token" in str(error_msg):
            error_msg = "❌ TOKEN INVÁLIDO. Revisa tu CODART_TOKEN"
        await context.bot.send_message(
            chat_id=chat_id, 
            text=header_futurista("ERROR API") + error_msg, 
            reply_markup=get_back_button(), 
            parse_mode="HTML"
        )

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cmds", cmds))
    application.add_handler(CommandHandler("dni", dni))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("SPECTER PERÚ BOT INICIADO")
    application.run_polling()

if __name__ == "__main__":
    main()
