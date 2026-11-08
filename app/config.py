"""Configuration lue dans les variables d'environnement (ou le fichier .env en développement)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Base PostgreSQL (Neon en production). Même base et mêmes tables que la version Next.js + Prisma.
    database_url: str = "postgresql://buildsen:buildsen@localhost:5432/buildsen"

    # Adresse publique du site (liens dans les e-mails)
    app_url: str = "http://localhost:3000"
    # Origines autorisées pour les requêtes qui modifient des données (protection CSRF), séparées par des virgules
    allowed_origins: str = "http://localhost:3000"
    environment: str = "development"

    # E-mails (Resend). Sans clé, aucun e-mail n'est envoyé : pratique en développement.
    resend_api_key: str = ""
    email_from: str = "BUILDSEN <notifications@buildsen.com>"

    # Stockage des fichiers (Supabase Storage, dossiers privés « justificatifs » et « portfolios »)
    supabase_url: str = ""
    supabase_secret_key: str = ""

    # Tâche planifiée des alertes différées (en-tête Authorization: Bearer <CRON_SECRET>)
    cron_secret: str = ""
    # Clé partagée avec le frontend pour ses appels serveur à serveur qui modifient des données
    internal_api_key: str = ""

    # Assistant vocal en wolof (Soynade AI). Sans clé, l'assistant est indisponible et le formulaire reste utilisable.
    soynade_api_key: str = ""

    # --- Assistant WhatsApp ---
    # "sim" : simulateur local (aucun compte Meta nécessaire, interdit en production) ; "meta" : API WhatsApp Cloud ; "off" : désactivé
    whatsapp_provider: str = "sim"
    # Jeton choisi par nous, que Meta nous renvoie pour vérifier l'adresse du webhook
    whatsapp_verify_token: str = ""
    # Secret de l'application Meta : sert à vérifier la signature de chaque message reçu
    whatsapp_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v21.0"
    # Numéro public de l'assistant (9 chiffres, sans indicatif) : lien et QR code du site
    whatsapp_bot_number: str = ""
    # Noms des messages modèles approuvés par Meta (hors fenêtre de 24 h)
    whatsapp_tpl_alert: str = "nouvelle_demande"
    whatsapp_tpl_message: str = "nouveau_message"
    whatsapp_tpl_code: str = "code_connexion"

    # --- Formules et paiement ---
    # false : tout est gratuit et sans limite (les formules sont masquées). true : limites par formule et paiement actifs.
    plans_enabled: bool = False
    # "sim" : paiement simulé (page de test, aucun argent, interdit en production) ; "paydunya" : Wave / Orange Money ; "off"
    payment_provider: str = "sim"
    paydunya_master_key: str = ""
    paydunya_private_key: str = ""
    paydunya_token: str = ""
    paydunya_mode: str = "test"  # "test" ou "live"
    # Wave : lien de paiement marchand (https://pay.wave.com/m/…). Le montant est ajouté au lien (?amount=). Wave n'avertit pas le serveur d'un paiement
    # reçu avec un simple lien : la formule s'active donc tout de suite à « J'ai payé » (activation provisoire), puis se confirme d'elle-même quand
    # l'avis de paiement arrive sur /api/payments/webhook/wave (secret ci-dessous), ou par un clic de l'équipe.
    wave_pay_link: str = ""
    wave_webhook_secret: str = ""
    # Orange Money : lien marchand (https://sugu.orange-sonatel.com/mp/…). Le montant n'est pas dans le lien : le client le saisit (BUILDSEN le lui affiche).
    # PAYMENT_PROVIDER=links (ou wave) active les liens configurés ; le client choisit Wave ou Orange Money.
    om_pay_link: str = ""
    om_webhook_secret: str = ""
    # 0 (recommandé) : la formule ne s'active qu'à la réception du vrai paiement. Une valeur > 0 active la formule quelques heures dès « J'ai payé » : à éviter, c'est contournable.
    wave_grace_hours: int = 0
    # Adresse du site vue depuis un téléphone du même réseau (ex. http://192.168.1.20:3000), utilisée par le simulateur
    lan_url: str = ""

    # Développement local seulement : code à 6 chiffres fixe accepté à la place du code de l'application d'authentification, et mot de passe
    # de l'administrateur de test créé par `python -m app.devseed`. Ignorés en production et sur toute base qui n'est pas locale.
    admin_dev_code: str = ""
    admin_dev_password: str = ""

    # Derrière un proxy (frontend Next.js, Render, Fly…) : l'adresse IP du visiteur est lue dans X-Forwarded-For
    trust_proxy: bool = True
    # Nombre de relais de confiance entre le visiteur et l'API (le site Next.js compte pour un) : sert à lire la vraie adresse dans X-Forwarded-For
    proxy_hops: int = 1

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @property
    def wa_sim(self) -> bool:
        """Simulateur WhatsApp actif : jamais en production."""
        return self.whatsapp_provider == "sim" and not self.production

    @property
    def wa_meta(self) -> bool:
        return self.whatsapp_provider == "meta" and bool(self.whatsapp_access_token and self.whatsapp_phone_number_id)

    def production_problems(self) -> list[str]:
        """Réglages dangereux en production : le serveur refuse de démarrer plutôt que de tourner avec une configuration fragile."""
        out = []
        if self.admin_dev_code or self.admin_dev_password:
            out.append("ADMIN_DEV_CODE / ADMIN_DEV_PASSWORD sont réservés au développement local : à retirer.")
        if len(self.cron_secret) < 16:
            out.append("CRON_SECRET manquant ou trop court (16 caractères minimum).")
        if len(self.internal_api_key) < 16:
            out.append("INTERNAL_API_KEY manquant ou trop court (16 caractères minimum) : il doit être identique côté site et côté API.")
        if not self.origins or any(not o.startswith("https://") for o in self.origins):
            out.append("ALLOWED_ORIGINS doit contenir uniquement des adresses https:// du site.")
        if not self.app_url.startswith("https://"):
            out.append("APP_URL doit être l'adresse https:// du site.")
        if not self.resend_api_key:
            out.append("RESEND_API_KEY manquant : aucun e-mail (alertes, rappels, réinitialisation de mot de passe) ne partirait.")
        if self.plans_enabled and not self.pay_on:
            out.append("PLANS_ENABLED=true mais aucun moyen de paiement actif : renseigner PAYMENT_PROVIDER=links et WAVE_PAY_LINK / OM_PAY_LINK.")
        if self.wave_grace_hours > 0:
            out.append("WAVE_GRACE_HOURS doit rester à 0 : sinon un simple clic sur « J'ai payé » activerait la formule.")
        return out

    @property
    def local_database(self) -> bool:
        from urllib.parse import urlsplit

        return (urlsplit(self.database_url).hostname or "") in ("localhost", "127.0.0.1", "db")

    @property
    def admin_dev_code_ok(self) -> bool:
        """Code fixe de développement actif : jamais en production, jamais sur une base distante."""
        return bool(self.admin_dev_code) and not self.production and self.local_database

    @property
    def pay_sim(self) -> bool:
        return self.payment_provider == "sim" and not self.production

    @property
    def pay_paydunya(self) -> bool:
        return self.payment_provider == "paydunya" and bool(self.paydunya_master_key and self.paydunya_private_key and self.paydunya_token)

    @property
    def pay_wave(self) -> bool:
        return self.payment_provider in ("wave", "links") and self.wave_pay_link.startswith("https://pay.wave.com/")

    @property
    def pay_om(self) -> bool:
        return self.payment_provider in ("wave", "links") and self.om_pay_link.startswith("https://sugu.orange-sonatel.com/")

    @property
    def pay_methods(self) -> list[str]:
        """Moyens de paiement par lien proposés au client, dans l'ordre."""
        return [m for m, on in (("wave", self.pay_wave), ("om", self.pay_om)) if on]

    @property
    def pay_on(self) -> bool:
        return self.pay_sim or self.pay_paydunya or self.pay_wave or self.pay_om

    @property
    def wa_on(self) -> bool:
        return self.wa_sim or self.wa_meta


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
