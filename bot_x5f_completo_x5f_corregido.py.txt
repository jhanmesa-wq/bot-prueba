import asyncio
import base64
import datetime
import html
import io
import json
import logging
import os
import re
from io import BytesIO
from threading import Thread
from typing import Any, Dict, Optional, Tuple
import httpx
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from flask import Flask, request, jsonify
#============================================================
#LOGGING
#============================================================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("specter_peru")
#============================================================
#CONFIGURACIÓN
#============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
ARCHIVO_USUARIOS = os.getenv("ARCHIVO_USUARIOS") or "usuarios.json"
BASE_URL = os.getenv("BASE_URL") or "https://api-codart.cgrt.org"
BOT_USER = "@specter_Dox44bot"
BOT_NAME = "⚜ SPECTER PERÚ ⚜"
CLAVE_SECRETA = os.getenv("CLAVE_SECRETA", "PON_TU_CLAVE_AQUI")
TASA_CREDITOS = 1
TU_CELULAR_YAPE = "925805734"
TU_NOMBRE = "CHRISTIAN GUSTAVO RAMOS GONZALES"
ADMIN_ID = {
    item.strip()
    for item in (os.getenv("ADMIN_ID") or "").split(",")
    if item.strip()
}
#Precios actualizados y nuevos comandos agregados
PRECIOS = {
    "dni": 4,
    "agv": 20,
    "telpcel": 15,
    "facial": 30,
    "ruc": 5,
    "suel": 5,
    "denuncia": 10,
    "placa": 12,
    "nm": 6,
    "hsoat": 8,
    "denpla": 30,
    "dnit": 5,
    "telp": 15,
    "revtec": 10,
    "dir": 6,
    "dnivel": 10,
    "rqh": 30,
    "denuncias": 30
}
#============================================================
#FLASK KEEP-ALIVE & WEBHOOK PAGO
#============================================================

app = Flask(__name__)
@app.route('/')
def home():
    return "🔥 SPECTER PERÚ BOT ACTIVO 24/7"
@app.route('/health')
def health():
 return "OK", 200
@app.route("/webhook-pagos/", methods=["POST"])
def recibir_pago():
    datos = request.get_json()
    if not datos:
        return jsonify({"error": "Sin datos"}), 400
    celular = str(datos.get("numero", "")).strip()
    monto_str = str(datos.get("monto", "0"))
    remitente = datos.get("de", "Desconocido")

    try:
        monto = float(monto_str.replace(",", "."))
    except:
        return jsonify({"error": "Monto inválido"}), 400

    creditos = int(monto * TASA_CREDITOS)
    usuarios = cargar_usuarios()
    user_id_encontrado = None
    for user_id, info in usuarios.items():
        if str(info.get("celular", "")).strip() == celular:
            user_id_encontrado = user_id
            break

    # Si encontramos usuario y hay créditos, actualizar y notificar
    if user_id_encontrado and creditos > 0:
        usuarios[user_id_encontrado]["creditos"] = int(usuarios[user_id_encontrado].get("creditos", 0)) + creditos
        guardar_usuarios(usuarios)
        # Notificación asíncrona mediante un thread para no bloquear Flask
        Thread(target=lambda: asyncio.run(notificar_usuario(user_id_encontrado, monto, creditos, remitente))).start()
        logger.info(f"✅ PAGO — S/{monto} de {remitente} → +{creditos} créditos a {user_id_encontrado}")
    else:
        logger.warning(f"⚠️ Pago S/{monto} de {remitente} — Usuario NO REGISTRADO: {celular}")

    return jsonify({"status": "ok"}), 200
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
#============================================================
#ESTILO FUTURISTA CENTRALIZADO
#============================================================
SEPARADOR = "━━━━━━━━━━━━━━━━━━━━━━"
SEPARADOR_CORTO = "━━━━━━━━━━━━━━━━━━"
BTN_VOLVER = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🏠 VOLVER AL MENÚ", callback_data="volver_cmds")]]
)
def menu_teclado() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🪪 RENIEC", callback_data="cmd_reniec"),
                InlineKeyboardButton("🏢 RUC", callback_data="cmd_ruc"),
            ],
            [
                InlineKeyboardButton("🚘 VEHÍCULOS", callback_data="cmd_vehiculos"),
                InlineKeyboardButton("📱 TELÉFONO", callback_data="cmd_telefono"),
            ],
            [
                InlineKeyboardButton("⚖️ DENUNCIAS", callback_data="cmd_denuncia"),
                InlineKeyboardButton("💰 SUELDOS", callback_data="cmd_sueldo"),
            ],
            [
                InlineKeyboardButton("🧬 FACIAL", callback_data="cmd_facial"),
                InlineKeyboardButton("🔍 OTROS", callback_data="cmd_otros"),
            ],
            [
                InlineKeyboardButton("💎 COMPRAR", callback_data="cmd_buy"),
            ],
        ]
    )
def titulo_sistema(nombre: str, icono: str = "⚡") -> str:
    return (
        f"╔═════════════════════╗\n"
        f"{icono} <b>{html.escape(nombre.upper())}</b>\n"
        f"╚═════════════════════╝"
    )
def error_html(texto: Any) -> str:
    if texto is None:
        return "-"
    return html.escape(str(texto))


def data_o_vacia(payload: Any) -> Dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def extraer_data_uri(data_uri: Any) -> bytes:
    if not data_uri or not isinstance(data_uri, str):
        raise ValueError("La API no devolvió un archivo válido.")
    if "," not in data_uri:
        raise ValueError("Formato de archivo inválido.")
    try:
        return base64.b64decode(data_uri.split(",", 1)[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("No se pudo decodificar el archivo recibido.") from exc
#============================================================
#BASE DE DATOS DE USUARIOS
#============================================================
def cargar_usuarios() -> Dict[str, Dict[str, Any]]:
    try:
        if not os.path.exists(ARCHIVO_USUARIOS):
            return {}
        with open(ARCHIVO_USUARIOS, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"Error cargando usuarios: {e}")
        return {}

def guardar_usuarios(usuarios: Dict[str, Dict[str, Any]]) -> None:
    try:
        with open(ARCHIVO_USUARIOS, "w", encoding="utf-8") as f:
            json.dump(usuarios, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error guardando usuarios: {e}")

def obtener_usuario(update: Update, usuarios: Dict[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    user = update.effective_user
    user_id = str(user.id)
    if user_id not in usuarios:
        usuarios[user_id] = {
            "id": user_id,
            "nombre": user.full_name,
            "username": user.username or "",
            "creditos": 0,
            "consultas": 0,
            "plan": "FREE",
            "celular": "",
            "fecha_registro": datetime.datetime.now().isoformat()
        }
        guardar_usuarios(usuarios)
    return user_id, usuarios[user_id]

async def validar_creditos(user_id: str, comando: str, usuarios: Dict[str, Dict[str, Any]]) -> Tuple[bool, Any]:
    if comando not in PRECIOS:
        return False, f"El comando {comando} no tiene precio configurado."
    costo = PRECIOS[comando]
    saldo = int(usuarios.get(user_id, {}).get("creditos", 0) or 0)
    if saldo < costo:
        return (
            False,
            "╔═════════════════════╗\n"
            "💳 <b>CRÉDITOS INSUFICIENTES</b>\n"
            "╚═════════════════════╝\n\n"
            f"❌ Saldo actual: <code>{saldo}</code> créditos\n"
            f"💎 Costo: <code>{costo}</code> créditos\n"
            f"📉 Faltan: <code>{costo - saldo}</code> créditos\n\n"
            "🛒 Usa <code>/buy</code> para recargar."
        )
    return True, costo
async def cobrar_creditos(
    user_id: str,
    comando: str,
    usuarios: Dict[str, Dict[str, Any]],
) -> int:
    costo = int(PRECIOS[comando])
    usuario = usuarios[user_id]
    saldo = int(usuario.get("creditos", 0) or 0)
    if saldo < costo:
        raise ValueError("Saldo insuficiente al intentar cobrar la consulta.")
    usuario["creditos"] = saldo - costo
    usuario["consultas"] = int(usuario.get("consultas", 0) or 0) + 1
    guardar_usuarios(usuarios)
    return usuario["creditos"]
async def preparar_consulta(
    update: Update,
    comando: str,
    usuarios: Dict[str, Dict[str, Any]],
    user_id: str,
) -> Optional[int]:
    ok, resultado = await validar_creditos(user_id, comando, usuarios)
    if not ok:
        await update.message.reply_text(
            resultado,
            parse_mode="HTML",
            reply_markup=BTN_VOLVER,
        )
        return None
    return int(resultado)
#============================================================
#CLIENTE API - IMPLEMENTACIÓN REAL CODART X V1
#============================================================


async def consultar_api_get(url: str, timeout: float = 35.0) -> Dict[str, Any]:
    """
    Implementación real GET a CODART API
    Headers: Content-Type application/json, Authorization Bearer {API_TOKEN}
    """
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 401:
                logger.error(f"401 en {url} - Token inválido")
                return {"error": "⛔ API_TOKEN inválido o expirado. Contacta @Sthep_18"}
            if response.status_code == 404:
                return {"error": "❌ No se encontraron resultados para esa consulta."}
            if response.status_code == 429:
                return {"error": "⏳ Demasiadas consultas, espera 10 segundos e intenta de nuevo."}
            if response.status_code >= 500:
                return {"error": f"🔥 Error del servidor CODART ({response.status_code}). Intenta más tarde."}
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                try:
                    err_json = exc.response.json()
                    msg = err_json.get("message") or err_json.get("error") or str(err_json)
                    return {"error": f"API Error: {msg}"}
                except:
                    return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"}
            try:
                data = response.json()
            except ValueError:
                return {"error": "La API devolvió una respuesta que no es JSON."}
            if not isinstance(data, dict):
                return {"error": "Respuesta JSON inválida de la API."}
            return data
    except httpx.TimeoutException:
        logger.warning(f"Timeout en {url}")
        return {"error": "⏰ La API tardó demasiado en responder. Intenta de nuevo."}
    except httpx.ConnectError:
        return {"error": "📡 No se pudo conectar a api-codart.cgrt.org. Verifica tu internet."}
    except httpx.RequestError as exc:
        logger.exception(f"RequestError {url}")
        return {"error": f"Error de conexión: {exc}"}
    except Exception as exc:
        logger.exception(f"Error inesperado GET {url}")
        return {"error": f"Error inesperado: {str(exc)[:200]}"}


async def consultar_api_post_facial(imagen: bytes) -> Dict[str, Any]:
    """
    Implementación real POST multipart/form-data a /api/v1/consultas/fd/facial/top
    Param: image_facial file jpg/jpeg/png
    Headers: Authorization Bearer, Accept application/json
    """
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "application/json",
    }
    files = {
        "image_facial": ("imagen.jpg", imagen, "image/jpeg")
    }
    url = f"{BASE_URL}/api/v1/consultas/fd/facial/top"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.post(
                url,
                headers=headers,
                files=files,
            )
            if response.status_code == 401:
                return {"error": "⛔ API_TOKEN inválido o expirado."}
            if response.status_code == 404:
                return {"error": "❌ Sin coincidencias faciales encontradas."}
            if response.status_code == 422:
                try:
                    j = response.json()
                    return {"error": f"❌ Imagen no válida: {j.get('message','Debe ser JPG/PNG')}"}
                except:
                    return {"error": "❌ Imagen no válida. Envía rostro frontal JPG/PNG."}
            if response.status_code != 200:
                return {"error": f"API HTTP {response.status_code}: {response.text[:400]}"}
            try:
                data = response.json()
            except ValueError:
                return {"error": "La API facial devolvió una respuesta inválida."}
            if not isinstance(data, dict):
                return {"error": "Respuesta facial inválida."}
            return data
    except httpx.TimeoutException:
        return {"error": "⏰ La API facial tardó demasiado. Reintenta."}
    except httpx.RequestError as exc:
        return {"error": f"📡 Error de conexión facial: {exc}"}
    except Exception as exc:
        logger.exception("Error API facial")
        return {"error": f"Error facial inesperado: {str(exc)[:200]}"}


async def notificar_usuario(user_id, monto, creditos, remitente):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    mensaje = (
        "✅ <b>PAGO DETECTADO</b>\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"• 💰 Recibido: <b>S/ {monto:.2f}</b>\n"
        f"• 🎁 +<b>{creditos}</b> Créditos agregados ✅\n"
        f"• 👤 De: {remitente}\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        "⚡ ¡Ya puedes usar tus créditos!"
    )
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                url,
                json={
                    "chat_id": user_id,
                    "text": mensaje,
                    "parse_mode": "HTML",
                },
            )
    except Exception as e:
        logger.error(f"Error notificando usuario {user_id}: {e}")
#============================================================
#UTILIDADES DE RESPUESTA
#============================================================
async def responder_error(update: Update, mensaje: str) -> None:
    await update.message.reply_text(
        f"❌ <b>{html.escape(mensaje)}</b>",
        parse_mode="HTML",
        reply_markup=BTN_VOLVER,
    )

async def editar_error(mensaje, mensaje_error: str) -> None:
    try:
        await mensaje.edit_text(
            f"❌ <b>{html.escape(mensaje_error)}</b>",
            parse_mode="HTML",
            reply_markup=BTN_VOLVER,
        )
    except Exception:
        # Fallback si el mensaje ya fue eliminado
        pass
#============================================================
#COMANDOS DE CONSULTA IMPLEMENTADOS - TODAS LAS APIS REALES
#============================================================
async def micelular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if not context.args:
        return await update.message.reply_text(
            "📱 <b>Uso:</b> /micelular 987654321\n\n"
            "Guarda tu número de Yape para que los pagos\n"
            "se sumen automáticamente a tu saldo ⚡",
            parse_mode="HTML"
        )

    celular = context.args[0].strip()
    if not re.match(r"^9\d{8}$", celular):
        return await update.message.reply_text(
            "❌ Número inválido. Debe empezar con 9 y tener 9 dígitos.",
            parse_mode="HTML"
        )

    usuario["celular"] = celular
    guardar_usuarios(usuarios)
    await update.message.reply_text(
        f"✅ <b>Número guardado:</b> {celular}\n\n"
        "💳 Ahora paga por Yape y los créditos\n"
        "se sumarán SOLOS en segundos ⚡",
        parse_mode="HTML"
    )
async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if not usuario.get("celular"):
        return await update.message.reply_text(
            "⚠️ Primero guarda tu número:\n<code>/micelular 987654321</code>",
            parse_mode="HTML"
        )

    monto = 5.0
    if context.args:
        try:
            monto = max(1.0, float(context.args[0].replace(",", ".")))
        except:
            monto = 5.0

    creditos = int(monto * TASA_CREDITOS)
    qr_url = "https://files.catbox.moe/0y85js.jpg"

    texto = (
        "💳 <b>INSTRUCCIONES DE PAGO</b>\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"• 💰 Monto: <b>S/ {monto:.2f}</b>\n"
        f"• 🎁 Recibes: <b>{creditos} Créditos</b>\n"
        f"• 📱 Paga al: <b>{TU_CELULAR_YAPE}</b>\n"
        f"• 👤 A nombre: <b>{TU_NOMBRE}</b>\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        "✅ Abre Yape → Escanea el QR o paga al número\n"
        "⚡ Los créditos se suman SOLOS en segundos\n"
        "⚠️ NO envíes comprobante, el sistema lo detecta solo."
    )

    await update.message.reply_photo(
        photo=qr_url,
        caption=texto,
        parse_mode="HTML"
    )


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    saldo_actual = usuario.get("creditos", 0)
    celular = usuario.get("celular", "No registrado")
    await update.message.reply_text(
        f"💰 <b>Tu Saldo:</b> {saldo_actual} Créditos\n"
        f"📱 Tu número: {celular}\n\n"
        "Usa /pagar para recargar más.",
        parse_mode="HTML"
    )


async def dni(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1:
        return await update.message.reply_text(
            f"{titulo_sistema('DNI • SISTEMA', '🪪')}\n\nUso: <code>/dni 12345678</code>\n💎 Costo: <code>{PRECIOS['dni']}</code> créditos",
            parse_mode="HTML",
            reply_markup=BTN_VOLVER,
        )

    dni_num = context.args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "El DNI debe contener exactamente 8 dígitos.")

    costo = await preparar_consulta(update, "dni", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text(
        f"🔎 <b>CONSULTANDO DNI</b>\n🪪 DNI: <code>{dni_num}</code>\n💎 Costo: <code>{costo}</code> créditos\n\n⏳ Procesando...",
        parse_mode="HTML"
    )

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/dni/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/dni/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "No encontrado."))

        info = data.get("data", {})
        d = info.get("dni", {})
        n = info.get("nacimiento", {})
        dom = info.get("domicilio", {})
        gen = info.get("informacion_general", {})
        saldo_restante = await cobrar_creditos(user_id, "dni", usuarios)

        texto = (
            f"{titulo_sistema('DNI • RESULTADO', '🪪')}\n\n"
            f"🪪 <b>DNI:</b> <code>{error_html(d.get('completo', dni_num))}</code>\n"
            f"👤 <b>NOMBRE:</b> <code>{error_html(info.get('nombres'))} {error_html(info.get('apellidos'))}</code>\n"
            f"⚧️ <b>GÉNERO:</b> <code>{error_html(info.get('genero'))}</code>\n"
            f"📅 <b>NACIMIENTO:</b> <code>{error_html(n.get('fecha'))} ({error_html(n.get('edad'))})</code>\n"
            f"📍 <b>LUGAR:</b> <code>{error_html(n.get('distrito'))}, {error_html(n.get('provincia'))}</code>\n"
            f"🏠 <b>DIRECCIÓN:</b> <code>{error_html(dom.get('direccion'))}</code>\n"
            f"💍 <b>ESTADO CIVIL:</b> <code>{error_html(gen.get('estado_civil'))}</code>\n"
            f"👨 <b>PADRE:</b> <code>{error_html(gen.get('padre'))}</code>\n"
            f"👩 <b>MADRE:</b> <code>{error_html(gen.get('madre'))}</code>\n\n"
            f"{SEPARADOR}\n"
            f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n"
            f"💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        )
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

        images = info.get("images", [])
        if images:
            try:
                raw = extraer_data_uri(images[0].get("data_uri"))
                await update.message.reply_photo(photo=BytesIO(raw), caption="📸 Foto RENIEC")
            except Exception as e_img:
                logger.warning(f"No se pudo enviar foto DNI {dni_num}: {e_img}")
    except Exception as e:
        logger.exception("Error en dni")
        await editar_error(mensaje, f"Error interno: {str(e)[:300]}")


async def dnit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1:
        return await update.message.reply_text(
            f"{titulo_sistema('DNI-T • SISTEMA', '💳')}\n\nUso: <code>/dnit 12345678</code>\n💎 Costo: <code>{PRECIOS['dnit']}</code> créditos",
            parse_mode="HTML",
            reply_markup=BTN_VOLVER,
        )

    dni_num = context.args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "El DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "dnit", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text("🔎 Consultando DNI Completo (T)...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/dnit/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/dnit/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "Error en DNIT"))

        info = data.get("data", {})
        d = info.get("dni", {})
        n = info.get("nacimiento", {})
        gen = info.get("informacion_general", {})
        dom = info.get("domicilio", {})
        saldo_restante = await cobrar_creditos(user_id, "dnit", usuarios)

        texto = (
            f"{titulo_sistema('DNI-T • DETALLADO', '💳')}\n\n"
            f"🪪 <b>DNI:</b> <code>{error_html(d.get('completo'))}</code>\n"
            f"👤 <b>TITULAR:</b> <code>{error_html(info.get('nombres'))} {error_html(info.get('apellidos'))}</code>\n"
            f"🎂 <b>EDAD:</b> <code>{error_html(n.get('edad'))}</code>\n"
            f"📅 <b>FECHA NAC:</b> <code>{error_html(n.get('fecha'))}</code>\n"
            f"📍 <b>NAC LUGAR:</b> <code>{error_html(n.get('departamento'))} - {error_html(n.get('provincia'))} - {error_html(n.get('distrito'))}</code>\n"
            f"🎓 <b>ESTUDIOS:</b> <code>{error_html(gen.get('nivel_educativo'))}</code>\n"
            f"📏 <b>ESTATURA:</b> <code>{error_html(gen.get('estatura'))}</code>\n"
            f"💍 <b>CIVIL:</b> <code>{error_html(gen.get('estado_civil'))}</code>\n"
            f"📑 <b>EMISIÓN:</b> <code>{error_html(gen.get('fecha_emision'))}</code>\n"
            f"📅 <b>CADUCIDAD:</b> <code>{error_html(gen.get('fecha_caducidad'))}</code>\n"
            f"🫀 <b>DONANTE:</b> <code>{error_html(gen.get('donante_organos'))}</code>\n"
            f"🏠 <b>DOMICILIO:</b> <code>{error_html(dom.get('direccion'))}</code>\n"
            f"📍 <b>UBICACIÓN:</b> <code>{error_html(dom.get('distrito'))} - {error_html(dom.get('provincia'))}</code>\n"
            f"{SEPARADOR}\n"
            f"💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        )
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

        for img in info.get("images", [])[:4]:
            try:
                raw = extraer_data_uri(img.get("data_uri"))
                await update.message.reply_photo(photo=BytesIO(raw))
            except Exception:
                continue
    except Exception as e:
        logger.exception("Error en dnit")
        await editar_error(mensaje, f"Error interno DNIT: {str(e)[:300]}")


async def telpcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1:
        return await update.message.reply_text(f"{titulo_sistema('TELPCEL', '📱')}\n\nUso: <code>/telpcel 900000001</code>\n💎 Costo: {PRECIOS['telpcel']} crd", parse_mode="HTML", reply_markup=BTN_VOLVER)

    numero = context.args[0].strip()
    if not (numero.isdigit() and len(numero) == 9):
        return await responder_error(update, "El número debe tener 9 dígitos y empezar con 9.")

    costo = await preparar_consulta(update, "telpcel", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text(f"📡 Buscando titular de línea <code>{numero}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/telp/cel/{numero}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/telp/cel/{numero}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "No se encontraron resultados."))

        res = data.get("data", {})
        titulares = res.get("titulares", [])
        if not titulares:
            return await editar_error(mensaje, f"Sin titulares para {numero}")

        saldo_restante = await cobrar_creditos(user_id, "telpcel", usuarios)

        texto = f"{titulo_sistema('TITULAR CELULAR', '📱')}\n\n"
        texto += f"📊 <b>ENCONTRADOS:</b> <code>{res.get('titulares_encontrados', len(titulares))}</code>\n{SEPARADOR}\n"
        for t in titulares:
            texto += (
                f"👤 <b>TITULAR:</b> <code>{error_html(t.get('titular'))}</code>\n"
                f"🪪 <b>DNI/RUC:</b> <code>{error_html(t.get('dni_ruc'))}</code>\n"
                f"📡 <b>OPERADOR:</b> <code>{error_html(t.get('operador'))}</code>\n"
                f"🏢 <b>EMPRESA:</b> <code>{error_html(t.get('empresa'))}</code>\n"
                f"💳 <b>PLAN:</b> <code>{error_html(t.get('plan'))}</code>\n"
                f"📧 <b>CORREO:</b> <code>{error_html(t.get('correo'))}</code>\n"
                f"📱 <b>NÚMERO:</b> <code>{error_html(t.get('telefono'))}</code>\n"
                f"📅 <b>PERIODO:</b> <code>{error_html(t.get('periodo'))}</code>\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error telpcel")
        await editar_error(mensaje, f"Error telpcel: {str(e)[:300]}")


async def telp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1:
        return await responder_error(update, "Uso: /telp DNI (8 dígitos)")
    dni_num = context.args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "telp", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text(f"🔎 Consultando líneas telefónicas de <code>{dni_num}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/telp/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/telp/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "Sin líneas registradas."))

        res = data.get("data", {})
        lineas = res.get("lineas", [])
        if not lineas:
            return await editar_error(mensaje, "Sin líneas registradas para ese DNI.")

        saldo_restante = await cobrar_creditos(user_id, "telp", usuarios)

        texto = f"{titulo_sistema('LÍNEAS ASOCIADAS', '📡')}\n\n"
        texto += f"🪪 <b>DNI:</b> <code>{dni_num}</code>\n"
        texto += f"📊 <b>TOTAL:</b> <code>{res.get('lineas_encontradas', len(lineas))}</code>\n{SEPARADOR}\n"
        for l in lineas:
            texto += (
                f"📱 <b>NÚMERO:</b> <code>{error_html(l.get('telefono'))}</code>\n"
                f"🏢 <b>OPERADOR:</b> <code>{error_html(l.get('operador'))}</code>\n"
                f"🏭 <b>EMPRESA:</b> <code>{error_html(l.get('empresa'))}</code>\n"
                f"📅 <b>PERIODO:</b> <code>{error_html(l.get('periodo'))}</code>\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error telp")
        await editar_error(mensaje, f"Error telp: {str(e)[:300]}")


async def agv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    args = context.args or []
    if len(args) != 1:
        return await responder_error(update, "Uso: /agv DNI (8 dígitos)")
    dni_num = args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "agv", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text("🔎 Consultando AGV...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/agv/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/agv/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "No se encontró data AGV."))

        res = data.get("data", {})
        saldo_restante = await cobrar_creditos(user_id, "agv", usuarios)

        texto = (
            f"{titulo_sistema('CONSULTA AGV', '🛰️')}\n\n"
            f"🪪 <b>DNI:</b> <code>{error_html(res.get('dni', dni_num))}</code>\n"
            f"👤 <b>NOMBRE:</b> <code>{error_html(res.get('nombres'))} {error_html(res.get('apellidos'))}</code>\n"
            f"⚧️ <b>GÉNERO:</b> <code>{error_html(res.get('genero'))}</code>\n"
            f"🎂 <b>EDAD:</b> <code>{error_html(res.get('edad'))}</code>\n\n"
            f"{SEPARADOR}\n"
            f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n"
            f"💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        )
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

        if res.get("images"):
            try:
                raw = extraer_data_uri(res["images"][0].get("data_uri"))
                await update.message.reply_photo(photo=BytesIO(raw), caption="📸 Foto AGV")
            except Exception:
                pass
    except Exception as e:
        logger.exception("Error agv")
        await editar_error(mensaje, f"Error AGV: {str(e)[:300]}")


async def den(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    args = context.args or []
    if len(args) != 1:
        return await responder_error(update, "Uso: /den DNI (8 dígitos)")
    dni_num = args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "denuncia", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text("🔎 Buscando historial de denuncias...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/den/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/den/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "Sin denuncias."))

        res = data.get("data", {})
        denuncias_list = res.get("denuncias", [])
        if not denuncias_list:
            return await editar_error(mensaje, "Sin denuncias registradas.")

        saldo_restante = await cobrar_creditos(user_id, "denuncia", usuarios)

        texto = f"{titulo_sistema('HISTORIAL DENUNCIAS', '🚨')}\n\n"
        texto += f"🪪 <b>DNI:</b> <code>{error_html(res.get('consulta', dni_num))}</code>\n"
        texto += f"📊 <b>TOTAL:</b> <code>{res.get('cantidad_denuncias', len(denuncias_list))}</code>\n{SEPARADOR}\n"
        for d in denuncias_list[:5]:
            texto += (
                f"📌 <b>TIPO:</b> <code>{error_html(d.get('tipo'))}</code>\n"
                f"🏢 <b>COMISARÍA:</b> <code>{error_html(d.get('comisaria'))}</code>\n"
                f"🔢 <b>ORDEN:</b> <code>{error_html(d.get('n_orden'))}</code>\n"
                f"📅 <b>HECHO:</b> <code>{error_html(d.get('f_hecho'))}</code>\n"
                f"📝 <b>REGISTRO:</b> <code>{error_html(d.get('f_registro'))}</code>\n"
                f"⚖️ <b>CONDICIÓN:</b> <code>{error_html(d.get('condicion'))}</code>\n"
                f"📄 <b>RESUMEN:</b> {error_html(d.get('resumen'))}\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error den")
        await editar_error(mensaje, f"Error den: {str(e)[:300]}")


async def denuncias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    args = context.args or []
    if len(args) != 1:
        return await responder_error(update, "Uso: /denuncias DNI (8 dígitos)")
    dni_num = args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "denuncias", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text("📂 Descargando archivos de denuncias (PDF)...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/denuncias/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/denuncias/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "No se encontraron documentos."))

        res = data.get("data", {})
        archivos = res.get("denuncias", [])
        if not archivos:
            return await editar_error(mensaje, "No se encontraron documentos PDF.")

        saldo_restante = await cobrar_creditos(user_id, "denuncias", usuarios)

        await mensaje.edit_text(f"✅ Se encontraron {len(archivos)} documentos. Enviando PDFs...", parse_mode="HTML")

        for doc in archivos:
            try:
                raw = extraer_data_uri(doc.get("data_uri"))
                archivo = BytesIO(raw)
                archivo.name = doc.get("nombre", f"DENUNCIAS-{dni_num}.pdf")
                caption = (
                    f"🚨 <b>TIPO:</b> {error_html(doc.get('tipo'))}\n"
                    f"🏢 <b>COMISARÍA:</b> {error_html(doc.get('comisaria'))}\n"
                    f"📅 <b>HECHO:</b> {error_html(doc.get('f_hecho'))}"
                )
                await update.message.reply_document(document=archivo, caption=caption, parse_mode="HTML")
            except Exception as e_doc:
                logger.warning(f"Error enviando PDF {doc.get('nombre')}: {e_doc}")
                continue

        await update.message.reply_text(f"💎 Costo: {costo} crd | 💳 Saldo: {saldo_restante} crd", parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error denuncias")
        await editar_error(mensaje, f"Error denuncias: {str(e)[:300]}")


async def hsoat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1:
        return await responder_error(update, "Uso: /hsoat PLACA (ej: D5G960)")
    placa = context.args[0].strip().upper()
    if not re.match(r"^[A-Z0-9]{6,7}$", placa):
        return await responder_error(update, "Placa inválida. Debe tener 6-7 caracteres alfanuméricos.")

    costo = await preparar_consulta(update, "hsoat", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text(f"🚘 Consultando SOAT de <code>{placa}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/hsoat/{placa}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/hsoat/{placa}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "Placa no encontrada en SOAT."))

        res = data.get("data", {})
        hist = res.get("historial", [])
        if not hist:
            return await editar_error(mensaje, "Sin historial SOAT para esa placa.")

        saldo_restante = await cobrar_creditos(user_id, "hsoat", usuarios)

        texto = f"{titulo_sistema('HISTORIAL SOAT', '🚗')}\n\n"
        texto += f"🔢 <b>PLACA:</b> <code>{error_html(res.get('placa', placa))}</code>\n"
        texto += f"📊 <b>REGISTROS:</b> <code>{res.get('cantidad_registros', len(hist))}</code>\n{SEPARADOR}\n"
        for h in hist:
            texto += (
                f"🏢 <b>CIA:</b> <code>{error_html(h.get('compania'))}</code>\n"
                f"✅ <b>ESTADO:</b> <code>{error_html(h.get('estado'))}</code>\n"
                f"📄 <b>TIPO:</b> <code>{error_html(h.get('tipo_certificado'))}</code>\n"
                f"🚙 <b>USO:</b> <code>{error_html(h.get('uso'))}</code>\n"
                f"📅 <b>INICIO:</b> <code>{error_html(h.get('fecha_inicio'))}</code>\n"
                f"📅 <b>FIN:</b> <code>{error_html(h.get('fecha_fin'))}</code>\n"
                f"📄 <b>PÓLIZA:</b> <code>{error_html(h.get('poliza'))}</code>\n"
                f"👮 <b>CONTROL:</b> <code>{error_html(h.get('control_policial'))}</code>\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error hsoat")
        await editar_error(mensaje, f"Error hsoat: {str(e)[:300]}")


async def suel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if not context.args or len(context.args) != 1:
        return await responder_error(update, "Uso: /suel DNI")
    dni_num = context.args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "suel", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text("💰 Consultando ingresos y sueldos...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/suel/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/suel/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "Sin registros laborales."))

        res = data.get("data", {})
        sueldos = res.get("sueldos", [])
        if not sueldos:
            return await editar_error(mensaje, "Sin registros laborales encontrados.")

        saldo_restante = await cobrar_creditos(user_id, "suel", usuarios)

        texto = f"{titulo_sistema('REPORTE LABORAL', '💼')}\n\n"
        texto += f"🪪 <b>DNI:</b> <code>{error_html(res.get('consulta', dni_num))}</code>\n"
        texto += f"📊 <b>REGISTROS:</b> <code>{res.get('total_registros', len(sueldos))}</code>\n{SEPARADOR}\n"
        for s in sueldos:
            texto += (
                f"🏢 <b>EMPRESA:</b> <code>{error_html(s.get('empresa'))}</code>\n"
                f"🔢 <b>RUC:</b> <code>{error_html(s.get('ruc'))}</code>\n"
                f"📅 <b>PERIODO:</b> <code>{error_html(s.get('periodo'))}</code>\n"
                f"💰 <b>MONTO:</b> <code>{error_html(s.get('sueldo'))}</code>\n"
                f"👔 <b>ESTADO:</b> <code>{error_html(s.get('situacion'))}</code>\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error suel")
        await editar_error(mensaje, f"Error suel: {str(e)[:300]}")


async def denpla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if not context.args or len(context.args) != 1:
        return await responder_error(update, "Uso: /denpla PLACA (ej: D4G860)")
    placa = context.args[0].strip().upper()
    if not re.match(r"^[A-Z0-9]{6,7}$", placa):
        return await responder_error(update, "Placa inválida. 6-7 alfanuméricos.")

    costo = await preparar_consulta(update, "denpla", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text(f"🚨 Consultando denuncias vehiculares de <code>{placa}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/denpla/{placa}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/denpla/{placa}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "Sin denuncias para esta placa."))

        res = data.get("data", {})
        denuncias_list = res.get("denuncias", [])
        if not denuncias_list:
            return await editar_error(mensaje, "Sin denuncias para esa placa.")

        saldo_restante = await cobrar_creditos(user_id, "denpla", usuarios)

        texto = f"{titulo_sistema('DENUNCIAS PLACA', '🚨')}\n\n"
        texto += f"🔢 <b>PLACA:</b> <code>{error_html(res.get('placa', placa))}</code>\n"
        texto += f"📊 <b>TOTAL:</b> <code>{res.get('cantidad_denuncias', len(denuncias_list))}</code>\n{SEPARADOR}\n"
        for d in denuncias_list:
            texto += (
                f"📌 <b>NÚMERO:</b> <code>{error_html(d.get('numero'))}</code>\n"
                f"🏷️ <b>TIPO:</b> <code>{error_html(d.get('tipo'))}</code>\n"
                f"🏢 <b>COMISARÍA:</b> <code>{error_html(d.get('comisaria'))}</code>\n"
                f"🔢 <b>ORDEN:</b> <code>{error_html(d.get('n_orden'))}</code>\n"
                f"📅 <b>HECHO:</b> <code>{error_html(d.get('f_hecho'))}</code>\n"
                f"📝 <b>REGISTRO:</b> <code>{error_html(d.get('f_registro'))}</code>\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

        # Enviar PDFs si vienen en data_uri
        for d in denuncias_list:
            if d.get("data_uri"):
                try:
                    raw = extraer_data_uri(d.get("data_uri"))
                    archivo = BytesIO(raw)
                    archivo.name = d.get("nombre", f"DENUNCIA-{placa}.pdf")
                    await update.message.reply_document(document=archivo, caption=f"🚨 {d.get('tipo')} - {d.get('comisaria')}")
                except Exception:
                    continue
    except Exception as e:
        logger.exception("Error denpla")
        await editar_error(mensaje, f"Error denpla: {str(e)[:300]}")


async def facial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)

    if not message.photo:
        return await message.reply_text(
            f"{titulo_sistema('SISTEMA FACIAL', '🧬')}\n\n"
            "📷 Envía una foto con <code>/facial</code> en el caption.",
            parse_mode="HTML", reply_markup=BTN_VOLVER
        )

    costo = await preparar_consulta(update, "facial", usuarios, user_id)
    if costo is None:
        return

    estado = await message.reply_text("🛰️ Escaneando rostro con CODART FACIAL TOP...", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        imagen = bytes(await tg_file.download_as_bytearray())

        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/facial/top
        data = await consultar_api_post_facial(imagen)

        if data.get("error"):
            return await editar_error(estado, data["error"])
        if not data.get("success"):
            return await editar_error(estado, data.get("message", "No se encontraron coincidencias faciales."))

        info = data.get("data", {})
        rostros = info.get("rostros", [])
        if not rostros:
            return await editar_error(estado, "No se detectaron rostros o coincidencias.")

        saldo_restante = await cobrar_creditos(user_id, "facial", usuarios)

        texto = f"{titulo_sistema('MATCH FACIAL', '🧬')}\n\n"
        texto += f"📊 <b>TIPO:</b> <code>{error_html(info.get('tipo_resultado'))}</code>\n"
        texto += f"👥 <b>ROSTROS:</b> <code>{info.get('total_rostros', len(rostros))}</code>\n{SEPARADOR}\n"
        for r in rostros:
            texto += f"🔎 <b>ROSTRO #{r.get('numero_rostro', 1)} - Coincidencias: {r.get('coincidencias_mostradas', 0)}</b>\n"
            for c in r.get("coincidencias", []):
                texto += (
                    f"👤 <b>NOMBRE:</b> <code>{error_html(c.get('nombre'))}</code>\n"
                    f"🪪 <b>DNI:</b> <code>{error_html(c.get('dni'))}</code>\n"
                    f"🎯 <b>SIMILITUD:</b> <code>{error_html(c.get('porcentaje'))}%</code>\n"
                    f"{SEPARADOR_CORTO}\n"
                )
            texto += f"{SEPARADOR}\n"
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await estado.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error facial")
        await editar_error(estado, f"Error facial: {str(e)[:300]}")


async def revtec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1:
        return await responder_error(update, "Uso: /revtec PLACA (ej: ABC123)")
    placa = context.args[0].strip().upper()
    if not re.match(r"^[A-Z0-9]{6,7}$", placa):
        return await responder_error(update, "Placa inválida. 6-7 alfanuméricos.")

    costo = await preparar_consulta(update, "revtec", usuarios, user_id)
    if costo is None:
        return

    mensaje = await update.message.reply_text(f"🔍 Consultando Revisiones Técnicas de <code>{placa}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/revtec/{placa}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/revtec/{placa}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"):
            return await editar_error(mensaje, data.get("message", "Sin historial de revisiones."))

        res = data.get("data", {})
        regs = res.get("registros", [])
        if not regs:
            return await editar_error(mensaje, "Sin historial de revisiones técnicas.")

        saldo_restante = await cobrar_creditos(user_id, "revtec", usuarios)

        texto = f"{titulo_sistema('REVISIÓN TÉCNICA', '🛠️')}\n\n"
        texto += f"🔢 <b>PLACA:</b> <code>{error_html(res.get('placa', placa))}</code>\n"
        texto += f"📊 <b>TOTAL:</b> <code>{res.get('total_resultados', len(regs))}</code>\n{SEPARADOR}\n"
        for r in regs:
            texto += (
                f"✅ <b>ESTADO:</b> <code>{error_html(r.get('estado'))}</code>\n"
                f"🏢 <b>ENTIDAD:</b> <code>{error_html(r.get('entidad'))}</code>\n"
                f"📍 <b>DIRECCIÓN:</b> <code>{error_html(r.get('direccion'))}</code>\n"
                f"📅 <b>INSPECCIÓN:</b> <code>{error_html(r.get('fecha_inspeccion'))}</code>\n"
                f"📅 <b>VENCE:</b> <code>{error_html(r.get('fecha_vencimiento'))}</code>\n"
                f"📜 <b>CERT:</b> <code>{error_html(r.get('certificado'))}</code>\n"
                f"📊 <b>RESULTADO:</b> <code>{error_html(r.get('resultado'))}</code>\n"
                f"🚛 <b>SERVICIO:</b> <code>{error_html(r.get('servicio'))}</code>\n"
                f"📝 <b>OBS:</b> <code>{error_html(r.get('observaciones'))}</code>\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error revtec")
        await editar_error(mensaje, f"Error revtec: {str(e)[:300]}")


async def dir_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if not context.args or len(context.args) != 1:
        return await responder_error(update, "Uso: /dir DNI (8 dígitos)")
    dni_num = context.args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "dir", usuarios, user_id)
    if costo is None: return

    mensaje = await update.message.reply_text(f"🏠 Buscando historial de direcciones de <code>{dni_num}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/dir/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/dir/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"): 
            return await editar_error(mensaje, data.get("message", "Sin direcciones registradas."))

        res = data.get("data", {})
        direcciones = res.get("direcciones", [])
        if not direcciones:
            return await editar_error(mensaje, "Sin direcciones registradas.")

        saldo_restante = await cobrar_creditos(user_id, "dir", usuarios)

        texto = f"{titulo_sistema('HISTORIAL DIRECCIONES', '📍')}\n\n"
        texto += f"🪪 <b>DNI:</b> <code>{error_html(res.get('consulta', dni_num))}</code>\n"
        texto += f"📊 <b>TOTAL:</b> <code>{res.get('total_registros', len(direcciones))}</code>\n{SEPARADOR}\n"
        for d in direcciones:
            texto += (
                f"🏠 <b>DIRECCIÓN:</b> <code>{error_html(d.get('direccion'))}</code>\n"
                f"📍 <b>UBICACIÓN:</b> <code>{error_html(d.get('ubicacion'))}</code>\n"
                f"📡 <b>FUENTE:</b> <code>{error_html(d.get('fuente'))}</code>\n"
                f"🪪 <b>DNI:</b> <code>{error_html(d.get('dni'))}</code>\n"
                f"{SEPARADOR}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
    except Exception as e:
        logger.exception("Error dir")
        await editar_error(mensaje, f"Error dir: {str(e)[:300]}")

async def dnivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1: 
        return await responder_error(update, "Uso: /dnivel DNI (8 dígitos)")
    dni_num = context.args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "dnivel", usuarios, user_id)
    if costo is None: return

    mensaje = await update.message.reply_text(f"🔎 Consultando DNI-Nivel <code>{dni_num}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/dnivel/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/dnivel/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"): 
            return await editar_error(mensaje, data.get("message", "No encontrado en DNIVEL."))

        res = data.get("data", {})
        saldo_restante = await cobrar_creditos(user_id, "dnivel", usuarios)

        texto = (
            f"{titulo_sistema('DNI NIVEL', '📊')}\n\n"
            f"🪪 <b>DNI:</b> <code>{error_html(res.get('dni', dni_num))}</code>\n"
            f"👤 <b>NOMBRE:</b> <code>{error_html(res.get('nombres'))} {error_html(res.get('apellidos'))}</code>\n"
            f"🎂 <b>EDAD:</b> <code>{error_html(res.get('edad'))}</code>\n"
            f"⚧️ <b>GÉNERO:</b> <code>{error_html(res.get('genero'))}</code>\n\n"
            f"{SEPARADOR}\n"
            f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n"
            f"💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        )
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

        if res.get("images"):
            for img in res["images"]:
                try:
                    raw = extraer_data_uri(img.get("data_uri"))
                    await update.message.reply_photo(photo=BytesIO(raw), caption="📸 DNIVEL")
                except Exception:
                    continue
    except Exception as e:
        logger.exception("Error dnivel")
        await editar_error(mensaje, f"Error dnivel: {str(e)[:300]}")


async def rqh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    if len(context.args) != 1: 
        return await responder_error(update, "Uso: /rqh DNI (8 dígitos)")
    dni_num = context.args[0].strip()
    if not (dni_num.isdigit() and len(dni_num) == 8):
        return await responder_error(update, "DNI debe tener 8 dígitos.")

    costo = await preparar_consulta(update, "rqh", usuarios, user_id)
    if costo is None: return

    mensaje = await update.message.reply_text(f"🚨 Consultando Requisitorias de <code>{dni_num}</code>...", parse_mode="HTML")

    try:
        # API: https://api-codart.cgrt.org/api/v1/consultas/fd/rqh/{dni}
        data = await consultar_api_get(f"{BASE_URL}/api/v1/consultas/fd/rqh/{dni_num}")
        if data.get("error"):
            return await editar_error(mensaje, data["error"])
        if not data.get("success"): 
            return await editar_error(mensaje, data.get("message", "Sin requisitorias registradas."))

        res = data.get("data", {})
        datos = res.get("datos_personales", {})
        resumen = res.get("resumen_requisitorias", {})
        detalles = res.get("detalle", [])

        saldo_restante = await cobrar_creditos(user_id, "rqh", usuarios)

        texto = (
            f"{titulo_sistema('REQUISITORIAS', '👮')}\n\n"
            f"🪪 <b>DNI:</b> <code>{error_html(datos.get('dni', dni_num))}</code>\n"
            f"👤 <b>NOMBRE:</b> <code>{error_html(datos.get('nombres'))}</code>\n"
            f"⚧️ <b>SEXO:</b> <code>{error_html(datos.get('sexo'))}</code>\n"
            f"🎂 <b>EDAD:</b> <code>{error_html(datos.get('edad'))}</code>\n"
            f"📅 <b>F.NAC:</b> <code>{error_html(datos.get('fecha_nacimiento'))}</code>\n"
            f"📍 <b>DISTRITO:</b> <code>{error_html(datos.get('distrito'))}</code>\n"
            f"🏠 <b>DIRECCIÓN:</b> <code>{error_html(datos.get('direccion'))}</code>\n\n"
            f"📊 <b>RESUMEN:</b>\n"
            f"   • Total: <code>{resumen.get('total', 0)}</code>\n"
            f"   • Activas: <code>{resumen.get('activas', 0)}</code>\n"
            f"   • Inactivas: <code>{resumen.get('inactivas', 0)}</code>\n"
            f"{SEPARADOR}\n"
        )
        for d in detalles:
            texto += (
                f"🔸 <b>ESTADO:</b> <code>{error_html(d.get('estado'))}</code> | <b>TIPO:</b> <code>{error_html(d.get('tipo'))}</code>\n"
                f"⚖️ <b>DELITO:</b> <code>{error_html(d.get('delito'))}</code>\n"
                f"📄 <b>MOTIVO:</b> <code>{error_html(d.get('motivo'))}</code>\n"
                f"🔢 <b>EXP:</b> <code>{error_html(d.get('exp'))}</code> | <b>NRQ:</b> <code>{error_html(d.get('nrq'))}</code>\n"
                f"🏢 <b>JUZGADO:</b> <code>{error_html(d.get('dependencia'))}</code>\n"
                f"📅 <b>INICIO:</b> <code>{error_html(d.get('inicio'))}</code> | <b>VENCE:</b> <code>{error_html(d.get('vence'))}</code>\n"
                f"{SEPARADOR_CORTO}\n"
            )
        texto += f"💎 <b>COSTO:</b> <code>{costo}</code> crd\n💳 <b>SALDO:</b> <code>{saldo_restante}</code> crd"
        await mensaje.edit_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

        for doc in res.get("documentos", []):
            try:
                raw = extraer_data_uri(doc.get("data_uri"))
                archivo = BytesIO(raw)
                archivo.name = doc.get("nombre", f"REQUISITORIA-{dni_num}.pdf")
                await update.message.reply_document(document=archivo, caption=f"🚨 {doc.get('nombre')}")
            except Exception:
                continue
    except Exception as e:
        logger.exception("Error rqh")
        await editar_error(mensaje, f"Error rqh: {str(e)[:300]}")
#============================================================
#COMANDOS GENERALES
#============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        f"{titulo_sistema('SPECTER PERÚ', '⚜️')}\n\n"
        "🚀 <b>PLATAFORMA DE CONSULTAS</b>\n\n"
        f"🏷️ Nombre: <b>{html.escape(BOT_NAME)}</b>\n"
        f"👤 Usuario: <b>{html.escape(BOT_USER)}</b>\n"
        "🛰️ Estado: <b>ONLINE</b>\n\n"
        f"{SEPARADOR}\n"
        "📚 <b>COMANDOS PRINCIPALES</b>\n\n"
        "📖 /cmds ➜ Ver servicios\n"
        "👤 /me ➜ Ver perfil\n"
        "💳 /buy ➜ Planes\n"
        "💰 /saldo ➜ Tu crédito\n\n"
        f"{SEPARADOR}\n"
        "⚡ Sistema actualizado CODART X V1"
    )
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

async def cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        f"{titulo_sistema('MENÚ DE SERVICIOS', '🛰️')}\n\n"
        "💎 Selecciona una categoría abajo para ver los costos y comandos disponibles."
    )
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=menu_teclado())

async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuarios = cargar_usuarios()
    user_id, usuario = obtener_usuario(update, usuarios)
    guardar_usuarios(usuarios)
    username = f"@{usuario.get('username')}" if usuario.get("username") else "Sin username"
    texto = (
        f"{titulo_sistema('PERFIL DE USUARIO', '👤')}\n\n"
        f"👤 Nombre: <code>{error_html(usuario.get('nombre', 'Usuario'))}</code>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📱 Celular: <code>{error_html(usuario.get('celular'))}</code>\n"
        f"💳 Créditos: <code>{usuario.get('creditos', 0)}</code>\n"
        f"📊 Consultas: <code>{usuario.get('consultas', 0)}</code>\n"
        f"⭐ Plan: <code>{error_html(usuario.get('plan', 'FREE'))}</code>\n"
        f"{SEPARADOR}\n⚜️ <b>SPECTER PERÚ</b>"
    )
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        f"{titulo_sistema('PLANES PREMIUM', '💎')}\n\n"
        "💰 <b>CRÉDITOS</b>\n"
        "🥉 100 crd ➜ S/ 10\n"
        "🥈 200 crd ➜ S/ 20\n"
        "🥇 400 crd ➜ S/ 30\n\n"
        "💳 <b>PAGOS:</b> Yape • Plin\n"
        "👤 <b>ADMIN:</b> @Sthep_18\n\n"
        "⚡ Usa /pagar para recarga automática."
    )
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)

async def staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        f"{titulo_sistema('STAFF OFICIAL', '👑')}\n\n"
        "🛡️ <b>ADMINISTRADOR PRINCIPAL</b>\n"
        "👤 @Sthep_18\n\n"
        "🛠️ Soporte técnico y ventas."
    )
    await update.message.reply_text(texto, parse_mode="HTML", reply_markup=BTN_VOLVER)
#============================================================
#ADMINISTRACIÓN
#============================================================
async def addcreditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_ID:
        return

    args = context.args or []
    if len(args) != 2:
        await update.message.reply_text("Uso: /addcreditos <user_id> <cantidad>")
        return

    target_id = args[0]
    try:
        cantidad = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ La cantidad debe ser un número entero.")
        return

    usuarios = cargar_usuarios() or {}
    if target_id not in usuarios:
        await update.message.reply_text(f"❌ No existe el usuario {target_id}.")
        return

    usuarios[target_id]["creditos"] = int(usuarios[target_id].get("creditos", 0)) + cantidad
    guardar_usuarios(usuarios)
    await update.message.reply_text(f"✅ Agregados {cantidad} a {target_id}")


async def quitarcrd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in ADMIN_ID:
        return

    args = context.args or []
    if len(args) != 2:
        await update.message.reply_text("Uso: /quitarcrd <user_id> <cantidad>")
        return

    target_id = args[0]
    try:
        cantidad = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ La cantidad debe ser un número entero.")
        return

    usuarios = cargar_usuarios() or {}
    if target_id not in usuarios:
        await update.message.reply_text(f"❌ No existe el usuario {target_id}.")
        return

    usuarios[target_id]["creditos"] = max(0, int(usuarios[target_id].get("creditos", 0)) - cantidad)
    guardar_usuarios(usuarios)
    await update.message.reply_text(f"✅ Quitados {cantidad} a {target_id}")


#============================================================
#MENÚ INTERACTIVO CALLBACKS
#============================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    if query.data == "volver_cmds":
        return await query.edit_message_text(
            f"{titulo_sistema('MENÚ DE SERVICIOS', '🛰️')}\n\nSelecciona una categoría:",
            parse_mode="HTML",
            reply_markup=menu_teclado(),
        )

    textos = {
        "cmd_reniec": (
            f"{titulo_sistema('RENIEC', '🪪')}\n\n"
            f"/dni ➜ {PRECIOS['dni']} crd\n"
            f"/dnit ➜ {PRECIOS['dnit']} crd\n"
            f"/agv ➜ {PRECIOS['agv']} crd\n"
            f"/dnivel ➜ {PRECIOS['dnivel']} crd"
        ),
        "cmd_ruc": (
            f"{titulo_sistema('RUC', '🏢')}\n\n"
            f"/ruc ➜ {PRECIOS['ruc']} crd"
        ),
        "cmd_vehiculos": (
            f"{titulo_sistema('VEHÍCULOS', '🚘')}\n\n"
            f"/hsoat ➜ {PRECIOS['hsoat']} crd\n"
            f"/denpla ➜ {PRECIOS['denpla']} crd\n"
            f"/revtec ➜ {PRECIOS['revtec']} crd"
        ),
        "cmd_telefono": (
            f"{titulo_sistema('TELEFONÍA', '📱')}\n\n"
            f"/telp ➜ {PRECIOS['telp']} crd\n"
            f"/telpcel ➜ {PRECIOS['telpcel']} crd"
        ),
        "cmd_denuncia": (
            f"{titulo_sistema('DENUNCIAS', '⚖️')}\n\n"
            f"/den ➜ {PRECIOS['denuncia']} crd\n"
            f"/denuncias ➜ {PRECIOS['denuncias']} crd"
        ),
        "cmd_sueldo": (
            f"{titulo_sistema('SUELDOS', '💰')}\n\n"
            f"/suel ➜ {PRECIOS['suel']} crd"
        ),
        "cmd_facial": (
            f"{titulo_sistema('FACIAL', '🧬')}\n\n"
            f"/facial ➜ {PRECIOS['facial']} crd"
        ),
        "cmd_otros": (
            f"{titulo_sistema('OTROS', '🔍')}\n\n"
            f"/dir ➜ {PRECIOS['dir']} crd\n"
            f"/rqh ➜ {PRECIOS['rqh']} crd"
        ),
        "cmd_buy": "Usa /buy para información de pagos."
    }

    if query.data in textos:
        await query.edit_message_text(textos[query.data], parse_mode="HTML", reply_markup=BTN_VOLVER)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Falta BOT_TOKEN")

    keep_alive()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cmds", cmds))
    application.add_handler(CommandHandler("me", me))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("staff", staff))
    application.add_handler(CommandHandler("saldo", saldo))
    application.add_handler(CommandHandler("micelular", micelular))
    application.add_handler(CommandHandler("pagar", pagar))
    application.add_handler(CommandHandler("addcreditos", addcreditos))
    application.add_handler(CommandHandler("quitarcrd", quitarcrd))

    # Consultas - TODAS LAS APIS IMPLEMENTADAS
    application.add_handler(CommandHandler("dni", dni))
    application.add_handler(CommandHandler("dnit", dnit))
    application.add_handler(CommandHandler("agv", agv))
    application.add_handler(CommandHandler("den", den))
    application.add_handler(CommandHandler("denuncias", denuncias))
    application.add_handler(CommandHandler("telp", telp))
    application.add_handler(CommandHandler("telpcel", telpcel))
    application.add_handler(CommandHandler("hsoat", hsoat))
    application.add_handler(CommandHandler("suel", suel))
    application.add_handler(CommandHandler("denpla", denpla))
    application.add_handler(CommandHandler("revtec", revtec))
    application.add_handler(CommandHandler("dir", dir_cmd))
    application.add_handler(CommandHandler("dnivel", dnivel))
    application.add_handler(CommandHandler("rqh", rqh))
    application.add_handler(CommandHandler("facial", facial))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/facial(?:\s|$)"), facial))

    logger.info("🚀 SPECTER PERÚ ONLINE - CODART X V1 COMPLETO")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
