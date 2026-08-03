import os

# En GitHub Actions estos valores vienen de los "secrets" del repo (ver workflow).
# Para correrlo local, copia este archivo como config.py y completa los valores
# directamente (no hace falta usar variables de entorno en tu PC).

EVENT_URL = os.environ.get(
    "EVENT_URL",
    "https://www.ticketmaster.cl/event/bts-world-tour-arirang-live-2026-scl",
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PON_AQUI_TU_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PON_AQUI_TU_CHAT_ID")

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
