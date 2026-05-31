"""SEO : métadonnées + données structurées (JSON-LD) façon marketplace.

On vise le même niveau qu'Upwork / Fiverr : titres et descriptions riches en
mots-clés, canonical, Open Graph, et surtout des données structurées
schema.org qui déclenchent les "rich results" Google :
  - Organization + WebSite (avec SearchAction -> sitelinks search box)
  - Person / ProfilePage pour les pilotes (note, localisation, métier)
  - JobPosting pour les missions (l'équivalent exact des annonces Upwork)
  - FAQPage sur l'accueil

Tout est bilingue (fr/en) selon `lang`. Les fonctions renvoient des dicts ;
les templates les sérialisent via le filtre Jinja `|tojson` (échappement sûr).
"""

CANONICAL_BASE = "https://pilot.aubeetoilee.com"
ORG_NAME = "AubePilot"
ORG_LOGO = CANONICAL_BASE + "/static/brand/og-image.png"

# Pages publiques exposées au sitemap (chemin, priorité, fréquence)
PUBLIC_ROUTES = [
    ("/", "1.0", "daily"),
    ("/pilotes", "0.9", "daily"),
    ("/missions", "0.9", "daily"),
    ("/mentions-legales", "0.3", "yearly"),
    ("/confidentialite", "0.3", "yearly"),
    ("/cgu", "0.3", "yearly"),
    ("/cookies", "0.3", "yearly"),
]

# Répertoires interdits aux robots (espace privé, paiement, admin, API)
ROBOTS_DISALLOW = [
    "/espace", "/reservations/", "/admin/", "/stripe/",
    "/api/", "/lang/", "/media/",
]


def _truncate(text, length=158):
    text = " ".join((text or "").split())
    if len(text) <= length:
        return text
    return text[:length - 1].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------- #
# JSON-LD globaux (sur toutes les pages)
# --------------------------------------------------------------------------- #

def organization_ld(lang="fr"):
    desc = ("AubePilot met en relation pilotes de drone certifiés et clients "
            "partout dans le monde : prises de vue aériennes, inspections, "
            "événements." if lang == "fr" else
            "AubePilot connects certified drone pilots with clients worldwide "
            "for aerial photography, inspections and events.")
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": ORG_NAME,
        "url": CANONICAL_BASE,
        "logo": ORG_LOGO,
        "image": ORG_LOGO,
        "description": desc,
        "slogan": ("Le ciel n'a pas de frontière. Vos pilotes non plus."
                   if lang == "fr" else "The sky has no borders. Neither do your pilots."),
    }


def website_ld(lang="fr"):
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": ORG_NAME,
        "url": CANONICAL_BASE,
        "inLanguage": lang,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": CANONICAL_BASE + "/pilotes?q={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def global_ld(lang="fr"):
    return [organization_ld(lang), website_ld(lang)]


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

def home(lang="fr"):
    if lang == "fr":
        title = "Louez un pilote de drone certifié — Devis gratuits | AubePilot"
        desc = ("Trouvez et réservez un pilote de drone certifié près de chez "
                "vous : prises de vue aériennes, inspections, mariages, "
                "immobilier. Devis gratuits, paiement sécurisé sous séquestre.")
        faq = [
            ("Comment trouver un pilote de drone ?",
             "Recherchez par ville, pays et type de mission, comparez les "
             "profils, avis et tarifs, puis demandez un devis gratuit. La mise "
             "en relation et les devis sont sans frais."),
            ("Combien coûte un pilote de drone ?",
             "Chaque pilote fixe ses tarifs. Vous recevez des devis détaillés "
             "(prestation, livrables, prix) et choisissez la meilleure offre. "
             "Le paiement est conservé en séquestre jusqu'à la livraison."),
            ("Les pilotes sont-ils certifiés et assurés ?",
             "Les pilotes renseignent leurs brevets (DGAC, EASA, Transport "
             "Canada, FAA...) et leur assurance responsabilité civile "
             "professionnelle, vérifiables sur leur profil."),
            ("Le paiement est-il sécurisé ?",
             "Oui. Les fonds sont conservés en séquestre via AubePilot et "
             "versés au pilote uniquement après validation de la livraison."),
        ]
    else:
        title = "Hire a Certified Drone Pilot — Free Quotes | AubePilot"
        desc = ("Find and book a certified drone pilot near you for aerial "
                "photography, inspections, weddings and real estate. Free "
                "quotes, secure escrow payments.")
        faq = [
            ("How do I find a drone pilot?",
             "Search by city, country and mission type, compare profiles, "
             "reviews and rates, then request a free quote."),
            ("How much does a drone pilot cost?",
             "Each pilot sets their own rates. You receive detailed quotes and "
             "pick the best offer; payment is held in escrow until delivery."),
            ("Are pilots certified and insured?",
             "Pilots list their licences (DGAC, EASA, Transport Canada, "
             "FAA...) and liability insurance, visible on their profile."),
            ("Is payment secure?",
             "Yes. Funds are held in escrow by AubePilot and released to the "
             "pilot only after you validate the delivery."),
        ]
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in faq
        ],
    }
    return {"title": title, "description": desc, "jsonld": [faq_ld]}


def pilots_list(lang="fr", params=None):
    params = params or {}
    place = params.get("city") or params.get("country") or ""
    if lang == "fr":
        title = "Pilotes de drone certifiés à louer"
        if place:
            title += f" à {place}"
        title += " | AubePilot"
        desc = ("Comparez les pilotes de drone certifiés"
                + (f" à {place}" if place else "")
                + " : avis, tarifs, brevets et matériel. Demandez des devis "
                  "gratuits pour vos prises de vue, inspections et événements.")
    else:
        title = "Certified Drone Pilots for Hire"
        if place:
            title += f" in {place}"
        title += " | AubePilot"
        desc = ("Compare certified drone pilots"
                + (f" in {place}" if place else "")
                + ": reviews, rates, licences and gear. Request free quotes for "
                  "aerial photography, inspections and events.")
    return {"title": title, "description": desc}


def missions_list(lang="fr"):
    if lang == "fr":
        title = "Missions & contrats de drone à pourvoir | AubePilot"
        desc = ("Trouvez des missions de pilotage de drone près de chez vous et "
                "envoyez vos devis : inspections, captations aériennes, "
                "événements, immobilier. Paiement sécurisé sous séquestre.")
    else:
        title = "Open Drone Jobs & Contracts | AubePilot"
        desc = ("Find drone piloting jobs near you and send your quotes: "
                "inspections, aerial filming, events, real estate. Secure "
                "escrow payments.")
    return {"title": title, "description": desc}


def pilot_profile(lang="fr", *, name, city="", country="", headline="",
                  bio="", rating=None, image=None, user_id=None):
    place = ", ".join(x for x in [city, country] if x)
    stars = ""
    if rating and rating.get("count"):
        stars = f" · {rating['avg']}★ ({rating['count']})"
    if lang == "fr":
        title = f"{name} — Pilote de drone"
        if place:
            title += f" à {place}"
        title += stars + " | AubePilot"
        desc = _truncate(headline or bio or
                         (f"Pilote de drone certifié{(' à ' + place) if place else ''} "
                          "sur AubePilot. Consultez avis, brevets, matériel et "
                          "demandez un devis gratuit."))
    else:
        title = f"{name} — Drone Pilot"
        if place:
            title += f" in {place}"
        title += stars + " | AubePilot"
        desc = _truncate(headline or bio or
                         (f"Certified drone pilot{(' in ' + place) if place else ''} "
                          "on AubePilot. See reviews, licences, gear and request "
                          "a free quote."))

    person = {
        "@type": "Person",
        "name": name,
        "jobTitle": "Pilote de drone" if lang == "fr" else "Drone Pilot",
        "worksFor": {"@type": "Organization", "name": ORG_NAME, "url": CANONICAL_BASE},
    }
    if headline:
        person["description"] = _truncate(headline, 250)
    if place:
        person["address"] = {
            "@type": "PostalAddress",
            **({"addressLocality": city} if city else {}),
            **({"addressCountry": country} if country else {}),
        }
    if image:
        person["image"] = image
    if user_id:
        person["url"] = f"{CANONICAL_BASE}/pilotes/{user_id}"
    if rating and rating.get("count"):
        person["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": rating["avg"],
            "reviewCount": rating["count"],
            "bestRating": 5, "worstRating": 1,
        }
    profile_ld = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "mainEntity": person,
    }
    return {"title": title, "description": desc, "og_image": image,
            "og_type": "profile", "jsonld": [profile_ld]}


def mission_posting(lang="fr", *, mission, mission_type_label="", url=None):
    title_txt = mission.get("title") or ("Mission drone" if lang == "fr" else "Drone job")
    place = ", ".join(x for x in [mission.get("city"), mission.get("country")] if x)
    if lang == "fr":
        title = f"{title_txt} — Mission drone"
        if place:
            title += f" à {place}"
        title += " | AubePilot"
    else:
        title = f"{title_txt} — Drone job"
        if place:
            title += f" in {place}"
        title += " | AubePilot"
    desc = _truncate(mission.get("description") or title_txt)

    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title_txt,
        "description": mission.get("description") or title_txt,
        "datePosted": (mission.get("created_at") or "")[:10],
        "employmentType": "CONTRACTOR",
        "industry": mission_type_label or "Services drone",
        "hiringOrganization": {
            "@type": "Organization", "name": ORG_NAME, "url": CANONICAL_BASE,
            "logo": ORG_LOGO,
        },
        "directApply": True,
    }
    if url:
        posting["url"] = url
    if mission.get("country"):
        posting["jobLocation"] = {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                **({"addressLocality": mission["city"]} if mission.get("city") else {}),
                **({"addressRegion": mission["region"]} if mission.get("region") else {}),
                "addressCountry": mission["country"],
            },
        }
    if mission.get("end_date"):
        posting["validThrough"] = str(mission["end_date"])[:10]
    bmin, bmax = mission.get("budget_min"), mission.get("budget_max")
    if bmin or bmax:
        val = {"@type": "QuantitativeValue", "unitText": "PROJECT"}
        if bmin and bmax:
            val["minValue"], val["maxValue"] = bmin, bmax
        else:
            val["value"] = bmin or bmax
        posting["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": mission.get("currency") or "EUR",
            "value": val,
        }
    return {"title": title, "description": desc, "jsonld": [posting]}
