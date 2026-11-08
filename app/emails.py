"""Modèles des e-mails envoyés par BUILDSEN : ton chaleureux, version HTML aux couleurs de la marque et version texte.

Transposition fidèle des modèles de la version Next.js (app/src/lib/emails.ts).
"""

import html
import re
from dataclasses import dataclass

from .config import settings
from .rules import CLIENTS_OPEN, LAUNCH_OFFER, OPENING, REVIEW_DELAY, SHOW_PRICING, SUPPORT_WHATSAPP_DISPLAY, TEAM_INBOX, whatsapp_link
from .utils import fcfa

CONTACT = TEAM_INBOX


@dataclass
class Mail:
    subject: str
    html: str
    text: str


@dataclass
class Ctx:
    whatsapp: str = SUPPORT_WHATSAPP_DISPLAY
    whatsapp_link: str = whatsapp_link("Bonjour BUILDSEN, j'ai une question.")
    welcome: int = LAUNCH_OFFER["months"]
    delay: str = REVIEW_DELAY
    opening: str = OPENING
    pricing: bool = SHOW_PRICING
    open: bool = CLIENTS_OPEN

    @staticmethod
    def url(path: str = "") -> str:
        return f"{settings.app_url}{path}"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s.replace("<br>", "\n")).replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"')


def layout(ctx: Ctx, *, title: str, paras: list[str], steps: list[str] | None = None, button: tuple[str, str] | None = None,
           note: str | None = None, sign: str | None = None, footer: str | None = None) -> tuple[str, str]:
    p = lambda t: f'<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#333333">{t}</p>'  # noqa: E731
    steps_html = ""
    if steps:
        rows = "".join(
            f'<tr><td valign="top" style="padding:0 12px 10px 0"><span style="display:inline-block;width:26px;height:26px;line-height:26px;border-radius:13px;background:#075B3C;color:#FFFFFF;text-align:center;font-weight:700;font-size:13px">{i + 1}</span></td>'
            f'<td style="padding:3px 0 10px;font-size:15px;line-height:1.5;color:#333333">{s}</td></tr>'
            for i, s in enumerate(steps)
        )
        steps_html = f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 18px">{rows}</table>'
    button_html = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 22px"><tr><td style="border-radius:8px;background:#15945C">'
        f'<a href="{button[1]}" style="display:inline-block;padding:13px 22px;font-size:15px;font-weight:700;color:#FFFFFF;text-decoration:none">{button[0]}</a></td></tr></table>'
        if button else ""
    )
    note_html = f'<div style="background:#E7F1EB;border-radius:8px;padding:14px 16px;margin:0 0 18px;font-size:14px;line-height:1.55;color:#075B3C">{note}</div>' if note else ""
    contact = (
        f'Une question ? Écrivez-nous sur WhatsApp au <a href="{ctx.whatsapp_link}" style="color:#075B3C;font-weight:700">{ctx.whatsapp}</a> '
        f'ou par e-mail à <a href="mailto:{CONTACT}" style="color:#075B3C;font-weight:700">{CONTACT}</a>, nous vous répondons avec plaisir.'
    )
    signature = sign or "<b>L'équipe BUILDSEN</b>"
    body = f"""<!doctype html><html lang="fr"><body style="margin:0;padding:0;background:#F2F5F3">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F5F3;padding:24px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#FFFFFF;border-radius:12px;border:1px solid #DCE3DE;font-family:Arial,Helvetica,sans-serif">
<tr><td style="padding:28px 32px 8px"><span style="font-size:24px;font-weight:800;letter-spacing:.5px"><span style="color:#000000">BUILD</span><span style="color:#075B3C">SEN</span></span><br><span style="font-size:12px;color:#444444">Construire avec confiance.</span></td></tr>
<tr><td style="padding:16px 32px 8px">
<h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;color:#000000">{title}</h1>
{"".join(p(x) for x in paras)}
{steps_html}
{button_html}
{note_html}
{p(contact)}
{p("À très vite,<br>" + signature)}
</td></tr>
<tr><td style="padding:16px 32px 24px;border-top:1px solid #DCE3DE;font-size:12px;color:#66716A">BUILDSEN · Dakar, Sénégal · {footer or "Vous recevez cet e-mail car vous avez un compte sur buildsen.com."}</td></tr>
</table></td></tr></table></body></html>"""
    lines = [_strip(title), ""]
    for x in paras:
        lines += [_strip(x), ""]
    if steps:
        lines += [f"{i + 1}. {_strip(s)}" for i, s in enumerate(steps)] + [""]
    if button:
        lines += [f"{button[0]} : {button[1]}", ""]
    if note:
        lines += [_strip(note), ""]
    lines += [f"Une question ? Écrivez-nous sur WhatsApp au {ctx.whatsapp} ({ctx.whatsapp_link}) ou par e-mail à {CONTACT}", "", "À très vite,", _strip(sign or "L'équipe BUILDSEN")]
    if footer:
        lines += ["", _strip(footer)]
    return body, "\n".join(lines)


def _mail(subject: str, parts: tuple[str, str]) -> Mail:
    return Mail(subject, parts[0], parts[1])


def welcome_mail(ctx: Ctx, first_name: str) -> Mail:
    n = esc(first_name)
    if ctx.pricing:
        note = f"<b>Offre de lancement</b> : les 50 premiers professionnels vérifiés ont <b>{ctx.welcome} mois de formule Pro offerts</b>. Ouverture aux particuliers prévue en {ctx.opening}."
    elif ctx.open:
        note = "Dès que votre profil est vérifié, vous pouvez consulter les demandes des particuliers et leur écrire."
    else:
        note = f"Ouverture aux particuliers prévue en <b>{ctx.opening}</b> : les profils vérifiés seront les premiers visibles."
    return _mail(f"Bienvenue sur BUILDSEN, {first_name} !", layout(
        ctx, title=f"Merci de nous avoir rejoints, {n} !",
        paras=[
            "Nous sommes ravis de vous compter parmi les tout premiers professionnels de BUILDSEN.",
            "BUILDSEN, c'est l'endroit où les particuliers de Dakar trouveront des ingénieurs et des techniciens de confiance pour leurs plans, métrés et études. Et vous en faites partie dès le départ.",
            "<b>Prochaine étape : faites vérifier votre profil.</b> Cela ne prend que quelques minutes :",
        ],
        steps=[
            "Choisissez votre statut et indiquez votre formation et votre diplôme.",
            "Envoyez votre pièce d'identité et votre diplôme (l'attestation d'expérience et le portfolio sont facultatifs, mais ils vous démarquent).",
            f"Soumettez votre dossier : notre équipe l'examine sous {ctx.delay}.",
        ],
        button=("Compléter mon dossier", ctx.url("/pro/verification")), note=note,
    ))


def dossier_submitted_mail(ctx: Ctx, first_name: str) -> Mail:
    return _mail("Nous avons bien reçu votre dossier", layout(
        ctx, title=f"Merci {esc(first_name)}, votre dossier est entre de bonnes mains !",
        paras=[
            f"Nous avons bien reçu votre dossier de vérification. Notre équipe l'examine avec attention sous {ctx.delay}, et nous vous écrivons dès que c'est fait.",
            "En attendant, vous pouvez compléter votre présentation et ajouter vos plus belles réalisations : c'est ce que les clients regarderont en premier.",
        ],
        button=("Compléter mon profil", ctx.url("/pro/profil")),
    ))


def dossier_reminder_mail(ctx: Ctx, first_name: str, missing: list[str], info_done: bool = True) -> Mail:
    n = len(missing)
    if not info_done:
        subject = "Votre profil BUILDSEN : terminez votre dossier en quelques minutes"
    elif n:
        subject = f"Votre profil BUILDSEN : plus que {n} document{'s' if n > 1 else ''} à envoyer"
    else:
        subject = "Votre profil BUILDSEN est presque prêt"
    return _mail(subject, layout(
        ctx, title=f"Votre profil est presque prêt, {esc(first_name)}",
        paras=[
            "Merci encore pour votre inscription sur BUILDSEN. Vos informations sont bien enregistrées : il ne reste que quelques minutes pour terminer."
            if info_done else
            "Merci encore pour votre inscription sur BUILDSEN. Votre compte est créé : il ne reste que quelques minutes pour terminer votre dossier.",
            "Depuis le 18 octobre, <b>les particuliers de Dakar peuvent décrire leur projet</b> sur BUILDSEN : plans, devis, métrés, études. Seuls les professionnels vérifiés pourront leur répondre.",
            "Voici ce qu'il vous reste à faire :",
        ],
        steps=[*map(esc, missing), "Cliquez sur « Soumettre mon dossier pour vérification »."],
        button=("Terminer mon dossier", ctx.url("/pro/verification")),
        note=f"Une photo nette prise avec votre téléphone suffit. Une fois le dossier envoyé, nous l'examinons sous {ctx.delay}, puis vous recevez les nouvelles demandes par e-mail et vous écrivez directement aux clients.",
    ))


def verified_mail(ctx: Ctx, first_name: str) -> Mail:
    paras = ["Notre équipe a validé votre dossier : vous portez désormais le badge <b>« Profil vérifié »</b>, gage de confiance pour les clients."]
    if ctx.pricing:
        paras.append(f"Offre de lancement : si vous faites partie des 50 premiers professionnels vérifiés, <b>{ctx.welcome} mois de formule Pro</b> vous sont offerts (rubrique « Mon abonnement »).")
    paras.append(
        "Vous pouvez dès maintenant consulter les demandes des particuliers et leur écrire dans la messagerie BUILDSEN. Vous recevrez aussi un e-mail à chaque nouvelle demande dans vos spécialités."
        if ctx.open else
        f"Les demandes des particuliers vous seront proposées dès l'ouverture de la plateforme, prévue en {ctx.opening}. Nous vous préviendrons par e-mail."
    )
    return _mail("Félicitations, votre profil BUILDSEN est vérifié !", layout(
        ctx, title=f"Félicitations {esc(first_name)}, votre profil est vérifié !", paras=paras,
        button=("Voir les demandes", ctx.url("/pro/demandes")) if ctx.open else ("Voir mon espace", ctx.url("/pro")),
        note="Astuce : un profil avec une présentation claire et quelques réalisations est beaucoup plus souvent contacté.",
    ))


def more_info_mail(ctx: Ctx, first_name: str, reason: str) -> Mail:
    return _mail("Votre dossier BUILDSEN : il nous manque un petit élément", layout(
        ctx, title=f"Bonjour {esc(first_name)}, on y est presque !",
        paras=[
            "Merci pour votre dossier. Pour pouvoir le valider, notre équipe a besoin d'un complément :",
            f"<i>« {esc(reason)} »</i>",
            "Il vous suffit de vous connecter, de corriger ou d'ajouter l'élément demandé, puis de soumettre à nouveau votre dossier.",
        ],
        button=("Compléter mon dossier", ctx.url("/pro/verification")),
    ))


def refused_mail(ctx: Ctx, first_name: str, reason: str) -> Mail:
    return _mail("Votre dossier BUILDSEN", layout(
        ctx, title=f"Bonjour {esc(first_name)},",
        paras=[
            "Merci de l'intérêt que vous portez à BUILDSEN. Après examen, nous ne pouvons malheureusement pas valider votre dossier pour le moment :",
            f"<i>« {esc(reason)} »</i>",
            "Si vous pensez qu'il s'agit d'une erreur, ou si votre situation évolue, n'hésitez pas à nous contacter : nous serons heureux d'en parler avec vous.",
        ],
    ))


def reset_mail(ctx: Ctx, first_name: str, link: str) -> Mail:
    return _mail("Choisissez un nouveau mot de passe BUILDSEN", layout(
        ctx, title=f"Bonjour {esc(first_name)},",
        paras=[
            "Vous avez demandé à changer votre mot de passe. Aucun souci : cliquez sur le bouton ci-dessous pour en choisir un nouveau. Le lien est valable 1 heure.",
            "Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet e-mail : votre mot de passe reste inchangé.",
        ],
        button=("Choisir un nouveau mot de passe", link),
    ))


def verify_email_mail(ctx: Ctx, first_name: str, link: str) -> Mail:
    return _mail("Confirmez votre adresse e-mail BUILDSEN", layout(
        ctx, title=f"Bienvenue sur BUILDSEN, {esc(first_name)} !",
        paras=["Merci pour votre inscription. Confirmez votre adresse e-mail pour pouvoir décrire votre projet :"],
        button=("Confirmer mon adresse", link),
        note="Ce lien est valable 48 heures. Si vous n'êtes pas à l'origine de cette inscription, ignorez simplement cet e-mail.",
    ))


def request_removed_mail(ctx: Ctx, first_name: str, title: str, reason: str) -> Mail:
    return _mail("Votre demande BUILDSEN a été retirée", layout(
        ctx, title=f"Bonjour {esc(first_name)}",
        paras=[
            f"Notre équipe a retiré votre demande <b>« {esc(title)} »</b>, qui ne respectait pas les règles de BUILDSEN.",
            f"<b>Motif :</b> {esc(reason)}",
            "Vous pouvez décrire un nouveau projet en tenant compte de cette remarque.",
        ],
        button=("Décrire un nouveau projet", ctx.url("/espace/demandes/nouvelle")),
    ))


def new_request_pro_mail(ctx: Ctx, first_name: str, *, id: str, title: str, commune: str, budget: int) -> Mail:
    return _mail(f"Nouvelle demande : {title}", layout(
        ctx, title=f"Une nouvelle demande pour vous, {esc(first_name)}",
        paras=[
            f"<b>{esc(title)}</b><br>{esc(commune)} · budget {fcfa(budget)}",
            "5 professionnels au maximum peuvent écrire au client : soyez parmi les premiers.",
        ],
        button=("Voir la demande", ctx.url(f"/pro/demandes/{id}")),
    ))


def request_reminder_mail(ctx: Ctx, first_name: str, *, id: str, title: str, commune: str, budget: int, places: int, total: int) -> Mail:
    return _mail(f"Toujours ouverte : {title}", layout(
        ctx, title=f"Cette demande attend encore une réponse, {esc(first_name)}",
        paras=[
            f"<b>{esc(title)}</b><br>{esc(commune)} · budget {fcfa(budget)}",
            f"Il reste {places} place{'s' if places > 1 else ''} sur {total} : le client choisit parmi les premiers professionnels qui lui écrivent.",
        ],
        button=("Voir la demande", ctx.url(f"/pro/demandes/{id}")),
    ))


def new_message_mail(ctx: Ctx, first_name: str, sender: str, title: str, link: str, first: bool) -> Mail:
    return _mail(f"{sender} vous a écrit sur BUILDSEN" if first else f"Nouveau message de {sender}", layout(
        ctx, title=f"{esc(sender)} vous a écrit" if first else f"Nouveau message de {esc(sender)}",
        paras=[
            f"Bonjour {esc(first_name)}, vous avez {'reçu un premier message' if first else 'un nouveau message'} au sujet de <b>« {esc(title)} »</b>.",
            "Pour votre sécurité, lisez et répondez depuis BUILDSEN.",
        ],
        button=("Lire le message", link),
    ))


def shared_mail(ctx: Ctx, first_name: str, client: str, title: str, link: str) -> Mail:
    return _mail(f"{client} a partagé son WhatsApp avec vous", layout(
        ctx, title=f"{esc(client)} souhaite continuer avec vous",
        paras=[
            f"Bonjour {esc(first_name)}, {esc(client)} a partagé son numéro WhatsApp avec vous pour la demande <b>« {esc(title)} »</b>.",
            "Vous pouvez maintenant poursuivre l'échange sur WhatsApp. Le prix, le délai et le paiement se décident directement entre vous.",
        ],
        button=("Voir la conversation", link),
    ))


def plan_paid_mail(ctx: Ctx, first_name: str, plan: str, amount: str, until: str, link: str) -> Mail:
    return _mail(f"Paiement reçu : formule {plan} active", layout(
        ctx, title=f"Votre formule {esc(plan)} est active",
        paras=[f"Bonjour {esc(first_name)}, nous avons bien reçu votre paiement de <b>{esc(amount)}</b>.", f"Votre formule <b>{esc(plan)}</b> est active jusqu'au <b>{esc(until)}</b>."],
        button=("Voir mon abonnement", link),
    ))


def plan_expiring_mail(ctx: Ctx, first_name: str, plan: str, until: str, link: str, stage: str = "3") -> Mail:
    if stage == "end":
        return _mail(f"Votre formule {plan} est terminée", layout(
            ctx, title=f"Votre formule {esc(plan)} s'est terminée le {esc(until)}",
            paras=[f"Bonjour {esc(first_name)}, votre formule <b>{esc(plan)}</b> est arrivée à son terme.",
                   "Vous gardez vos avantages encore 2 jours. Renouvelez maintenant pour ne pas repasser en formule Gratuite (3 contacts par mois, nouvelles demandes signalées avec 48 h de retard)."],
            button=("Renouveler mon abonnement", link),
        ))
    left = "dans 7 jours" if stage == "7" else "dans 3 jours"
    return _mail(f"Votre formule {plan} se termine bientôt", layout(
        ctx, title=f"Votre formule {esc(plan)} se termine le {esc(until)}",
        paras=[f"Bonjour {esc(first_name)}, votre formule <b>{esc(plan)}</b> arrive à son terme {left}, le <b>{esc(until)}</b>.",
               "Sans renouvellement, vous repassez en formule Gratuite : 3 contacts par mois et nouvelles demandes signalées avec 48 h de retard."],
        button=("Renouveler mon abonnement", link),
    ))
