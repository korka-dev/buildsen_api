"""Assistant vocal en wolof du site, pour décrire un projet (Soynade AI). Le travail vocal est dans services/voice_core.py.

- GET  /api/client/voice/prompts        textes des questions (wolof et français)
- GET  /api/client/voice/audio/{key}    question lue en wolof (MP3, générée une fois puis gardée en mémoire)
- POST /api/client/voice/understand     réponse enregistrée (WAV) -> valeur du formulaire
- POST /api/client/voice/recap          récapitulatif traduit en wolof et lu à voix haute
- POST /api/client/voice/say            courte phrase lue en wolof
"""

import base64
import logging

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session as Db

from ..db import get_db
from ..deps import require_client
from ..errors import ApiError
from ..models import User
from ..services import voice_core as vc
from ..services.voice_parse import PARSERS
from ..throttle import too_many

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/client/voice", tags=["Assistant vocal"])


async def _run(fn, *args):
    """Exécute un appel Soynade (bloquant) hors de la boucle, en traduisant ses erreurs."""
    try:
        return await run_in_threadpool(fn, *args)
    except vc.VoiceError as e:
        raise ApiError(e.message, e.status) from e


@router.get("/prompts")
def prompts(user: User = Depends(require_client)):
    return {"prompts": vc.PROMPTS}


@router.get("/audio/{key}")
async def prompt_audio(key: str, user: User = Depends(require_client)):
    if key not in vc.PROMPTS:
        raise ApiError("Question inconnue.", 404)
    audio = await _run(vc.prompt_audio, key)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/understand")
async def understand(request: Request, field: str = Query(...), user: User = Depends(require_client), db: Db = Depends(get_db)):
    """Réponse enregistrée dans le navigateur (WAV), en wolof ou en français, puis valeur du formulaire."""
    if field not in PARSERS:
        raise ApiError("Question inconnue.")
    if too_many(db, f"voice:{user.id}", 120, 60):
        raise ApiError("Vous avez beaucoup utilisé l'assistant vocal. Réessayez dans une heure ou remplissez le formulaire.", 429)
    db.commit()
    return await _run(vc.understand_wav, await request.body(), field)


@router.post("/recap")
async def recap(body: dict = Body(...), user: User = Depends(require_client), db: Db = Depends(get_db)):
    """Récapitulatif des réponses, traduit en wolof et lu à voix haute avant la confirmation."""
    if too_many(db, f"voice-recap:{user.id}", 60, 60):
        raise ApiError("Vous avez beaucoup utilisé l'assistant vocal. Réessayez dans une heure.", 429)
    db.commit()
    fr, wo = vc.recap_texts(body)
    audio = await _run(vc.say_audio, f"{wo} {vc.PROMPTS['final']['wo']}")
    return {"fr": fr, "wo": wo, "audio": base64.b64encode(audio).decode()}


@router.post("/say")
async def say(body: dict = Body(...), user: User = Depends(require_client), db: Db = Depends(get_db)):
    """Phrase courte lue en wolof : l'assistant répète ce qu'il a compris."""
    text = str(body.get("text") or "").strip()[:300]
    if not text:
        raise ApiError("Texte manquant.")
    if too_many(db, f"voice-say:{user.id}", 200, 60):
        raise ApiError("Vous avez beaucoup utilisé l'assistant vocal. Réessayez dans une heure.", 429)
    db.commit()
    return Response(await _run(vc.say_audio, text), media_type="audio/mpeg")
