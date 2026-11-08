"""Stockage de fichiers sur le disque du serveur (liens signés, jamais de lecture libre). Réponse 404 dès que Supabase est configuré."""

import json
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ..errors import ApiError, not_found
from ..rules import UPLOADS
from ..storage import check_token, local, local_file

router = APIRouter(prefix="/api/dev-storage", tags=["Développement"])
MAX = max(u["max_bytes"] for u in UPLOADS.values())


@router.put("/upload/{bucket}/{path:path}")
async def upload(bucket: str, path: str, request: Request, t: str = Query("")):
    if not local():
        raise not_found()
    if not check_token("put", bucket, path, t):
        raise ApiError("Lien d'envoi invalide.", 403)
    if int(request.headers.get("content-length") or 0) > MAX:
        raise ApiError("Fichier trop lourd.", 413)
    body = await request.body()
    if len(body) > MAX:
        raise ApiError("Fichier trop lourd.", 413)
    f = local_file(bucket, path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(body)
    Path(str(f) + ".meta").write_text(json.dumps({"mime": request.headers.get("content-type", "")}))
    return {"ok": True}


@router.get("/get/{bucket}/{path:path}")
def get(bucket: str, path: str, e: int = Query(0), t: str = Query("")):
    if not local():
        raise not_found()
    if not e or not check_token("get", bucket, path, t, e):
        raise ApiError("Lien expiré ou invalide.", 403)
    f = local_file(bucket, path)
    if not f.exists():
        raise not_found()
    meta = json.loads(Path(str(f) + ".meta").read_text()) if Path(str(f) + ".meta").exists() else {}
    return Response(f.read_bytes(), media_type=meta.get("mime") or "application/octet-stream",
                    headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})
