# ruff: noqa: E501  (catalogue de messages : une ligne par texte)
"""Langues de l'assistant WhatsApp : français, wolof et anglais. Chaque personne choisit sa langue au début de la discussion.

Le catalogue donne chaque message dans les trois langues, pour ne jamais mélanger. Le wolof est à faire relire par un locuteur natif
avant l'ouverture au public. Les valeurs enregistrées d'une demande (prestation, type de bâtiment, délai, commune) restent celles du
site ; seul leur affichage est traduit (voir `value_label`).
"""

from ...rules import BUILDING_TYPES, CATEGORIES, DEADLINES

LANGS = ("fr", "wo", "en")
DEFAULT = "fr"
NAMES = {"fr": "Français", "wo": "Wolof", "en": "English"}
# Langue du modèle de message (Meta ne propose pas le wolof : les modèles sont envoyés en français)
TEMPLATE_LANG = {"fr": "fr", "wo": "fr", "en": "en"}

_KEYWORDS = {
    "fr": ("francais", "français", "french", "fr", "1"),
    "wo": ("wolof", "wo", "2"),
    "en": ("english", "anglais", "en", "3"),
}


def pick(text: str) -> str | None:
    """Langue citée dans un message libre (« wolof », « english »…)."""
    t = " ".join((text or "").lower().split())
    return next((lg for lg, words in _KEYWORDS.items() if t in words), None)


C: dict[str, dict[str, str]] = {
    # --- langue ---------------------------------------------------------------------------------------------------
    "choose_lang": {k: "Choisissez votre langue 🌍\nTànnal sa làkk\nChoose your language" for k in LANGS},
    "lang_set": {"fr": "Langue : français ✅", "wo": "Làkk bi : wolof ✅", "en": "Language: English ✅"},
    # --- accueil et compte ----------------------------------------------------------------------------------------
    "welcome": {
        "fr": "Bienvenue sur la plateforme BUILDSEN ! 👋\nBUILDSEN met en relation les particuliers de Dakar avec des professionnels du bâtiment vérifiés. "
              "C'est gratuit pour vous.\n\nQui êtes-vous ?",
        "wo": "Dalal ak jamm ci plateforme BUILDSEN ! 👋\nBUILDSEN dafay jàppale nit ñi nekk Dakar ñu gis liggéeykat yu baax ci tabax, yu ñu seetlu. "
              "Du fey dara.\n\nXan nga ?",
        "en": "Welcome to BUILDSEN! 👋\nBUILDSEN connects people in Dakar with verified building professionals. It is free for you.\n\nWho are you?",
    },
    "btn_client": {"fr": "Décrire un projet", "wo": "Wax sa projet", "en": "Describe a project"},
    "btn_pro": {"fr": "Je suis professionnel", "wo": "Professionnel laa", "en": "I'm a professional"},
    "consent": {
        "fr": "Pour créer votre compte gratuit, j'enregistre votre numéro WhatsApp et les messages échangés ici avec BUILDSEN et les professionnels. "
              "Conditions et confidentialité : {url}\n\nAcceptez-vous ?",
        "wo": "Ngir sos sa compte (du fey), dinaa denc sa numéro WhatsApp ak xibaar yi nu jaxasoo fi ak BUILDSEN ak professionnel yi. "
              "Sàrt yi ak sutura : {url}\n\nBëgg nga ko ?",
        "en": "To create your free account, I save your WhatsApp number and the messages exchanged here with BUILDSEN and the professionals. "
              "Terms and privacy: {url}\n\nDo you accept?",
    },
    "btn_accept": {"fr": "J'accepte", "wo": "Waaw, bëgg naa", "en": "I accept"},
    "btn_decline": {"fr": "Pas maintenant", "wo": "Bi ngir tay déedéet", "en": "Not now"},
    "declined": {
        "fr": "D'accord. Écrivez « bonjour » quand vous voulez créer votre compte.",
        "wo": "Baax na. Bind « bonjour » bu la neexee ngir sos sa compte.",
        "en": "OK. Write \"hello\" whenever you want to create your account.",
    },
    "ask_name": {
        "fr": "Merci ! Quel est votre prénom et votre nom ? Écrivez-les, par exemple : Awa Diop.",
        "wo": "Jërëjëf ! Naka la tur ak sant bi di wax ? Bindal ko, ci misaal : Awa Diop.",
        "en": "Thank you! What are your first and last names? Write them, for example: Awa Diop.",
    },
    "name_retry": {
        "fr": "J'ai besoin de votre prénom et de votre nom, par exemple : Awa Diop.",
        "wo": "Soxla naa sa tur ak sa sant, ci misaal : Awa Diop.",
        "en": "I need your first and last names, for example: Awa Diop.",
    },
    "pro_signup": {
        "fr": "Les professionnels s'inscrivent sur le site, avec leurs justificatifs, puis reçoivent les demandes ici : {url}",
        "wo": "Professionnel yi dañuy bind seen tur ci site bi, ak seen papiye, ba noppi dinañu jot demande yi fii : {url}",
        "en": "Professionals sign up on the website with their documents, then receive requests here: {url}",
    },
    "welcome_user": {"fr": "Bienvenue {first} ! Votre compte est créé ✅", "wo": "Dalal ak jamm {first} ! Sa compte am na ✅", "en": "Welcome {first}! Your account is created ✅"},
    # --- demande --------------------------------------------------------------------------------------------------
    "intro_first": {
        "fr": "Je vais vous poser six questions. Répondez par écrit ou par note vocale, en français ou en wolof.",
        "wo": "Dinaa la laaj juróom-benni laaj. Tontul ci bind walla ci kàddu (note vocale), ci wolof walla ci français.",
        "en": "I will ask you six questions. Answer in writing in English, or by voice note in French or Wolof.",
    },
    "intro_again": {
        "fr": "C'est parti : six questions. Répondez par écrit ou par note vocale, en français ou en wolof.",
        "wo": "Nanu tàmbali : juróom-benni laaj. Tontul ci bind walla ci kàddu, ci wolof walla ci français.",
        "en": "Let's go: six questions. Answer in writing in English, or by voice note in French or Wolof.",
    },
    "voice_intro": {"fr": "", "wo": "Dinaa la laaj juróom-benni laaj ngir nga wax sa projet.", "en": ""},
    "voice_welcome": {"fr": "", "wo": "Dalal ak jamm ci plateforme BUILDSEN !", "en": ""},
    "t_category": {"fr": "Prestation", "wo": "Liggéey bi", "en": "Service"},
    "t_buildingType": {"fr": "Bâtiment", "wo": "Tabax bi", "en": "Building"},
    "t_description": {"fr": "Projet", "wo": "Projet bi", "en": "Project"},
    "t_commune": {"fr": "Commune", "wo": "Commune", "en": "Area"},
    "t_budget": {"fr": "Budget", "wo": "Xaalis bi", "en": "Budget"},
    "t_deadline": {"fr": "Délai", "wo": "Diir bi", "en": "Deadline"},
    "q_category": {
        "fr": "Quelle prestation recherchez-vous ? Des plans de maison, un devis avec métrés, du dessin technique, ou une étude de structure ?",
        "wo": "Ban liggéey ngay wut ? Plan u kër, devis ak métré, dessin technique, walla étude de structure ?",
        "en": "Which service do you need? House plans, a quote with quantities, technical drawing, or a structural study?",
    },
    "q_buildingType": {
        "fr": "Quel type de bâtiment voulez-vous construire ? Par exemple une maison, une maison à étage, un immeuble ou un local commercial.",
        "wo": "Ban xeetu tabax nga bëgg a def ? Ci misaal kër, kër bu am étage, immeuble walla local commercial.",
        "en": "What kind of building do you want to build? For example a house, a house with a floor, an apartment building or a shop.",
    },
    "q_description": {
        "fr": "Décrivez votre projet : le nombre de chambres, les pièces que vous voulez et ce que vous attendez.",
        "wo": "Waxal sa projet : ñaata néeg, néeg yi nga bëgg ak li nga yaakaar.",
        "en": "Describe your project: number of bedrooms, the rooms you want and what you expect.",
    },
    "q_commune": {"fr": "Dans quelle commune se trouve votre projet ?", "wo": "Ci ban commune la sa projet nekk ?", "en": "In which area is your project?"},
    "q_budget": {
        "fr": "Quel est votre budget pour cette prestation, en francs CFA ?",
        "wo": "Ñaata xaalis nga jagleel liggéey bii, ci franc CFA ?",
        "en": "What is your budget for this service, in CFA francs?",
    },
    "q_deadline": {
        "fr": "Dans quel délai voulez-vous recevoir le travail ?",
        "wo": "Ci ban diir nga bëgg a jot liggéey bi ?",
        "en": "By when do you want to receive the work?",
    },
    "retry": {
        "fr": "Je n'ai pas bien compris. Pouvez-vous répéter, s'il vous plaît ?",
        "wo": "Dégguma bu baax. Mën nga ko waxaat, su la neexee ?",
        "en": "I didn't quite understand. Could you say it again, please?",
    },
    "tip_description": {
        "fr": "Décrivez un peu plus votre projet (20 caractères minimum), par exemple : villa R+1, 4 chambres, salon, cuisine fermée.",
        "wo": "Waxal ab tuuti ci sa projet (20 araf ci kanam), ci misaal : villa R+1, 4 néeg, salon, cuisine.",
        "en": "Describe your project a bit more (at least 20 characters), for example: 2-storey villa, 4 bedrooms, living room, closed kitchen.",
    },
    "tip_budget": {
        "fr": "Écrivez le montant en chiffres, par exemple : 600000.",
        "wo": "Bindal xaalis bi ci xayma, ci misaal : 600000.",
        "en": "Write the amount in digits, for example: 600000.",
    },
    "tip_commune": {
        "fr": "Écrivez le nom de la commune, par exemple : Ouakam.",
        "wo": "Bindal tur u commune bi, ci misaal : Ouakam.",
        "en": "Write the name of the area, for example: Ouakam.",
    },
    "write_answer": {"fr": "Écrivez votre réponse.", "wo": "Bindal sa tontu.", "en": "Write your answer."},
    "choose_number": {"fr": "Choisissez en écrivant le numéro :", "wo": "Tànnal ci bind numéro bi :", "en": "Choose by writing the number:"},
    "ack": {"fr": "✓ {title} : {value}", "wo": "✓ {title} : {value}", "en": "✓ {title}: {value}"},
    "recap_head": {"fr": "Voici votre demande :", "wo": "Lii la sa demande :", "en": "Here is your request:"},
    "recap_tail": {"fr": "Tout est correct ?", "wo": "Lépp jaar na yoon ?", "en": "Is everything correct?"},
    "btn_confirm": {"fr": "Confirmer", "wo": "Dëggal", "en": "Confirm"},
    "btn_edit": {"fr": "Modifier", "wo": "Soppi", "en": "Edit"},
    "btn_cancel": {"fr": "Annuler", "wo": "Neenal", "en": "Cancel"},
    "pick_edit": {
        "fr": "Quelle partie voulez-vous modifier ? Écrivez le numéro :\n{lines}",
        "wo": "Ban pàrt nga bëgg a soppi ? Bindal numéro bi :\n{lines}",
        "en": "Which part do you want to change? Write the number:\n{lines}",
    },
    "pick_number": {"fr": "Écrivez un numéro entre 1 et {n}.", "wo": "Bindal numéro diggante 1 ak {n}.", "en": "Write a number between 1 and {n}."},
    "touch_confirm": {
        "fr": "Touchez Confirmer, Modifier ou Annuler.", "wo": "Bësal Dëggal, Soppi walla Neenal.", "en": "Tap Confirm, Edit or Cancel.",
    },
    "cancelled": {"fr": "Demande annulée. Que voulez-vous faire ?", "wo": "Demande bi neenu na. Lan nga bëgg a def ?", "en": "Request cancelled. What would you like to do?"},
    "published": {
        "fr": "✅ Votre demande {ref} est publiée.\nJusqu'à {max} professionnels vérifiés peuvent vous écrire. Vous recevrez leurs messages ici, sans que votre numéro soit montré.",
        "wo": "✅ Sa demande {ref} yeete nañu ko.\nJëm ci {max} professionnel yu ñu seetlu mën nañu la bind. Dinga jot seeni xibaar fii, te duñu gis sa numéro.",
        "en": "✅ Your request {ref} is published.\nUp to {max} verified professionals can write to you. You will receive their messages here, without your number being shown.",
    },
    # --- menu et aide ---------------------------------------------------------------------------------------------
    "menu_q": {"fr": "Que voulez-vous faire ?", "wo": "Lan nga bëgg a def ?", "en": "What would you like to do?"},
    "hello_user": {"fr": "Bonjour {first} !", "wo": "Salaam {first} !", "en": "Hello {first}!"},
    "not_understood": {"fr": "Je n'ai pas compris.", "wo": "Dégguma ko.", "en": "I didn't understand."},
    "btn_new": {"fr": "Nouvelle demande", "wo": "Demande bu bees", "en": "New request"},
    "btn_mine": {"fr": "Mes demandes", "wo": "Samay demande", "en": "My requests"},
    "btn_help": {"fr": "Aide", "wo": "Ndimbal", "en": "Help"},
    "btn_close": {"fr": "Clôturer", "wo": "Tëj", "en": "Close"},
    "btn_recent": {"fr": "Demandes récentes", "wo": "Demande yu mujj", "en": "Recent requests"},
    "btn_plan": {"fr": "Mon abonnement", "wo": "Sama abonnement", "en": "My plan"},
    "btn_menu": {"fr": "Menu", "wo": "Menu", "en": "Menu"},
    "help": {
        "fr": "Je vous aide à trouver un professionnel du bâtiment.\n• Écrivez « menu » pour revenir au début\n• « mes demandes » pour voir vos demandes\n"
              "• « abonnement » pour voir votre formule (professionnels)\n• « langue » pour changer de langue\n"
              "• « stop » pour ne plus recevoir de messages, « start » pour reprendre\nLe site : {url}",
        "wo": "Dinaa la jàppale ngir gis ab professionnel bu tabax.\n• Bind « menu » ngir dellusi ci njëkk\n• « samay demande » ngir gis sa demande yi\n"
              "• « abonnement » ngir gis sa formule (professionnel yi)\n• « làkk » ngir soppi làkk\n"
              "• « stop » ngir bañ a jot xibaar, « start » ngir ko tàmbalaat\nSite bi : {url}",
        "en": "I help you find a building professional.\n• Write \"menu\" to go back to the start\n• \"my requests\" to see your requests\n"
              "• \"plan\" to see your plan (professionals)\n• \"language\" to change language\n"
              "• \"stop\" to stop receiving messages, \"start\" to resume\nThe website: {url}",
    },
    "no_requests": {"fr": "Vous n'avez pas encore de demande.", "wo": "Amul demande leegi.", "en": "You don't have any request yet."},
    "your_requests": {"fr": "Vos demandes :", "wo": "Sa demande yi :", "en": "Your requests:"},
    "st_PUBLISHED": {"fr": "publiée", "wo": "yeete nañu ko", "en": "published"},
    "st_PENDING": {"fr": "en attente", "wo": "ñu koy xaar", "en": "pending"},
    "st_REJECTED": {"fr": "retirée", "wo": "ñu ko fábbi", "en": "removed"},
    "st_CLOSED": {"fr": "clôturée", "wo": "tëj nañu ko", "en": "closed"},
    "pro_count": {"fr": "{n}/{max} pro", "wo": "{n}/{max} pro", "en": "{n}/{max} pros"},
    "none_open": {"fr": "Vous n'avez aucune demande en cours.", "wo": "Amul demande bu jàll.", "en": "You have no open request."},
    "close_which": {
        "fr": "Quelle demande clôturer ? Écrivez le numéro (ou « menu » pour annuler) :\n{lines}",
        "wo": "Ban demande ngay tëj ? Bindal numéro bi (walla « menu » ngir neenal) :\n{lines}",
        "en": "Which request do you want to close? Write the number (or \"menu\" to cancel):\n{lines}",
    },
    "close_number": {
        "fr": "Écrivez le numéro de la demande, ou « menu » pour annuler.",
        "wo": "Bindal numéro demande bi, walla « menu » ngir neenal.",
        "en": "Write the request number, or \"menu\" to cancel.",
    },
    "closed_ok": {
        "fr": "La demande {ref} est clôturée ✅ Que voulez-vous faire ?", "wo": "Demande {ref} tëj nañu ko ✅ Lan nga bëgg a def ?",
        "en": "Request {ref} is closed ✅ What would you like to do?",
    },
    # --- messages avec l'autre personne ----------------------------------------------------------------------------
    "conv_gone": {"fr": "Cette conversation n'est plus disponible.", "wo": "Waxtaan woowu amatul.", "en": "This conversation is no longer available."},
    "conv_gone_menu": {
        "fr": "Cette conversation n'est plus disponible. Écrivez « menu ».", "wo": "Waxtaan woowu amatul. Bind « menu ».",
        "en": "This conversation is no longer available. Write \"menu\".",
    },
    "reply_prompt": {
        "fr": "Écrivez votre réponse ou enregistrez une note vocale : je la transmets.",
        "wo": "Bindal sa tontu walla jëlal note vocale : dinaa ko yónnee.",
        "en": "Write your reply or record a voice note: I will pass it on.",
    },
    "shared_ok": {
        "fr": "Votre numéro est partagé avec ce professionnel ✅ Il peut maintenant vous écrire directement.",
        "wo": "Yónnee nañu sa numéro professionnel bi ✅ Mën na la bind ci kaw.",
        "en": "Your number is shared with this professional ✅ They can now write to you directly.",
    },
    "sent": {"fr": "Envoyé ✅", "wo": "Yónnee nañu ko ✅", "en": "Sent ✅"},
    "heard_nothing": {
        "fr": "Je n'ai rien entendu. Réessayez ou écrivez votre message.", "wo": "Dégguma dara. Jéemaat walla bindal sa xibaar.",
        "en": "I heard nothing. Try again or write your message.",
    },
    "voice_heavy": {
        "fr": "Vous avez beaucoup utilisé la voix. Écrivez votre réponse, ou réessayez dans une heure.",
        "wo": "Yàgg nga jëfandikoo kàddu. Bindal sa tontu, walla jéemaat ci benn waxtu.",
        "en": "You have used voice a lot. Write your answer, or try again in an hour.",
    },
    "voice_failed": {
        "fr": "Je n'ai pas pu lire ce message vocal. Écrivez votre réponse.", "wo": "Mënuma dégg note vocale bi. Bindal sa tontu.",
        "en": "I couldn't read this voice note. Please write your answer.",
    },
    "voice_en": {
        "fr": "", "wo": "",
        "en": "Voice notes are understood in French and Wolof only. Please write your answer in English, or change language with \"language\".",
    },
    "voice_unavailable": {
        "fr": "L'assistant vocal est momentanément indisponible. Écrivez votre réponse.",
        "wo": "Assistant vocal bi amul fi léegi. Bindal sa tontu.",
        "en": "The voice assistant is temporarily unavailable. Please write your answer.",
    },
    "only_text_voice": {
        "fr": "Je comprends les messages écrits et les notes vocales. Écrivez ou enregistrez votre message.",
        "wo": "Dégg naa bind ak note vocale. Bindal walla jëlal sa xibaar.",
        "en": "I understand written messages and voice notes. Please write or record your message.",
    },
    # --- professionnel -------------------------------------------------------------------------------------------
    "pro_pending": {
        "fr": "Bonjour {first} ! Votre dossier est en cours de vérification. Vous recevrez les demandes ici dès qu'il sera validé.\nSuivre mon dossier : {url}",
        "wo": "Salaam {first} ! Seetlu nañu sa dossier. Dinga jot demande yi fii bu ñu ko validé.\nXool sa dossier : {url}",
        "en": "Hello {first}! Your file is being verified. You will receive requests here as soon as it is approved.\nFollow my file: {url}",
    },
    "btn_view": {"fr": "Voir", "wo": "Xool", "en": "View"},
    "btn_reply": {"fr": "Répondre", "wo": "Tontu", "en": "Reply"},
    "btn_skip": {"fr": "Ignorer", "wo": "Bàyyi", "en": "Skip"},
    "btn_share": {"fr": "Partager mon numéro", "wo": "Yónnee sama numéro", "en": "Share my number"},
    "none_for_pro": {
        "fr": "Aucune demande pour vos spécialités en ce moment. Je vous écris dès qu'il y en a une.",
        "wo": "Amul demande ci sa spécialité leegi. Dinaa la bind bu amee.",
        "en": "No request for your specialties right now. I will write to you as soon as there is one.",
    },
    "req_text": {
        "fr": "*{title}*\n{commune} · budget {budget} · délai : {deadline}\n\n{desc}\n\n{cat} · {n}/{max} professionnels ont déjà écrit.",
        "wo": "*{title}*\n{commune} · xaalis {budget} · diir : {deadline}\n\n{desc}\n\n{cat} · {n}/{max} professionnel bind nañu ko.",
        "en": "*{title}*\n{commune} · budget {budget} · deadline: {deadline}\n\n{desc}\n\n{cat} · {n}/{max} professionals have already written.",
    },
    "req_gone": {"fr": "Cette demande n'est plus disponible.", "wo": "Demande bii amatul.", "en": "This request is no longer available."},
    "req_full": {
        "fr": "Cette demande est complète : {max} professionnels ont déjà écrit au client.",
        "wo": "Demande bii fees na : {max} professionnel bind nañu client bi.", "en": "This request is full: {max} professionals have already written to the client.",
    },
    "skip_ack": {"fr": "D'accord, demande ignorée.", "wo": "Baax na, bàyyi nanu ko.", "en": "OK, request skipped."},
    "pro_intro": {
        "fr": "Présentez-vous au client : ce que vous proposez, un prix indicatif et votre délai. Écrivez ou enregistrez une note vocale.",
        "wo": "Misaal sa bopp client bi : li nga jox, ay jëfandikoo ak sa diir. Bindal walla jëlal note vocale.",
        "en": "Introduce yourself to the client: what you offer, an indicative price and your timeline. Write or record a voice note.",
    },
    "pro_sent": {
        "fr": "Envoyé au client ✅ {n} professionnel{s} sur {max} {a} déjà écrit.\nSes réponses arriveront ici.",
        "wo": "Yónnee nañu ko client bi ✅ {n} professionnel ci {max} bind nañu ko.\nSeeni tontu dinañu dikk fii.",
        "en": "Sent to the client ✅ {n} of {max} professionals {a} already written.\nTheir replies will arrive here.",
    },
    "a_one": {"fr": "a", "wo": "", "en": "has"},
    "a_many": {"fr": "ont", "wo": "", "en": "have"},
    # --- système -------------------------------------------------------------------------------------------------
    "maintenance": {
        "fr": "L'assistant est momentanément indisponible. Vous pouvez utiliser le site : {url}",
        "wo": "Assistant bi amul fi leegi. Mën nga jëfandikoo site bi : {url}",
        "en": "The assistant is temporarily unavailable. You can use the website: {url}",
    },
    "stop_ok": {
        "fr": "C'est noté : vous ne recevrez plus d'alertes. Écrivez « start » pour les reprendre.",
        "wo": "Baax na : doo jot alerte. Bind « start » ngir ko tàmbalaat.", "en": "Noted: you will no longer receive alerts. Write \"start\" to resume.",
    },
    "start_ok": {"fr": "Les alertes sont reprises ✅", "wo": "Alerte yi tàmbali nañu ✅", "en": "Alerts are back on ✅"},
    "suspended": {
        "fr": "Ce compte est suspendu. Contactez l'équipe BUILDSEN depuis le site.", "wo": "Compte bi tëj nañu ko. Jokkool ak ekip BUILDSEN ci site bi.",
        "en": "This account is suspended. Contact the BUILDSEN team from the website.",
    },
    "error": {
        "fr": "Désolé, une erreur est survenue. Réessayez dans un instant, ou écrivez « menu ».",
        "wo": "Baal ma, am na njuumte. Jéemaat ci ab diir, walla bind « menu ».", "en": "Sorry, something went wrong. Try again in a moment, or write \"menu\".",
    },
    # --- formules ------------------------------------------------------------------------------------------------
    "limit_msg": {
        "fr": "Vous avez utilisé vos {limit} contacts du mois (formule {plan}). Passez à une formule supérieure pour continuer à écrire aux clients.",
        "wo": "Jëfandikoo nga sa {limit} contact ci weer wi (formule {plan}). Wàll ci formule bu gën a mag ngir kontinuwe ci bind client yi.",
        "en": "You have used your {limit} contacts for this month ({plan} plan). Move to a higher plan to keep writing to clients.",
    },
    "limit_resend": {
        "fr": "Après le paiement, renvoyez simplement votre message.", "wo": "Bu fey jeexee, yónneeti sa xibaar.",
        "en": "After paying, simply send your message again.",
    },
    "limit_retap": {
        "fr": "Après le paiement, touchez de nouveau « Répondre » sous la demande.", "wo": "Bu fey jeexee, bësaatal « Tontu » ci suuf demande bi.",
        "en": "After paying, tap \"Reply\" again under the request.",
    },
    "plans_off": {
        "fr": "Les formules ne sont pas encore ouvertes : pour l'instant, tout est gratuit et sans limite 🎉",
        "wo": "Formule yi ubbiwuñu : leegi lépp du fey te amul dayo 🎉", "en": "Plans are not open yet: for now everything is free and unlimited 🎉",
    },
    "plan_line": {"fr": "Formule *{plan}*", "wo": "Formule *{plan}*", "en": "*{plan}* plan"},
    "plan_until": {"fr": ", active jusqu'au {date}", "wo": ", ba {date}", "en": ", active until {date}"},
    "plan_used": {"fr": "Contacts ce mois-ci : {used}/{limit}", "wo": "Contact yi ci weer wii : {used}/{limit}", "en": "Contacts this month: {used}/{limit}"},
    "plan_unlimited": {"fr": "Contacts : illimités", "wo": "Contact : amul dayo", "en": "Contacts: unlimited"},
    "plan_upgrade": {
        "fr": "Pour écrire à plus de clients, passez à une formule supérieure :", "wo": "Ngir bind ay client yu ëpp, wàll ci formule bu gën a mag :",
        "en": "To write to more clients, move to a higher plan:",
    },
    "plan_row": {
        "fr": "• {plan} : {price} par mois, {contacts}", "wo": "• {plan} : {price} ci weer, {contacts}", "en": "• {plan}: {price} per month, {contacts}",
    },
    "contacts_n": {"fr": "{n} contacts", "wo": "{n} contact", "en": "{n} contacts"},
    "contacts_all": {"fr": "contacts illimités", "wo": "contact yu amul dayo", "en": "unlimited contacts"},
    "btn_upgrade": {"fr": "Passer à {plan}", "wo": "Wàll ci {plan}", "en": "Go {plan}"},
    "checkout_link": {
        "fr": "Formule *{plan}* : {amount} pour 1 mois.\nPayez avec {method} sur ce lien {note} :\n{url}\n\nUne fois payé, touchez « J'ai payé » : votre formule s'active dès que nous recevons le paiement, en général en quelques minutes.",
        "wo": "Formule *{plan}* : {amount} ngir 1 weer.\nFeyal ak {method} ci lien bi {note} :\n{url}\n\nBu fey bi jeexee, ñaan « Fey naa » : formule bi dina tàmbali bu fey bi dikkee, ci kanam bu gàtt.",
        "en": "*{plan}* plan: {amount} for 1 month.\nPay with {method} on this link {note}:\n{url}\n\nOnce paid, tap \"I paid\": your plan starts as soon as we receive the payment, usually within minutes.",
    },
    "note_prefilled": {"fr": "(le montant est déjà rempli)", "wo": "(xaalis bi mu nekk na ba noppi)", "en": "(the amount is already filled in)"},
    "note_enter_amount": {"fr": "(saisissez exactement {amount})", "wo": "(bind {amount} rekk)", "en": "(enter exactly {amount})"},
    "pick_method": {"fr": "Comment voulez-vous payer ?", "wo": "Naka ngay fey ?", "en": "How would you like to pay?"},
    "btn_paid": {"fr": "J'ai payé", "wo": "Fey naa", "en": "I paid"},
    "claimed": {
        "fr": "Merci ! Votre formule *{plan}* est active. Nous confirmons votre paiement Wave, vous n'avez rien à faire.",
        "wo": "Jërëjëf ! Formule *{plan}* bi mu tàmbali na. Dinanu seetlu fey bi ci Wave, amul dara ngay def.",
        "en": "Thank you! Your *{plan}* plan is active. We are confirming your Wave payment, nothing more to do.",
    },
    "claimed_wait": {
        "fr": "Merci ! Nous vérifions votre paiement Wave : votre formule sera activée dès sa confirmation.",
        "wo": "Jërëjëf ! Dinanu seetlu fey bi ci Wave : formule bi dina tàmbali bu leegi.",
        "en": "Thank you! We are checking your Wave payment: your plan will be activated as soon as it is confirmed.",
    },
    "checkout": {
        "fr": "Formule *{plan}* : {amount} pour 1 mois.\nPayez par Wave ou Orange Money sur ce lien sécurisé :\n{url}\n\nJe vous préviens ici dès que le paiement est confirmé.",
        "wo": "Formule *{plan}* : {amount} ngir 1 weer.\nFeyal ak Wave walla Orange Money ci lien bi :\n{url}\n\nDinaa la xamal fii bu fey bi jeexee.",
        "en": "*{plan}* plan: {amount} for 1 month.\nPay with Wave or Orange Money on this secure link:\n{url}\n\nI will let you know here as soon as the payment is confirmed.",
    },
    "paid": {
        "fr": "Paiement reçu ✅ {amount}.\nVotre formule {plan} est active jusqu'au {date} ({contacts}).",
        "wo": "Fey bi dugg na ✅ {amount}.\nSa formule {plan} dafay doon ba {date} ({contacts}).",
        "en": "Payment received ✅ {amount}.\nYour {plan} plan is active until {date} ({contacts}).",
    },
    "renew_soon": {
        "fr": "Votre formule {plan} se termine le {date}. Touchez « Renouveler », ou faites-le depuis le site : {url}",
        "wo": "Sa formule {plan} dina jeex ci {date}. Ñaan « Yeesalaat », walla jëfandikoo site bi : {url}",
        "en": "Your {plan} plan ends on {date}. Tap \"Renew\", or do it on the website: {url}",
    },
    "renew_ended": {
        "fr": "Votre formule {plan} s'est terminée le {date}. Vous gardez vos avantages 2 jours : touchez « Renouveler » pour ne pas repasser en formule Gratuite. Ou depuis le site : {url}",
        "wo": "Sa formule {plan} jeex na ci {date}. Mu des ñaari fan : ñaan « Yeesalaat » ngir bul dellusi ci formule Gratuite. Walla ci site bi : {url}",
        "en": "Your {plan} plan ended on {date}. You keep your benefits for 2 more days: tap \"Renew\" to avoid going back to the Free plan. Or on the website: {url}",
    },
    "btn_renew": {"fr": "Renouveler", "wo": "Yeesalaat", "en": "Renew"},
    "contacts_month": {"fr": "{n} contacts par mois", "wo": "{n} contact ci weer", "en": "{n} contacts per month"},
    # --- alertes envoyées par la plateforme -----------------------------------------------------------------------
    "alert_body": {
        "fr": "Nouvelle demande à {commune} : {service}.\nBudget : {budget}.\nTouchez Répondre pour écrire au client.",
        "wo": "Demande bu bees ci {commune} : {service}.\nXaalis : {budget}.\nBësal Tontu ngir bind client bi.",
        "en": "New request in {commune}: {service}.\nBudget: {budget}.\nTap Reply to write to the client.",
    },
    "shared_number": {
        "fr": "{who} a partagé son numéro avec vous : {phone}\nVous pouvez continuer directement sur WhatsApp : {link}",
        "wo": "{who} yónnee na la sa numéro : {phone}\nMën nga kontinuwe ci WhatsApp : {link}",
        "en": "{who} shared their number with you: {phone}\nYou can continue directly on WhatsApp: {link}",
    },
    "shared_tpl": {"fr": "Numéro partagé : {phone}", "wo": "Numéro bi yónnee nañu ko : {phone}", "en": "Number shared: {phone}"},
    "login_code": {
        "fr": "{code} est votre code de connexion BUILDSEN. Il expire dans 10 minutes. Ne le partagez avec personne.",
        "wo": "{code} mooy sa code ngir dugg ci BUILDSEN. Dina jeex ci 10 simili. Bu ko jox kenn.",
        "en": "{code} is your BUILDSEN login code. It expires in 10 minutes. Do not share it with anyone.",
    },
}

# Valeurs du site affichées dans la langue de la personne (la valeur enregistrée reste celle du site)
_VALUES: dict[str, dict[str, str]] = {
    "category": {
        "plans": {"wo": "Plan ak conception", "en": "Plans and design"},
        "metres": {"wo": "Devis ak métré", "en": "Quotes and quantities"},
        "dao": {"wo": "DAO ak dessin technique", "en": "CAD and technical drawing"},
        "etudes": {"wo": "Études techniques", "en": "Technical studies"},
    },
    "buildingType": {
        BUILDING_TYPES[0]: {"wo": "Kër (ci suuf rekk)", "en": "House (ground floor)"},
        BUILDING_TYPES[1]: {"wo": "Kër (R+1 walla ëpp)", "en": "House (R+1 or more)"},
        BUILDING_TYPES[2]: {"wo": "Immeuble", "en": "Apartment building"},
        BUILDING_TYPES[3]: {"wo": "Local commercial walla bureau", "en": "Shop or offices"},
        BUILDING_TYPES[4]: {"wo": "Extension walla surélévation", "en": "Extension or added floor"},
        BUILDING_TYPES[5]: {"wo": "Aménagement ci biir", "en": "Interior fit-out"},
        BUILDING_TYPES[6]: {"wo": "Yeneen", "en": "Other"},
    },
    "deadline": {
        DEADLINES[0]: {"wo": "Ci gaaw", "en": "As soon as possible"},
        DEADLINES[1]: {"wo": "Ci 2 ayi-bés", "en": "Within 2 weeks"},
        DEADLINES[2]: {"wo": "Ci 1 weer", "en": "Within 1 month"},
        DEADLINES[3]: {"wo": "Ci 2 weer", "en": "Within 2 months"},
        DEADLINES[4]: {"wo": "Amul presse", "en": "No rush"},
    },
}


def lang_or_default(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT


def tr(lang: str | None, key: str, **kw) -> str:
    row = C[key]
    text = row.get(lang_or_default(lang)) or row[DEFAULT]
    return text.format(**kw) if kw else text.replace("{{", "{")


def value_label(lang: str | None, field: str, value: str) -> str:
    """Valeur du formulaire du site, affichée dans la langue de la personne (inchangée si pas de traduction)."""
    lg = lang_or_default(lang)
    if lg == "fr":
        return CATEGORIES.get(value, {}).get("name", value) if field == "category" else value
    return _VALUES.get(field, {}).get(value, {}).get(lg) or (CATEGORIES.get(value, {}).get("name", value) if field == "category" else value)


# Messages d'erreur du métier (communs au site, écrits en français) : traduits pour l'assistant, sinon laissés tels quels
_ERRORS: dict[str, tuple[str, str]] = {
    "Le dépôt de demandes n'est pas encore ouvert.": ("Dépôt demande yi ubbiwul léegi.", "Requests are not open yet."),
    "Vous avez déposé beaucoup de demandes aujourd'hui. Réessayez demain ou contactez-nous.": (
        "Yaa ngi yeete ay demande yu bari tay. Jéemaatal ëllëg walla jokkool ak nu.", "You have posted many requests today. Try again tomorrow or contact us."),
    "Demande introuvable.": ("Gisuma demande bi.", "Request not found."),
    "Cette demande est clôturée : la conversation est en lecture seule.": (
        "Demande bi tëj nañu ko : waxtaan wi mën nga ko jàng rekk.", "This request is closed: the conversation is read-only."),
    "Écrivez un message.": ("Bindal benn xibaar.", "Write a message."),
    "Message : 2 000 caractères maximum.": ("Xibaar : 2 000 araf rekk.", "Message: 2,000 characters maximum."),
    "Vous envoyez beaucoup de messages. Patientez un peu.": ("Yaa ngi yónnee ay xibaar yu bari. Muñal ab diir.", "You are sending many messages. Please wait a moment."),
    "Les demandes des particuliers ne sont pas encore ouvertes.": ("Demande yu nit ñi ubbiwuñu léegi.", "Requests from individuals are not open yet."),
    "Votre profil doit être vérifié pour consulter les demandes et écrire aux clients.": (
        "Sa profil dafa war a seetlu ngir gis demande yi te bind client yi.", "Your profile must be verified to view requests and write to clients."),
    "Écrivez un premier message (20 caractères minimum) : présentez-vous, ce que vous proposez et votre délai.": (
        "Bindal benn xibaar bu njëkk (20 araf ci kanam) : misaal sa bopp, li nga jox ak sa diir.",
        "Write a first message (at least 20 characters): introduce yourself, what you offer and your timeline."),
    "Cette demande n'est plus disponible.": ("Demande bii amatul.", "This request is no longer available."),
    "Vous avez ouvert beaucoup de conversations aujourd'hui. Réessayez demain.": (
        "Ubbi nga ay waxtaan yu bari tay. Jéemaatal ëllëg.", "You have opened many conversations today. Try again tomorrow."),
    "L'envoi n'a pas abouti, la connexion est lente. Votre message est conservé : réessayez.": (
        "Yónnee bi jàllul, réseau bi dafa yomb. Jéemaatal.", "Sending failed, the connection is slow. Please try again."),
    "Cette demande est clôturée.": ("Demande bi tëj nañu ko.", "This request is closed."),
    "Conversation introuvable.": ("Gisuma waxtaan wi.", "Conversation not found."),
    "Les formules payantes ne sont pas encore ouvertes.": ("Formule yu fey ubbiwuñu léegi.", "Paid plans are not open yet."),
    "Votre profil doit être vérifié avant de choisir une formule.": (
        "Sa profil dafa war a seetlu balaa nga tànn formule.", "Your profile must be verified before choosing a plan."),
    "Vous avez déjà une formule supérieure.": ("Am nga ab formule bu gën a mag.", "You already have a higher plan."),
    "Trop de tentatives. Réessayez dans un moment.": ("Jéem nga lool. Jéemaatal ci ab diir.", "Too many attempts. Try again in a moment."),
    "Le paiement est momentanément indisponible. Réessayez dans un instant.": (
        "Fey bi amul fi léegi. Jéemaatal ci ab diir.", "Payment is temporarily unavailable. Try again in a moment."),
}


def tr_error(lang: str | None, message: str) -> str:
    """Message d'erreur du métier dans la langue de la personne (inchangé s'il n'a pas de traduction)."""
    if message.startswith("Cette demande est complète"):
        n = "".join(c for c in message if c.isdigit()) or "5"
        return {"wo": f"Demande bii fees na : {n} professionnel bind nañu client bi.", "en": f"This request is full: {n} professionals have already written to the client."}.get(
            lang_or_default(lang), message)
    pair = _ERRORS.get(message)
    if not pair:
        return message
    return {"wo": pair[0], "en": pair[1]}.get(lang_or_default(lang), message)
