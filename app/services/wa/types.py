"""Types communs : un message reçu, quel que soit le fournisseur."""

from dataclasses import dataclass


@dataclass
class Inbound:
    id: str  # identifiant WhatsApp du message (sert à ignorer les doublons)
    phone: str  # 9 chiffres, sans indicatif
    kind: str  # text | audio | button | other
    text: str = ""
    button_id: str = ""
    media_id: str = ""  # note vocale à télécharger chez Meta
    audio: bytes | None = None  # note vocale déjà fournie (simulateur)
    context_id: str = ""  # message auquel la personne répond
    name: str = ""  # nom du profil WhatsApp
