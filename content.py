"""Contenus éditoriaux bilingues partagés entre pages, accueil et SEO.

- FAQ : une seule source de vérité pour la page /faq, l'aperçu de l'accueil et
  les données structurées FAQPage (rich results Google). Les réponses citent
  les vraies règles de la plateforme (commission, séquestre, auto-libération,
  annulation) via des placeholders formatés depuis config — jamais de chiffre
  codé en dur qui divergerait du code.
"""
from config import (
    AUTO_RELEASE_DAYS,
    CANCELLATION_GRACE_HOURS,
    CANCELLATION_SERVICE_FEE_CAP,
    CANCELLATION_SERVICE_FEE_PCT,
    LATE_CANCELLATION_FEE_PCT,
    LATE_CANCELLATION_HOURS,
    PILOT_SHARE_PCT,
    PLATFORM_FEE_PCT,
    PLATFORM_FEE_TIERS,
)

_FMT = {
    "fee": int(PLATFORM_FEE_PCT),
    "pilot_share": int(PILOT_SHARE_PCT),
    "auto_release_days": AUTO_RELEASE_DAYS,
    "late_hours": LATE_CANCELLATION_HOURS,
    "late_fee": int(LATE_CANCELLATION_FEE_PCT),
    "grace_hours": CANCELLATION_GRACE_HOURS,
    "service_fee": int(CANCELLATION_SERVICE_FEE_PCT),
    "service_fee_cap": int(CANCELLATION_SERVICE_FEE_CAP),
    # paliers degressifs (missions terminees entre les memes parties)
    "tier2_from": PLATFORM_FEE_TIERS[1][0] + 1 if len(PLATFORM_FEE_TIERS) > 1 else 0,
    "tier2_fee": int(PLATFORM_FEE_TIERS[1][1]) if len(PLATFORM_FEE_TIERS) > 1 else int(PLATFORM_FEE_PCT),
    "tier3_from": PLATFORM_FEE_TIERS[2][0] + 1 if len(PLATFORM_FEE_TIERS) > 2 else 0,
    "tier3_fee": int(PLATFORM_FEE_TIERS[2][1]) if len(PLATFORM_FEE_TIERS) > 2 else int(PLATFORM_FEE_PCT),
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
            "l'hébergement des livrables et le support. Elle est dégressive pour "
            "les binômes qui retravaillent ensemble : {tier2_fee} % à partir de la "
            "{tier2_from}e mission terminée entre les mêmes parties, {tier3_fee} % à "
            "partir de la {tier3_from}e.",
      "en": "Searching for a pilot, posting a mission and receiving quotes is free, "
            "with no subscription. A {fee}% fee is included in the displayed price "
            "and only charged when a mission is confirmed and paid: it funds escrow "
            "payment, mediation, deliverable hosting and support. It decreases for "
            "pairs who work together again: {tier2_fee}% from the {tier2_from}th "
            "completed mission between the same parties, {tier3_fee}% from the "
            "{tier3_from}th."}),
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
     {"fr": "Oui. Avant paiement, l'annulation est libre, et jusqu'à {grace_hours} h "
            "après le paiement vous êtes remboursé intégralement. Passé ce délai, "
            "des frais de service de {service_fee} % (max {service_fee_cap} dans la "
            "devise de la mission) sont retenus, car le contact du pilote vous a été "
            "révélé et sa date bloquée. Si vous annulez à moins de {late_hours} h de "
            "la mission, {late_fee} % du devis dédommagent en plus le pilote. Le "
            "reste vous est toujours remboursé. Si c'est le pilote qui se désiste, "
            "vous êtes remboursé à 100 % et la mission est remise en ligne.",
      "en": "Yes. Before payment, cancellation is free, and up to {grace_hours} h "
            "after payment you get a full refund. After that, a {service_fee}% "
            "service fee (max {service_fee_cap} in the mission currency) is kept, "
            "because the pilot's contact was revealed and their date blocked. If "
            "you cancel less than {late_hours} h before the mission, {late_fee}% of "
            "the quote also compensates the pilot. The rest is always refunded. If "
            "the pilot withdraws, you are refunded 100% and the mission goes back "
            "online."}),
    ("verified", "clients", True,
     {"fr": "Qu'est-ce qu'un profil « vérifié » ?",
      "en": "What is a “verified” profile?"},
     {"fr": "Le pilote a téléversé le justificatif de son brevet (DGAC, EASA, "
            "Transport Canada, FAA, ASECNA…) et notre équipe l'a contrôlé "
            "manuellement. Le badge apparaît sur le brevet et sur le profil, et le "
            "nom du pilote est alors verrouillé. L'assurance responsabilité civile "
            "professionnelle suit le même chemin : le badge « RC pro » ne s'affiche "
            "qu'après contrôle de l'attestation, et tombe à son échéance. Vous pouvez "
            "filtrer la recherche sur l'un comme sur l'autre.",
      "en": "The pilot uploaded proof of their licence (DGAC, EASA, Transport "
            "Canada, FAA, ASECNA…) and our team checked it manually. The badge "
            "appears on the licence and on the profile, the pilot's name is then "
            "locked. Professional liability insurance follows the same path: the "
            "\u201cInsured\u201d badge only appears once the certificate has been "
            "checked, and drops at its expiry date. You can filter the search on "
            "either one."}),
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


# ---------------------------------------------------------------------------
# Nouveautés : ce qui a changé POUR LES PILOTES ET LES CLIENTS, du plus récent
# au plus ancien. Une entrée = une fonction qu'on peut aller voir sur le site.
# Jamais de cuisine interne (serveur, clés, données, sécurité, outils
# d'administration) : c'est une page pour les utilisateurs, pas un journal
# technique. Trois langues, comme la FAQ ; ailleurs la page reste en français.
# ---------------------------------------------------------------------------

UPDATES_LANGS = ("fr", "en", "es")

_UPDATES = [
    ("2026-09", {"fr": "Septembre 2026", "en": "September 2026", "es": "Septiembre de 2026"}, [
        {"fr": "Le site se lit en 14 langues, chaque page avec sa propre adresse : français, anglais, "
               "espagnol, portugais, allemand, arabe, russe, ukrainien, turc, hindi, ourdou, bengali, "
               "vietnamien et indonésien.",
         "en": "The site now reads in 14 languages, each page with its own address: French, English, "
               "Spanish, Portuguese, German, Arabic, Russian, Ukrainian, Turkish, Hindi, Urdu, Bengali, "
               "Vietnamese and Indonesian.",
         "es": "El sitio se lee en 14 idiomas, cada página con su propia dirección: francés, inglés, "
               "español, portugués, alemán, árabe, ruso, ucraniano, turco, hindi, urdu, bengalí, "
               "vietnamita e indonesio."},
        {"fr": "Carte : météo en direct avec verdict de vol, radar de précipitations, trafic aérien "
               "autour de la zone et vue satellite.",
         "en": "Map: live weather with a fly / no-fly verdict, precipitation radar, live air traffic "
               "around the area and a satellite view.",
         "es": "Mapa: meteorología en directo con veredicto de vuelo, radar de precipitaciones, tráfico "
               "aéreo alrededor de la zona y vista satélite."},
        {"fr": "Repères de confiance sur chaque fiche pilote : membre depuis, délai de réponse habituel, "
               "missions livrées.",
         "en": "Trust markers on every pilot page: member since, usual response time, missions delivered.",
         "es": "Señales de confianza en cada ficha de piloto: miembro desde, tiempo de respuesta habitual, "
               "misiones entregadas."},
        {"fr": "Assurance responsabilité civile : le badge « RC pro » n'apparaît qu'une fois l'attestation "
               "vérifiée par l'équipe.",
         "en": "Liability insurance: the \"Pro liability\" badge only appears once the certificate has been "
               "checked by the team.",
         "es": "Seguro de responsabilidad civil: la insignia « RC pro » solo aparece cuando el equipo ha "
               "verificado el certificado."},
        {"fr": "Les pilotes voient combien de fois leur fiche a été consultée (7 et 30 jours) et ce qui "
               "leur manque pour être mieux trouvés.",
         "en": "Pilots see how many times their page was viewed (7 and 30 days) and what they still need "
               "to add to be found more easily.",
         "es": "Los pilotos ven cuántas veces se consultó su ficha (7 y 30 días) y qué les falta para que "
               "los encuentren mejor."},
        {"fr": "Partage de la fiche pilote en un clic, avec un visuel prêt pour les réseaux sociaux.",
         "en": "Share a pilot page in one click, with a preview image ready for social networks.",
         "es": "Compartir la ficha de piloto en un clic, con una imagen lista para las redes sociales."},
        {"fr": "Fiche pilote plus complète : livrables proposés, présence professionnelle (site, réseaux), "
               "spécialités regroupées par thème.",
         "en": "Fuller pilot page: deliverables offered, professional presence (website, social links), "
               "specialties grouped by theme.",
         "es": "Ficha de piloto más completa: entregables ofrecidos, presencia profesional (sitio web, "
               "redes), especialidades agrupadas por tema."},
        {"fr": "« Soutenez ce pilote » : un pilote peut présenter un drone ou un équipement à financer et "
               "recevoir des contributions.",
         "en": "\"Support this pilot\": a pilot can present a drone or equipment to fund and receive "
               "contributions.",
         "es": "« Apoye a este piloto »: un piloto puede presentar un dron o un equipo por financiar y "
               "recibir aportaciones."},
        {"fr": "Six palettes de couleurs au choix dans le compte (Aube, Ambre, Émeraude, Corail, Azur, "
               "Ardoise), en plus du mode nuit.",
         "en": "Six colour palettes to choose from in your account (Dawn, Amber, Emerald, Coral, Azure, "
               "Slate), on top of night mode.",
         "es": "Seis paletas de colores a elegir en la cuenta (Amanecer, Ámbar, Esmeralda, Coral, Azur, "
               "Pizarra), además del modo noche."},
        {"fr": "Annuaire : deux nouvelles catégories, entreprises de services et boutiques, à côté des "
               "pilotes et des écoles.",
         "en": "Directory: two new categories, service companies and shops, alongside pilots and schools.",
         "es": "Directorio: dos categorías nuevas, empresas de servicios y tiendas, junto a pilotos y "
               "escuelas."},
        {"fr": "Pays : liste complète des 195 pays à l'inscription et dans la recherche ; la ville est "
               "placée sur la carte automatiquement.",
         "en": "Countries: the full list of 195 countries at sign-up and in search; the city is placed on "
               "the map automatically.",
         "es": "Países: lista completa de los 195 países al registrarse y en la búsqueda; la ciudad se "
               "sitúa en el mapa automáticamente."},
        {"fr": "Connexion : bouton pour afficher le mot de passe, lien « mot de passe oublié », numéro de "
               "téléphone avec l'indicatif du pays.",
         "en": "Sign-in: show-password button, \"forgot password\" link, phone number with country code.",
         "es": "Inicio de sesión: botón para mostrar la contraseña, enlace « contraseña olvidada », "
               "teléfono con el prefijo del país."},
        {"fr": "Fiche école : courriel et téléphone de contact affichés, page « Formations et tarifs ».",
         "en": "School page: contact email and phone shown, \"Courses and prices\" page.",
         "es": "Ficha de escuela: correo y teléfono de contacto visibles, página « Formaciones y tarifas »."},
    ]),
    ("2026-08", {"fr": "Août 2026", "en": "August 2026", "es": "Agosto de 2026"}, [
        {"fr": "Recherche par code postal partout dans le monde, et filtre « pilotes certifiés ».",
         "en": "Search by postal code anywhere in the world, plus a \"certified pilots\" filter.",
         "es": "Búsqueda por código postal en todo el mundo, y filtro « pilotos certificados »."},
        {"fr": "Commission dégressive pour les pilotes : {fee} % sur les premières missions avec un client, "
               "{tier2_fee} % à partir de la mission {tier2_from}, {tier3_fee} % à partir de la mission "
               "{tier3_from}.",
         "en": "Sliding commission for pilots: {fee}% on the first missions with a client, {tier2_fee}% "
               "from mission {tier2_from}, {tier3_fee}% from mission {tier3_from}.",
         "es": "Comisión decreciente para los pilotos: {fee} % en las primeras misiones con un cliente, "
               "{tier2_fee} % a partir de la misión {tier2_from}, {tier3_fee} % a partir de la misión "
               "{tier3_from}."},
        {"fr": "Écoles de pilotage : page publique des écoles et inscription des organismes de formation.",
         "en": "Flight schools: public schools page and sign-up for training organisations.",
         "es": "Escuelas de pilotaje: página pública de escuelas e inscripción de organismos de formación."},
        {"fr": "Types de profil : pilote professionnel, pilote récréatif ou école, chacun avec sa fiche "
               "adaptée.",
         "en": "Profile types: professional pilot, recreational pilot or school, each with its own kind of "
               "page.",
         "es": "Tipos de perfil: piloto profesional, piloto recreativo o escuela, cada uno con su ficha "
               "adaptada."},
        {"fr": "FAQ complète et formulaire de contact.",
         "en": "Full FAQ and contact form.",
         "es": "FAQ completa y formulario de contacto."},
        {"fr": "On reste connecté : la session se prolonge tant qu'on revient, plus besoin de se "
               "reconnecter sans cesse.",
         "en": "You stay signed in: the session extends as long as you keep coming back, no more constant "
               "re-login.",
         "es": "La sesión se mantiene: se prolonga mientras vuelva, sin tener que iniciar sesión una y "
               "otra vez."},
        {"fr": "Carte : épingles regroupées façon annuaire, icônes distinctes pour pilotes, écoles et "
               "missions.",
         "en": "Map: clustered pins, distinct icons for pilots, schools and missions.",
         "es": "Mapa: marcadores agrupados, iconos distintos para pilotos, escuelas y misiones."},
    ]),
    ("2026-06", {"fr": "Juin 2026", "en": "June 2026", "es": "Junio de 2026"}, [
        {"fr": "Carte interactive sur les pages Pilotes et Missions.",
         "en": "Interactive map on the Pilots and Missions pages.",
         "es": "Mapa interactivo en las páginas Pilotos y Misiones."},
        {"fr": "Ouverture aux pilotes du monde entier, États-Unis compris.",
         "en": "Open to pilots worldwide, United States included.",
         "es": "Abierto a pilotos de todo el mundo, Estados Unidos incluido."},
        {"fr": "Portfolio : vidéos en HD lisibles partout, photos et réalisations sur la fiche.",
         "en": "Portfolio: HD videos that play everywhere, photos and past work on the page.",
         "es": "Portafolio: vídeos en HD que se ven en cualquier dispositivo, fotos y trabajos en la ficha."},
        {"fr": "Une seule photo de profil pour tout l'écosystème Aube, et une pastille « mon compte » dans "
               "l'en-tête.",
         "en": "One profile photo across the whole Aube ecosystem, and a \"my account\" chip in the header.",
         "es": "Una sola foto de perfil para todo el ecosistema Aube, y una pastilla « mi cuenta » en la "
               "cabecera."},
    ]),
    ("2026-05", {"fr": "Mai 2026 · lancement", "en": "May 2026 · launch", "es": "Mayo de 2026 · lanzamiento"}, [
        {"fr": "Annuaire de pilotes avec brevets, spécialités, rayon d'action et disponibilité.",
         "en": "Pilot directory with licences, specialties, operating radius and availability.",
         "es": "Directorio de pilotos con licencias, especialidades, radio de acción y disponibilidad."},
        {"fr": "Les clients publient des missions, les pilotes répondent avec un devis.",
         "en": "Clients post missions, pilots reply with a quote.",
         "es": "Los clientes publican misiones, los pilotos responden con un presupuesto."},
        {"fr": "Devis détaillé exportable en PDF, forfaits et livrables proposés par le pilote.",
         "en": "Detailed quote exportable as PDF, packages and deliverables offered by the pilot.",
         "es": "Presupuesto detallado exportable en PDF, paquetes y entregables ofrecidos por el piloto."},
        {"fr": "Avis laissés par les clients après une mission réalisée.",
         "en": "Reviews left by clients after a completed mission.",
         "es": "Reseñas de los clientes tras una misión realizada."},
        {"fr": "Alerte par courriel aux pilotes quand une mission est publiée dans leur rayon.",
         "en": "Email alert to pilots when a mission is posted within their radius.",
         "es": "Aviso por correo a los pilotos cuando se publica una misión en su radio."},
        {"fr": "Catalogue de brevets pré-rempli selon l'autorité (Transport Canada, FAA, EASA et d'autres).",
         "en": "Licence catalogue pre-filled by authority (Transport Canada, FAA, EASA and others).",
         "es": "Catálogo de licencias precargado según la autoridad (Transport Canada, FAA, EASA y otras)."},
    ]),
]


def updates(lang: str = "fr") -> list:
    """[{month, title, items}] du plus récent au plus ancien, dans la langue
    voulue (français si la page n'y existe pas)."""
    lang = lang if lang in UPDATES_LANGS else "fr"
    return [{"month": month, "title": title[lang], "items": [i[lang].format(**_FMT) for i in items]}
            for month, title, items in _UPDATES]
