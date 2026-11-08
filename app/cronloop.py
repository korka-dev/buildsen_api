"""Tâches planifiées en local (remplace le planificateur du serveur de production) : python -m app.cronloop

Toutes les 10 minutes : alertes différées des formules gratuites. Une fois par jour : nettoyage du journal WhatsApp.
En production, appeler à la place GET /api/cron/alertes et GET /api/cron/nettoyage avec l'en-tête Authorization: Bearer <CRON_SECRET>.
"""

import logging
import time

import httpx

from .config import settings

log = logging.getLogger("buildsen.cron")
BASE = "http://backend:8000/api/cron"


def call(path: str) -> None:
    try:
        r = httpx.get(f"{BASE}/{path}", headers={"Authorization": f"Bearer {settings.cron_secret}"}, timeout=60)
        log.info("cron %s -> %s %s", path, r.status_code, r.text[:80])
    except httpx.HTTPError as e:
        log.warning("cron %s indisponible : %s", path, e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tick = 0
    while True:
        call("alertes")
        if tick % 144 == 0:  # 144 x 10 minutes = 24 heures
            call("nettoyage")
        tick += 1
        time.sleep(600)


if __name__ == "__main__":
    main()
