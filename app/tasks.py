"""Travaux exécutés après la réponse (e-mails, alertes), chacun avec sa propre session de base de données.

FastAPI ferme la session de la requête avant les tâches d'arrière-plan : on en ouvre donc une nouvelle ici.
"""

import logging
from collections.abc import Callable

from .db import SessionLocal

log = logging.getLogger("buildsen.tasks")


def with_db(fn: Callable, *args, **kwargs) -> None:
    db = SessionLocal()
    try:
        fn(db, *args, **kwargs)
    except Exception:  # noqa: BLE001 - une tâche d'arrière-plan ne doit jamais faire échouer la requête
        log.exception("Tâche d'arrière-plan en échec : %s", getattr(fn, "__name__", fn))
    finally:
        db.close()
