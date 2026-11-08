"""Notes vocales WhatsApp (OGG/Opus) -> WAV 16 kHz mono, le format que comprend Soynade."""

import shutil
import subprocess

from ..voice_core import VoiceError


def to_wav(data: bytes) -> bytes:
    if data[:4] == b"RIFF":  # déjà un WAV (simulateur, navigateur)
        return data
    if not shutil.which("ffmpeg"):
        raise VoiceError("Les messages vocaux ne sont pas disponibles pour le moment. Écrivez votre réponse.", 503)
    try:
        p = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
            input=data, capture_output=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise VoiceError("Le message vocal n'a pas pu être lu. Réessayez.", 502) from e
    if p.returncode != 0 or not p.stdout:
        raise VoiceError("Le message vocal n'a pas pu être lu. Réessayez.", 400)
    return p.stdout
