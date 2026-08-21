import os
import re
import base64
import threading
from io import BytesIO
from dotenv import load_dotenv
import httpx
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_TOKEN = os.getenv("API_TOKEN", "")
BASE_URL = "https://api-codart.cgrt.org/api/v1/consultas/fd"

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot CODART Online 24/7"

def validar_dni(dni: str) -> str:
    dni = dni.strip()
    if not re.fullmatch(r"\d{8}", dni):
        return None
    return dni

async def llamar_api(endpoint: str, dni: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}",
    }
    url = f"{BASE_URL}/{endpoint}/{dni}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 401:
                return {"error": "Token inválido"}
            if resp.status_code == 404:
                return {"error": "DNI no encontrado"}
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "Tiempo agotado"}
    except Exception as e:
        return {"error": str(e)}

def decodificar_imagen(data_uri: str):
    if not data_uri or "," not in data_uri:
        return None
    try:
        return BytesIO(base64.b64decode(data_uri.split(",", 1)[1]))
    except Exception:
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot activo.\n\n"
        "Comandos:\n"
        "/dni 12345678 — Consulta DNI\n"
        "/agv 12345678 — Consulta AGV"
    )

async def dni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Uso: /dni 12345678")

    dni_num = validar_dni(context.args[0])
    if not dni_num:
        return await update.message.reply_text("DNI debe tener 8 dígitos numéricos")

    mensaje = await update.message.reply_text("Consultando...")
    resp = await llamar_api("dni", dni_num)

    if "error" in resp:
        return await mensaje.edit_text(f"Error: {resp['error']}")

    if not resp.get("success"):
        return await mensaje.edit_text("No se obtuvo respuesta exitosa")

    data = resp.get("data", {})
    dni_info = data.get("dni", {})
    nacimiento = data.get("nacimiento", {})
    domicilio = data.get("domicilio", {})
    general = data.get("informacion_general", {})

    texto = (
        f"📋 DNI: {dni_info.get('completo', dni_num)}\n"
        f"Nombre: {data.get('nombres', '')} {data.get('apellidos', '')}\n"
        f"Género: {data.get('genero', '')}\n"
        f"Edad: {nacimiento.get('edad', '')}\n"
        f"Dirección: {domicilio.get('direccion', '')}, {domicilio.get('distrito', '')}"
    )

    await mensaje.edit_text(texto)

    imagenes = data.get("images", [])
    if imagenes:
        foto = decodificar_imagen(imagenes[0].get("data_uri", ""))
        if foto:
            await update.message.reply_photo(foto)

async def agv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Uso: /agv 12345678")

    dni_num = validar_dni(context.args[0])
    if not dni_num:
        return await update.message.reply_text("DNI debe tener 8 dígitos numéricos")

    mensaje = await update.message.reply_text("Consultando...")
    resp = await llamar_api("agv", dni_num)

    if "error" in resp:
        return await mensaje.edit_text(f"Error: {resp['error']}")

    if not resp.get("success"):
        return await mensaje.edit_text("No se obtuvo respuesta exitosa")

    data = resp.get("data", {})

    texto = (
        f"📋 DNI: {data.get('dni', '')}\n"
        f"Nombre: {data.get('nombres', '')} {data.get('apellidos', '')}\n"
        f"Género: {data.get('genero', '')}\n"
        f"Edad: {data.get('edad', '')}"
    )

    await mensaje.edit_text(texto)

    imagenes = data.get("images", [])
    if imagenes:
        foto = decodificar_imagen(imagenes[0].get("data_uri", ""))
        if foto:
            await update.message.reply_photo(foto)

def run_bot():
    if not BOT_TOKEN or not API_TOKEN:
        print("ERROR: Faltan BOT_TOKEN o API_TOKEN")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("dni", dni))
    application.add_handler(CommandHandler("agv", agv))

    print("Bot iniciado")
    application.run_polling()

if __name__ == "__main__":
    # Hilo para el bot
    threading.Thread(target=run_bot).start()
    # Flask para que Render no lo apague
    app.run(host='0.0.0.0', port=10000)
