"""AubePilot - marketplace pilotes <-> clients.

Point d'entree Flask. Auth PAM partagee, SQLite local, templates Jinja
+ une API JSON pour la recherche dynamique cote frontend.
"""
import logging
import os
import re
import time
from datetime import datetime, timezone

from flask import (
    Flask, abort, flash, g, jsonify, make_response, redirect,
    render_template, request, send_from_directory, url_for,
)

import auth
import content
import db
import geocode
import i18n
import payments
import security
import seo
import services
from config import (
    ALLOWED_DOC_EXT,
    CANCELLATION_GRACE_HOURS,
    CANCELLATION_SERVICE_FEE_CAP,
    CANCELLATION_SERVICE_FEE_PCT,
    LATE_CANCELLATION_FEE_PCT,
    LATE_CANCELLATION_HOURS,
    PLATFORM_FEE_TIERS,
    ANDROID_CERT_SHA256,
    ANDROID_PACKAGE,
    AUBECREW_URL,
    AUBEMAIL_URL,
    AUTO_RELEASE_DAYS,
    CURRENCIES,
    DEFAULT_CURRENCY,
    DEFAULT_SEARCH_RADIUS_KM,
    DRONE_BRANDS,
    DRONE_CAPABILITIES,
    DRONE_CATEGORIES,
    DRONE_MODELS_BY_BRAND,
    FEATURED_COUNTRIES,
    GOOGLE_SITE_VERIFICATION,
    HOST,
    LICENCE_AUTHORITIES,
    LICENCE_TITLES_BY_AUTHORITY,
    LICENCE_VERIFY_HINTS,
    PROFILE_KINDS,
    PROFILE_KIND_CODES,
    MAX_UPLOAD_MB,
    MAX_DELIVERABLE_MB,
    MAX_AVATAR_MB,
    MAX_PORTFOLIO_MB,
    MAX_PORTFOLIO_VIDEOS,
    ALLOWED_DELIVERABLE_EXT,
    ALLOWED_AVATAR_EXT,
    ALLOWED_PORTFOLIO_EXT,
    CONTACT_EMAIL,
    CONTACT_REPLY_HOURS,
    MISSION_TYPES,
    PILOT_SHARE_PCT,
    PLATFORM_FEE_PCT,
    PORT,
    SEARCH_RADIUS_CHOICES,
    SECRET_KEY,
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME_DAYS,
    SOCIAL_LINKS,
    STRIPE_PUBLISHABLE_KEY,
    UPLOAD_DIR,
)
import aube_push
import mailer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("aubepilot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)
app.secret_key = SECRET_KEY
# Le max global doit couvrir le PLUS GROS upload accepte par n'importe quelle
# route (sinon Flask coupe en 413 avant la verif fine). La limite par usage
# est verifiee dans chaque route (10 Mo docs, 1 Go portfolio/livrables).
app.config["MAX_CONTENT_LENGTH"] = (
    max(MAX_UPLOAD_MB, MAX_PORTFOLIO_MB, MAX_DELIVERABLE_MB) * 1024 * 1024
)
app.config["SITE_URL"] = os.environ.get("SITE_URL", f"http://localhost:{PORT}")

# Cookies durcis : httpOnly toujours, secure si HTTPS, SameSite=Lax
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=app.config["SITE_URL"].startswith("https://"),
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # 30 jours
)

# Refuse la cle SECRET par defaut en prod
security.assert_production_ready(app)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_db():
    db.init_schema(SCHEMA_PATH)
    log.info("schema initialise -> %s", db.DB_PATH)


with app.app_context():
    # schema_ready() plutot que "fichier present" : avec plusieurs workers
    # gunicorn, un worker peut voir le fichier cree par un autre avant que le
    # schema n'y soit (init_schema est idempotent : CREATE ... IF NOT EXISTS).
    if not db.schema_ready():
        bootstrap_db()
    db.run_migrations()


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

@app.teardown_appcontext
def _teardown(exc):
    db.close_db(exc)


@app.before_request
def _attach():
    auth.attach_user()
    g.lang = i18n.resolve_lang()
    # Langue preferee du compte (parametres) si aucun choix explicite (cookie)
    u = getattr(g, "user", None)
    if u and u.get("lang") in i18n.SUPPORTED \
            and request.cookies.get(i18n.COOKIE) not in i18n.SUPPORTED:
        g.lang = u["lang"]


@app.before_request
def _csrf_check():
    security.validate_csrf()


@app.after_request
def _security_headers(resp):
    return security.apply_security_headers(resp)


@app.after_request
def _refresh_session_cookie(resp):
    # Renouvellement glissant (voir auth.load_user_from_request) : re-pose le
    # cookie avec un max_age complet, sinon le navigateur l'expire 30 jours
    # apres le login initial meme pour un utilisateur actif tous les jours.
    token = getattr(g, "refreshed_session_token", None)
    if token:
        resp.set_cookie(
            SESSION_COOKIE_NAME, token, httponly=True, samesite="Lax",
            secure=app.config.get("SESSION_COOKIE_SECURE", False),
            max_age=60 * 60 * 24 * SESSION_LIFETIME_DAYS,
        )
    return resp


@app.context_processor
def _inject_globals():
    return {
        "current_user": getattr(g, "user", None),
        "mission_types": MISSION_TYPES,
        "drone_categories": DRONE_CATEGORIES,
        "drone_capabilities": DRONE_CAPABILITIES,
        "drone_brands": DRONE_BRANDS,
        "drone_models_by_brand": DRONE_MODELS_BY_BRAND,
        "licence_authorities": LICENCE_AUTHORITIES,
        "licence_titles_by_authority": LICENCE_TITLES_BY_AUTHORITY,
        "featured_countries": FEATURED_COUNTRIES,
        "currencies": CURRENCIES,
        "default_currency": DEFAULT_CURRENCY,
        "now": lambda: datetime.now(timezone.utc),
        # i18n
        "t": lambda key, **kwargs: i18n.t(key, lang=getattr(g, "lang", i18n.DEFAULT), **kwargs),
        "lang": getattr(g, "lang", i18n.DEFAULT),
        "supported_langs": i18n.SUPPORTED,
        # Stripe
        "stripe_mode": payments.banner_mode(),
        "stripe_pubkey": STRIPE_PUBLISHABLE_KEY,
        # URLs cross-service ecosysteme
        "aubecrew_url": AUBECREW_URL,
        "aubemail_url": AUBEMAIL_URL,
        # Contact public + reseaux (pied de page, page contact, accueil)
        "contact_email": CONTACT_EMAIL,
        "contact_reply_hours": CONTACT_REPLY_HOURS,
        "social_links": SOCIAL_LINKS,
        "search_radius_choices": SEARCH_RADIUS_CHOICES,
        # Economie plateforme : surface unique (jamais "20 %"/"80 %" en dur en vue)
        "platform_fee_pct": int(PLATFORM_FEE_PCT),
        "pilot_share_pct": int(PILOT_SHARE_PCT),
        "platform_fee_tiers": _fee_tiers_display(),
        "booking_fee_pct": services.booking_fee_pct,   # taux reel d'un booking
        "cancellation_grace_hours": CANCELLATION_GRACE_HOURS,
        "cancellation_service_fee_pct": int(CANCELLATION_SERVICE_FEE_PCT),
        "cancellation_service_fee_cap": int(CANCELLATION_SERVICE_FEE_CAP),
        "late_cancellation_hours": LATE_CANCELLATION_HOURS,
        "late_cancellation_fee_pct": int(LATE_CANCELLATION_FEE_PCT),
        # CSRF
        "csrf_token": security.csrf_token,
        "csrf_input": security.csrf_input,
        # Cache-busting des assets statiques (?v=<mtime>)
        "static_v": _static_v,
        # SEO : base canonique + JSON-LD globaux (Organization + WebSite)
        "canonical_base": seo.CANONICAL_BASE,
        "google_site_verification": GOOGLE_SITE_VERIFICATION,
        "seo_global": seo.global_ld(getattr(g, "lang", i18n.DEFAULT)),
        "seo": {},   # defaut ; surcharge par page via render_template(seo=...)
    }


def _fee_tiers_display() -> list:
    """Paliers de commission lisibles : [{'from': 1, 'to': 3, 'pct': 20}, ...]
    ('to' = None pour le dernier palier)."""
    out = []
    for i, (threshold, pct) in enumerate(PLATFORM_FEE_TIERS):
        nxt = PLATFORM_FEE_TIERS[i + 1][0] if i + 1 < len(PLATFORM_FEE_TIERS) else None
        out.append({"from": threshold + 1, "to": nxt, "pct": int(pct)})
    return out


def _static_v(filename: str) -> str:
    """URL d'un asset statique avec un suffixe ?v=<mtime> pour casser le cache navigateur."""
    url = url_for("static", filename=filename)
    try:
        path = os.path.join(app.static_folder or "static", filename)
        return f"{url}?v={int(os.path.getmtime(path))}"
    except OSError:
        return url


def _label(pairs, code, fallback=""):
    for k, v in pairs:
        if k == code:
            return v
    return fallback or code


def _mission_label(code: str) -> str:
    """Libelle d'un type de mission, traduit via i18n si dispo (cle mission.<code>)."""
    lang = getattr(g, "lang", i18n.DEFAULT)
    key = f"mission.{code}"
    if i18n._T.get(key):
        return i18n.t(key, lang=lang)
    return _label(MISSION_TYPES, code, code)


def _mission_group_label(name: str) -> str:
    """Libelle d'un groupe de specialites, traduit via i18n (cle mission_group.<nom>)."""
    lang = getattr(g, "lang", i18n.DEFAULT)
    key = f"mission_group.{name}"
    if i18n._T.get(key):
        return i18n.t(key, lang=lang)
    return name


def _pilot_payout_context() -> dict:
    """Contexte du bandeau « Activez vos paiements » pour le pilote connecte.
    Non-pilote / anonyme -> pas de bandeau (valeurs True)."""
    u = getattr(g, "user", None)
    if not u or u.get("role") not in ("pilot", "both"):
        return {"pilot_payouts_ok": True, "pilot_country_payable": True}
    prof = services.get_pilot_profile(u["id"])
    return {
        "pilot_payouts_ok": bool(prof and prof.get("stripe_charges_enabled")),
        "pilot_country_payable": payments.country_is_payable(u.get("country") or ""),
    }


@app.context_processor
def _inject_helpers():
    from i18n import status_label
    return {
        "mission_label": _mission_label,
        "profile_kinds": PROFILE_KINDS,
        "kind_label": lambda c, short=False: i18n.t(
            f"kind.{c if c in PROFILE_KIND_CODES else 'pro'}" + (".short" if short else ""),
            lang=getattr(g, "lang", i18n.DEFAULT)),
        # Nom public d'une fiche : ecole en clair, personne masquee
        "public_name": services.public_name,
        "drone_label": lambda c: _label(DRONE_CATEGORIES, c, c),
        "auth_label": lambda c: _label(LICENCE_AUTHORITIES, c, c),
        "mission_groups": __import__("config").MISSION_TYPE_GROUPS,
        "mission_group_label": _mission_group_label,
        "status_label": status_label,
        # Anti-bypass : nom anonymise dans toutes les listes / cartes
        # (la fiche detail decide cas par cas via has_funded_relation).
        "mask_name": services.mask_full_name,
        **_pilot_payout_context(),
    }


# ---------------------------------------------------------------------------
# Helpers requete
# ---------------------------------------------------------------------------

def _to_float(v, default=None):
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _to_int(v, default=None):
    try:
        return int(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _to_bool(v):
    return str(v).lower() in {"1", "true", "on", "yes", "oui"}


def _profile_kind(value) -> str:
    """Type de profil valide ('' = tous)."""
    v = (value or "").strip()
    return v if v in PROFILE_KIND_CODES else ""


def _resolve_near(country: str = "") -> dict:
    """Lit les parametres geo d'une recherche : `lat`/`lng` explicites
    (geoloc navigateur, pastille zone) ou `near` = code postal / adresse /
    ville a geocoder (cf. geocode.py). Retourne un dict :
      lat, lng        -> centre (ou None)
      near            -> saisie brute (re-affichee dans le champ)
      near_label      -> libelle resolu (« 75011 Paris, France »)
      near_error      -> True si la saisie n'a pas pu etre localisee
      radius_km       -> rayon choisi (borne)
      near_only       -> True = exclure hors rayon (sinon tri seulement)
    """
    near = (request.args.get("near") or "").strip()[:geocode.MAX_QUERY_LEN]
    lat = _to_float(request.args.get("lat"))
    lng = _to_float(request.args.get("lng"))
    radius_km = _to_int(request.args.get("radius_km"), DEFAULT_SEARCH_RADIUS_KM)
    radius_km = max(1, min(radius_km or DEFAULT_SEARCH_RADIUS_KM, SEARCH_RADIUS_CHOICES[-1]))
    out = {"near": near, "near_label": "", "near_error": False,
           "radius_km": radius_km,
           "near_only": _to_bool(request.args.get("near_only", "0"))}
    if near and (lat is None or lng is None):
        geo = geocode.lookup(near, country or "", getattr(g, "lang", i18n.DEFAULT))
        if geo:
            lat, lng = geo["lat"], geo["lng"]
            out["near_label"] = geo["label"]
        else:
            out["near_error"] = True
    out["lat"], out["lng"] = lat, lng
    return out


def _map_center(geo: dict) -> dict:
    """Centre a passer a la carte (_map.html) quand une zone est resolue."""
    if geo.get("lat") is None or geo.get("lng") is None:
        return {}
    return {"lat": geo["lat"], "lng": geo["lng"],
            "label": geo.get("near_label") or "", "radius_km": geo["radius_km"]}


# ---------------------------------------------------------------------------
# Selecteur de langue
# ---------------------------------------------------------------------------

@app.route("/lang/<code>")
def set_lang(code):
    if code not in i18n.SUPPORTED:
        abort(404)
    next_url = request.args.get("next") or request.referrer or url_for("index")
    resp = make_response(redirect(next_url))
    resp.set_cookie(
        i18n.COOKIE, code,
        max_age=i18n.COOKIE_MAX_AGE,
        httponly=False, samesite="Lax",
    )
    return resp


# ---------------------------------------------------------------------------
# Pages publiques
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        stats=services.public_stats(),
        featured_pilots=services.featured_pilots(8),
        latest_missions=services.latest_missions(8),
        country_breakdown=services.country_breakdown(12),
        faq_entries=content.faq(getattr(g, "lang", i18n.DEFAULT), featured_only=True),
        seo=seo.home(getattr(g, "lang", i18n.DEFAULT)),
    )


@app.route("/robots.txt")
def robots_txt():
    lines = ["User-agent: *", "Allow: /$", "Allow: /"]
    lines += [f"Disallow: {d}" for d in seo.ROBOTS_DISALLOW]
    lines.append("")
    lines.append(f"Sitemap: {seo.CANONICAL_BASE}/sitemap.xml")
    resp = make_response("\n".join(lines) + "\n")
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    return resp


@app.route("/.well-known/assetlinks.json")
def assetlinks_json():
    """App Links Android : declare que l'app mobile (package + certificat)
    est autorisee a ouvrir les liens du site directement."""
    return jsonify([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": ANDROID_PACKAGE,
            "sha256_cert_fingerprints": ANDROID_CERT_SHA256,
        },
    }])


@app.route("/sw.js")
def service_worker():
    """Service worker servi a la racine (scope '/') plutot que /static/."""
    resp = send_from_directory(app.static_folder or "static", "sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/offline")
def offline():
    """Page de secours affichee par le service worker sans reseau."""
    return render_template("offline.html")


@app.route("/sitemap.xml")
def sitemap_xml():
    base = seo.CANONICAL_BASE
    entries = [(base + path, "", prio, freq) for path, prio, freq in seo.PUBLIC_ROUTES]
    for p in services.sitemap_pilots():
        entries.append((f"{base}/pilotes/{p['id']}",
                        str(p.get("lastmod") or "")[:10], "0.7", "weekly"))
    for m in services.sitemap_missions():
        entries.append((f"{base}/missions/{m['id']}",
                        str(m.get("lastmod") or "")[:10], "0.6", "weekly"))
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, prio, freq in entries:
        xml.append("  <url>")
        xml.append(f"    <loc>{loc}</loc>")
        if lastmod:
            xml.append(f"    <lastmod>{lastmod}</lastmod>")
        xml.append(f"    <changefreq>{freq}</changefreq>")
        xml.append(f"    <priority>{prio}</priority>")
        xml.append("  </url>")
    xml.append("</urlset>")
    resp = make_response("\n".join(xml))
    resp.headers["Content-Type"] = "application/xml; charset=utf-8"
    return resp


# ---------------------------------------------------------------------------
# Pages legales (Loi 25 Quebec — confidentialite, mentions, CGU, cookies)
# ---------------------------------------------------------------------------

@app.route("/confidentialite")
def page_privacy():
    return render_template("legal_privacy.html")


@app.route("/mentions-legales")
def page_legal():
    return render_template("legal_notice.html")


@app.route("/cgu")
def page_terms():
    return render_template("legal_terms.html")


@app.route("/cookies")
def page_cookies():
    return render_template("legal_cookies.html")


# ---------------------------------------------------------------------------
# FAQ + contact (pages de confiance : ce qu'un annuaire local affiche, en
# version marketplace mondiale)
# ---------------------------------------------------------------------------

@app.route("/ecoles")
def schools():
    """Page publique « Écoles & organismes de formation » : pourquoi
    s'inscrire, la liste des ecoles deja presentes, l'inscription directe."""
    lang = getattr(g, "lang", i18n.DEFAULT)
    schools_list = services.search_pilots(kind="school", only_available=False, limit=100)
    return render_template(
        "schools.html", schools=schools_list,
        seo=seo.schools_page(lang),
    )


@app.route("/faq")
def faq():
    lang = getattr(g, "lang", i18n.DEFAULT)
    return render_template(
        "faq.html",
        faq_entries=content.faq(lang),
        faq_categories=content.faq_categories(lang),
        seo=seo.faq_page(lang),
    )


CONTACT_TOPICS = [
    ("client",      {"fr": "Je cherche un pilote / une mission en cours",
                     "en": "I'm looking for a pilot / an ongoing mission"}),
    ("pilot",       {"fr": "Je suis pilote (profil, brevets, paiements)",
                     "en": "I'm a pilot (profile, licences, payouts)"}),
    ("partnership", {"fr": "Partenariat, école, boutique, location",
                     "en": "Partnership, school, shop, rental"}),
    ("press",       {"fr": "Presse / média", "en": "Press / media"}),
    ("other",       {"fr": "Autre question", "en": "Other question"}),
]


def _contact_topics(lang: str) -> list:
    return [(code, labels["en" if lang == "en" else "fr"]) for code, labels in CONTACT_TOPICS]


def _contact_context(form: dict) -> dict:
    lang = getattr(g, "lang", i18n.DEFAULT)
    u = getattr(g, "user", None)
    return {
        "topics": _contact_topics(lang),
        "form": {
            "name": form.get("name") or (u.get("full_name") if u else "") or "",
            "email": form.get("email") or (u.get("email") if u else "") or "",
            "topic": form.get("topic") or "",
            "message": form.get("message") or "",
        },
        "seo": seo.contact_page(lang),
    }


@app.route("/contact", methods=["GET"])
def contact_form():
    return render_template("contact.html", **_contact_context({}))


@app.route("/contact", methods=["POST"])
@security.rate_limit(per_minute=3, per_hour=12)
def contact_submit():
    lang = getattr(g, "lang", i18n.DEFAULT)
    fr = lang != "en"
    form = {k: (request.form.get(k) or "").strip() for k in
            ("name", "email", "topic", "message", "website")}
    # Pot de miel : un humain ne voit pas le champ `website` (masque en CSS) ;
    # un bot le remplit -> on fait semblant d'accepter, sans rien envoyer.
    if form["website"]:
        flash("Merci, votre message est bien enregistré." if fr
              else "Thank you, your message has been recorded.", "success")
        return redirect(url_for("contact_form"))
    topics = dict(_contact_topics(lang))
    errors = []
    if len(form["name"]) < 2:
        errors.append("Indiquez votre nom." if fr else "Please enter your name.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", form["email"]):
        errors.append("Adresse courriel invalide." if fr else "Invalid email address.")
    if form["topic"] not in topics:
        errors.append("Choisissez un sujet." if fr else "Please pick a topic.")
    if len(form["message"]) < 10:
        errors.append("Votre message est trop court (10 caractères minimum)." if fr
                      else "Your message is too short (10 characters minimum).")
    if len(form["message"]) > 5000:
        errors.append("Votre message est trop long (5 000 caractères maximum)." if fr
                      else "Your message is too long (5,000 characters maximum).")
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("contact.html", **_contact_context(form)), 400

    topic_label = topics[form["topic"]]
    user = getattr(g, "user", None)
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    # 1) Enregistre d'abord (boite de reception admin) : un SMTP en panne ne
    #    fait jamais perdre une demande.
    msg_id = services.create_contact_message(
        name=form["name"], email=form["email"], topic=topic_label,
        body=form["message"], user_id=user["id"] if user else None, ip=ip,
    )
    # 2) Courriel a l'equipe, synchrone pour savoir s'il est parti.
    notified = mailer.send_contact_message(
        to=CONTACT_EMAIL, name=form["name"], email=form["email"],
        topic=topic_label, body=form["message"],
        user={"id": user["id"], "username": user["username"]} if user else None,
        ip=ip, async_=False,
    )
    if notified:
        services.mark_contact_notified(msg_id)
    else:
        log.error("contact #%s : courriel equipe NON transmis (SMTP) — visible "
                  "dans /admin/messages", msg_id)
    # 3) Accuse de reception a l'expediteur (best effort).
    mailer.send_contact_ack(
        to=form["email"], name=form["name"], topic=topic_label,
        body=form["message"], reply_hours=CONTACT_REPLY_HOURS,
    )
    security.audit(user["id"] if user else None, "contact_message",
                   target=form["email"],
                   payload={"topic": form["topic"], "id": msg_id, "notified": notified})
    flash((f"Merci, votre message est bien enregistré. On vous répond par courriel "
           f"sous {CONTACT_REPLY_HOURS} h ouvrables.") if fr
          else (f"Thank you, your message has been recorded. We reply by email "
                f"within {CONTACT_REPLY_HOURS} business hours."),
          "success")
    return redirect(url_for("contact_form"))


# Les API JSON publiques (/api/near, /api/pilotes, /api/missions) ne sont pas
# authentifiees : elles ne doivent exposer QUE ce que voit un visiteur anonyme
# sur les pages HTML (nom masque « Prenom X. », coordonnees floutees). Les
# routines de recherche renvoient les lignes brutes (nom complet, lat/lng
# exacts, username, bio, nom du client, adresse de mission) pour l'usage
# serveur : on les assainit ici avant serialisation (minimisation, Loi 25).
def _public_pilot_json(p: dict) -> dict:
    out = dict(p)
    # Nom public masque (les ecoles gardent leur raison sociale, non nominative).
    out["full_name"] = services.public_name(p)
    # Coordonnees floutees ~11 km, comme /api/map — jamais l'adresse exacte.
    out["lat"] = services._fuzz_coord(p.get("lat"), 1)
    out["lng"] = services._fuzz_coord(p.get("lng"), 1)
    out.pop("username", None)   # handle d'identite AubeMail -> pas public
    out.pop("bio", None)        # texte libre potentiellement nominatif
    return out


def _public_mission_json(m: dict) -> dict:
    out = dict(m)
    out.pop("client_name", None)     # nom du donneur d'ordre -> non public
    out.pop("client_user_id", None)
    out.pop("address", None)         # adresse precise du chantier -> non public
    out["lat"] = services._fuzz_coord(m.get("lat"), 2)
    out["lng"] = services._fuzz_coord(m.get("lng"), 2)
    return out


@app.route("/api/near")
@security.rate_limit(per_minute=60, per_hour=600)
def api_near():
    lat = _to_float(request.args.get("lat"))
    lng = _to_float(request.args.get("lng"))
    radius = _to_int(request.args.get("radius_km"), 100)
    if lat is None or lng is None:
        return jsonify({"error": "lat/lng requis"}), 400
    res = services.near_geo(lat, lng, radius_km=radius)
    return jsonify({
        "pilots": [_public_pilot_json(p) for p in res.get("pilots", [])],
        "missions": [_public_mission_json(m) for m in res.get("missions", [])],
    })


@app.route("/api/country-breakdown")
@security.rate_limit(per_minute=60, per_hour=600)
def api_country_breakdown():
    return jsonify(services.country_breakdown(_api_limit(50)))


@app.route("/api/map")
@security.rate_limit(per_minute=60, per_hour=600)
def api_map():
    # Marqueurs carte : pilotes (coords floutees ~11 km) + missions ouvertes.
    return jsonify(services.map_markers(
        country=request.args.get("country", "").strip(),
        mission_type=request.args.get("mission_type", "").strip(),
        kind=_profile_kind(request.args.get("kind")),
        limit=_api_limit(500),
    ))


@app.route("/pilotes")
def pilots_search():
    # 'near_only' borne au rayon ; sinon lat/lng ne servent qu'au tri par
    # distance (les pilotes lointains restent visibles -> pilote de partout).
    # `near` (code postal / adresse / ville) est geocode cote serveur.
    country = request.args.get("country", "").strip()
    geo = _resolve_near(country)
    params = {
        "country": country,
        "city": request.args.get("city", "").strip(),
        "mission_type": request.args.get("mission_type", "").strip(),
        "capability": request.args.get("capability", "").strip(),
        "min_rating": _to_float(request.args.get("min_rating"), 0) or 0,
        "only_available": _to_bool(request.args.get("only_available", "1")),
        "only_verified": _to_bool(request.args.get("only_verified", "0")),
        "only_insured": _to_bool(request.args.get("only_insured", "0")),
        "authority": request.args.get("authority", "").strip(),
        "kind": _profile_kind(request.args.get("kind")),
        "text": request.args.get("q", "").strip(),
    }
    pilots = services.search_pilots(
        lat=geo["lat"], lng=geo["lng"], radius_km=geo["radius_km"],
        strict_radius=geo["near_only"], **params,
    )
    params.update(geo)
    return render_template(
        "pilots_search.html", pilots=pilots, params=params,
        kind_counts=services.count_pilots_by_kind(params["only_available"]),
        map_center=_map_center(geo),
        seo=seo.pilots_list(getattr(g, "lang", i18n.DEFAULT), params),
    )


@app.route("/missions")
def missions_search():
    # Pilote de n'importe ou : par defaut on montre les missions de PARTOUT.
    # 'anywhere' (ou pays vide) = aucun filtre pays ; lat/lng servent au tri
    # par distance sans EXCLURE (strict_radius=False) sauf si 'near_only'.
    anywhere = _to_bool(request.args.get("anywhere", "0"))
    country = "" if anywhere else request.args.get("country", "").strip()
    geo = _resolve_near(country)
    params = {
        "country": country,
        "city": request.args.get("city", "").strip(),
        "mission_type": request.args.get("mission_type", "").strip(),
        "only_urgent": _to_bool(request.args.get("only_urgent", "0")),
        "anywhere": anywhere,
        **geo,
    }
    missions = services.search_missions(
        status="open",
        country=params["country"], city=params["city"],
        mission_type=params["mission_type"], only_urgent=params["only_urgent"],
        lat=geo["lat"], lng=geo["lng"], radius_km=geo["radius_km"],
        strict_radius=geo["near_only"],
    )
    return render_template(
        "missions_search.html", missions=missions, params=params,
        map_center=_map_center(geo),
        seo=seo.missions_list(getattr(g, "lang", i18n.DEFAULT)),
    )


@app.route("/pilotes/<int:user_id>")
def pilot_detail(user_id):
    profile = services.get_pilot_profile(user_id)
    if not profile or profile.get("role") not in ("pilot", "both"):
        abort(404)
    viewer_id = (g.user["id"] if getattr(g, "user", None) else 0)
    reveal = services.has_funded_relation(viewer_id, user_id)
    can_view_credentials = services.client_can_view_pilot_credentials(viewer_id, user_id)
    masked = services.mask_full_name(profile["full_name"])
    # Nom public (masque sauf relation financee), conforme a l'affichage page
    public_name = profile["full_name"] if reveal else masked
    avatar = profile.get("avatar_path") or ""
    avatar_url = (seo.CANONICAL_BASE + "/media/" + avatar[8:]) if avatar.startswith("uploads/") else None
    page_seo = seo.pilot_profile(
        getattr(g, "lang", i18n.DEFAULT),
        name=public_name, city=profile.get("city") or "",
        country=profile.get("country") or "",
        headline=profile.get("headline") or "", bio=profile.get("bio") or "",
        rating=services.pilot_rating(user_id), image=avatar_url, user_id=user_id,
    )
    return render_template(
        "pilot_detail.html",
        pilot=profile,
        reviews=services.reviews_for(user_id),
        reveal_identity=reveal,
        can_view_credentials=can_view_credentials,
        masked_name=masked,
        packages=services.list_pilot_packages(user_id, only_active=True),
        portfolio=services.list_portfolio_items(user_id),
        my_review_booking=services.reviewable_booking_for(viewer_id, user_id),
        seo=page_seo,
    )


@app.route("/pilotes/<int:user_id>/avis", methods=["POST"])
@auth.login_required
def pilot_review(user_id):
    profile = services.get_pilot_profile(user_id)
    if not profile or profile.get("role") not in ("pilot", "both"):
        abort(404)
    booking = services.reviewable_booking_for(g.user["id"], user_id)
    if not booking:
        flash("Vous ne pouvez laisser un avis qu'après une mission réalisée avec ce pilote.", "error")
        return redirect(url_for("pilot_detail", user_id=user_id))
    services.add_review(
        booking_id=booking["id"],
        author_user_id=g.user["id"],
        target_user_id=user_id,
        rating=_to_int(request.form.get("rating"), 5) or 5,
        comment=(request.form.get("comment") or "").strip(),
    )
    flash("Merci pour votre avis.", "success")
    return redirect(url_for("pilot_detail", user_id=user_id) + "#avis")


@app.route("/missions/<int:mission_id>")
def mission_detail(mission_id):
    mission = services.get_mission(mission_id)
    if not mission:
        abort(404)
    user = getattr(g, "user", None)
    all_bids = mission.get("bids") or []
    is_client = bool(user and user["id"] == mission["client_user_id"])
    my_bid = None
    if user and not is_client:
        for b in all_bids:
            if b["pilot_user_id"] == user["id"]:
                my_bid = b
                break
    has_my_bid = my_bid is not None

    # Historique de revisions :
    #   - cote pilote : son propre devis
    #   - cote client : tous les devis (pour comprendre l'evolution)
    bid_revisions = {}
    if is_client:
        for b in all_bids:
            revs = services.list_bid_revisions(b["id"])
            if revs:
                bid_revisions[b["id"]] = revs
    elif my_bid:
        revs = services.list_bid_revisions(my_bid["id"])
        if revs:
            bid_revisions[my_bid["id"]] = revs

    # Fil de discussion pre-booking : client <-> chaque pilote ayant
    # soumis un devis (cote client), ou pilote <-> client (cote pilote).
    threads = {}
    if user:
        if is_client:
            for b in all_bids:
                threads[b["pilot_user_id"]] = services.thread(
                    mission_id, user["id"], b["pilot_user_id"],
                )
        elif has_my_bid:
            threads[mission["client_user_id"]] = services.thread(
                mission_id, user["id"], mission["client_user_id"],
            )

    # Loi 101 / 96 : si client OU pilote (visiteur courant) est au
    # Quebec, le devis signe doit etre redige en francais.
    client_party = db.fetchone(
        "SELECT country, city FROM users WHERE id=?",
        (mission["client_user_id"],),
    )
    pilot_party = None
    if user and not is_client:
        pilot_party = {"country": user.get("country"), "city": user.get("city")}
    french_only = services.contract_french_only([
        dict(client_party) if client_party else None,
        pilot_party,
    ])

    # Reservation issue d'un devis accepte (pour lier vers le suivi de mission)
    bookings_by_bid = {}
    if is_client:
        for b in all_bids:
            if b["status"] == "accepted":
                bk = services.get_booking_by_bid(b["id"])
                if bk:
                    bookings_by_bid[b["id"]] = bk
    elif my_bid and my_bid["status"] == "accepted":
        bk = services.get_booking_by_bid(my_bid["id"])
        if bk:
            bookings_by_bid[my_bid["id"]] = bk

    mission_seo = seo.mission_posting(
        getattr(g, "lang", i18n.DEFAULT), mission=mission,
        mission_type_label=_mission_label(mission.get("mission_type") or ""),
        url=f"{seo.CANONICAL_BASE}/missions/{mission_id}",
    )
    return render_template(
        "mission_detail.html",
        mission=mission,
        has_my_bid=has_my_bid,
        is_client=is_client,
        my_bid=my_bid,
        bid_count=len(all_bids),
        bid_revisions=bid_revisions,
        threads=threads,
        french_only=french_only,
        bookings_by_bid=bookings_by_bid,
        seo=mission_seo,
    )


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------

@app.route("/inscription", methods=["GET", "POST"])
@security.rate_limit(per_minute=4, per_hour=15)
def register():
    if request.method == "POST":
        username = auth.normalize_username(request.form.get("username") or "")
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        full_name = (request.form.get("full_name") or "").strip()
        role = request.form.get("role") or "client"
        country = (request.form.get("country") or "").strip()
        city = (request.form.get("city") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        lat = _to_float(request.form.get("lat"))
        lng = _to_float(request.form.get("lng"))

        if role not in ("client", "pilot", "both"):
            role = "client"
        kind = _profile_kind(request.form.get("kind")) or "pro"
        if not username or not password or not full_name:
            flash("Identifiant, mot de passe et nom complet sont requis.", "error")
            return render_template("register.html")
        if password != confirm:
            flash("Les mots de passe ne correspondent pas.", "error")
            return render_template("register.html")
        if db.fetchone("SELECT 1 FROM users WHERE username=?", (username,)):
            flash("Cet identifiant est deja pris.", "error")
            return render_template("register.html")

        # SECURITE : si l'identifiant correspond a un compte systeme AubeMail
        # (mot de passe gere par PAM, prod Linux), l'inscription ne doit PAS
        # provisionner un profil et ouvrir une session sans preuve que la
        # personne detient ce compte. Sinon, un attaquant reclame l'identite
        # AubeMail de quelqu'un d'autre (qui n'a pas encore de profil AubePilot)
        # et se retrouve connecte sous son nom. On EXIGE donc que le mot de
        # passe AubeMail fourni soit valide. (No-op en dev/macOS : pas de PAM.)
        if auth.password_managed_by_aubemail(username) \
                and not auth.authenticate(username, password):
            flash(
                "Ce compte AubeMail existe déjà. Connectez-vous avec son mot "
                "de passe AubeMail pour compléter votre profil AubePilot.",
                "error",
            )
            return render_template("register.html")

        try:
            user_id = auth.create_user(
                username=username, password=password, full_name=full_name,
                role=role, country=country, city=city, phone=phone, lat=lat, lng=lng,
                kind=kind,
            )
            if role in ("pilot", "both") and kind == "school":
                # Une ecole / un organisme s'affiche sous son nom : initialise
                # avec le nom saisi + les champs propres aux organismes
                # (site web, formations proposees), modifiables dans le profil.
                services.upsert_pilot_profile(
                    user_id, business_name=full_name,
                    portfolio_url=(request.form.get("website") or "").strip()[:300] or None,
                    school_programs=(request.form.get("school_programs") or "").strip()[:2000] or None,
                )
        except auth.AubeMailRequiredError:
            flash(
                "Ce compte n'existe pas dans AubeMail. Créez-le d'abord sur "
                "<a href='https://aubemail.com'>aubemail.com</a>, "
                "puis revenez ici pour compléter votre profil pilote.",
                "error",
            )
            return render_template("register.html")
        token = auth.create_session(user_id, request.user_agent.string, request.remote_addr or "")
        resp = make_response(redirect(url_for("dashboard")))
        resp.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="Lax",
                        secure=app.config.get("SESSION_COOKIE_SECURE", False),
                        max_age=60 * 60 * 24 * 30)
        flash("Bienvenue sur AubePilot.", "success")
        return resp
    return render_template("register.html")


@app.route("/connexion", methods=["GET", "POST"])
@security.rate_limit(per_minute=8, per_hour=40)
def login():
    next_url = security.safe_next(
        request.args.get("next") or request.form.get("next"),
        fallback=url_for("dashboard"),
    )
    if request.method == "POST":
        username = auth.normalize_username(request.form.get("username") or "")
        password = request.form.get("password") or ""
        if not auth.authenticate(username, password):
            flash("Identifiants invalides.", "error")
            return render_template("login.html", next_url=next_url)
        # Si le compte AubeMail existe mais pas encore le profil AubePilot,
        # on le cree a la volee — cas d'un user qui se cree sur AubeMail
        # puis vient ici pour la 1ere fois.
        row = db.fetchone("SELECT id, deleted_at FROM users WHERE username=?", (username,))
        if row and row["deleted_at"]:
            flash("Ce compte a été supprimé.", "error")
            return render_template("login.html", next_url=next_url)
        if not row:
            email = auth.normalize_email(username, None)
            cur = db.execute(
                "INSERT INTO users (username, email, full_name, role) "
                "VALUES (?, ?, ?, 'client')",
                (username, email, username),
            )
            row = {"id": cur.lastrowid}
        # Rotation de session : on nettoie tout etat anonyme (CSRF token, etc)
        # avant de poser la nouvelle session authentifiee. Defense anti session
        # fixation : un token CSRF qui aurait fuite avant le login devient
        # inutilisable.
        from flask import session as flask_session
        flask_session.clear()
        token = auth.create_session(row["id"], request.user_agent.string, request.remote_addr or "")
        resp = make_response(redirect(next_url))
        resp.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="Lax",
                        secure=app.config.get("SESSION_COOKIE_SECURE", False),
                        max_age=60 * 60 * 24 * 30)
        return resp
    return render_template("login.html", next_url=next_url)


@app.route("/deconnexion", methods=["POST", "GET"])
def logout():
    auth.revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
    resp = make_response(redirect(url_for("index")))
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


# ---------------------------------------------------------------------------
# Espace utilisateur
# ---------------------------------------------------------------------------

@app.route("/espace")
@auth.login_required
def dashboard():
    user = g.user
    is_pilot = user["role"] in ("pilot", "both")
    is_client = user["role"] in ("client", "both")
    return render_template(
        "dashboard.html",
        is_pilot=is_pilot,
        is_client=is_client,
        my_pilot_profile=services.get_pilot_profile(user["id"]) if is_pilot else None,
        my_missions=services.list_missions_by_client(user["id"]) if is_client else [],
        my_bids=services.list_missions_by_pilot(user["id"]) if is_pilot else [],
        my_bookings=services.list_bookings_for(user["id"]),
        unread=services.unread_count(user["id"]),
        admin_new_messages=(services.count_contact_messages("new")
                            if user.get("is_admin") else 0),
        admin_pending_certs=(services.count_certifications_pending()
                             if user.get("is_admin") else 0),
    )


# ---------------------------------------------------------------------------
# Parametres du compte
# ---------------------------------------------------------------------------

def _settings_context():
    uid = g.user["id"]
    user = dict(db.fetchone("SELECT * FROM users WHERE id=?", (uid,)) or g.user)
    return {
        "user": user,
        "is_pilot": user["role"] in ("pilot", "both"),
        "is_client": user["role"] in ("client", "both"),
        "name_locked": services.is_identity_locked(uid),
        "password_managed_elsewhere": auth.password_managed_by_aubemail(user["username"]),
        "sessions": services.list_sessions(uid, auth.current_sid()),
        "deletion_blockers": services.account_deletion_blockers(uid),
    }


@app.route("/espace/parametres")
@auth.login_required
def settings():
    return render_template("settings.html", **_settings_context())


@app.route("/espace/parametres/compte", methods=["POST"])
@auth.login_required
def settings_account():
    lang = (request.form.get("lang") or "").strip()
    res = services.update_account(
        g.user["id"],
        full_name=request.form.get("full_name"),
        phone=request.form.get("phone"),
        country=request.form.get("country"),
        city=request.form.get("city"),
        lat=_to_float(request.form.get("lat")),
        lng=_to_float(request.form.get("lng")),
        lang=lang,
    )
    flash("Paramètres enregistrés." + (" (Le nom est verrouillé : passez par une demande de changement de nom.)"
                                       if res["name_locked"] and request.form.get("full_name") else ""),
          "success")
    resp = make_response(redirect(url_for("settings")))
    if lang in i18n.SUPPORTED:
        resp.set_cookie(i18n.COOKIE, lang, max_age=i18n.COOKIE_MAX_AGE, httponly=False, samesite="Lax")
    return resp


@app.route("/espace/parametres/notifications", methods=["POST"])
@auth.login_required
def settings_notifications():
    services.update_notification_prefs(
        g.user["id"], {k: _to_bool(request.form.get(k, "0")) for k in services.NOTIFY_KEYS},
    )
    flash("Préférences de notification enregistrées.", "success")
    return redirect(url_for("settings"))


@app.route("/espace/parametres/mot-de-passe", methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=5, per_hour=20)
def settings_password():
    new = request.form.get("new") or ""
    if new != (request.form.get("confirm") or ""):
        flash("Les deux nouveaux mots de passe ne correspondent pas.", "error")
        return redirect(url_for("settings"))
    ok, reason = auth.change_password(g.user["username"], request.form.get("current") or "", new)
    if ok:
        # Les autres appareils sont deconnectes ; celui-ci reste connecte.
        auth.revoke_all_sessions(g.user["id"], keep_sid=auth.current_sid())
        security.audit(g.user["id"], "password_changed")
        flash("Mot de passe modifié. Les autres appareils ont été déconnectés.", "success")
    else:
        flash({"aubemail": "Ce mot de passe se change sur AubeMail.",
               "wrong": "Mot de passe actuel incorrect.",
               "weak": "Le nouveau mot de passe doit faire au moins 8 caractères."}.get(reason, "Échec."),
              "error")
    return redirect(url_for("settings"))


@app.route("/espace/parametres/sessions/deconnecter", methods=["POST"])
@auth.login_required
def settings_sessions_revoke():
    n = auth.revoke_all_sessions(g.user["id"], keep_sid=auth.current_sid())
    flash(f"{n} autre(s) appareil(s) déconnecté(s).", "success")
    return redirect(url_for("settings"))


@app.route("/espace/parametres/export.json")
@auth.login_required
@security.rate_limit(per_minute=3, per_hour=10)
def settings_export():
    data = services.export_user_data(g.user["id"])
    security.audit(g.user["id"], "data_export")
    resp = jsonify(data)
    resp.headers["Content-Disposition"] = f'attachment; filename="aubepilot-{g.user["username"]}.json"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/espace/parametres/supprimer", methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=3, per_hour=10)
def settings_delete():
    if (request.form.get("confirm_word") or "").strip() != "SUPPRIMER":
        flash("Tapez SUPPRIMER pour confirmer.", "error")
        return redirect(url_for("settings"))
    if not auth.password_managed_by_aubemail(g.user["username"]) and \
            not auth.authenticate(g.user["username"], request.form.get("password") or ""):
        flash("Mot de passe incorrect.", "error")
        return redirect(url_for("settings"))
    res = services.delete_account(g.user["id"])
    if not res["ok"]:
        flash("Suppression impossible : " + " ; ".join(res["blockers"]) + ".", "error")
        return redirect(url_for("settings"))
    resp = make_response(redirect(url_for("index")))
    resp.delete_cookie(SESSION_COOKIE_NAME)
    flash("Votre compte a été supprimé. Merci d'avoir volé avec nous.", "info")
    return resp


# ---------------------------------------------------------------------------
# Profil pilote (edition)
# ---------------------------------------------------------------------------

@app.route("/espace/pilote", methods=["GET", "POST"])
@auth.login_required
def pilot_edit():
    user = g.user
    if user["role"] not in ("pilot", "both"):
        # bascule de role si l'utilisateur veut devenir pilote
        if request.method == "POST" and request.form.get("become_pilot"):
            new_role = "both" if user["role"] == "client" else "pilot"
            db.execute("UPDATE users SET role=? WHERE id=?", (new_role, user["id"]))
            db.execute("INSERT OR IGNORE INTO pilot_profiles (user_id) VALUES (?)", (user["id"],))
            services.upsert_pilot_profile(user["id"], kind=_profile_kind(request.form.get("kind")) or "pro")
            return redirect(url_for("pilot_edit"))
        return render_template("pilot_become.html")

    if request.method == "POST":
        services.upsert_pilot_profile(
            user["id"],
            headline=(request.form.get("headline") or "").strip() or None,
            business_name=(request.form.get("business_name") or "").strip() or None,
            years_experience=_to_int(request.form.get("years_experience"), 0),
            hourly_rate=_to_float(request.form.get("hourly_rate")),
            daily_rate=_to_float(request.form.get("daily_rate")),
            currency=(request.form.get("currency") or DEFAULT_CURRENCY).upper(),
            travel_radius_km=_to_int(request.form.get("travel_radius_km"), 50),
            accepts_remote=1 if _to_bool(request.form.get("accepts_remote")) else 0,
            insurance=1 if _to_bool(request.form.get("insurance")) else 0,
            insurance_company=(request.form.get("insurance_company") or "").strip() or None,
            insurance_policy=(request.form.get("insurance_policy") or "").strip() or None,
            is_available=1 if _to_bool(request.form.get("is_available")) else 0,
            languages=(request.form.get("languages") or "").strip() or None,
            portfolio_url=(request.form.get("portfolio_url") or "").strip() or None,
            accepts_urgent=1 if _to_bool(request.form.get("accepts_urgent")) else 0,
            kind=_profile_kind(request.form.get("kind")) or "pro",
            school_programs=(request.form.get("school_programs") or "").strip()[:2000],
        )
        services.set_pilot_specialties(user["id"], request.form.getlist("specialties"))
        countries = request.form.getlist("territory_country")
        regions = request.form.getlist("territory_region")
        services.set_pilot_territories(
            user["id"],
            [{"country": c, "region": r} for c, r in zip(countries, regions) if c],
        )
        # bio + ville
        db.execute(
            "UPDATE users SET bio=?, city=?, country=?, lat=?, lng=?, phone=? WHERE id=?",
            (
                (request.form.get("bio") or "").strip() or None,
                (request.form.get("city") or "").strip() or None,
                (request.form.get("country") or "").strip() or None,
                _to_float(request.form.get("lat")),
                _to_float(request.form.get("lng")),
                (request.form.get("phone") or "").strip() or None,
                user["id"],
            ),
        )
        flash("Profil pilote mis a jour.", "success")
        return redirect(url_for("pilot_edit"))

    return render_template(
        "pilot_edit.html",
        profile=services.get_pilot_profile(user["id"]),
        identity_locked=services.is_identity_locked(user["id"]),
        pending_name_change=services.has_pending_name_change(user["id"]),
    )


@app.route("/espace/pilote/certification", methods=["POST"])
@auth.login_required
def pilot_add_certification():
    user = g.user
    if user["role"] not in ("pilot", "both"):
        abort(403)
    doc_path = ""
    f = request.files.get("document")
    if f and f.filename:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext in ALLOWED_DOC_EXT:
            safe = f"u{user['id']}_cert_{int(time.time())}.{ext}"
            f.save(os.path.join(UPLOAD_DIR, safe))
            doc_path = f"uploads/{safe}"
    services.add_certification(
        user["id"],
        authority=(request.form.get("authority") or "autre").strip(),
        title=(request.form.get("title") or "").strip(),
        reference=(request.form.get("reference") or "").strip(),
        issued_at=(request.form.get("issued_at") or "").strip(),
        expires_at=(request.form.get("expires_at") or "").strip(),
        document_path=doc_path,
    )
    flash("Certification ajoutee.", "success")
    return redirect(url_for("pilot_edit"))


@app.route("/espace/pilote/certification/<int:cert_id>/supprimer", methods=["POST"])
@auth.login_required
def pilot_delete_certification(cert_id):
    services.delete_certification(cert_id, g.user["id"])
    return redirect(url_for("pilot_edit"))


# ---------------------------------------------------------------------------
# Forfaits pilote (catalogue de packages)
# ---------------------------------------------------------------------------

@app.route("/espace/pilote/forfaits", methods=["GET", "POST"])
@auth.login_required
def pilot_packages():
    user = g.user
    if user["role"] not in ("pilot", "both"):
        abort(403)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        price = _to_float(request.form.get("price"))
        if not title or len(description) < 20 or not price or price <= 0:
            flash(
                "Titre, description (>=20c) et prix sont requis.",
                "error",
            )
            return redirect(url_for("pilot_packages"))
        services.create_pilot_package(
            user["id"],
            title=title,
            description=description,
            price=price,
            currency=(request.form.get("currency") or DEFAULT_CURRENCY).upper(),
            mission_type=(request.form.get("mission_type") or "").strip() or None,
            duration_hours=_to_float(request.form.get("duration_hours")),
            deliverables=(request.form.get("deliverables") or "").strip(),
            capabilities=",".join(request.form.getlist("capabilities")),
            is_active=True,
        )
        flash("Forfait ajoute au catalogue.", "success")
        return redirect(url_for("pilot_packages"))
    return render_template(
        "pilot_packages.html",
        packages=services.list_pilot_packages(user["id"]),
    )


@app.route("/espace/pilote/forfaits/<int:package_id>/modifier", methods=["POST"])
@auth.login_required
def pilot_package_update(package_id):
    user = g.user
    fields = {
        "title": (request.form.get("title") or "").strip()[:160] or None,
        "description": (request.form.get("description") or "").strip()[:4000] or None,
        "price": _to_float(request.form.get("price")),
        "currency": (request.form.get("currency") or DEFAULT_CURRENCY).upper(),
        "mission_type": (request.form.get("mission_type") or "").strip() or None,
        "duration_hours": _to_float(request.form.get("duration_hours")),
        "deliverables": (request.form.get("deliverables") or "").strip()[:2000] or None,
        "capabilities": ",".join(request.form.getlist("capabilities")) or None,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    services.update_pilot_package(package_id, user["id"], **fields)
    flash("Forfait mis a jour.", "success")
    return redirect(url_for("pilot_packages"))


@app.route("/espace/pilote/forfaits/<int:package_id>/toggle", methods=["POST"])
@auth.login_required
def pilot_package_toggle(package_id):
    services.toggle_pilot_package(package_id, g.user["id"])
    return redirect(url_for("pilot_packages"))


@app.route("/espace/pilote/forfaits/<int:package_id>/supprimer", methods=["POST"])
@auth.login_required
def pilot_package_delete(package_id):
    services.delete_pilot_package(package_id, g.user["id"])
    flash("Forfait supprime.", "info")
    return redirect(url_for("pilot_packages"))


# ---------------------------------------------------------------------------
# Demande de changement de nom (verrouillage post-upload brevet)
# ---------------------------------------------------------------------------

@app.route("/profil/changer-nom", methods=["GET", "POST"])
@auth.login_required
def request_name_change():
    user = g.user
    if services.has_pending_name_change(user["id"]):
        flash("Une demande est deja en cours. L'admin la traitera bientot.", "info")
        return redirect(url_for("pilot_edit") if user["role"] in ("pilot", "both") else url_for("dashboard"))

    if request.method == "POST":
        requested = (request.form.get("requested_name") or "").strip()
        reason = (request.form.get("reason") or "").strip()
        if not requested or len(requested) < 3:
            flash("Le nouveau nom complet doit faire au moins 3 caracteres.", "error")
            return render_template("name_change_request.html", current_name=user["full_name"])
        if requested == user["full_name"]:
            flash("Le nom demande est identique au nom actuel.", "error")
            return render_template("name_change_request.html", current_name=user["full_name"])

        justif_path = ""
        f = request.files.get("justif")
        if not f or not f.filename:
            flash("Un justificatif officiel (carte d'identite, passeport, etc.) est obligatoire.", "error")
            return render_template("name_change_request.html", current_name=user["full_name"])
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_DOC_EXT:
            flash(f"Format non accepte. Utiliser : {', '.join(sorted(ALLOWED_DOC_EXT))}.", "error")
            return render_template("name_change_request.html", current_name=user["full_name"])
        safe = f"u{user['id']}_namechange_{int(time.time())}.{ext}"
        f.save(os.path.join(UPLOAD_DIR, safe))
        justif_path = f"uploads/{safe}"

        services.create_name_change_request(
            user_id=user["id"],
            current_name=user["full_name"],
            requested_name=requested,
            reason=reason,
            justif_path=justif_path,
        )
        flash("Demande envoyee. Un admin la traitera sous 48h.", "success")
        return redirect(url_for("pilot_edit") if user["role"] in ("pilot", "both") else url_for("dashboard"))

    return render_template("name_change_request.html", current_name=user["full_name"])


@app.route("/admin/changements-nom")
@auth.admin_required
def admin_name_changes():
    return render_template(
        "admin_name_changes.html",
        requests=services.list_pending_name_changes(),
    )


@app.route("/admin/changements-nom/<int:req_id>/justif")
@auth.admin_required
def admin_name_change_justif(req_id):
    req = services.get_name_change_request(req_id)
    if not req or not req.get("justif_path"):
        abort(404)
    rel = req["justif_path"]
    if not rel.startswith("uploads/"):
        abort(404)
    filename = rel[len("uploads/"):]
    resp = make_response(send_from_directory(UPLOAD_DIR, filename, as_attachment=False))
    resp.headers["Cache-Control"] = "private, no-store, max-age=0"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Apercu integre dans /admin/certifications (iframe / img same-origin) :
    # les en-tetes globaux (DENY / frame-ancestors 'none') sont poses en
    # setdefault, on les remplace ici par une version same-origin.
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@app.route("/admin/changements-nom/<int:req_id>/approuver", methods=["POST"])
@auth.admin_required
def admin_approve_name_change(req_id):
    note = (request.form.get("note") or "").strip()
    if services.approve_name_change(req_id, g.user["id"], note):
        flash("Changement de nom valide.", "success")
    else:
        flash("Demande introuvable ou deja traitee.", "error")
    return redirect(url_for("admin_name_changes"))


@app.route("/admin/changements-nom/<int:req_id>/refuser", methods=["POST"])
@auth.admin_required
def admin_reject_name_change(req_id):
    note = (request.form.get("note") or "").strip()
    if services.reject_name_change(req_id, g.user["id"], note):
        flash("Demande refusee.", "success")
    else:
        flash("Demande introuvable ou deja traitee.", "error")
    return redirect(url_for("admin_name_changes"))


# ---------------------------------------------------------------------------
# Admin — verification des certifications pilote
# ---------------------------------------------------------------------------

@app.route("/admin/messages")
@auth.admin_required
def admin_messages():
    status = request.args.get("status", "new")
    if status not in ("new", "replied", "archived", "all"):
        status = "new"
    return render_template(
        "admin_messages.html",
        messages=services.list_contact_messages(status),
        status=status,
        counts={st: services.count_contact_messages(st)
                for st in ("new", "replied", "archived")},
    )


@app.route("/admin/messages/<int:msg_id>/repondre", methods=["POST"])
@auth.admin_required
def admin_message_reply(msg_id):
    result = services.reply_contact_message(
        msg_id, g.user["id"], request.form.get("reply") or "",
    )
    if not result["ok"]:
        flash(result.get("reason") or "Réponse impossible.", "error")
    elif result["sent"]:
        flash("Réponse envoyée par courriel.", "success")
    else:
        flash("Réponse enregistrée, mais le courriel n'est PAS parti (SMTP "
              "indisponible) : utilisez le bouton « Envoyer depuis ma messagerie ».",
              "error")
    return redirect(url_for("admin_messages",
                            status="replied" if result["ok"] else "new"))


@app.route("/admin/messages/<int:msg_id>/statut", methods=["POST"])
@auth.admin_required
def admin_message_status(msg_id):
    status = request.form.get("status") or ""
    try:
        ok = services.set_contact_status(msg_id, status)
    except ValueError:
        ok = False
    flash("Statut mis à jour." if ok else "Message introuvable.", "success" if ok else "error")
    return redirect(url_for("admin_messages", status=request.form.get("back") or "new"))


@app.route("/admin/certifications")
@auth.admin_required
def admin_certifications():
    status = request.args.get("status", "pending")
    if status not in ("pending", "verified", "rejected", "all"):
        status = "pending"
    return render_template(
        "admin_certifications.html",
        certifications=services.list_certifications_for_review(status),
        status=status,
        verify_hints=LICENCE_VERIFY_HINTS,
        counts={
            "pending": services.count_certifications_pending(),
            "verified": len(services.list_certifications_for_review("verified", limit=10000)),
            "rejected": len(services.list_certifications_for_review("rejected", limit=10000)),
        },
    )


# Controles que l'admin doit attester avant de verifier un brevet : on
# verifie le DOCUMENT contre le COMPTE (nom identique), pas seulement le PDF.
VERIFY_CHECKS = (
    ("check_name", "nom du document identique au nom du compte"),
    ("check_number", "autorité, intitulé et numéro identiques au document"),
    ("check_valid", "document lisible, authentique et en cours de validité"),
)


def _admin_review(cert_id: int, decision: str, ok_msg: str):
    note = (request.form.get("note") or "").strip()
    if decision == "rejected" and len(note) < 3:
        flash("Indiquez le motif du refus (le pilote le verra).", "error")
        return redirect(url_for("admin_certifications", status="pending") + f"#cert-{cert_id}")
    if decision == "verified":
        missing = [label for key, label in VERIFY_CHECKS if not _to_bool(request.form.get(key))]
        if missing:
            flash("Vérification refusée : cochez chaque contrôle (" + " ; ".join(missing) + ").", "error")
            return redirect(url_for("admin_certifications", status="pending") + f"#cert-{cert_id}")
        note = ("contrôles : nom ✓ numéro ✓ validité ✓" + (f" — {note}" if note else ""))
    res = services.review_certification(cert_id, g.user["id"], decision, note)
    if not res:
        flash("Brevet introuvable.", "error")
    else:
        extra = " Badge « vérifié » actif sur le profil." if res["profile_verified"] else \
            (" Le profil n'a plus de brevet vérifié valide : badge retiré."
             if decision != "verified" else " (Brevet expiré : badge profil non activé.)")
        flash(ok_msg + extra, "success" if decision == "verified" else "info")
    return redirect(url_for("admin_certifications",
                            status=request.form.get("back") or "pending"))


@app.route("/admin/certifications/<int:cert_id>/verifier", methods=["POST"])
@auth.admin_required
def admin_verify_certification(cert_id):
    return _admin_review(cert_id, "verified", "Brevet vérifié.")


@app.route("/admin/certifications/<int:cert_id>/refuser", methods=["POST"])
@auth.admin_required
def admin_reject_certification(cert_id):
    return _admin_review(cert_id, "rejected", "Justificatif refusé, pilote prévenu.")


@app.route("/admin/certifications/<int:cert_id>/devalider", methods=["POST"])
@auth.admin_required
def admin_unverify_certification(cert_id):
    return _admin_review(cert_id, "pending", "Brevet remis en attente.")


# ---------------------------------------------------------------------------
# Telechargement du brevet (gating client legitime)
# ---------------------------------------------------------------------------

@app.route("/pilotes/<int:user_id>/brevets/<int:cert_id>/document")
@auth.login_required
def pilot_certification_document(user_id, cert_id):
    viewer_id = g.user["id"]
    is_admin = bool(g.user.get("is_admin"))
    if not is_admin and not services.client_can_view_pilot_credentials(viewer_id, user_id):
        abort(403)
    cert = services.get_certification(cert_id)
    if not cert or cert["pilot_user_id"] != user_id or not cert.get("document_path"):
        abort(404)
    rel = cert["document_path"]
    if not rel.startswith("uploads/"):
        abort(404)
    filename = rel[len("uploads/"):]
    resp = make_response(send_from_directory(UPLOAD_DIR, filename, as_attachment=False))
    resp.headers["Cache-Control"] = "private, no-store, max-age=0"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Apercu integre dans /admin/certifications (iframe / img same-origin) :
    # les en-tetes globaux (DENY / frame-ancestors 'none') sont poses en
    # setdefault, on les remplace ici par une version same-origin.
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    resp.headers["Content-Disposition"] = "inline"
    return resp


@app.route("/espace/pilote/drone", methods=["POST"])
@auth.login_required
def pilot_add_drone():
    user = g.user
    if user["role"] not in ("pilot", "both"):
        abort(403)
    photo_path = ""
    f = request.files.get("photo")
    if f and f.filename:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext in ALLOWED_DOC_EXT:
            safe = f"u{user['id']}_drone_{int(time.time())}.{ext}"
            f.save(os.path.join(UPLOAD_DIR, safe))
            photo_path = f"uploads/{safe}"
    services.add_drone(
        user["id"],
        category=request.form.get("category") or "loisir",
        brand=(request.form.get("brand") or "").strip(),
        model=(request.form.get("model") or "").strip(),
        serial_number=(request.form.get("serial_number") or "").strip(),
        weight_g=_to_int(request.form.get("weight_g")),
        max_payload_g=_to_int(request.form.get("max_payload_g")),
        flight_time_min=_to_int(request.form.get("flight_time_min")),
        capabilities=request.form.getlist("capabilities"),
        notes=(request.form.get("notes") or "").strip(),
        photo_path=photo_path,
    )
    flash("Drone ajoute.", "success")
    return redirect(url_for("pilot_edit"))


@app.route("/espace/pilote/drone/<int:drone_id>/supprimer", methods=["POST"])
@auth.login_required
def pilot_delete_drone(drone_id):
    services.delete_drone(drone_id, g.user["id"])
    return redirect(url_for("pilot_edit"))


# ---------------------------------------------------------------------------
# Avatar pilote (photo de profil)
# ---------------------------------------------------------------------------

@app.route("/espace/pilote/avatar", methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=10, per_hour=30)
def pilot_upload_avatar():
    user = g.user
    f = request.files.get("avatar")
    if not f or not f.filename:
        flash("Aucun fichier selectionne.", "error")
        return redirect(url_for("pilot_edit"))
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_AVATAR_EXT:
        flash(
            f"Format non accepte. Utiliser : "
            f"{', '.join(sorted(ALLOWED_AVATAR_EXT))}.",
            "error",
        )
        return redirect(url_for("pilot_edit"))
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_AVATAR_MB * 1024 * 1024:
        flash(f"Avatar trop lourd (max {MAX_AVATAR_MB} Mo).", "error")
        return redirect(url_for("pilot_edit"))

    # Remplacement : supprime l'ancien fichier sur disque s'il existait.
    old = services.clear_user_avatar(user["id"])
    if old and old.startswith("uploads/"):
        try:
            os.remove(os.path.join(UPLOAD_DIR, old[len("uploads/"):]))
        except OSError:
            pass

    safe = f"avatar_u{user['id']}_{int(time.time())}.{ext}"
    f.save(os.path.join(UPLOAD_DIR, safe))
    services.set_user_avatar(user["id"], f"uploads/{safe}")
    flash("Photo de profil mise a jour.", "success")
    return redirect(url_for("pilot_edit"))


@app.route("/espace/pilote/avatar/supprimer", methods=["POST"])
@auth.login_required
def pilot_delete_avatar():
    old = services.clear_user_avatar(g.user["id"])
    if old and old.startswith("uploads/"):
        try:
            os.remove(os.path.join(UPLOAD_DIR, old[len("uploads/"):]))
        except OSError:
            pass
    flash("Photo de profil retiree.", "info")
    return redirect(url_for("pilot_edit"))


# Avatars et media portfolio servis depuis /media/<path>. Stockes
# physiquement dans data/uploads/ (avec UPLOAD_DIR).
# Types "actifs" refuses par /media : un SVG ou un HTML uploade comme avatar
# ou item de portfolio pourrait executer du script dans l'origine du site
# (XSS stocke). Les images / videos / PDF legitimes passent.
_MEDIA_BLOCKED_EXT = {"svg", "svgz", "html", "htm", "xhtml", "xml", "js", "mjs"}

# /media est PUBLIC (aucune authentification). On n'y sert donc QUE des medias
# publics par nature : avatars, photos de drone et pieces du portfolio (le
# showreel affiche sur les fiches). Tout le reste est refuse en dur.
# Les fichiers PRIVES vivent dans le meme UPLOAD_DIR mais ont chacun leur route
# authentifiee avec controle du proprietaire, et ne doivent JAMAIS passer ici :
#   - u<id>_cert_*         documents de brevet   -> pilot_certification_document
#   - u<id>_namechange_*   pieces d'identite      -> admin_name_change_justif
#   - booking_<id>/*       livrables payants      -> booking_deliverable_download
# Sans cette liste blanche, /media/u1_cert_....pdf servait la piece d'identite
# d'un pilote a n'importe quel visiteur (contournement total du controle).
_MEDIA_PUBLIC_RE = re.compile(
    r"^(?:avatar_[^/]+|u\d+_drone_[^/]+|portfolio_u\d+/[^/]+)$"
)


@app.route("/media/<path:filename>")
def media_file(filename):
    # Pas de traversee de chemin : send_from_directory gere deja
    # le ".." ; on accepte uniquement sous-dossiers connus.
    if ".." in filename or filename.startswith("/"):
        abort(404)
    # Liste blanche des medias publics : refuse cert / namechange / livrables.
    if not _MEDIA_PUBLIC_RE.match(filename):
        abort(404)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _MEDIA_BLOCKED_EXT:
        abort(404)
    resp = make_response(send_from_directory(UPLOAD_DIR, filename))
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = "inline"
    return resp


def _ensure_web_video(stored_rel, fallback_mime="application/octet-stream"):
    """Garantit une video lisible dans TOUS les navigateurs.

    .mp4/.m4v/.webm sont deja web-compatibles. Le .mov (QuickTime) ne se lit
    pas dans Chrome/Firefox : on le reconditionne en .mp4. Les videos de
    telephone etant en H.264/AAC, c'est un simple remux (-c copy +faststart) :
    quasi instantane, sans re-encodage ni perte. Si ffmpeg manque ou echoue
    (codec exotique type ProRes/HEVC), on garde l'original.

    Retourne (stored_rel, mime_type).
    """
    ext = stored_rel.rsplit(".", 1)[-1].lower() if "." in stored_rel else ""
    web_mimes = {"mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm"}
    if ext in web_mimes:
        return stored_rel, web_mimes[ext]
    if ext != "mov":
        return stored_rel, fallback_mime
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        return stored_rel, "video/quicktime"
    src = os.path.join(UPLOAD_DIR, stored_rel)
    dst_rel = stored_rel[:-4] + ".mp4"
    dst = os.path.join(UPLOAD_DIR, dst_rel)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-c", "copy",
             "-movflags", "+faststart", dst],
            check=True, capture_output=True, timeout=180,
        )
    except Exception as exc:
        app.logger.warning("remux video %s -> mp4 echoue: %s", stored_rel, exc)
        try:
            os.remove(dst)
        except OSError:
            pass
        return stored_rel, "video/quicktime"
    try:
        os.remove(src)
    except OSError:
        pass
    return dst_rel, "video/mp4"


# ---------------------------------------------------------------------------
# Portfolio pilote (showreel photos + videos)
# ---------------------------------------------------------------------------

@app.route("/espace/pilote/portfolio", methods=["GET", "POST"])
@auth.login_required
def pilot_portfolio():
    user = g.user
    if user["role"] not in ("pilot", "both"):
        abort(403)

    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Aucun fichier selectionne.", "error")
            return redirect(url_for("pilot_portfolio"))
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_PORTFOLIO_EXT:
            flash(
                f"Format non accepte. Utiliser : "
                f"{', '.join(sorted(ALLOWED_PORTFOLIO_EXT))}.",
                "error",
            )
            return redirect(url_for("pilot_portfolio"))

        # Photos illimitees ; videos plafonnees (cf MAX_PORTFOLIO_VIDEOS).
        kind = services.portfolio_kind_from_ext(ext)
        if (kind == "video" and MAX_PORTFOLIO_VIDEOS
                and services.count_portfolio_items(user["id"], "video")
                >= MAX_PORTFOLIO_VIDEOS):
            flash(
                f"Maximum {MAX_PORTFOLIO_VIDEOS} vidéos dans le portfolio "
                f"(les photos restent illimitées). Supprime une vidéo pour en "
                f"ajouter une nouvelle.",
                "error",
            )
            return redirect(url_for("pilot_portfolio"))

        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_PORTFOLIO_MB * 1024 * 1024:
            flash(f"Fichier trop lourd (max {MAX_PORTFOLIO_MB} Mo).", "error")
            return redirect(url_for("pilot_portfolio"))

        safe_base = "".join(c for c in f.filename
                            if c.isalnum() or c in "._-")[:80] or "file"
        stored = f"portfolio_u{user['id']}/{int(time.time())}_{safe_base}"
        os.makedirs(os.path.join(UPLOAD_DIR, f"portfolio_u{user['id']}"),
                    exist_ok=True)
        os.makedirs(os.path.dirname(os.path.join(UPLOAD_DIR, stored)) or UPLOAD_DIR, exist_ok=True)
        f.save(os.path.join(UPLOAD_DIR, stored))

        mime = f.mimetype or "application/octet-stream"
        if kind == "video":
            # .mov (QuickTime) ne se lit pas dans Chrome/Firefox -> on
            # reconditionne en .mp4 (remux sans ré-encodage si H.264/AAC :
            # rapide, sans perte). La taille peut changer -> on la relit.
            stored, mime = _ensure_web_video(stored, mime)
            try:
                size = os.path.getsize(os.path.join(UPLOAD_DIR, stored))
            except OSError:
                pass

        services.add_portfolio_item(
            pilot_user_id=user["id"],
            title=(request.form.get("title") or "").strip(),
            description=(request.form.get("description") or "").strip(),
            kind=kind,
            original_filename=f.filename,
            stored_filename=stored,
            mime_type=mime,
            size_bytes=size,
        )
        flash("Realisation ajoutee au portfolio.", "success")
        return redirect(url_for("pilot_portfolio"))

    return render_template(
        "pilot_portfolio.html",
        items=services.list_portfolio_items(user["id"]),
        max_mb=MAX_PORTFOLIO_MB,
        max_videos=MAX_PORTFOLIO_VIDEOS,
        video_count=services.count_portfolio_items(user["id"], "video"),
    )


@app.route("/espace/pilote/portfolio/<int:item_id>/modifier", methods=["POST"])
@auth.login_required
def pilot_portfolio_update(item_id):
    services.update_portfolio_item(
        item_id, g.user["id"],
        title=(request.form.get("title") or "").strip(),
        description=(request.form.get("description") or "").strip(),
    )
    flash("Realisation mise a jour.", "success")
    return redirect(url_for("pilot_portfolio"))


@app.route("/espace/pilote/portfolio/<int:item_id>/supprimer", methods=["POST"])
@auth.login_required
def pilot_portfolio_delete(item_id):
    item = services.delete_portfolio_item(item_id, g.user["id"])
    if item:
        try:
            os.remove(os.path.join(UPLOAD_DIR, item["stored_filename"]))
        except OSError:
            pass
        if item.get("thumb_filename"):
            try:
                os.remove(os.path.join(UPLOAD_DIR, item["thumb_filename"]))
            except OSError:
                pass
        flash("Realisation retiree.", "info")
    return redirect(url_for("pilot_portfolio"))


# ---------------------------------------------------------------------------
# Missions client
# ---------------------------------------------------------------------------

@app.route("/missions/nouvelle", methods=["GET", "POST"])
@auth.login_required
def mission_create():
    if request.method == "POST":
        from_package_id = _to_int(request.form.get("from_package_id"))
        targeted_pilot_id = _to_int(request.form.get("targeted_pilot_id"))
        try:
            mission_id = services.create_mission(
                g.user["id"],
                title=(request.form.get("title") or "").strip(),
                description=(request.form.get("description") or "").strip(),
                mission_type=request.form.get("mission_type") or "autre",
                country=(request.form.get("country") or "").strip(),
                region=(request.form.get("region") or "").strip(),
                city=(request.form.get("city") or "").strip(),
                lat=_to_float(request.form.get("lat")),
                lng=_to_float(request.form.get("lng")),
                address=(request.form.get("address") or "").strip(),
                budget_min=_to_float(request.form.get("budget_min")),
                budget_max=_to_float(request.form.get("budget_max")),
                currency=(request.form.get("currency") or DEFAULT_CURRENCY).upper(),
                duration_hours=_to_float(request.form.get("duration_hours")),
                start_date=(request.form.get("start_date") or "").strip() or None,
                end_date=(request.form.get("end_date") or "").strip() or None,
                is_urgent=_to_bool(request.form.get("is_urgent")),
                requires_insurance=_to_bool(request.form.get("requires_insurance")),
                requires_certifications=request.form.getlist("requires_certifications"),
                requires_capabilities=request.form.getlist("requires_capabilities"),
            )
            # Tracabilite : mission issue d'un forfait + ciblee sur un pilote
            if from_package_id or targeted_pilot_id:
                db.execute(
                    "UPDATE missions SET from_package_id=?, "
                    "  targeted_pilot_id=? WHERE id=?",
                    (from_package_id or None, targeted_pilot_id or None, mission_id),
                )
        except Exception as exc:  # garde large : on remonte un message clair a l'UI
            flash(f"Mission invalide: {exc}", "error")
            return render_template("mission_create.html", form=request.form)
        # Alerte les pilotes disponibles dont le rayon couvre la mission.
        # Pas de diffusion pour une commande ciblee (forfait / pilote vise).
        if not targeted_pilot_id:
            try:
                import mailer
                full = services.get_mission(mission_id)
                if full:
                    recipients = services.pilots_for_mission_alert(
                        full, exclude_user_id=g.user["id"])
                    mailer.send_mission_alerts(recipients, full)
            except Exception as exc:
                log.warning("alertes mission %s echouees: %s", mission_id, exc)
        flash("Mission publiee.", "success")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    target_pilot = None
    pilot_arg = _to_int(request.args.get("pilot"))
    if pilot_arg:
        prof = services.get_pilot_profile(pilot_arg)
        if prof and prof.get("role") in ("pilot", "both"):
            target_pilot = prof
    # Preremplissage depuis un forfait selectionne
    prefill_package = None
    form_prefill: dict = {}
    pkg_arg = _to_int(request.args.get("package"))
    if pkg_arg:
        pkg = services.get_pilot_package(pkg_arg)
        if pkg and pkg.get("is_active"):
            prefill_package = pkg
            if not target_pilot:
                prof = services.get_pilot_profile(pkg["pilot_user_id"])
                if prof:
                    target_pilot = prof
            form_prefill = {
                "title": pkg["title"],
                "description": pkg["description"]
                    + (f"\n\nLivrables : {pkg['deliverables']}" if pkg.get("deliverables") else ""),
                "mission_type": pkg.get("mission_type") or "autre",
                "duration_hours": pkg.get("duration_hours") or "",
                "budget_min": int(pkg["price"]),
                "budget_max": int(pkg["price"]),
                "currency": pkg["currency"],
                "requires_capabilities": (pkg.get("capabilities") or "").split(","),
            }
    return render_template(
        "mission_create.html",
        form=form_prefill,
        target_pilot=target_pilot,
        prefill_package=prefill_package,
    )


@app.route("/missions/<int:mission_id>/cloturer", methods=["POST"])
@auth.login_required
def mission_close(mission_id):
    m = db.fetchone("SELECT client_user_id, status FROM missions WHERE id=?", (mission_id,))
    if not m or m["client_user_id"] != g.user["id"]:
        abort(403)
    services.update_mission_status(mission_id, "cancelled")
    flash("Mission annulee.", "info")
    return redirect(url_for("mission_detail", mission_id=mission_id))


# ---------------------------------------------------------------------------
# Encheres
# ---------------------------------------------------------------------------

@app.route("/missions/<int:mission_id>/enchere", methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=20, per_hour=100)
def bid_place(mission_id):
    if g.user["role"] not in ("pilot", "both"):
        flash("Seuls les pilotes peuvent soumissionner.", "error")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    price = _to_float(request.form.get("price"))
    if not price or price <= 0:
        flash("Tarif invalide.", "error")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    description = (request.form.get("description") or "").strip()[:5000]
    # Devis minimal obligatoire : description >= 30 caracteres. Le pilote
    # ne peut pas lacher un prix sec, le client doit pouvoir comparer.
    if len(description) < 30:
        flash(
            "Decrivez votre devis en au moins 30 caracteres "
            "(approche, materiel, deroule).",
            "error",
        )
        return redirect(url_for("mission_detail", mission_id=mission_id))
    try:
        services.place_bid(
            mission_id, g.user["id"],
            price=price,
            currency=(request.form.get("currency") or DEFAULT_CURRENCY).upper(),
            eta_hours=_to_float(request.form.get("eta_hours")),
            message=(request.form.get("message") or "").strip(),
            description=description,
            deliverables=(request.form.get("deliverables") or "").strip()[:2000],
            terms=(request.form.get("terms") or "").strip()[:2000],
        )
    except (LookupError, ValueError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    flash("Devis envoye au client.", "success")
    return redirect(url_for("mission_detail", mission_id=mission_id))


@app.route("/encheres/<int:bid_id>/retirer", methods=["POST"])
@auth.login_required
def bid_withdraw(bid_id):
    services.withdraw_bid(bid_id, g.user["id"])
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/missions/<int:mission_id>/accepter/<int:bid_id>", methods=["POST"])
@auth.login_required
def bid_accept(mission_id, bid_id):
    try:
        booking_id = services.accept_bid(mission_id, bid_id, g.user["id"])
    except (LookupError, ValueError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    flash("Devis valide, reservation creee.", "success")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/missions/<int:mission_id>/refuser/<int:bid_id>", methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=20, per_hour=100)
def bid_reject(mission_id, bid_id):
    """Le client refuse un devis. La mission reste ouverte, le pilote
    peut alors reviser et resoumettre un nouveau devis."""
    reason = (request.form.get("reason") or "").strip()
    if services.reject_bid(mission_id, bid_id, g.user["id"], reason=reason):
        flash("Devis refuse. Le pilote pourra proposer une revision.", "info")
    else:
        flash("Impossible de refuser ce devis.", "error")
    return redirect(url_for("mission_detail", mission_id=mission_id))


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

@app.route("/reservations/<int:booking_id>")
@auth.login_required
def booking_detail(booking_id):
    booking = services.get_booking(booking_id)
    if not booking:
        abort(404)
    if g.user["id"] not in (booking["client_user_id"], booking["pilot_user_id"]):
        abort(403)
    peer_id = booking["pilot_user_id"] if g.user["id"] == booking["client_user_id"] else booking["client_user_id"]
    is_client_view = g.user["id"] == booking["client_user_id"]
    cancellable = booking["status"] in ("pending_payment", "funded", "in_progress")
    cancellation_preview = (
        services.compute_cancellation_fee(booking)
        if is_client_view and cancellable else None
    )
    return render_template(
        "booking_detail.html",
        booking=booking,
        peer_id=peer_id,
        thread=services.thread(booking["mission_id"], g.user["id"], peer_id),
        cancellation_preview=cancellation_preview,
        pilot_can_cancel=(not is_client_view) and cancellable,
        is_client_view=is_client_view,
        deliverables=services.list_deliverables(booking_id),
        max_deliverable_mb=MAX_DELIVERABLE_MB,
    )


@app.route("/reservations/<int:booking_id>/devis.pdf")
@auth.login_required
def booking_devis_pdf(booking_id):
    booking = services.get_booking(booking_id)
    if not booking:
        abort(404)
    if g.user["id"] not in (booking["client_user_id"], booking["pilot_user_id"]):
        abort(403)
    import pdfgen
    pdf = pdfgen.devis_pdf(booking, _mission_label(booking.get("mission_type") or ""))
    resp = make_response(pdf)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="devis-AP-{booking_id}.pdf"'
    )
    return resp


@app.route("/reservations/<int:booking_id>/statut", methods=["POST"])
@auth.login_required
def booking_status(booking_id):
    status = request.form.get("status") or ""
    try:
        services.update_booking_status(booking_id, status, g.user["id"])
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/reservations/<int:booking_id>/annuler", methods=["POST"])
@auth.login_required
def booking_cancel_client(booking_id):
    """Annulation initiee par le client : applique la regle de preavis
    (LATE_CANCELLATION_HOURS) et retient une part du devis pour le pilote
    en cas d'annulation tardive (LATE_CANCELLATION_FEE_PCT, defaut 25%)."""
    reason = (request.form.get("reason") or "").strip()
    result = services.cancel_booking_by_client(
        booking_id, g.user["id"], reason=reason,
    )
    if not result.get("ok"):
        flash(result.get("reason") or "Annulation refusee.", "error")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    if not result.get("paid"):
        flash("Réservation annulée (aucun paiement n'avait été effectué).", "success")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    parts = []
    if result["is_late"]:
        parts.append(f"annulation tardive : {int(result['fee_pct'])} % du devis "
                     f"({result['fee_amount']:.2f}) versés au pilote")
    if result["service_fee_amount"] > 0:
        parts.append(f"frais de service {int(result['service_fee_pct'])} % "
                     f"({result['service_fee_amount']:.2f}) retenus")
    if parts:
        flash(f"Réservation annulée — {' ; '.join(parts)} ; "
              f"{result['refund_amount']:.2f} remboursés.", "info")
    else:
        flash(f"Réservation annulée. Remboursement intégral : "
              f"{result['refund_amount']:.2f}.", "success")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/reservations/<int:booking_id>/annuler-pilote", methods=["POST"])
@auth.login_required
def booking_cancel_pilot(booking_id):
    """Desistement du pilote : remboursement integral du client, devis retire,
    mission remise en ligne. Jamais de frais pour le client."""
    reason = (request.form.get("reason") or "").strip()
    result = services.cancel_booking_by_pilot(booking_id, g.user["id"], reason=reason)
    if not result.get("ok"):
        flash(result.get("reason") or "Désistement refusé.", "error")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    if result.get("paid"):
        flash(f"Vous vous êtes désisté. Le client est remboursé intégralement "
              f"({result['refund_amount']:.2f}) et la mission est remise en ligne.", "info")
    else:
        flash("Vous vous êtes désisté. La mission est remise en ligne.", "info")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/reservations/<int:booking_id>/avis", methods=["POST"])
@auth.login_required
def booking_review(booking_id):
    booking = services.get_booking(booking_id)
    if not booking:
        abort(404)
    if g.user["id"] not in (booking["client_user_id"], booking["pilot_user_id"]):
        abort(403)
    target = booking["pilot_user_id"] if g.user["id"] == booking["client_user_id"] else booking["client_user_id"]
    services.add_review(
        booking_id=booking_id,
        author_user_id=g.user["id"],
        target_user_id=target,
        rating=_to_int(request.form.get("rating"), 5) or 5,
        comment=(request.form.get("comment") or "").strip(),
    )
    flash("Merci pour votre avis.", "success")
    return redirect(url_for("booking_detail", booking_id=booking_id))


# ---------------------------------------------------------------------------
# Livrables booking (upload pilote, download client+pilote, push Aube)
# ---------------------------------------------------------------------------

def _deliverable_dir(booking_id: int) -> str:
    """Sous-dossier dedie a une booking : data/uploads/booking_<id>/."""
    p = os.path.join(UPLOAD_DIR, f"booking_{booking_id}")
    os.makedirs(p, exist_ok=True)
    return p


@app.route("/reservations/<int:booking_id>/livrables", methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=20, per_hour=200)
def booking_deliverable_upload(booking_id):
    booking = services.get_booking(booking_id)
    if not booking:
        abort(404)
    # Seul le pilote attribue peut uploader. Le client telecharge ensuite.
    if g.user["id"] != booking["pilot_user_id"]:
        abort(403)
    if booking["status"] not in ("funded", "in_progress", "completed"):
        flash("Livrables acceptes uniquement apres financement de la mission.", "error")
        return redirect(url_for("booking_detail", booking_id=booking_id))

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Aucun fichier selectionne.", "error")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_DELIVERABLE_EXT:
        flash(
            f"Format non accepte. Formats : "
            f"{', '.join(sorted(ALLOWED_DELIVERABLE_EXT))}.",
            "error",
        )
        return redirect(url_for("booking_detail", booking_id=booking_id))

    # Verifie la taille (Flask MAX_CONTENT_LENGTH garde un filet de
    # securite global mais on veut un message metier propre ici).
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_DELIVERABLE_MB * 1024 * 1024:
        flash(f"Fichier trop lourd (max {MAX_DELIVERABLE_MB} Mo).", "error")
        return redirect(url_for("booking_detail", booking_id=booking_id))

    safe_base = "".join(c for c in f.filename if c.isalnum() or c in "._-")[:80] or "file"
    stored = f"booking_{booking_id}/{int(time.time())}_{safe_base}"
    os.makedirs(os.path.dirname(os.path.join(UPLOAD_DIR, stored)) or UPLOAD_DIR, exist_ok=True)
    f.save(os.path.join(UPLOAD_DIR, stored))

    services.add_deliverable(
        booking_id=booking_id,
        uploaded_by_user_id=g.user["id"],
        label=(request.form.get("label") or "").strip(),
        original_filename=f.filename,
        stored_filename=stored,
        mime_type=(f.mimetype or "application/octet-stream"),
        size_bytes=size,
        kind=services.deliverable_kind_from_ext(ext),
    )
    flash("Livrable televerse.", "success")
    return redirect(url_for("booking_detail", booking_id=booking_id))


def _can_access_deliverable(booking: dict, user_id: int) -> bool:
    return user_id in (booking["pilot_user_id"], booking["client_user_id"])


@app.route("/reservations/<int:booking_id>/livrables/<int:deliv_id>/download")
@auth.login_required
def booking_deliverable_download(booking_id, deliv_id):
    booking = services.get_booking(booking_id)
    d = services.get_deliverable(deliv_id)
    if not booking or not d or d["booking_id"] != booking_id:
        abort(404)
    if not _can_access_deliverable(booking, g.user["id"]):
        abort(403)
    resp = make_response(send_from_directory(
        UPLOAD_DIR, d["stored_filename"],
        as_attachment=True, download_name=d["original_filename"],
    ))
    resp.headers["Cache-Control"] = "private, no-store, max-age=0"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/reservations/<int:booking_id>/livrables/<int:deliv_id>/supprimer",
           methods=["POST"])
@auth.login_required
def booking_deliverable_delete(booking_id, deliv_id):
    booking = services.get_booking(booking_id)
    if not booking:
        abort(404)
    if g.user["id"] != booking["pilot_user_id"]:
        abort(403)
    d = services.delete_deliverable(deliv_id, g.user["id"])
    if d:
        try:
            os.remove(os.path.join(UPLOAD_DIR, d["stored_filename"]))
        except OSError:
            pass
        flash("Livrable supprime.", "info")
    else:
        flash("Livrable introuvable.", "error")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/reservations/<int:booking_id>/livrables/<int:deliv_id>/envoyer/<service>",
           methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=10, per_hour=60)
def booking_deliverable_push(booking_id, deliv_id, service):
    if service not in ("aubedrive", "aubephotos"):
        abort(404)
    booking = services.get_booking(booking_id)
    d = services.get_deliverable(deliv_id)
    if not booking or not d or d["booking_id"] != booking_id:
        abort(404)
    # Seul le client (proprietaire des livrables) peut pousser sur SON
    # AubeDrive / AubePhotos. Le pilote n'a pas a copier dans le drive
    # du client.
    if g.user["id"] != booking["client_user_id"]:
        abort(403)
    # username AubeMail du client (clef d'identite cross-services Aube)
    client = db.fetchone("SELECT username FROM users WHERE id=?",
                         (booking["client_user_id"],))
    if not client:
        abort(404)

    if service == "aubedrive":
        result = aube_push.push_to_aubedrive(d, client["username"], booking_id)
    else:
        result = aube_push.push_to_aubephotos(d, client["username"], booking_id)

    if result["ok"]:
        services.mark_deliverable_pushed(deliv_id, service, result["url"])
        flash(f"Livrable envoye vers {service.capitalize()}.", "success")
    else:
        flash(
            f"Envoi vers {service.capitalize()} echoue : "
            f"{result.get('reason') or 'erreur inconnue'}. "
            "Telechargez puis uploadez manuellement.",
            "error",
        )
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/missions/<int:mission_id>/messages", methods=["POST"])
@auth.login_required
@security.rate_limit(per_minute=30, per_hour=300)
def mission_message(mission_id):
    peer = _to_int(request.form.get("peer_id"))
    body = (request.form.get("body") or "").strip()
    if peer and body:
        # Autorisation : seuls le client de la mission et un pilote ayant
        # depose un devis dessus peuvent echanger (anti-spam / anti-tiers).
        if not services.can_message(mission_id, g.user["id"], peer):
            abort(403)
        # Filtre anti-bypass : avant que la mission ne soit fundee, on bloque
        # les coordonnees externes (email, tel, whatsapp, etc).
        booking = db.fetchone(
            "SELECT status FROM bookings WHERE mission_id=? "
            "AND (client_user_id=? OR pilot_user_id=?) ORDER BY id DESC LIMIT 1",
            (mission_id, g.user["id"], g.user["id"]),
        )
        funded = booking and booking["status"] in (
            "funded", "in_progress", "completed", "disputed"
        )
        ok, reason = services.message_passes_filter(body, bool(funded))
        if not ok:
            flash(reason or "Message bloqué.", "error")
            return redirect(request.referrer or url_for("dashboard"))
        services.send_message(
            mission_id=mission_id,
            sender_user_id=g.user["id"],
            recipient_user_id=peer,
            body=body,
        )
    return redirect(request.referrer or url_for("dashboard"))


# ---------------------------------------------------------------------------
# Stripe Connect — onboarding pilote
# ---------------------------------------------------------------------------

@app.route("/espace/pilote/stripe", methods=["GET", "POST"])
@auth.login_required
def stripe_onboard():
    user = g.user
    if user["role"] not in ("pilot", "both"):
        abort(403)
    profile = services.get_pilot_profile(user["id"])
    account_id = profile.get("stripe_account_id") if profile else None
    try:
        if not account_id:
            account_id, url = payments.create_pilot_account(user)
            services.set_pilot_stripe_account(user["id"], account_id)
        else:
            url = payments.fresh_onboarding_link(account_id)
    except payments.StripeCountryUnsupportedError:
        # Pays non supporte par Stripe Connect : on ne cree pas de compte
        # casse. Le pilote peut quand meme proposer des missions ; versement
        # manuel. Cf. payments.country_is_payable / bandeau base.html.
        flash(
            "Les paiements Stripe ne sont pas encore disponibles dans votre "
            "pays. Vous pouvez tout de même proposer des missions — le "
            "versement se fera manuellement. Si votre pays est incorrect, "
            "corrigez-le dans votre profil.",
            "info",
        )
        return redirect(url_for("pilot_edit"))
    except Exception as exc:
        # Ex. Connect pas encore active sur le compte Stripe -> message clair
        # au lieu d'un 500. Cf. dashboard.stripe.com/connect.
        log.error("stripe onboarding failed for user=%s : %s", user["id"], exc)
        flash("L'activation Stripe est momentanément indisponible "
              "(la plateforme finalise sa configuration de paiement). "
              "Réessayez d'ici peu — vous serez prévenu dès que c'est prêt.",
              "error")
        return redirect(url_for("pilot_edit"))
    return redirect(url)


@app.route("/stripe/return")
@auth.login_required
def stripe_return():
    user = g.user
    profile = services.get_pilot_profile(user["id"])
    if profile and profile.get("stripe_account_id"):
        st = payments.get_pilot_status(profile["stripe_account_id"])
        services.update_pilot_stripe_status(
            user["id"], st["charges_enabled"], st["payouts_enabled"]
        )
        if st["charges_enabled"]:
            flash("Compte Stripe activé. Vous pouvez accepter des missions payées.", "success")
        else:
            flash("Compte Stripe en attente de validation. Revenez plus tard.", "info")
    return redirect(url_for("pilot_edit"))


@app.route("/stripe/fake-onboarding/<account_id>")
@auth.login_required
def stripe_fake_onboarding(account_id):
    """Page interne qui simule l'onboarding Stripe en mode FAKE."""
    if not payments.is_fake():
        abort(404)
    return render_template("stripe_fake_onboarding.html", account_id=account_id)


# ---------------------------------------------------------------------------
# Stripe — paiement client
# ---------------------------------------------------------------------------

@app.route("/reservations/<int:booking_id>/payer", methods=["GET", "POST"])
@auth.login_required
@security.rate_limit(per_minute=10, per_hour=50)
def booking_pay(booking_id):
    booking = services.get_booking(booking_id)
    if not booking or booking["client_user_id"] != g.user["id"]:
        abort(403)
    if booking["status"] != "pending_payment":
        flash(f"Cette réservation n'est plus à payer (statut : {booking['status']}).", "info")
        return redirect(url_for("booking_detail", booking_id=booking_id))

    pilot_acc = services.get_pilot_stripe_account(booking["pilot_user_id"])
    if not pilot_acc:
        flash("Le pilote n'a pas finalisé son inscription Stripe — il a été notifié.", "error")
        try:
            import mailer
            pilot = db.fetchone(
                "SELECT id, email, full_name FROM users WHERE id=?",
                (booking["pilot_user_id"],),
            )
            if pilot:
                mailer.send_pilot_stripe_required(
                    pilot=dict(pilot),
                    mission={"id": booking.get("mission_id"),
                             "title": booking.get("mission_title", "Mission AubePilot"),
                             "city": booking.get("city")},
                    booking=booking,
                    client={"id": g.user["id"], "full_name": g.user["full_name"]},
                )
        except Exception as exc:
            log.warning("email pilot_stripe_required failed for booking=%s : %s",
                        booking_id, exc)
        return redirect(url_for("booking_detail", booking_id=booking_id))

    session_id, url = payments.create_checkout_session(
        booking_id=booking["id"],
        amount=booking["agreed_price"],
        currency=booking["currency"],
        mission_title=booking.get("mission_title", "Mission AubePilot"),
        client_email=g.user["email"],
    )
    services.attach_payment_session(booking_id, session_id)
    return redirect(url)


@app.route("/stripe/fake-checkout/<int:booking_id>", methods=["GET", "POST"])
@auth.login_required
def stripe_fake_checkout(booking_id):
    """Page de paiement simulé en mode FAKE."""
    if not payments.is_fake():
        abort(404)
    booking = services.get_booking(booking_id)
    if not booking or booking["client_user_id"] != g.user["id"]:
        abort(403)
    if request.method == "POST":
        # Simule un paiement réussi
        services.mark_booking_funded(
            booking_id,
            payment_intent_id=f"pi_fake_{booking_id}",
        )
        flash("Paiement simulé reçu. Mission financée.", "success")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    return render_template("stripe_fake_checkout.html", booking=booking)


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    # En mode FAKE (demo sans cle), le financement passe par
    # /stripe/fake-checkout, jamais par ce webhook : on refuse tout POST ici
    # pour qu'un tiers ne puisse pas forger un evenement non signe marquant
    # une reservation comme 'funded' sans paiement reel.
    if payments.is_fake():
        abort(404)
    payload = request.data
    signature = request.headers.get("Stripe-Signature", "")
    event = payments.parse_webhook(payload, signature)
    if not event:
        return ("bad signature", 400)

    etype = event["type"] if isinstance(event, dict) else event.type

    if etype == "checkout.session.completed":
        obj = event["data"]["object"] if isinstance(event, dict) else event.data.object
        bid = obj.get("metadata", {}).get("booking_id") if isinstance(obj, dict) else obj.metadata.get("booking_id")
        pi_id = obj.get("payment_intent") if isinstance(obj, dict) else obj.payment_intent
        if bid:
            services.mark_booking_funded(int(bid), payment_intent_id=str(pi_id) if pi_id else None)

    elif etype == "account.updated":
        obj = event["data"]["object"] if isinstance(event, dict) else event.data.object
        acc_id = obj.get("id") if isinstance(obj, dict) else obj.id
        ce = bool(obj.get("charges_enabled") if isinstance(obj, dict) else obj.charges_enabled)
        pe = bool(obj.get("payouts_enabled") if isinstance(obj, dict) else obj.payouts_enabled)
        if acc_id:
            row = db.fetchone(
                "SELECT user_id FROM pilot_profiles WHERE stripe_account_id=?",
                (acc_id,),
            )
            if row:
                services.update_pilot_stripe_status(row["user_id"], ce, pe)

    elif etype == "charge.refunded":
        obj = event["data"]["object"] if isinstance(event, dict) else event.data.object
        pi_id = obj.get("payment_intent") if isinstance(obj, dict) else obj.payment_intent
        if pi_id:
            db.execute(
                "UPDATE bookings SET status='refunded', refunded_at=datetime('now') "
                "WHERE stripe_payment_intent_id=?",
                (str(pi_id),),
            )
    return ("ok", 200)


# ---------------------------------------------------------------------------
# Confirmation client / dispute / refund
# ---------------------------------------------------------------------------

@app.route("/reservations/<int:booking_id>/valider", methods=["POST"])
@auth.login_required
def booking_confirm(booking_id):
    if services.confirm_completion(booking_id, g.user["id"]):
        flash("Mission validée. Le pilote a été payé.", "success")
    else:
        flash("Impossible de valider la mission (statut ou droits).", "error")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/reservations/<int:booking_id>/dispute", methods=["POST"])
@auth.login_required
def booking_dispute_open(booking_id):
    reason = (request.form.get("reason") or "").strip()
    if services.open_dispute(booking_id, g.user["id"], reason):
        flash("Litige ouvert. L'équipe AubePilot prend contact sous 48 h.", "info")
    else:
        flash("Impossible d'ouvrir un litige sur cette réservation.", "error")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/admin/reservations/<int:booking_id>/refund", methods=["POST"])
@auth.admin_required
def admin_refund(booking_id):
    amount = _to_float(request.form.get("amount"))
    if services.refund_booking(booking_id, amount=amount, admin_user=g.user["id"]):
        flash("Remboursement effectué.", "success")
    else:
        flash("Refund échoué.", "error")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/admin/disputes")
@auth.admin_required
def admin_disputes():
    rows = db.fetchall(
        "SELECT b.*, m.title AS mission_title, "
        "       cu.full_name AS client_name, pu.full_name AS pilot_name "
        "FROM bookings b "
        "JOIN missions m ON m.id=b.mission_id "
        "JOIN users cu ON cu.id=b.client_user_id "
        "JOIN users pu ON pu.id=b.pilot_user_id "
        "WHERE b.status='disputed' ORDER BY b.id DESC"
    )
    return render_template("admin_disputes.html", disputes=[dict(r) for r in rows])


# ---------------------------------------------------------------------------
# API JSON
# ---------------------------------------------------------------------------

# Limite anti-DoS sur les endpoints de listing
API_MAX_LIMIT = 100


def _api_limit(default: int) -> int:
    return min(max(_to_int(request.args.get("limit"), default) or default, 1), API_MAX_LIMIT)


@app.route("/api/geocode")
@security.rate_limit(per_minute=30, per_hour=300)
def api_geocode():
    """Code postal / adresse / ville -> coordonnees (cache serveur).
    Utilise par les formulaires de recherche et l'app mobile."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"error": "q requis"}), 400
    geo = geocode.lookup(q[:geocode.MAX_QUERY_LEN],
                         (request.args.get("country") or "").strip(),
                         getattr(g, "lang", i18n.DEFAULT))
    if not geo:
        return jsonify({"found": False, "q": q})
    return jsonify({"found": True, "q": q, **geo})


@app.route("/api/pilotes")
@security.rate_limit(per_minute=60, per_hour=600)
def api_pilots():
    country = request.args.get("country", "").strip()
    geo = _resolve_near(country)
    pilots = services.search_pilots(
        country=country,
        city=request.args.get("city", "").strip(),
        mission_type=request.args.get("mission_type", "").strip(),
        capability=request.args.get("capability", "").strip(),
        text=request.args.get("q", "").strip(),
        lat=geo["lat"], lng=geo["lng"], radius_km=geo["radius_km"],
        strict_radius=geo["near_only"],
        min_rating=_to_float(request.args.get("min_rating"), 0) or 0,
        only_available=_to_bool(request.args.get("only_available", "1")),
        only_verified=_to_bool(request.args.get("only_verified", "0")),
        only_insured=_to_bool(request.args.get("only_insured", "0")),
        authority=request.args.get("authority", "").strip(),
        kind=_profile_kind(request.args.get("kind")),
        limit=_api_limit(50),
    )
    return jsonify({
        "count": len(pilots), "pilots": [_public_pilot_json(p) for p in pilots],
        "near": {"query": geo["near"], "label": geo["near_label"],
                 "lat": geo["lat"], "lng": geo["lng"],
                 "radius_km": geo["radius_km"], "found": not geo["near_error"]},
    })


@app.route("/api/missions")
@security.rate_limit(per_minute=60, per_hour=600)
def api_missions():
    country = request.args.get("country", "").strip()
    geo = _resolve_near(country)
    missions = services.search_missions(
        country=country,
        city=request.args.get("city", "").strip(),
        mission_type=request.args.get("mission_type", "").strip(),
        status=request.args.get("status", "open"),
        lat=geo["lat"], lng=geo["lng"], radius_km=geo["radius_km"],
        strict_radius=geo["near_only"],
        only_urgent=_to_bool(request.args.get("only_urgent", "0")),
        limit=_api_limit(100),
    )
    return jsonify({
        "count": len(missions), "missions": [_public_mission_json(m) for m in missions],
        "near": {"query": geo["near"], "label": geo["near_label"],
                 "lat": geo["lat"], "lng": geo["lng"],
                 "radius_km": geo["radius_km"], "found": not geo["near_error"]},
    })


@app.route("/api/stats")
@security.rate_limit(per_minute=60, per_hour=600)
def api_stats():
    return jsonify(services.public_stats())


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def _404(_e):
    return render_template("error.html", code=404, message="Page introuvable."), 404


@app.errorhandler(403)
def _403(_e):
    return render_template("error.html", code=403, message="Acces refuse."), 403


@app.errorhandler(413)
def _413(_e):
    # MAX_CONTENT_LENGTH = max(MAX_UPLOAD_MB, MAX_DELIVERABLE_MB) : on affiche
    # le vrai plafond global, pas l'ancien 10 Mo trompeur. Les routes (portfolio
    # 200 Mo, livrables 1 Go) renvoient deja leur propre message plus precis.
    return render_template("error.html", code=413, message=f"Fichier trop volumineux (max {MAX_DELIVERABLE_MB} Mo)."), 413


@app.context_processor
def _inject_payments():
    """Expose des helpers paiement pour les templates."""
    return {
        "auto_release_days": AUTO_RELEASE_DAYS,
    }


# ---------------------------------------------------------------------------
# CLI / run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not db.schema_ready():
        bootstrap_db()
    app.run(host=HOST, port=PORT, debug=os.environ.get("FLASK_DEBUG", "1") == "1")
