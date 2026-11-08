"""Assistant WhatsApp de BUILDSEN.

- types.py      message reçu normalisé
- templates.py  messages modèles (à faire approuver par Meta) et leur rendu pour le simulateur
- provider.py   envoi et téléchargement : simulateur local ou API WhatsApp Cloud (Meta)
- sender.py     envoi avec journal, fenêtre de 24 heures et interrupteur d'urgence
- media.py      conversion des notes vocales WhatsApp en WAV
- bot.py        l'assistant : accueil, création de compte, demandes, réponses des professionnels, messages relayés
- notify.py     alertes et notifications envoyées depuis le reste de la plateforme
"""
