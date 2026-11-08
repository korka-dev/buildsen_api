"""Point d'entrée de l'API BUILDSEN (FastAPI).

Le navigateur n'appelle jamais l'API directement : le frontend Next.js relaie /api/* vers ce service.
Les cookies de session restent donc sur le domaine du site, et l'API n'a pas besoin d'être ouverte en CORS.
"""

import ipaddress
import logging
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import errors
from .config import settings
from .routers import admin, auth, client, cron, devstorage, files, me, messages, payments, pro, public, voice, wa

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

if settings.production and (problems := settings.production_problems()):
    raise RuntimeError("Configuration de production refusée :\n- " + "\n- ".join(problems))

app = FastAPI(
    title="BUILDSEN API",
    version="2.0.0",
    description="Mise en relation des particuliers de Dakar avec des professionnels du bâtiment vérifiés.",
    docs_url=None if settings.production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.production else "/api/openapi.json",
)
errors.install(app)

# Requêtes qui modifient des données sans cookie de navigateur à protéger (jeton secret, ou appel planifié)
_CSRF_EXEMPT = {"/api/auth/verify-email", "/api/cron/alertes", "/api/wa/webhook", "/api/payments/webhook/paydunya", "/api/payments/webhook/wave", "/api/payments/webhook/om"}


def _origin_host(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urlsplit(value).netloc or None
    except ValueError:
        return None


def _local_network(host: str | None) -> bool:
    """Développement : le site ouvert depuis un téléphone du même réseau (adresse privée 192.168.x.x, 10.x.x.x, 172.16-31.x.x ou *.local)."""
    name = (host or "").rsplit(":", 1)[0]
    if name.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(name).is_private
    except ValueError:
        return False


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Protection CSRF : une requête POST/PUT/PATCH/DELETE doit venir du site (en-tête Origin ou Referer autorisé)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") and request.url.path not in _CSRF_EXEMPT:
        internal = settings.internal_api_key and request.headers.get("x-internal-key") == settings.internal_api_key
        host = _origin_host(request.headers.get("origin")) or _origin_host(request.headers.get("referer"))
        allowed = {_origin_host(o) for o in settings.origins}
        if not internal and host not in allowed and not (not settings.production and _local_network(host)):
            return JSONResponse({"error": "Origine refusée."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


for r in (auth.router, me.router, client.router, voice.router, wa.router, devstorage.router, payments.router, pro.router, messages.router, files.router, public.router, admin.router, cron.router):
    app.include_router(r)
