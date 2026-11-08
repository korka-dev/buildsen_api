"""Voix : synthèse et compréhension en wolof ou en français (Soynade AI), partagées par l'assistant du site et celui de WhatsApp.

La clé Soynade reste sur le serveur. On ne journalise que le statut et le code d'erreur, jamais l'audio ni le texte.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..config import settings
from ..rules import CATEGORIES
from .voice_parse import interpret, label

log = logging.getLogger("buildsen.voice")

API = "https://api.soynade.ai/v1"
MAX_AUDIO_BYTES = 5_000_000  # environ 2 minutes en WAV 16 kHz mono ; Soynade conseille moins de 30 s par réponse

# Questions de l'assistant. Le wolof provient d'une traduction automatique (Soynade) retouchée :
# à faire relire par un locuteur wolof avant la mise en production.
PROMPTS: dict[str, dict[str, str]] = {
    "intro": {
        "fr": (
            "Bienvenue sur la plateforme BUILDSEN ! Je vais vous poser six questions pour décrire votre projet. "
            "Appuyez sur le micro et répondez en wolof ou en français."
        ),
        "wo": (
            "Dalal ak jamm ci plateforme BUILDSEN ! Dinaa la laaj juróom-benni laaj ngir nga wax sa projet. "
            "Bësal ci mikro bi te tontu ci wolof walla ci français."
        ),
    },
    "category": {
        "fr": "Quelle prestation recherchez-vous ? Des plans de maison, un devis avec métrés, du dessin technique, ou une étude de structure ?",
        "wo": "Ban liggéey ngay wut ? Plan u kër, devis ak métré, dessin technique, walla étude de structure ?",
    },
    "buildingType": {
        "fr": "Quel type de bâtiment voulez-vous construire ? Par exemple une maison, une maison à étage, un immeuble ou un local commercial.",
        "wo": "Ban xeetu tabax nga bëgg a def ? Ci misaal kër, kër bu am étage, immeuble walla local commercial.",
    },
    "description": {
        "fr": "Décrivez votre projet : le nombre de chambres, les pièces que vous voulez et ce que vous attendez.",
        "wo": "Waxal sa projet : ñaata néeg, néeg yi nga bëgg ak li nga yaakaar.",
    },
    "commune": {"fr": "Dans quelle commune se trouve votre projet ?", "wo": "Ci ban commune la sa projet nekk ?"},
    "budget": {"fr": "Quel est votre budget pour cette prestation, en francs CFA ?", "wo": "Ñaata xaalis nga jagleel liggéey bii, ci franc CFA ?"},
    "deadline": {"fr": "Dans quel délai voulez-vous recevoir le travail ?", "wo": "Ci ban diir nga bëgg a jot liggéey bi ?"},
    "retry": {"fr": "Je n'ai pas bien compris. Pouvez-vous répéter, s'il vous plaît ?", "wo": "Dégguma bu baax. Mën nga ko waxaat, su la neexee ?"},
    "silence": {"fr": "Je ne vous ai pas entendu. Parlez après la question.", "wo": "Dégguma la. Waxal ginnaaw laaj bi."},
    "again": {"fr": "D'accord, recommençons cette question.", "wo": "Waaw, nanu ko waxaat."},
    "yesno": {"fr": "Dites oui ou non, s'il vous plaît.", "wo": "Waxal waaw walla déedéet, su la neexee."},
    "final": {"fr": "Si tout est correct, dites oui pour remplir le formulaire.", "wo": "Bu lépp jaar yoon, waxal waaw ngir nu duggal formulaire bi."},
}


class VoiceError(Exception):
    """Erreur de l'assistant vocal, avec un message pour l'utilisateur et le code HTTP à renvoyer."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message, self.status = message, status


_audio_cache: dict[str, bytes] = {}
_say_cache: dict[str, bytes] = {}  # phrases courtes déjà lues (mêmes communes, mêmes délais…)


def _headers() -> dict[str, str]:
    if not settings.soynade_api_key:
        raise VoiceError("L'assistant vocal n'est pas disponible pour le moment.", 503)
    return {"Authorization": f"Bearer {settings.soynade_api_key}"}


def _fail(r: httpx.Response, what: str) -> VoiceError:
    code = ""
    try:
        code = (r.json().get("error") or {}).get("code", "")
    except ValueError:
        pass
    log.warning("Soynade %s : HTTP %s %s", what, r.status_code, code)
    if r.status_code == 402:
        return VoiceError("L'assistant vocal est momentanément indisponible (crédits épuisés).", 503)
    if r.status_code == 429:
        return VoiceError("L'assistant vocal est très sollicité. Réessayez dans un instant.", 429)
    return VoiceError("L'assistant vocal n'a pas pu traiter la demande. Réessayez.", 502)


def _post(c: httpx.Client, path: str, **kw) -> httpx.Response:
    """Limite de débit Soynade (429) : deux nouveaux essais rapprochés avant d'abandonner."""
    import time

    for attempt in range(3):
        r = c.post(path, **kw)
        if r.status_code != 429 or attempt == 2:
            return r
        time.sleep(1.0 + attempt)
    return r


def speak_wolof(text: str) -> bytes:
    """Texte lu en wolof (MP3)."""
    with httpx.Client(base_url=API, timeout=60, headers=_headers()) as c:
        r = _post(c, "/text-to-speech", json={"text": text, "language": "wo", "output_format": "mp3"})
    if r.status_code != 200:
        raise _fail(r, "synthèse")
    return r.content


def prompt_audio(key: str) -> bytes:
    if key not in _audio_cache:
        _audio_cache[key] = speak_wolof(PROMPTS[key]["wo"])
    return _audio_cache[key]


def say_audio(text: str) -> bytes:
    if text not in _say_cache:
        if len(_say_cache) > 500:
            _say_cache.clear()
        _say_cache[text] = speak_wolof(text)
    return _say_cache[text]


def _transcripts(wav: bytes) -> tuple[str, str, str]:
    """Traduction française (sens d'une réponse en wolof), transcription wolof (noms propres, nombres) et transcription française."""
    if not wav:
        raise VoiceError("Aucun son reçu.", 400)
    if len(wav) > MAX_AUDIO_BYTES:
        raise VoiceError("Réponse trop longue : parlez moins de 30 secondes.", 400)
    files = lambda: {"file": ("reponse.wav", wav, "audio/wav")}  # noqa: E731
    with httpx.Client(base_url=API, timeout=60, headers=_headers()) as c, ThreadPoolExecutor(max_workers=3) as pool:
        tr = pool.submit(_post, c, "/audio/translations", files=files(), data={"source_language": "wo", "target_language": "fr"})
        ts = pool.submit(_post, c, "/audio/transcriptions", files=files(), data={"language": "wo"})
        tf = pool.submit(_post, c, "/audio/transcriptions", files=files(), data={"language": "fr"})
        tr, ts, tf = tr.result(), ts.result(), tf.result()
    if tr.status_code != 200:
        raise _fail(tr, "traduction vocale")
    fr = (tr.json().get("text") or "").strip()
    wo = (ts.json().get("text") or "").strip() if ts.status_code == 200 else ""
    said = (tf.json().get("text") or "").strip() if tf.status_code == 200 else ""
    return fr, wo, said


def understand_wav(wav: bytes, field: str) -> dict:
    """Réponse enregistrée (WAV), en wolof ou en français : valeur du formulaire pour la question « field »."""
    fr, wo, said = _transcripts(wav)
    value = interpret(field, fr, wo, said) if (fr or wo or said) else None
    return {"heard": fr, "heardWo": wo, "value": value, "label": label(field, value) if value is not None else None}


def transcribe_wav(wav: bytes) -> str:
    """Texte d'un message vocal (messagerie entre client et professionnel) : la transcription wolof comprend aussi le français."""
    if not wav:
        raise VoiceError("Aucun son reçu.", 400)
    if len(wav) > MAX_AUDIO_BYTES:
        raise VoiceError("Message trop long.", 400)
    with httpx.Client(base_url=API, timeout=60, headers=_headers()) as c:
        r = _post(c, "/audio/transcriptions", files={"file": ("message.wav", wav, "audio/wav")}, data={"language": "wo"})
    if r.status_code != 200:
        raise _fail(r, "transcription")
    return (r.json().get("text") or "").strip()


def recap_texts(values: dict) -> tuple[str, str]:
    """Récapitulatif des réponses, en français et en wolof (les valeurs restent celles du formulaire)."""
    cat = CATEGORIES.get(str(values.get("category") or ""), {}).get("name", "")
    budget = str(values.get("budget") or "")
    money = f"{int(budget):,}".replace(",", " ") if budget.isdigit() else ""
    fr = ["Voici votre demande."]
    wo = ["Lii mooy sa laaj."]
    for text_fr, text_wo in (
        (f"Prestation : {cat}." if cat else "", f"Liggéey bi : {cat}." if cat else ""),
        (
            f"Bâtiment : {values['buildingType']}." if values.get("buildingType") else "",
            f"Tabax bi : {values['buildingType']}." if values.get("buildingType") else "",
        ),
        (f"Commune : {values['commune']}." if values.get("commune") else "", f"Commune : {values['commune']}." if values.get("commune") else ""),
        (f"Budget : {money} francs CFA." if money else "", f"Budget : {money} franc CFA." if money else ""),
        (f"Délai : {values['deadline']}." if values.get("deadline") else "", f"Diir : {values['deadline']}." if values.get("deadline") else ""),
    ):
        if text_fr:
            fr.append(text_fr)
            wo.append(text_wo)
    return " ".join(fr), " ".join(wo)
