"""AubeDroniste - marketplace dronistes <-> clients.

Point d'entree Flask. Auth PAM partagee, SQLite local, templates Jinja
+ une API JSON pour la recherche dynamique cote frontend.
"""
import logging
import os
import time
from datetime import datetime, timezone

from flask import (
    Flask, abort, flash, g, jsonify, make_response, redirect,
    render_template, request, url_for,
)

import auth
import db
import i18n
import payments
import services
from config import (
    ALLOWED_DOC_EXT,
    AUTO_RELEASE_DAYS,
    CURRENCIES,
    DEFAULT_CURRENCY,
    DEFAULT_SEARCH_RADIUS_KM,
    DRONE_CAPABILITIES,
    DRONE_CATEGORIES,
    FEATURED_COUNTRIES,
    HOST,
    LICENCE_AUTHORITIES,
    MAX_UPLOAD_MB,
    MISSION_TYPES,
    PORT,
    SECRET_KEY,
    SESSION_COOKIE_NAME,
    STRIPE_PUBLISHABLE_KEY,
    UPLOAD_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("aubedroniste")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_db():
    db.init_schema(SCHEMA_PATH)
    log.info("schema initialise -> %s", db.DB_PATH)


with app.app_context():
    if not os.path.exists(db.DB_PATH):
        bootstrap_db()


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


@app.context_processor
def _inject_globals():
    return {
        "current_user": getattr(g, "user", None),
        "mission_types": MISSION_TYPES,
        "drone_categories": DRONE_CATEGORIES,
        "drone_capabilities": DRONE_CAPABILITIES,
        "licence_authorities": LICENCE_AUTHORITIES,
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
    }


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


@app.context_processor
def _inject_helpers():
    return {
        "mission_label": _mission_label,
        "drone_label": lambda c: _label(DRONE_CATEGORIES, c, c),
        "auth_label": lambda c: _label(LICENCE_AUTHORITIES, c, c),
        "mission_groups": __import__("config").MISSION_TYPE_GROUPS,
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
    )


@app.route("/api/near")
def api_near():
    lat = _to_float(request.args.get("lat"))
    lng = _to_float(request.args.get("lng"))
    radius = _to_int(request.args.get("radius_km"), 100)
    if lat is None or lng is None:
        return jsonify({"error": "lat/lng requis"}), 400
    return jsonify(services.near_geo(lat, lng, radius_km=radius))


@app.route("/api/country-breakdown")
def api_country_breakdown():
    return jsonify(services.country_breakdown(int(request.args.get("limit", 50))))


@app.route("/dronistes")
def pilots_search():
    params = {
        "country": request.args.get("country", "").strip(),
        "city": request.args.get("city", "").strip(),
        "mission_type": request.args.get("mission_type", "").strip(),
        "capability": request.args.get("capability", "").strip(),
        "min_rating": _to_float(request.args.get("min_rating"), 0) or 0,
        "lat": _to_float(request.args.get("lat")),
        "lng": _to_float(request.args.get("lng")),
        "radius_km": _to_int(request.args.get("radius_km"), DEFAULT_SEARCH_RADIUS_KM),
        "only_available": _to_bool(request.args.get("only_available", "1")),
    }
    pilots = services.search_pilots(**params)
    return render_template("pilots_search.html", pilots=pilots, params=params)


@app.route("/missions")
def missions_search():
    params = {
        "country": request.args.get("country", "").strip(),
        "mission_type": request.args.get("mission_type", "").strip(),
        "lat": _to_float(request.args.get("lat")),
        "lng": _to_float(request.args.get("lng")),
        "radius_km": _to_int(request.args.get("radius_km"), DEFAULT_SEARCH_RADIUS_KM),
        "only_urgent": _to_bool(request.args.get("only_urgent", "0")),
    }
    missions = services.search_missions(status="open", **params)
    return render_template("missions_search.html", missions=missions, params=params)


@app.route("/dronistes/<int:user_id>")
def pilot_detail(user_id):
    profile = services.get_pilot_profile(user_id)
    if not profile or profile.get("role") not in ("droniste", "both"):
        abort(404)
    return render_template(
        "pilot_detail.html",
        pilot=profile,
        reviews=services.reviews_for(user_id),
    )


@app.route("/missions/<int:mission_id>")
def mission_detail(mission_id):
    mission = services.get_mission(mission_id)
    if not mission:
        abort(404)
    user = getattr(g, "user", None)
    has_my_bid = False
    if user:
        my = db.fetchone(
            "SELECT 1 FROM bids WHERE mission_id=? AND pilot_user_id=?",
            (mission_id, user["id"]),
        )
        has_my_bid = bool(my)
    return render_template(
        "mission_detail.html",
        mission=mission,
        has_my_bid=has_my_bid,
    )


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------

@app.route("/inscription", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        full_name = (request.form.get("full_name") or "").strip()
        role = request.form.get("role") or "client"
        country = (request.form.get("country") or "").strip()
        city = (request.form.get("city") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        lat = _to_float(request.form.get("lat"))
        lng = _to_float(request.form.get("lng"))

        if role not in ("client", "droniste", "both"):
            role = "client"
        if not username or not password or not full_name:
            flash("Identifiant, mot de passe et nom complet sont requis.", "error")
            return render_template("register.html")
        if password != confirm:
            flash("Les mots de passe ne correspondent pas.", "error")
            return render_template("register.html")
        if db.fetchone("SELECT 1 FROM users WHERE username=?", (username,)):
            flash("Cet identifiant est deja pris.", "error")
            return render_template("register.html")

        user_id = auth.create_user(
            username=username, password=password, full_name=full_name,
            role=role, country=country, city=city, phone=phone, lat=lat, lng=lng,
        )
        token = auth.create_session(user_id, request.user_agent.string, request.remote_addr or "")
        resp = make_response(redirect(url_for("dashboard")))
        resp.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="Lax", max_age=60 * 60 * 24 * 30)
        flash("Bienvenue sur AubeDroniste.", "success")
        return resp
    return render_template("register.html")


@app.route("/connexion", methods=["GET", "POST"])
def login():
    next_url = request.args.get("next") or request.form.get("next") or url_for("dashboard")
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        if not auth.authenticate(username, password):
            flash("Identifiants invalides.", "error")
            return render_template("login.html", next_url=next_url)
        row = db.fetchone("SELECT id FROM users WHERE username=?", (username,))
        if not row:
            flash("Compte inconnu.", "error")
            return render_template("login.html", next_url=next_url)
        token = auth.create_session(row["id"], request.user_agent.string, request.remote_addr or "")
        resp = make_response(redirect(next_url))
        resp.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, samesite="Lax", max_age=60 * 60 * 24 * 30)
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
    is_pilot = user["role"] in ("droniste", "both")
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
    )


# ---------------------------------------------------------------------------
# Profil droniste (edition)
# ---------------------------------------------------------------------------

@app.route("/espace/droniste", methods=["GET", "POST"])
@auth.login_required
def pilot_edit():
    user = g.user
    if user["role"] not in ("droniste", "both"):
        # bascule de role si l'utilisateur veut devenir droniste
        if request.method == "POST" and request.form.get("become_pilot"):
            new_role = "both" if user["role"] == "client" else "droniste"
            db.execute("UPDATE users SET role=? WHERE id=?", (new_role, user["id"]))
            db.execute("INSERT OR IGNORE INTO pilot_profiles (user_id) VALUES (?)", (user["id"],))
            return redirect(url_for("pilot_edit"))
        return render_template("pilot_become.html")

    if request.method == "POST":
        services.upsert_pilot_profile(
            user["id"],
            headline=(request.form.get("headline") or "").strip() or None,
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
        flash("Profil droniste mis a jour.", "success")
        return redirect(url_for("pilot_edit"))

    return render_template(
        "pilot_edit.html",
        profile=services.get_pilot_profile(user["id"]),
    )


@app.route("/espace/droniste/certification", methods=["POST"])
@auth.login_required
def pilot_add_certification():
    user = g.user
    if user["role"] not in ("droniste", "both"):
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


@app.route("/espace/droniste/certification/<int:cert_id>/supprimer", methods=["POST"])
@auth.login_required
def pilot_delete_certification(cert_id):
    services.delete_certification(cert_id, g.user["id"])
    return redirect(url_for("pilot_edit"))


@app.route("/espace/droniste/drone", methods=["POST"])
@auth.login_required
def pilot_add_drone():
    user = g.user
    if user["role"] not in ("droniste", "both"):
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


@app.route("/espace/droniste/drone/<int:drone_id>/supprimer", methods=["POST"])
@auth.login_required
def pilot_delete_drone(drone_id):
    services.delete_drone(drone_id, g.user["id"])
    return redirect(url_for("pilot_edit"))


# ---------------------------------------------------------------------------
# Missions client
# ---------------------------------------------------------------------------

@app.route("/missions/nouvelle", methods=["GET", "POST"])
@auth.login_required
def mission_create():
    if request.method == "POST":
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
        except Exception as exc:  # garde large : on remonte un message clair a l'UI
            flash(f"Mission invalide: {exc}", "error")
            return render_template("mission_create.html", form=request.form)
        flash("Mission publiee.", "success")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    return render_template("mission_create.html", form={})


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
def bid_place(mission_id):
    if g.user["role"] not in ("droniste", "both"):
        flash("Seuls les dronistes peuvent soumissionner.", "error")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    price = _to_float(request.form.get("price"))
    if not price or price <= 0:
        flash("Tarif invalide.", "error")
        return redirect(url_for("mission_detail", mission_id=mission_id))
    services.place_bid(
        mission_id, g.user["id"],
        price=price,
        currency=(request.form.get("currency") or DEFAULT_CURRENCY).upper(),
        eta_hours=_to_float(request.form.get("eta_hours")),
        message=(request.form.get("message") or "").strip(),
    )
    flash("Enchere envoyee.", "success")
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
    flash("Enchere acceptee, reservation creee.", "success")
    return redirect(url_for("booking_detail", booking_id=booking_id))


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
    return render_template(
        "booking_detail.html",
        booking=booking,
        peer_id=peer_id,
        thread=services.thread(booking["mission_id"], g.user["id"], peer_id),
    )


@app.route("/reservations/<int:booking_id>/statut", methods=["POST"])
@auth.login_required
def booking_status(booking_id):
    status = request.form.get("status") or ""
    try:
        services.update_booking_status(booking_id, status, g.user["id"])
    except ValueError as exc:
        flash(str(exc), "error")
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


@app.route("/missions/<int:mission_id>/messages", methods=["POST"])
@auth.login_required
def mission_message(mission_id):
    peer = _to_int(request.form.get("peer_id"))
    body = (request.form.get("body") or "").strip()
    if peer and body:
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

@app.route("/espace/droniste/stripe", methods=["GET", "POST"])
@auth.login_required
def stripe_onboard():
    user = g.user
    if user["role"] not in ("droniste", "both"):
        abort(403)
    profile = services.get_pilot_profile(user["id"])
    account_id = profile.get("stripe_account_id") if profile else None
    if not account_id:
        account_id, url = payments.create_pilot_account(user)
        services.set_pilot_stripe_account(user["id"], account_id)
    else:
        url = payments.fresh_onboarding_link(account_id)
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
        # On peut lui envoyer un mail de relance (TODO)
        return redirect(url_for("booking_detail", booking_id=booking_id))

    session_id, url = payments.create_checkout_session(
        booking_id=booking["id"],
        amount=booking["agreed_price"],
        currency=booking["currency"],
        mission_title=booking.get("mission_title", "Mission AubeDroniste"),
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
        flash("Litige ouvert. L'équipe AubeDroniste prend contact sous 48 h.", "info")
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

@app.route("/api/dronistes")
def api_pilots():
    pilots = services.search_pilots(
        country=request.args.get("country", "").strip(),
        city=request.args.get("city", "").strip(),
        mission_type=request.args.get("mission_type", "").strip(),
        capability=request.args.get("capability", "").strip(),
        lat=_to_float(request.args.get("lat")),
        lng=_to_float(request.args.get("lng")),
        radius_km=_to_int(request.args.get("radius_km"), DEFAULT_SEARCH_RADIUS_KM),
        min_rating=_to_float(request.args.get("min_rating"), 0) or 0,
        only_available=_to_bool(request.args.get("only_available", "1")),
    )
    return jsonify({"count": len(pilots), "pilots": pilots})


@app.route("/api/missions")
def api_missions():
    missions = services.search_missions(
        country=request.args.get("country", "").strip(),
        mission_type=request.args.get("mission_type", "").strip(),
        status=request.args.get("status", "open"),
        lat=_to_float(request.args.get("lat")),
        lng=_to_float(request.args.get("lng")),
        radius_km=_to_int(request.args.get("radius_km"), DEFAULT_SEARCH_RADIUS_KM),
        only_urgent=_to_bool(request.args.get("only_urgent", "0")),
    )
    return jsonify({"count": len(missions), "missions": missions})


@app.route("/api/stats")
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
    return render_template("error.html", code=413, message=f"Fichier trop lourd (max {MAX_UPLOAD_MB} Mo)."), 413


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
    if not os.path.exists(db.DB_PATH):
        bootstrap_db()
    app.run(host=HOST, port=PORT, debug=os.environ.get("FLASK_DEBUG", "1") == "1")
