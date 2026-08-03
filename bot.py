import difflib
import json
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

import truststore
from playwright.sync_api import sync_playwright

import config

# La consola de Windows suele usar cp1252, que no soporta emojis en los mensajes.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Usa el almacén de certificados nativo de Windows (evita fallos de verificación SSL
# cuando el antivirus/proxy local inspecciona HTTPS con su propio certificado raíz).
truststore.inject_into_ssl()

STATE_FILE = Path(__file__).parent / "estado.json"
META_FILE = Path(__file__).parent / "meta.json"

# Cuantos ciclos seguidos tienen que fallar antes de avisar que el bot dejo de funcionar
FALLOS_PARA_ALERTAR = 3

# Telegram corta los mensajes mas largos que 4096 caracteres
MAX_MENSAJE_TELEGRAM = 3500

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Lineas que cambian solas segun el idioma/ubicacion detectada del visitante
# (ej. el link de cookies), sin que haya cambiado nada real en la pagina.
LINEAS_RUIDO = {"Manage my cookies", "Preferencias de cookies"}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def enviar_telegram(mensaje: str) -> None:
    if "PON_AQUI" in config.TELEGRAM_BOT_TOKEN or "PON_AQUI" in config.TELEGRAM_CHAT_ID:
        log("Telegram no configurado todavia (revisa config.py). Aviso solo por consola:")
        log(mensaje)
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": mensaje,
        "disable_web_page_preview": "true",
    }).encode()

    try:
        with urllib.request.urlopen(url, data=data, timeout=15) as resp:
            resp.read()
        log("Aviso enviado a Telegram.")
    except Exception as exc:
        log(f"No se pudo enviar el aviso a Telegram: {exc}")


def obtener_datos_evento() -> dict:
    """Extrae la tabla de fechas de la gira y el texto visible completo de la página."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(config.EVENT_URL, wait_until="domcontentloaded", timeout=60000)
        # Es una SPA antigua (Rendr); esperamos a que la tabla de fechas exista en el DOM.
        page.wait_for_selector("table tbody tr", timeout=20000)
        page.wait_for_timeout(2000)

        filas = []
        for fila in page.locator("table tbody tr").all():
            celdas = [c.inner_text().strip() for c in fila.locator("td").all()]
            if len(celdas) >= 5:
                filas.append({
                    "fecha": celdas[0],
                    "ciudad": celdas[1],
                    "venue": celdas[2],
                    "preventa_army": celdas[3],
                    "venta_general": celdas[4],
                })

        texto_completo = page.locator("body").inner_text()
        texto_completo = "\n".join(
            linea for linea in texto_completo.splitlines() if linea not in LINEAS_RUIDO
        )
        browser.close()

    return {"fechas": filas, "texto": texto_completo}


def _diff_de_texto(texto_antes: str, texto_ahora: str) -> str:
    diff = difflib.unified_diff(
        texto_antes.splitlines(), texto_ahora.splitlines(), lineterm=""
    )
    lineas = [
        linea for linea in diff
        if linea[:1] in ("+", "-") and not linea.startswith(("+++", "---"))
    ]
    return "\n".join(lineas)


def comparar_y_notificar(anterior: dict, actual: dict) -> None:
    fechas_antes = {f["fecha"]: f for f in anterior.get("fechas", [])}
    fechas_ahora = {f["fecha"]: f for f in actual.get("fechas", [])}

    nuevas = [f for fecha, f in fechas_ahora.items() if fecha not in fechas_antes]
    for f in nuevas:
        enviar_telegram(
            "¡NUEVA FECHA DE BTS EN CHILE!\n\n"
            f"Fecha: {f['fecha']}\n"
            f"Ciudad: {f['ciudad']}\n"
            f"Venue: {f['venue']}\n"
            f"Preventa Army Membership: {f['preventa_army']}\n"
            f"Venta general: {f['venta_general']}\n\n"
            f"{config.EVENT_URL}"
        )

    hubo_cambio_de_fecha_existente = False
    for fecha, f_actual in fechas_ahora.items():
        f_antes = fechas_antes.get(fecha)
        if f_antes and f_antes != f_actual:
            hubo_cambio_de_fecha_existente = True
            cambios = [
                f"- {campo}: '{f_antes[campo]}' -> '{f_actual[campo]}'"
                for campo in f_actual
                if f_antes[campo] != f_actual[campo]
            ]
            enviar_telegram(
                f"Cambio detectado en la funcion del {fecha}:\n"
                + "\n".join(cambios)
                + f"\n\n{config.EVENT_URL}"
            )

    # Red de seguridad: compara el texto visible de TODA la pagina (no solo la tabla)
    # para avisar de cualquier cambio -nueva seccion, aviso agregado, texto editado, etc.-
    # que no haya quedado ya cubierto por los avisos especificos de arriba.
    texto_antes = anterior.get("texto", "")
    texto_ahora = actual.get("texto", "")
    if texto_antes and texto_antes != texto_ahora and not nuevas and not hubo_cambio_de_fecha_existente:
        diff = _diff_de_texto(texto_antes, texto_ahora)
        if len(diff) > MAX_MENSAJE_TELEGRAM:
            diff = diff[:MAX_MENSAJE_TELEGRAM] + "\n... (recortado, revisa la pagina)"
        enviar_telegram(
            "La pagina de BTS cambio. Esto se agrego (+) o se quito (-):\n\n"
            f"{diff}\n\n{config.EVENT_URL}"
        )


def cargar_estado() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def guardar_estado(estado: dict) -> None:
    STATE_FILE.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def cargar_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    return {"fallos_seguidos": 0, "alerta_de_falla_enviada": False}


def guardar_meta(meta: dict) -> None:
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def ciclo() -> bool:
    """Ejecuta un ciclo de revision. Devuelve False si no se pudo leer la pagina."""
    estado_anterior = cargar_estado()
    try:
        estado_actual = obtener_datos_evento()
    except Exception as exc:
        log(f"Error al obtener datos de la pagina: {exc}")
        return False

    if estado_anterior:
        comparar_y_notificar(estado_anterior, estado_actual)
    else:
        log("Primera ejecucion: guardando estado inicial (sin avisos).")
        log(f"Fechas encontradas actualmente: {[f['fecha'] for f in estado_actual['fechas']]}")

    guardar_estado(estado_actual)
    return True


def procesar_resultado(exito: bool, meta: dict) -> dict:
    """Actualiza los contadores de fallos y dispara los avisos de fallo/recuperacion."""
    if exito:
        if meta.get("alerta_de_falla_enviada"):
            enviar_telegram("El bot volvio a leer la pagina de Ticketmaster con normalidad.")
        meta["fallos_seguidos"] = 0
        meta["alerta_de_falla_enviada"] = False
    else:
        meta["fallos_seguidos"] = meta.get("fallos_seguidos", 0) + 1
        if meta["fallos_seguidos"] >= FALLOS_PARA_ALERTAR and not meta.get("alerta_de_falla_enviada"):
            enviar_telegram(
                f"El bot lleva {meta['fallos_seguidos']} intentos seguidos sin poder leer la "
                "pagina de Ticketmaster (puede que haya cambiado la pagina o este "
                f"bloqueando el acceso). Revisa manualmente:\n{config.EVENT_URL}"
            )
            meta["alerta_de_falla_enviada"] = True

    meta["ultima_revision"] = datetime.now().isoformat(timespec="seconds")
    return meta


def main() -> None:
    """Loop continuo: para correr localmente o en un servidor/VPS siempre encendido."""
    log("Bot de monitoreo de BTS World Tour iniciado.")
    log(f"Revisando cada {config.CHECK_INTERVAL_SECONDS} segundos: {config.EVENT_URL}")

    meta = cargar_meta()
    while True:
        meta = procesar_resultado(ciclo(), meta)
        guardar_meta(meta)
        time.sleep(config.CHECK_INTERVAL_SECONDS)


def ejecutar_una_vez() -> None:
    """Un solo ciclo y termina: para ejecutores externos como GitHub Actions."""
    meta = procesar_resultado(ciclo(), cargar_meta())
    guardar_meta(meta)


if __name__ == "__main__":
    if "--once" in sys.argv:
        ejecutar_una_vez()
    else:
        main()
