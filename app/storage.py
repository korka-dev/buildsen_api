"""Stockage des fichiers.

Par défaut : disque du serveur (volume Docker, variable STORAGE_DIR), sans service externe : c'est le mode prévu pour un serveur EC2.
Optionnel : Supabase Storage par son API REST si SUPABASE_URL est renseigné.
Dans les deux cas les dossiers sont privés : les fichiers ne sont lisibles que par des liens signés de courte durée,
et l'envoi se fait avec un lien d'envoi signé.
"""

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import httpx

from .config import settings

_TIMEOUT = httpx.Timeout(15.0)

# Stockage sur disque (routers/devstorage.py) : actif tant que Supabase n'est pas configuré, en développement comme en production.
LOCAL_DIR = Path(os.environ.get("STORAGE_DIR") or os.environ.get("DEV_STORAGE_DIR") or "/tmp/buildsen-storage")


def local() -> bool:
    return not settings.supabase_url


def _token(kind: str, bucket: str, path: str, expires: int = 0) -> str:
    key = (settings.cron_secret or settings.internal_api_key or "dev-storage").encode()
    return hmac.new(key, f"{kind}|{bucket}|{path}|{expires}".encode(), hashlib.sha256).hexdigest()[:40]


def check_token(kind: str, bucket: str, path: str, token: str, expires: int = 0) -> bool:
    return (expires == 0 or expires > time.time()) and hmac.compare_digest(token, _token(kind, bucket, path, expires))


def local_file(bucket: str, path: str) -> Path:
    p = (LOCAL_DIR / bucket / path).resolve()
    if LOCAL_DIR.resolve() not in p.parents:  # jamais en dehors du dossier de stockage
        raise ValueError("chemin invalide")
    return p


def _base() -> str:
    return f"{settings.supabase_url}/storage/v1"


def _auth() -> dict[str, str]:
    key = settings.supabase_secret_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def sign_upload(bucket: str, path: str) -> str:
    if local():
        return f"/api/dev-storage/upload/{bucket}/{path}?t={_token('put', bucket, path)}"
    r = httpx.post(f"{_base()}/object/upload/sign/{bucket}/{path}", headers=_auth(), timeout=_TIMEOUT)
    r.raise_for_status()
    return f"{_base()}{r.json()['url']}"


def object_info(bucket: str, path: str) -> dict | None:
    """Taille et type réels d'un fichier envoyé, ou None s'il n'est pas arrivé."""
    if local():
        f = local_file(bucket, path)
        if not f.exists():
            return None
        meta = json.loads(Path(str(f) + ".meta").read_text()) if Path(str(f) + ".meta").exists() else {}
        return {"size": f.stat().st_size, "mime": meta.get("mime", "")}
    folder, _, name = path.rpartition("/")
    r = httpx.post(
        f"{_base()}/object/list/{bucket}", headers=_auth(), timeout=_TIMEOUT,
        json={"prefix": folder, "search": name, "limit": 5},
    )
    if r.status_code != 200:
        return None
    for it in r.json():
        if it.get("name") == name:
            meta = it.get("metadata") or {}
            return {"size": meta.get("size", 0), "mime": meta.get("mimetype", "")}
    return None


def peek(bucket: str, path: str, n: int = 16) -> bytes | None:
    """Premiers octets du fichier reçu, pour vérifier qu'il correspond bien au type annoncé."""
    if local():
        f = local_file(bucket, path)
        return f.read_bytes()[:n] if f.exists() else None
    try:
        r = httpx.get(signed_url(bucket, path, 30), headers={"Range": f"bytes=0-{n - 1}"}, timeout=_TIMEOUT)
    except httpx.HTTPError:
        return None
    return r.content[:n] if r.status_code in (200, 206) else None


def signed_url(bucket: str, path: str, seconds: int = 60) -> str:
    if local():
        exp = int(time.time()) + seconds
        return f"/api/dev-storage/get/{bucket}/{path}?e={exp}&t={_token('get', bucket, path, exp)}"
    r = httpx.post(f"{_base()}/object/sign/{bucket}/{path}", headers=_auth(), json={"expiresIn": seconds}, timeout=_TIMEOUT)
    r.raise_for_status()
    return f"{_base()}{r.json()['signedURL']}"


def remove_object(bucket: str, path: str) -> None:
    if local():
        f = local_file(bucket, path)
        f.unlink(missing_ok=True)
        Path(str(f) + ".meta").unlink(missing_ok=True)
        return
    try:
        httpx.request("DELETE", f"{_base()}/object/{bucket}", headers=_auth(), json={"prefixes": [path]}, timeout=_TIMEOUT)
    except httpx.HTTPError:
        pass  # Un fichier orphelin dans le stockage n'empêche pas l'action en cours
