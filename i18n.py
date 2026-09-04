"""Internationalisation AubePilot, FR + EN.

Approche minimaliste, sans dependance externe :
- table de traductions plate `_T = {key: {'fr': ..., 'en': ...}}`
- `resolve_lang()` lit le cookie `aube_lang` puis l'en-tete Accept-Language
- `t(key, **kwargs)` avec interpolation {var}
- exposes `t` et `lang` comme globals Jinja via le context_processor d'app.py

Les emails sont bilingues empiles (FR puis EN) pour qu'un destinataire
recoive toujours sa langue, peu importe sa preference cote serveur.
"""
from typing import Optional

from flask import request

from config import PILOT_SHARE_PCT, PLATFORM_FEE_PCT

# Variables toujours disponibles dans les traductions (pas de "30 %" en dur) :
#   {fee} = commission plateforme, {pilot_share} = part reversee au pilote.
_FMT_DEFAULTS = {"fee": int(PLATFORM_FEE_PCT), "pilot_share": int(PILOT_SHARE_PCT)}

SUPPORTED = ("fr", "en")
DEFAULT = "fr"
COOKIE = "aube_lang"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 an


def resolve_lang() -> str:
    """Cookie -> Accept-Language -> defaut."""
    try:
        cookie = request.cookies.get(COOKIE)
    except RuntimeError:
        return DEFAULT
    if cookie in SUPPORTED:
        return cookie
    try:
        return request.accept_languages.best_match(SUPPORTED) or DEFAULT
    except Exception:
        return DEFAULT


def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Traduit `key` en `lang`. Si la cle manque, retourne la cle telle quelle
    (visible pour repérer les manques)."""
    if lang not in SUPPORTED:
        lang = DEFAULT
    entry = _T.get(key)
    if not entry:
        return key
    val = entry.get(lang) or entry.get(DEFAULT) or key
    if "{" in val:
        try:
            val = val.format(**{**_FMT_DEFAULTS, **kwargs})
        except (KeyError, IndexError, ValueError):
            pass
    return val


# ---------------------------------------------------------------------------
# Table de traductions
# ---------------------------------------------------------------------------

_T = {
    # ---- Navigation
    "nav.home":         {"fr": "Accueil",            "en": "Home"},
    "nav.publish_short":{"fr": "Publier",            "en": "Post"},
    "nav.pilots":       {"fr": "Pilotes",            "en": "Pilots"},
    "nav.missions":     {"fr": "Missions",           "en": "Missions"},
    "nav.publish":      {"fr": "Publier une mission","en": "Publish a mission"},
    "nav.dashboard":    {"fr": "Espace",             "en": "Dashboard"},
    "nav.account":      {"fr": "Mon compte",         "en": "My account"},
    "nav.logout":       {"fr": "Quitter",            "en": "Sign out"},
    "nav.login":        {"fr": "Connexion",          "en": "Sign in"},
    "nav.register":     {"fr": "Rejoindre",          "en": "Join"},
    "nav.brand_sub":    {"fr": "Réseau pilote · FR", "en": "Pilot network · EN"},
    "nav.theme_dark":   {"fr": "Nuit",               "en": "Night"},
    "nav.theme_light":  {"fr": "Aube",               "en": "Dawn"},
    "nav.zone":         {"fr": "Ma zone",            "en": "My zone"},
    "nav.skip":         {"fr": "Aller au contenu",   "en": "Skip to content"},

    # ---- Footer
    "footer.tagline":     {"fr": "Une marque de L'Aube Étoilée · Auth partagée @aubemail.com",
                           "en": "A brand of L'Aube Étoilée · Shared @aubemail.com auth"},
    "footer.cities":      {"fr": "Édité depuis Montréal",
                           "en": "Crafted in Montréal"},

    # ---- Landing
    "home.eyebrow":     {"fr": "Réseau pilote international, depuis 2026",
                         "en": "International pilot network, since 2026"},
    "home.h1_a":        {"fr": "Le ciel n'a pas de frontière.",
                         "en": "The sky knows no border."},
    "home.h1_b":        {"fr": "Vos pilotes",       "en": "Your pilots"},
    "home.h1_b_em":     {"fr": "non plus.",         "en": "either."},
    "home.lead":        {"fr": "AubePilot connecte des pilotes certifiés (DGAC, EASA, Transport Canada, FAA, ASECNA, CAA UK, Rosaviatsia, CAAC, JCAB, CASA et les autres) avec les commanditaires qui en ont besoin. Tournage de mariage, cartographie RTK d'un chantier minier, inspection thermique d'une éolienne. Sans intermédiaire, partout dans le monde.",
                         "en": "AubePilot connects certified drone pilots (DGAC, EASA, Transport Canada, FAA, CAA UK, Rosaviatsia, CAAC, JCAB, CASA, ASECNA and others) with the people who need them. Wedding shoots, RTK mapping of a mining site, thermal inspection of a wind turbine. No middlemen, anywhere in the world."},
    "home.cta_publish": {"fr": "Publier une mission",   "en": "Publish a mission"},
    "home.cta_pilot":   {"fr": "Inscrire mon brevet de pilote →",
                         "en": "Register my pilot license →"},
    "home.search_title":  {"fr": "Trouver un pilote",   "en": "Find a pilot"},
    "home.search_country":{"fr": "Pays",                "en": "Country"},
    "home.search_country_any": {"fr": "Toutes destinations", "en": "All destinations"},
    "home.search_near":   {"fr": "Code postal, adresse ou ville",
                           "en": "Postal code, address or city"},
    "home.search_near_ph":{"fr": "H2X 1Y4 · 75011 · G5Y · Casablanca",
                           "en": "H2X 1Y4 · 75011 · SW1A 1AA · Toronto"},
    "home.search_city":   {"fr": "Ville ou repère",     "en": "City or landmark"},
    "home.search_city_ph":{"fr": "Casablanca · Lyon · Montréal",
                           "en": "Casablanca · Lyon · Montréal"},
    "home.search_type":   {"fr": "Spécialité",           "en": "Specialty"},
    "home.search_type_any":{"fr": "Toutes spécialités",  "en": "All specialties"},
    "home.search_btn":    {"fr": "Lancer la recherche",  "en": "Search"},

    "home.notam.pilots":   {"fr": "Pilotes en piste",     "en": "Pilots on tarmac"},
    "home.notam.missions": {"fr": "Missions ouvertes",    "en": "Open missions"},
    "home.notam.countries":{"fr": "Pays couverts",         "en": "Countries served"},
    "home.notam.completed":{"fr": "Vols livrés",           "en": "Flights delivered"},

    "home.nearby.title":   {"fr": "Trouvez ce qui vole près de vous.",
                             "en": "Find what's flying near you."},
    "home.nearby.lead":    {"fr": "Avec votre permission, on identifie les pilotes et les missions dans un rayon de 100 km. Aucune donnée stockée côté plateforme, tout reste dans votre navigateur.",
                             "en": "With your permission, we identify pilots and missions within a 100 km radius. Nothing stored server-side, everything stays in your browser."},
    "home.nearby.cta":     {"fr": "⌖ Activer ma zone",      "en": "⌖ Enable my zone"},
    "home.nearby.alt":     {"fr": "Voir tous les pilotes", "en": "Browse all pilots"},

    "home.steps.eyebrow":  {"fr": "Comment on opère",       "en": "How it flies"},
    "home.steps.h":        {"fr": "Un canevas, quatre escales.",
                             "en": "One process, four steps."},
    "home.steps.lead":     {"fr": "De la dépose d'une mission à la livraison du média, le parcours est cousu pour que vous gardiez la main, qu'on soit client pressé ou pilote en escale.",
                             "en": "From posting a mission to delivering the footage, the journey is built so you stay in control, whether you're a client in a hurry or a pilot between flights."},

    "home.step1.h": {"fr": "Vous décrivez le besoin.",
                     "en": "You describe what you need."},
    "home.step1.p": {"fr": "Type, lieu, fenêtre de tir, contraintes (RTK, thermique, RC pro). Cinq champs, deux minutes.",
                     "en": "Type, location, window, constraints (RTK, thermal, liability). Five fields, two minutes."},
    "home.step2.h": {"fr": "Les pilotes locaux soumettent.",
                     "en": "Local pilots submit offers."},
    "home.step2.p": {"fr": "Tarif chiffré, délai, message personnalisé. Vous comparez à la lumière de leurs avis et brevets.",
                     "en": "Quoted price, lead time, personal message. You compare against ratings and licenses."},
    "home.step3.h": {"fr": "Vous validez. La mission décolle.",
                     "en": "You confirm. The mission takes off."},
    "home.step3.p": {"fr": "Acceptation = réservation. Messagerie privée pour caler logistique, NOTAM et accès au site.",
                     "en": "Accept = booked. Private messaging to handle logistics, NOTAMs and site access."},
    "home.step4.h": {"fr": "Livraison, paiement, avis.",
                     "en": "Delivery, payment, review."},
    "home.step4.p": {"fr": "Le pilote livre les rushs. Vous validez, l'argent file, chacun laisse une note. Commission plateforme : {fee} %.",
                     "en": "The pilot delivers the footage. You confirm, the funds clear, both leave a review. Platform fee: {fee}%."},

    "home.featured.eyebrow": {"fr": "En piste cette semaine", "en": "On tarmac this week"},
    "home.featured.h":       {"fr": "Pilotes que l'on suit.",  "en": "Pilots we follow."},
    "home.featured.all":     {"fr": "Voir tous les pilotes →", "en": "Browse all pilots →"},

    "home.map.eyebrow":      {"fr": "Carte interactive", "en": "Interactive map"},
    "home.map.h":            {"fr": "Les pilotes et les missions, sur la carte.", "en": "Pilots and missions, on the map."},
    "home.map.lead":         {"fr": "Zoomez, déplacez, cliquez un marqueur pour ouvrir le profil ou la mission. Bleu : pilotes pro · turquoise : récréatifs · vert : écoles · orange : missions ouvertes. Les positions des pilotes sont floutées d'environ 10 km. Puis cliquez un pays pour filtrer.",
                               "en": "Zoom, pan, click a marker to open the profile or the mission. Blue: pro pilots · teal: recreational · green: schools · orange: open missions. Pilot positions are blurred by about 10 km. Then click a country to filter."},
    "home.map.all":          {"fr": "Annuaire complet →",  "en": "Full directory →"},

    "home.manifesto.eyebrow":{"fr": "Manifeste", "en": "Manifesto"},
    "home.manifesto.h":      {"fr": "Un pilote n'est pas un freelance jetable.",
                               "en": "A pilot is not a disposable freelancer."},
    "home.manifesto.p1":     {"fr": "Sur AubePilot, votre brevet, votre flotte et vos heures de vol comptent autant que votre tarif. Les autorités (DGAC, EASA, TC, FAA, ASECNA) sont mises en avant ; les amateurs aussi, à leur juste place.",
                               "en": "On AubePilot, your license, your fleet and your flight hours count as much as your price. Authorities (DGAC, EASA, TC, FAA, ASECNA) are highlighted; amateurs too, in their right place."},
    "home.manifesto.p2":     {"fr": "Et nous ne facturons que sur les missions livrées. Pas d'abonnement, pas de pop-up.",
                               "en": "And we only bill on delivered missions. No subscription, no pop-ups."},

    "home.dep.eyebrow": {"fr": "Tableau des départs",      "en": "Departure board"},
    "home.dep.h":       {"fr": "Missions qui cherchent un pilote.",
                         "en": "Missions looking for a pilot."},
    "home.dep.all":     {"fr": "Voir toutes les missions →",
                         "en": "Browse all missions →"},

    # ---- Landing : pourquoi AubePilot (ce qu'un simple annuaire n'a pas)
    "home.why.eyebrow": {"fr": "Pourquoi AubePilot", "en": "Why AubePilot"},
    "home.why.h":       {"fr": "Plus qu'un annuaire : la mission de bout en bout.",
                         "en": "More than a directory: the whole mission, end to end."},
    "home.why.lead":    {"fr": "Trouver un pilote sur une carte, c'est le début. Ici vous comparez des devis, payez sous séquestre, recevez les fichiers et laissez un avis — au même endroit, en français et en anglais, partout dans le monde.",
                         "en": "Finding a pilot on a map is just the start. Here you compare quotes, pay into escrow, receive the files and leave a review — in one place, in French and English, anywhere in the world."},
    "home.why.1.h": {"fr": "Brevets vérifiés, pas seulement déclarés.",
                     "en": "Licences verified, not just declared."},
    "home.why.1.p": {"fr": "Le pilote téléverse son justificatif (DGAC, EASA, Transport Canada, FAA, ASECNA…), notre équipe le contrôle. Filtrez pour ne voir que les profils vérifiés et assurés.",
                     "en": "The pilot uploads proof (DGAC, EASA, Transport Canada, FAA, ASECNA…) and our team checks it. Filter to see only verified, insured profiles."},
    "home.why.2.h": {"fr": "Recherche par code postal, partout.",
                     "en": "Postal code search, everywhere."},
    "home.why.2.p": {"fr": "Un code postal, une adresse ou une ville : les pilotes et missions s'affichent du plus proche au plus loin, sur la carte et dans la liste, avec le rayon de votre choix.",
                     "en": "A postal code, an address or a city: pilots and missions show nearest first, on the map and in the list, within the radius you choose."},
    "home.why.3.h": {"fr": "Paiement sous séquestre.",
                     "en": "Escrow payment."},
    "home.why.3.p": {"fr": "Vous payez à la réservation, les fonds restent bloqués jusqu'à votre validation de la livraison. Litige, médiation et remboursement gérés sur la plateforme.",
                     "en": "You pay at booking; funds stay locked until you validate the delivery. Disputes, mediation and refunds are handled on the platform."},
    "home.why.4.h": {"fr": "Des devis comparables, exportables en PDF.",
                     "en": "Comparable quotes, exportable as PDF."},
    "home.why.4.p": {"fr": "Chaque pilote détaille prestation, livrables, délai et conditions. Vous refusez, il révise ; vous acceptez, le devis devient un PDF à sa marque.",
                     "en": "Each pilot details the service, deliverables, lead time and terms. Decline and they revise; accept and the quote becomes a branded PDF."},
    "home.why.5.h": {"fr": "Livrables et avis au même endroit.",
                     "en": "Deliverables and reviews in one place."},
    "home.why.5.p": {"fr": "Photos, vidéo 4K, RAW, orthophotos, nuages de points : jusqu'à 1 Go par fichier, directement sur la réservation. Les avis ne viennent que de missions réellement payées.",
                     "en": "Photos, 4K video, RAW, orthophotos, point clouds: up to 1 GB per file, straight on the booking. Reviews only come from missions that were actually paid."},
    "home.why.6.h": {"fr": "Bilingue, mobile, sans traceur.",
                     "en": "Bilingual, mobile, tracker-free."},
    "home.why.6.p": {"fr": "Français et anglais, thème jour et nuit, application Android et écran d'accueil iPhone. Aucune publicité, positions floutées, identités révélées seulement après paiement.",
                     "en": "French and English, day and night themes, Android app and iPhone home screen. No ads, blurred positions, identities revealed only after payment."},

    # ---- Landing : rejoindre le reseau
    "home.join.eyebrow":    {"fr": "Rejoindre le réseau", "en": "Join the network"},
    "home.join.h":          {"fr": "Vous êtes client, pilote — ou les deux ?",
                             "en": "Are you a client, a pilot — or both?"},
    "home.join.lead":       {"fr": "Un seul compte, sans abonnement. Vous apparaissez dans les recherches et sur la carte dès que votre profil est complet ; le badge « vérifié » arrive après contrôle de votre brevet.",
                             "en": "One account, no subscription. You appear in searches and on the map as soon as your profile is complete; the “verified” badge comes once your licence is checked."},
    "home.join.client.tag": {"fr": "Client · entreprise", "en": "Client · business"},
    "home.join.client.h":   {"fr": "Je cherche un pilote", "en": "I'm looking for a pilot"},
    "home.join.client.p":   {"fr": "Publiez une mission ou réservez un forfait, comparez les devis, payez sous séquestre, récupérez vos fichiers.",
                             "en": "Post a mission or book a package, compare quotes, pay into escrow, collect your files."},
    "home.join.pilot.tag":  {"fr": "Pilote certifié", "en": "Certified pilot"},
    "home.join.pilot.h":    {"fr": "Je suis pilote", "en": "I'm a pilot"},
    "home.join.pilot.p":    {"fr": "Brevets, flotte, portfolio, forfaits à prix fixe, alertes missions dans votre rayon, paiements automatiques.",
                             "en": "Licences, fleet, portfolio, fixed-price packages, mission alerts within your range, automatic payouts."},
    "home.join.both.tag":   {"fr": "Mixte", "en": "Both"},
    "home.join.both.h":     {"fr": "Je commande et j'opère", "en": "I order and I operate"},
    "home.join.both.p":     {"fr": "Opérateur qui sous-traite les pics d'activité, école qui forme et place ses pilotes : un compte, deux rôles.",
                             "en": "An operator subcontracting peak periods, a school training and placing its pilots: one account, two roles."},
    "home.join.cta":        {"fr": "Créer mon compte", "en": "Create my account"},
    "home.join.how":        {"fr": "Comment ça marche ↑", "en": "How it works ↑"},
    "home.join.partner":    {"fr": "École, boutique, loueur ? Parlons partenariat →",
                             "en": "School, shop, rental company? Let's talk partnership →"},

    # ---- Landing : FAQ + contact
    "home.faq.eyebrow":   {"fr": "Questions fréquentes", "en": "Frequently asked questions"},
    "home.faq.h":         {"fr": "On répond à vos questions.", "en": "We answer your questions."},
    "home.faq.all":       {"fr": "Voir toutes les questions →", "en": "See all questions →"},
    "home.contact.eyebrow": {"fr": "Nous joindre", "en": "Contact us"},
    "home.contact.h":     {"fr": "Une question ? On répond vite.", "en": "A question? We answer fast."},
    "home.contact.p":     {"fr": "Clients, pilotes, écoles, boutiques : écrivez-nous, on est du métier. Réponse sous {hours} h ouvrables, en français ou en anglais.",
                           "en": "Clients, pilots, schools, shops: write to us, we know the trade. Reply within {hours} business hours, in French or English."},
    "home.contact.cta":   {"fr": "Écrire à l'équipe", "en": "Write to the team"},

    # ---- Types de profil (onglets annuaire)
    "kind.pro":          {"fr": "Pilote pro",        "en": "Pro pilot"},
    "kind.recreational": {"fr": "Pilote récréatif", "en": "Recreational pilot"},
    "kind.school":       {"fr": "École de formation", "en": "Training school"},
    "kind.pro.short":          {"fr": "Pro",        "en": "Pro"},
    "kind.recreational.short": {"fr": "Récréatif", "en": "Recreational"},
    "kind.school.short":       {"fr": "École",     "en": "School"},

    # ---- Landing : rejoindre — ecole
    "home.join.school.tag": {"fr": "École de formation", "en": "Training school"},
    "home.join.school.h":   {"fr": "Je forme des pilotes", "en": "I train pilots"},
    "home.join.school.p":   {"fr": "Votre école sur la carte : formations proposées (opérations de base, avancées, A2, STS…), coordonnées, avis des élèves devenus pilotes.",
                             "en": "Your school on the map: programmes offered (basic, advanced, A2, STS…), contact details, reviews from students who became pilots."},

    # ---- Common
    "common.verified":     {"fr": "vérifié", "en": "verified"},
    "common.urgent":       {"fr": "urgent",  "en": "urgent"},
    "common.new_pilot":    {"fr": "nouveau", "en": "new"},
    "common.reviews":      {"fr": "{n} avis", "en": "{n} review(s)"},
    "common.no_quote":     {"fr": "sur devis", "en": "on quote"},
    "common.per_hour":     {"fr": "{amount} {currency}/h", "en": "{amount} {currency}/h"},
    "common.km_away":      {"fr": "≈ {n} km", "en": "≈ {n} km"},
    "common.see_all":      {"fr": "Voir tout →", "en": "See all →"},
    "common.required":     {"fr": "requis",   "en": "required"},
    "common.optional":     {"fr": "optionnel","en": "optional"},
    "common.save":         {"fr": "Enregistrer", "en": "Save"},
    "common.cancel":       {"fr": "Annuler",     "en": "Cancel"},
    "common.delete":       {"fr": "supprimer",   "en": "delete"},
    "common.country":      {"fr": "Pays", "en": "Country"},
    "common.city":         {"fr": "Ville","en": "City"},
    "common.password":     {"fr": "Mot de passe", "en": "Password"},
    "common.username":     {"fr": "Identifiant", "en": "Username"},

    # ---- Login
    "login.title":      {"fr": "Connexion, AubePilot", "en": "Sign in, AubePilot"},
    "login.eyebrow":    {"fr": "Retour en cabine", "en": "Back to the cockpit"},
    "login.h1":         {"fr": "Bonjour, commandant.", "en": "Welcome, commander."},
    "login.h1_em":      {"fr": "commandant", "en": "commander"},
    "login.lead":       {"fr": "Vos identifiants AubePilot vous donnent aussi accès à tous les services L'Aube Étoilée, un seul compte pour tout l'écosystème.",
                         "en": "Your AubePilot credentials also unlock every L'Aube Étoilée service, one account for the whole ecosystem."},
    "login.bullet1":    {"fr": "Authentification partagée @aubemail.com.",
                         "en": "Shared @aubemail.com authentication."},
    "login.bullet2":    {"fr": "Aucun mot de passe stocké en clair, sessions signées côté serveur.",
                         "en": "No passwords stored in clear, server-signed sessions."},
    "login.bullet3":    {"fr": "Vous pouvez gérer plusieurs rôles depuis un seul espace.",
                         "en": "Manage multiple roles from a single dashboard."},
    "login.h2":         {"fr": "Connexion", "en": "Sign in"},
    "login.section":    {"fr": "Identifiants", "en": "Credentials"},
    "login.id_lbl":     {"fr": "Identifiant ou compte AubeMail",
                         "en": "Username or AubeMail account"},
    "login.pwd_lbl":    {"fr": "Mot de passe", "en": "Password"},
    "login.btn":        {"fr": "Entrer en cabine", "en": "Enter cockpit"},
    "login.alt":        {"fr": "Pas encore inscrit ?", "en": "Not registered yet?"},
    "login.alt_link":   {"fr": "Rejoindre le réseau", "en": "Join the network"},

    # ---- Register
    "reg.title":     {"fr": "Rejoindre, AubePilot", "en": "Join, AubePilot"},
    "reg.eyebrow":   {"fr": "Rejoindre le réseau", "en": "Join the network"},
    "reg.h1":        {"fr": "Embarquez dans l'équipage.", "en": "Step into the crew."},
    "reg.h1_em":     {"fr": "l'équipage", "en": "the crew"},
    "reg.lead":      {"fr": "Que vous cherchiez un pilote ou que vous le soyez, vous gardez un seul compte AubePilot. Bascule entre les rôles à tout moment depuis votre espace.",
                       "en": "Whether you're looking for a pilot or you are one, you keep one AubePilot account. Switch roles anytime from your dashboard."},
    "reg.bullet1":   {"fr": "Compte unique pour publier et piloter, sans abonnement.",
                       "en": "Single account to post and fly, no subscription."},
    "reg.bullet2":   {"fr": "Vos brevets et certifications mis en avant, DGAC, EASA, TC, FAA, ASECNA.",
                       "en": "Your licenses and certifications highlighted, DGAC, EASA, TC, FAA, ASECNA."},
    "reg.bullet3":   {"fr": "Recherche géolocalisée mondiale : Europe, Amérique, Maghreb, Afrique, Russie, Asie-Pacifique, Moyen-Orient.",
                       "en": "Worldwide geo-located search: Europe, Americas, Maghreb, Africa, Russia, Asia-Pacific, Middle East."},
    "reg.bullet4":   {"fr": "Commission plateforme uniquement sur missions livrées ({fee} %).",
                       "en": "Platform fee only on delivered missions ({fee}%)."},
    "reg.bullet5":   {"fr": "Auth partagée avec l'écosystème L'Aube Étoilée.",
                       "en": "Shared auth with the L'Aube Étoilée ecosystem."},
    "reg.h2":        {"fr": "Créer mon compte", "en": "Create my account"},
    "reg.role":      {"fr": "Mon rôle au décollage", "en": "My role at takeoff"},
    "reg.role.client":      {"fr": "Je cherche un pilote", "en": "I'm looking for a pilot"},
    "reg.role.client.desc": {"fr": "Je publie des missions et je sélectionne mon pilote.",
                              "en": "I post missions and pick my drone pilot."},
    "reg.role.pilot":       {"fr": "Je suis pilote", "en": "I'm a drone pilot"},
    "reg.role.pilot.desc":  {"fr": "Je présente mes brevets, ma flotte et je soumissionne.",
                              "en": "I list my licenses, my fleet and I bid."},
    "reg.role.both":        {"fr": "Les deux", "en": "Both"},
    "reg.role.both.desc":   {"fr": "Je commande et j'opère selon les jours.",
                              "en": "I order and I operate, depending on the day."},
    "reg.identity":  {"fr": "Identité", "en": "Identity"},
    "reg.fullname":  {"fr": "Nom et prénom", "en": "Full name"},
    "reg.username":  {"fr": "Identifiant",   "en": "Username"},
    "reg.pwd":       {"fr": "Mot de passe",  "en": "Password"},
    "reg.confirm":   {"fr": "Confirmer le mot de passe", "en": "Confirm password"},
    "reg.phone":     {"fr": "Téléphone",     "en": "Phone"},
    "reg.email_lbl": {"fr": "Email AubeMail","en": "AubeMail email"},
    "reg.base":      {"fr": "Base d'opération","en": "Home base"},
    "reg.btn":       {"fr": "Créer mon compte","en": "Create my account"},
    "reg.alt":       {"fr": "Déjà membre ?", "en": "Already a member?"},
    "reg.alt_link":  {"fr": "Connexion",     "en": "Sign in"},
    "reg.foot":      {"fr": "Vous pouvez compléter votre profil pilote après l'inscription.",
                       "en": "You can complete your pilot profile after signup."},
    "reg.geo_btn":   {"fr": "⌖ Ma position", "en": "⌖ My location"},

    # ---- Spécialités / mission types
    "mission.photo":          {"fr": "Photographie aérienne",                "en": "Aerial photography"},
    "mission.video":          {"fr": "Vidéo aérienne / clip",                "en": "Aerial video / clip"},
    "mission.cinema":         {"fr": "Cinéma & FPV (cascades, racing)",      "en": "Cinema & FPV (stunts, racing)"},
    "mission.reportage":      {"fr": "Reportage / documentaire",             "en": "Reporting / documentary"},
    "mission.mariage":        {"fr": "Mariage / événement privé",            "en": "Wedding / private event"},
    "mission.evenement":      {"fr": "Événement public / sport",             "en": "Public event / sports"},
    "mission.immobilier":     {"fr": "Immobilier résidentiel & commercial",  "en": "Real estate (residential & commercial)"},
    "mission.patrimoine":     {"fr": "Patrimoine & monuments historiques",   "en": "Heritage & historical monuments"},
    "mission.mapping":        {"fr": "Cartographie / orthophoto (RTK / PPK)","en": "Mapping / orthophoto (RTK / PPK)"},
    "mission.3d":             {"fr": "Modélisation 3D / photogrammétrie",    "en": "3D modeling / photogrammetry"},
    "mission.topographie":    {"fr": "Topographie & levés de terrain",       "en": "Topographic survey"},
    "mission.volumes":        {"fr": "Mesure de volumes (stocks, carrières)","en": "Volume measurement (stockpiles, quarries)"},
    "mission.inspection":     {"fr": "Inspection technique (général)",       "en": "Technical inspection (general)"},
    "mission.toiture":        {"fr": "Inspection toiture & façade",          "en": "Roof & facade inspection"},
    "mission.ouvrage_art":    {"fr": "Inspection ouvrages d'art (ponts, viaducs)", "en": "Civil structure inspection (bridges, viaducts)"},
    "mission.eolienne":       {"fr": "Inspection éolienne",                  "en": "Wind turbine inspection"},
    "mission.photovoltaique": {"fr": "Inspection panneaux solaires",         "en": "Solar panel inspection"},
    "mission.ligne_ht":       {"fr": "Inspection lignes électriques HT",     "en": "High-voltage power line inspection"},
    "mission.pipeline":       {"fr": "Inspection pipelines / oléoducs",      "en": "Pipeline inspection"},
    "mission.ferroviaire":    {"fr": "Inspection ferroviaire",               "en": "Rail inspection"},
    "mission.industriel":     {"fr": "Inspection industrielle (silos, cheminées)", "en": "Industrial inspection (silos, stacks)"},
    "mission.thermographie":  {"fr": "Thermographie énergétique / bâtiment", "en": "Thermal imaging (energy / buildings)"},
    "mission.btp":            {"fr": "BTP / suivi de chantier",              "en": "Construction / site progress"},
    "mission.agriculture":    {"fr": "Agriculture (NDVI, suivi parcelle)",   "en": "Agriculture (NDVI, plot monitoring)"},
    "mission.epandage":       {"fr": "Pulvérisation & épandage agricole",    "en": "Crop spraying"},
    "mission.foresterie":     {"fr": "Foresterie & inventaire forestier",    "en": "Forestry & forest inventory"},
    "mission.feux_foret":     {"fr": "Détection / suivi feux de forêt",      "en": "Wildfire detection / monitoring"},
    "mission.surveillance":   {"fr": "Surveillance / sécurité privée",       "en": "Site surveillance / private security"},
    "mission.sdis":           {"fr": "Sécurité civile / SDIS / pompiers",    "en": "Civil safety / fire departments"},
    "mission.recherche":      {"fr": "Recherche & sauvetage (SAR)",          "en": "Search and rescue (SAR)"},
    "mission.sinistre":       {"fr": "Constat d'assurance / sinistre",       "en": "Insurance claim / damage assessment"},
    "mission.environnement":  {"fr": "Suivi environnemental / pollution",    "en": "Environmental monitoring / pollution"},
    "mission.livraison":      {"fr": "Livraison (charges légères)",          "en": "Light-payload delivery"},
    "mission.formation":      {"fr": "Formation / accompagnement vol",       "en": "Training / flight mentorship"},
    "mission.autre":          {"fr": "Autre / sur mesure",                   "en": "Other / custom"},

    # ---- Groupes de spécialités (regroupement à l'affichage)
    "mission_group.Médias & cinéma":          {"fr": "Médias & cinéma",          "en": "Media & film"},
    "mission_group.Immobilier & patrimoine":  {"fr": "Immobilier & patrimoine",  "en": "Real estate & heritage"},
    "mission_group.Cartographie & mesure":    {"fr": "Cartographie & mesure",    "en": "Mapping & surveying"},
    "mission_group.Inspection technique":     {"fr": "Inspection technique",     "en": "Technical inspection"},
    "mission_group.Construction & BTP":       {"fr": "Construction & BTP",       "en": "Construction"},
    "mission_group.Agriculture & foresterie": {"fr": "Agriculture & foresterie", "en": "Agriculture & forestry"},
    "mission_group.Sécurité & urgence":       {"fr": "Sécurité & urgence",       "en": "Safety & emergency"},
    "mission_group.Environnement":            {"fr": "Environnement",            "en": "Environment"},
    "mission_group.Logistique & autres":      {"fr": "Logistique & autres",      "en": "Logistics & other"},

    # ---- Emails (sujets)
    "email.welcome.subject":      {"fr": "Bienvenue sur AubePilot",
                                    "en": "Welcome to AubePilot"},
    "email.new_bid.subject":      {"fr": "Nouvelle offre sur « {title} »",
                                    "en": "New bid on « {title} »"},
    "email.bid_accepted.subject": {"fr": "Votre offre est acceptée, « {title} »",
                                    "en": "Your bid was accepted, « {title} »"},
    "email.new_message.subject":  {"fr": "Nouveau message, {title}",
                                    "en": "New message, {title}"},

    # ---- Footer légal (Loi 25 Québec)
    "footer.privacy":  {"fr": "Confidentialité",     "en": "Privacy"},
    "footer.legal":    {"fr": "Mentions légales",    "en": "Legal notice"},
    "footer.terms":    {"fr": "Conditions d'utilisation", "en": "Terms of use"},
    "footer.cookies":  {"fr": "Cookies",             "en": "Cookies"},
    "footer.rprp":     {"fr": "Contacter le RPRP",   "en": "Contact our DPO"},
    "footer.nav_label":{"fr": "Le réseau",           "en": "The network"},
    "footer.become_pilot": {"fr": "Devenir pilote",  "en": "Become a pilot"},
    "footer.how":      {"fr": "Comment ça marche",   "en": "How it works"},
    "footer.schools":  {"fr": "Écoles & organismes", "en": "Schools & organisations"},
    "footer.faq":      {"fr": "FAQ",                 "en": "FAQ"},
    "footer.contact":  {"fr": "Nous joindre",        "en": "Contact us"},

    # ---- Statuts (mission / bid / booking), affichés dans les .tag
    "status.open":            {"fr": "ouverte",        "en": "open"},
    "status.assigned":        {"fr": "attribuée",      "en": "assigned"},
    "status.in_progress":     {"fr": "en cours",       "en": "in progress"},
    "status.done":            {"fr": "terminée",       "en": "done"},
    "status.cancelled":       {"fr": "annulée",        "en": "cancelled"},
    "status.pending":         {"fr": "en attente",     "en": "pending"},
    "status.accepted":        {"fr": "acceptée",       "en": "accepted"},
    "status.rejected":        {"fr": "refusée",        "en": "rejected"},
    "status.withdrawn":       {"fr": "retirée",        "en": "withdrawn"},
    "status.pending_payment": {"fr": "paiement en attente", "en": "pending payment"},
    "status.funded":          {"fr": "payée",          "en": "funded"},
    "status.completed":       {"fr": "terminée",       "en": "completed"},
    "status.disputed":        {"fr": "en litige",      "en": "disputed"},
    "status.refunded":        {"fr": "remboursée",     "en": "refunded"},
}


def status_label(code: str, lang: Optional[str] = None) -> str:
    """Label localise pour les status mission/bid/booking. Fallback : code brut."""
    if not code:
        return ""
    return t(f"status.{code}", lang=lang)
