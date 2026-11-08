"""Fournisseurs de paiement : « sim » (page de test locale, aucun argent) et PayDunya (Wave et Orange Money).

Un fournisseur sait créer un lien de paiement et dire, à la demande, si un paiement est réglé. Le serveur ne croit jamais
ce que dit le navigateur ou la notification reçue : il interroge toujours le fournisseur (`verify`) avant d'activer une formule.
"""

import secrets
from dataclasses import dataclass

import httpx

from ...config import settings
from ...errors import ApiError
from ...mailer import app_url
from ...models import Payment

PAYDUNYA = {"test": "https://app.paydunya.com/sandbox-api/v1", "live": "https://app.paydunya.com/api/v1"}


@dataclass
class Checkout:
    token: str
    url: str


@dataclass
class Verdict:
    state: str  # "paid", "failed" ou "pending"
    amount: int | None = None


def base_url() -> str:
    """Adresse du site pour les liens de paiement : en simulation, celle du réseau local si elle est connue (ouverture depuis un téléphone)."""
    return (settings.lan_url if settings.pay_sim and settings.lan_url else settings.app_url).rstrip("/")


class SimPay:
    name = "sim"

    def create(self, amount: int, description: str) -> Checkout:
        token = "sim_" + secrets.token_urlsafe(18)
        return Checkout(token, f"{base_url()}/paiement/simule?ref={token}")

    def verify(self, p: Payment) -> Verdict:
        return Verdict({"paid": "paid", "failed": "failed"}.get((p.data or {}).get("sim"), "pending"), p.amount)


class PayDunya:
    name = "paydunya"

    def _headers(self) -> dict:
        return {
            "PAYDUNYA-MASTER-KEY": settings.paydunya_master_key, "PAYDUNYA-PRIVATE-KEY": settings.paydunya_private_key,
            "PAYDUNYA-TOKEN": settings.paydunya_token, "Content-Type": "application/json",
        }

    @property
    def _api(self) -> str:
        return PAYDUNYA["live" if settings.paydunya_mode == "live" else "test"]

    def create(self, amount: int, description: str) -> Checkout:
        body = {
            "invoice": {"total_amount": amount, "description": description},
            "store": {"name": "BUILDSEN", "website_url": app_url()},
            "actions": {
                "callback_url": app_url("/api/payments/webhook/paydunya"), "return_url": app_url("/paiement/retour"), "cancel_url": app_url("/pro/abonnement"),
            },
        }
        try:
            r = httpx.post(f"{self._api}/checkout-invoice/create", json=body, headers=self._headers(), timeout=20)
            data = r.json()
        except (httpx.HTTPError, ValueError):
            raise ApiError("Le paiement est momentanément indisponible. Réessayez dans un instant.", 503) from None
        if data.get("response_code") != "00" or not data.get("token"):
            raise ApiError("Le paiement est momentanément indisponible. Réessayez dans un instant.", 503)
        return Checkout(data["token"], data["response_text"])

    def verify(self, p: Payment) -> Verdict:
        try:
            r = httpx.get(f"{self._api}/checkout-invoice/confirm/{p.token}", headers=self._headers(), timeout=20)
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return Verdict("pending")
        status = str(data.get("status", "")).lower()
        amount = (data.get("invoice") or {}).get("total_amount")
        state = "paid" if status == "completed" else "failed" if status in ("cancelled", "canceled", "failed") else "pending"
        try:
            return Verdict(state, int(float(amount)) if amount is not None else None)
        except (TypeError, ValueError):
            return Verdict(state)


class WaveLink:
    """Lien de paiement marchand Wave avec le montant en paramètre. Le paiement se confirme par `confirm_external` (avis reçu) ou par l'équipe."""

    name = "wave"

    def create(self, amount: int, description: str) -> Checkout:
        base = settings.wave_pay_link.split("?")[0]
        return Checkout("wave_" + secrets.token_urlsafe(18), f"{base}?amount={amount}")

    def verify(self, p: Payment) -> Verdict:
        return Verdict("pending")  # aucune interrogation possible : voir `manual.confirm_external`


class OrangeMoneyLink:
    """Lien marchand Orange Money (sans montant dans le lien : le client le saisit). Confirmation comme pour Wave : avis reçu, export ou équipe."""

    name = "om"

    def create(self, amount: int, description: str) -> Checkout:
        return Checkout("om_" + secrets.token_urlsafe(18), settings.om_pay_link.split("?")[0])

    def verify(self, p: Payment) -> Verdict:
        return Verdict("pending")


def get_provider(name: str | None = None):
    name = name or settings.payment_provider
    if name == "paydunya":
        return PayDunya()
    if name == "wave" or (name == "links" and settings.pay_wave):
        return WaveLink()
    if name == "om" or name == "links":
        return OrangeMoneyLink()
    return SimPay()
