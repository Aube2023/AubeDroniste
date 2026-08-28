"""Contenus éditoriaux bilingues partagés entre pages, accueil et SEO.

- FAQ : une seule source de vérité pour la page /faq, l'aperçu de l'accueil et
  les données structurées FAQPage (rich results Google). Les réponses citent
  les vraies règles de la plateforme (commission, séquestre, auto-libération,
  annulation) via des placeholders formatés depuis config — jamais de chiffre
  codé en dur qui divergerait du code.
"""
from config import (
    AUTO_RELEASE_DAYS,
    LATE_CANCELLATION_FEE_PCT,
    LATE_CANCELLATION_HOURS,
    PILOT_SHARE_PCT,
    PLATFORM_FEE_PCT,
)

_FMT = {
    "fee": int(PLATFORM_FEE_PCT),
    "pilot_share": int(PILOT_SHARE_PCT),
    "auto_release_days": AUTO_RELEASE_DAYS,
    "late_hours": LATE_CANCELLATION_HOURS,
    "late_fee": int(LATE_CANCELLATION_FEE_PCT),
}

FAQ_CATEGORIES = [
    ("clients",   {"fr": "Pour les clients",        "en": "For clients"}),
    ("pilots",    {"fr": "Pour les pilotes",        "en": "For pilots"}),
    ("payment",   {"fr": "Paiement & sécurité",     "en": "Payment & safety"}),
    ("platform",  {"fr": "La plateforme",           "en": "The platform"}),
]

# (id, catégorie, question fr/en, réponse fr/en, en_vedette_sur_l_accueil)
_FAQ = [
    ("find", "clients", True,
     {"fr": "Comment trouver un pilote près de chez moi ?",
      "en": "How do I find a drone pilot near me?"},
     {"fr": "Tapez un code postal, une adresse ou une ville dans la recherche, "
            "choisissez une spécialité si besoin : les pilotes s'affichent sur la "
            "carte et dans la liste, du plus proche au plus éloigné. Chaque profil "
            "montre les brevets, l'assurance, la flotte, les avis et le portfolio.",
      "en": "Type a postal code, an address or a city in the search box, pick a "
            "specialty if needed: pilots appear on the map and in the list, nearest "
            "first. Each profile shows licences, insurance, fleet, reviews and "
            "portfolio."}),
    ("cost", "clients", True,
     {"fr": "Combien ça coûte d'utiliser AubePilot ?",
      "en": "How much does AubePilot cost?"},
     {"fr": "Chercher un pilote, publier une mission et recevoir des devis est "
            "gratuit, sans abonnement. Une commission de {fee} % est incluse dans "
            "le prix affiché et n'est prélevée que lorsqu'une mission est confirmée "
            "et payée : elle finance le paiement sous séquestre, la médiation, "
            "l'hébergement des livrables et le support. Elle devient dégressive "
            "pour les binômes client-pilote qui travaillent régulièrement ensemble.",
      "en": "Searching for a pilot, posting a mission and receiving quotes is free, "
            "with no subscription. A {fee}% fee is included in the displayed price "
            "and only charged when a mission is confirmed and paid: it funds escrow "
            "payment, mediation, deliverable hosting and support. It decreases for "
            "client-pilot pairs who work together regularly."}),
    ("payment", "payment", True,
     {"fr": "Comment se passe le paiement ?",
      "en": "How does payment work?"},
     {"fr": "Vous acceptez un devis, puis vous payez 100 % du montant, qui reste "
            "sous séquestre (Stripe). Le pilote ne touche les fonds ({pilot_share} % "
            "du prix) qu'après votre validation de la livraison, ou automatiquement "
            "{auto_release_days} jours après la livraison si vous ne répondez pas. "
            "Aucun acompte versé au pilote avant la mission.",
      "en": "You accept a quote, then pay 100% of the amount, which is held in "
            "escrow (Stripe). The pilot only receives the funds ({pilot_share}% of "
            "the price) once you validate the delivery, or automatically "
            "{auto_release_days} days after delivery if you don't respond. No "
            "deposit is paid to the pilot before the mission."}),
    ("dispute", "payment", False,
     {"fr": "Que se passe-t-il si la mission se passe mal ?",
      "en": "What if the mission goes wrong?"},
     {"fr": "Tant que les fonds sont sous séquestre, vous pouvez ouvrir un litige "
            "depuis la réservation. Notre équipe examine les échanges et les "
            "livrables, puis rembourse tout ou partie du montant si la prestation "
            "n'a pas été réalisée comme convenu.",
      "en": "While funds are in escrow you can open a dispute from the booking. "
            "Our team reviews the exchanges and deliverables, then refunds all or "
            "part of the amount if the service was not delivered as agreed."}),
    ("cancel", "payment", False,
     {"fr": "Puis-je annuler une mission réservée ?",
      "en": "Can I cancel a booked mission?"},
     {"fr": "Oui. Avant paiement, l'annulation est libre. Après paiement, vous "
            "êtes remboursé intégralement si vous annulez plus de {late_hours} h "
            "avant la mission ; en deçà, {late_fee} % du devis dédommagent le "
            "pilote qui avait bloqué sa journée, et le reste vous est remboursé.",
      "en": "Yes. Before payment, cancellation is free. After payment you get a "
            "full refund if you cancel more than {late_hours} h before the mission; "
            "closer than that, {late_fee}% of the quote compensates the pilot who "
            "had blocked the day, and the rest is refunded."}),
    ("verified", "clients", True,
     {"fr": "Qu'est-ce qu'un profil « vérifié » ?",
      "en": "What is a “verified” profile?"},
     {"fr": "Le pilote a téléversé le justificatif de son brevet (DGAC, EASA, "
            "Transport Canada, FAA, ASECNA…) et notre équipe l'a contrôlé "
            "manuellement. Le badge apparaît sur le brevet et sur le profil, le nom "
            "du pilote est alors verrouillé, et l'assurance responsabilité civile "
            "professionnelle est affichée avec sa compagnie et son numéro de "
            "police. Vous pouvez filtrer la recherche pour ne voir que ces profils.",
      "en": "The pilot uploaded proof of their licence (DGAC, EASA, Transport "
            "Canada, FAA, ASECNA…) and our team checked it manually. The badge "
            "appears on the licence and on the profile, the pilot's name is then "
            "locked, and professional liability insurance is shown with its "
            "company and policy number. You can filter the search to only see "
            "these profiles."}),
    ("urgent", "clients", False,
     {"fr": "Puis-je publier une mission urgente ?",
      "en": "Can I post an urgent mission?"},
     {"fr": "Oui. Cochez « urgent » à la publication : la mission est mise en "
            "avant et les pilotes disponibles dans le rayon reçoivent une alerte "
            "par courriel. Vous pouvez aussi cibler directement un pilote depuis "
            "son profil ou réserver l'un de ses forfaits à prix fixe.",
      "en": "Yes. Tick “urgent” when posting: the mission is highlighted and "
            "available pilots within range receive an email alert. You can also "
            "target a pilot directly from their profile or book one of their "
            "fixed-price packages."}),
    ("appear", "pilots", True,
     {"fr": "Je suis pilote. Comment apparaître sur la carte ?",
      "en": "I'm a pilot. How do I appear on the map?"},
     {"fr": "Créez votre compte (deux minutes), renseignez votre base "
            "d'opération, votre rayon, vos spécialités et vos tarifs, puis ajoutez "
            "vos brevets et vos drones. Vous apparaissez aussitôt sur la carte et "
            "dans les recherches — votre position exacte est floutée d'environ "
            "10 km par respect de votre vie privée. Un brevet vérifié vous place "
            "en tête des résultats.",
      "en": "Create your account (two minutes), fill in your home base, range, "
            "specialties and rates, then add your licences and drones. You appear "
            "right away on the map and in searches — your exact position is "
            "blurred by about 10 km to protect your privacy. A verified licence "
            "puts you at the top of results."}),
    ("licences", "pilots", False,
     {"fr": "Quelles certifications sont acceptées ?",
      "en": "Which certifications are accepted?"},
     {"fr": "Toutes les autorités : DGAC et EASA (A1/A3, A2, STS), Transport "
            "Canada (opérations de base, avancées, BVLOS), FAA Part 107, CAA UK, "
            "OFAC, ASECNA, DGAC Maroc, ANAC Tunisie, CASA, JCAB, CAAC et plus de "
            "vingt autres. Vous joignez le justificatif PDF ; il n'est visible que "
            "des clients qui ouvrent une demande de mission avec vous.",
      "en": "All authorities: DGAC and EASA (A1/A3, A2, STS), Transport Canada "
            "(basic, advanced, BVLOS), FAA Part 107, CAA UK, FOCA, ASECNA, DGAC "
            "Morocco, ANAC Tunisia, CASA, JCAB, CAAC and twenty more. You attach "
            "the PDF proof; only clients who open a mission request with you can "
            "see it."}),
    ("paid", "pilots", False,
     {"fr": "Comment suis-je payé ?",
      "en": "How do I get paid?"},
     {"fr": "Vous activez vos paiements en quelques minutes (Stripe Connect, "
            "vérification d'identité incluse). Dès que le client valide la "
            "livraison, {pilot_share} % du prix part automatiquement vers votre "
            "compte bancaire. Dans les pays non couverts par Stripe, le versement "
            "est fait manuellement par notre équipe.",
      "en": "You activate payouts in a few minutes (Stripe Connect, identity "
            "check included). As soon as the client validates delivery, "
            "{pilot_share}% of the price is automatically transferred to your bank "
            "account. In countries not covered by Stripe, our team pays out "
            "manually."}),
    ("packages", "pilots", False,
     {"fr": "Puis-je proposer des forfaits à prix fixe ?",
      "en": "Can I offer fixed-price packages?"},
     {"fr": "Oui. Depuis votre espace, créez des forfaits (ex. « Immobilier — "
            "20 photos + vidéo 60 s ») avec prix, durée et livrables. Les clients "
            "les réservent directement depuis votre profil, sans passer par un "
            "appel d'offres.",
      "en": "Yes. From your dashboard, create packages (e.g. “Real estate — 20 "
            "photos + 60 s video”) with price, duration and deliverables. Clients "
            "book them straight from your profile, with no bidding round."}),
    ("deliver", "pilots", False,
     {"fr": "Comment livrer mes fichiers au client ?",
      "en": "How do I deliver files to the client?"},
     {"fr": "Directement sur la réservation : photos, vidéos 4K, RAW, "
            "orthophotos, nuages de points (LAS/LAZ), PDF… jusqu'à 1 Go par "
            "fichier. Le client télécharge, valide, et vos fonds sont libérés. Le "
            "devis accepté est aussi exportable en PDF à votre marque.",
      "en": "Straight from the booking: photos, 4K video, RAW, orthophotos, point "
            "clouds (LAS/LAZ), PDFs… up to 1 GB per file. The client downloads, "
            "validates, and your funds are released. The accepted quote can also "
            "be exported as a branded PDF."}),
    ("where", "platform", False,
     {"fr": "Dans quels pays AubePilot fonctionne-t-elle ?",
      "en": "Which countries does AubePilot cover?"},
     {"fr": "Partout. La recherche par code postal ou adresse fonctionne dans le "
            "monde entier, les brevets de toutes les autorités sont acceptés et "
            "les paiements sous séquestre sont disponibles dans plus de 45 pays "
            "(Canada, France, États-Unis, Europe, Maghreb…).",
      "en": "Everywhere. Postal code and address search works worldwide, licences "
            "from every authority are accepted and escrow payments are available "
            "in more than 45 countries (Canada, France, USA, Europe, Maghreb…)."}),
    ("bilingual", "platform", False,
     {"fr": "AubePilot est-elle bilingue ? Existe-t-il une application ?",
      "en": "Is AubePilot bilingual? Is there an app?"},
     {"fr": "Oui : tout le site et les courriels existent en français et en "
            "anglais, avec un thème jour et un thème nuit. AubePilot s'installe "
            "aussi comme application (Android et écran d'accueil iPhone) et "
            "fonctionne hors connexion pour vos pages récentes.",
      "en": "Yes: the whole site and all emails exist in French and English, with "
            "a day and a night theme. AubePilot also installs as an app (Android "
            "and iPhone home screen) and works offline for your recent pages."}),
    ("privacy", "platform", False,
     {"fr": "Que faites-vous de mes données ?",
      "en": "What do you do with my data?"},
     {"fr": "Le minimum : aucun traceur publicitaire, aucun outil d'analyse "
            "tiers. Les positions des pilotes sont floutées sur la carte, "
            "l'identité complète et les coordonnées ne sont révélées qu'après le "
            "paiement d'une mission, et les justificatifs de brevet restent "
            "privés. Conformité Loi 25 (Québec) et RGPD.",
      "en": "The minimum: no ad trackers, no third-party analytics. Pilot positions "
            "are blurred on the map, full identity and contact details are only "
            "revealed after a mission is paid, and licence proofs stay private. "
            "Compliant with Quebec's Law 25 and the GDPR."}),
]


def faq(lang: str = "fr", featured_only: bool = False) -> list:
    """Liste de dicts {id, category, question, answer} dans la langue voulue."""
    lang = "en" if lang == "en" else "fr"
    out = []
    for fid, cat, featured, q, a in _FAQ:
        if featured_only and not featured:
            continue
        out.append({
            "id": fid, "category": cat, "featured": featured,
            "question": q[lang],
            "answer": a[lang].format(**_FMT),
        })
    return out


def faq_categories(lang: str = "fr") -> list:
    lang = "en" if lang == "en" else "fr"
    return [(code, labels[lang]) for code, labels in FAQ_CATEGORIES]
