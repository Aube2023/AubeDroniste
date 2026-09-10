"""Logique metier AubePilot : missions, pilotes, encheres, bookings.

Garde le code SQL ici pour ne pas alourdir app.py. Pas d'ORM, on prefere
voir les requetes a plat. Les fonctions retournent des dicts (sqlite3.Row
converti) pour rester serialisables JSON.
"""
import json
from urllib.parse import urlparse
import os
import logging
import math
import re
from datetime import datetime
from typing import Iterable, Optional

import db
from config import (
    STRIPE_CONNECT_ENABLED,
    DELIVERABLES,
    MAX_PILOT_LINKS,
    PILOT_LINK_KINDS,
    PILOT_LINK_KIND_CODES,
    BID_STATUS,
    CURRENCIES,
    ORG_KINDS,
    BOOKING_STATUS,
    DEFAULT_CURRENCY,
    DEFAULT_SEARCH_RADIUS_KM,
    MAX_SEARCH_RADIUS_KM,
    MISSION_STATUS,
    MISSION_TYPES,
    MESSAGE_BANNED_PATTERNS,
    CANCELLATION_GRACE_HOURS,
    CANCELLATION_SERVICE_FEE_CAP,
    CANCELLATION_SERVICE_FEE_PCT,
    PLATFORM_FEE_PCT,
    PLATFORM_FEE_TIERS,
    PROFILE_KIND_CODES,
)

log = logging.getLogger("aubepilot.services")


def _csv(values: Optional[Iterable[str]]) -> Optional[str]:
    if not values:
        return None
    seen, out = set(), []
    for v in values:
        v = (v or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return ",".join(out) if out else None


def row_to_dict(row) -> Optional[dict]:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Profil pilote
# ---------------------------------------------------------------------------

def upsert_pilot_profile(user_id: int, **fields) -> Optional[dict]:
    existing = db.fetchone("SELECT 1 FROM pilot_profiles WHERE user_id=?", (user_id,))
    allowed = {
        "headline", "business_name", "years_experience", "hourly_rate", "daily_rate",
        "currency", "travel_radius_km", "accepts_remote", "insurance",
        "insurance_company", "insurance_policy", "is_available",
        "languages", "portfolio_url", "accepts_urgent",
        "kind", "school_programs", "business_email",
    }
    data = {k: fields[k] for k in fields if k in allowed and fields[k] is not None}
    if not existing:
        cols = ["user_id"] + list(data.keys())
        placeholders = ",".join("?" for _ in cols)
        db.execute(
            f"INSERT INTO pilot_profiles ({','.join(cols)}) VALUES ({placeholders})",
            [user_id] + [data[c] for c in data],
        )
    elif data:
        sets = ", ".join(f"{k}=?" for k in data)
        params = list(data.values()) + [user_id]
        db.execute(
            f"UPDATE pilot_profiles SET {sets}, updated_at=datetime('now') WHERE user_id=?",
            params,
        )
    return get_pilot_profile(user_id)


def mask_full_name(full_name: str) -> str:
    """Anonymise un nom complet en gardant le prenom et l'initiale du nom.

    "Amine Benali"   -> "Amine B."
    "Sophie Tremblay" -> "Sophie T."
    "Marie Dubois Pellerin" -> "Marie D."   (premier nom de famille)
    "Cher" / mononyme -> "Cher"             (rien a masquer)
    """
    name = (full_name or "").strip()
    if not name:
        return ""
    parts = name.split()
    if len(parts) < 2:
        return parts[0]
    return f"{parts[0]} {parts[1][0].upper()}."


def public_name(p: dict) -> str:
    """Nom affiche dans les listes/cartes : les ORGANISATIONS (entreprise,
    ecole, boutique) sous leur raison sociale en clair — elles veulent etre
    identifiees ; les personnes physiques sous leur nom masque."""
    if (p.get("kind") in ORG_KINDS) and (p.get("business_name") or "").strip():
        return p["business_name"].strip()
    return mask_full_name(p.get("full_name") or "")


def count_pilots_by_kind(only_available: bool = True) -> dict:
    """Effectifs par type de profil pour les onglets de l'annuaire."""
    q = ("SELECT COALESCE(p.kind, 'pro') AS kind, COUNT(*) AS n "
         "FROM users u JOIN pilot_profiles p ON p.user_id = u.id "
         "WHERE u.role IN ('pilot', 'both') AND u.deleted_at IS NULL ")
    if only_available:
        q += "AND p.is_available = 1 "
    q += "GROUP BY COALESCE(p.kind, 'pro')"
    out = {k: 0 for k in PROFILE_KIND_CODES}
    for r in db.fetchall(q):
        out[r["kind"] if r["kind"] in out else "pro"] += int(r["n"])
    out["all"] = sum(out[k] for k in PROFILE_KIND_CODES)
    return out


def has_funded_relation(viewer_user_id: int, pilot_user_id: int) -> bool:
    """True si le viewer (probablement un client) a au moins un booking
    paye en escrow avec ce pilote (funded / in_progress / completed /
    disputed). Utilise pour decider si on revele le nom complet, la
    ville exacte et le portfolio_url du pilote.

    Si viewer == pilote lui-meme, retourne True (il voit toujours sa propre
    fiche en clair).
    """
    if not viewer_user_id or not pilot_user_id:
        return False
    if viewer_user_id == pilot_user_id:
        return True
    row = db.fetchone(
        "SELECT 1 FROM bookings "
        "WHERE pilot_user_id=? AND client_user_id=? "
        "AND status IN ('funded','in_progress','completed','disputed') "
        "LIMIT 1",
        (pilot_user_id, viewer_user_id),
    )
    return bool(row)


# ---------------------------------------------------------------------------
# Compteur de visites du site (vanity metric publique). La valeur stockee est
# le cumul reel ; l'affichage ajoute config.SITE_VISIT_BASE.
# ---------------------------------------------------------------------------
def bump_visits() -> int:
    """Incremente le compteur de visites et retourne la nouvelle valeur brute."""
    db.execute(
        "INSERT INTO site_counters(name, value) VALUES('visits', 1) "
        "ON CONFLICT(name) DO UPDATE SET value = value + 1"
    )
    row = db.fetchone("SELECT value FROM site_counters WHERE name='visits'")
    return int(row["value"]) if row else 0


def get_visits() -> int:
    """Valeur brute du compteur de visites (0 si jamais incremente)."""
    row = db.fetchone("SELECT value FROM site_counters WHERE name='visits'")
    return int(row["value"]) if row else 0


# ---------------------------------------------------------------------------
# Provenance des visiteurs
#
# On compte des PAGES VUES par pays et par jour, pas des visiteurs uniques :
# sans cookie ni empreinte, deux visites ne peuvent pas etre rattachees a la
# meme personne, et c'est voulu. Aucune IP n'entre en base.
# ---------------------------------------------------------------------------

def bump_country_visit(country: Optional[str], *, is_bot: bool = False) -> None:
    db.execute(
        "INSERT INTO visit_countries(day, country, kind, views) "
        "VALUES(date('now'), ?, ?, 1) "
        "ON CONFLICT(day, country, kind) DO UPDATE SET views = views + 1",
        ((country or "??").upper()[:2], "bot" if is_bot else "human"),
    )


def visits_by_country(days: int = 30, *, kind: str = "human") -> list:
    """[{country, views, share}] sur les N derniers jours, du plus visite au
    moins visite. `share` est le pourcentage du total."""
    rows = db.fetchall(
        "SELECT country, SUM(views) AS views FROM visit_countries "
        "WHERE kind = ? AND day >= date('now', ?) "
        "GROUP BY country ORDER BY views DESC, country",
        (kind, f"-{max(1, int(days))} days"),
    )
    out = [{"country": r["country"], "views": int(r["views"])} for r in rows]
    total = sum(r["views"] for r in out) or 1
    for r in out:
        r["share"] = round(100 * r["views"] / total, 1)
    return out


def visits_daily(days: int = 30, *, kind: str = "human") -> list:
    """[{day, views}] pour la courbe, jours sans visite compris."""
    rows = db.fetchall(
        "SELECT day, SUM(views) AS views FROM visit_countries "
        "WHERE kind = ? AND day >= date('now', ?) GROUP BY day ORDER BY day",
        (kind, f"-{max(1, int(days))} days"),
    )
    return [{"day": r["day"], "views": int(r["views"])} for r in rows]


def visits_totals(days: int = 30) -> dict:
    rows = db.fetchall(
        "SELECT kind, SUM(views) AS views FROM visit_countries "
        "WHERE day >= date('now', ?) GROUP BY kind",
        (f"-{max(1, int(days))} days",),
    )
    by = {r["kind"]: int(r["views"]) for r in rows}
    return {"human": by.get("human", 0), "bot": by.get("bot", 0)}


def get_pilot_profile(user_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT u.*, p.headline, p.business_name, p.business_email, p.kind, p.school_programs, "
        "p.years_experience, p.hourly_rate, p.daily_rate, "
        "p.currency AS p_currency, p.travel_radius_km, p.accepts_remote, p.insurance, "
        "p.insurance_company, p.insurance_policy, p.is_available, p.languages, "
        "p.insurance_expires_at, p.insurance_document_path, p.insurance_note, "
        "COALESCE(p.insurance_status, 'none') AS insurance_status, "
        "p.insurance_reviewed_at, "
        "p.portfolio_url, p.accepts_urgent, p.updated_at AS pilot_updated_at, "
        "COALESCE(p.stripe_charges_enabled, 0) AS stripe_charges_enabled "
        "FROM users u LEFT JOIN pilot_profiles p ON p.user_id = u.id "
        "WHERE u.id=?",
        (user_id,),
    )
    if not row:
        return None
    out = dict(row)
    out["specialties"] = [
        r["mission_type"]
        for r in db.fetchall(
            "SELECT mission_type FROM pilot_specialties WHERE pilot_user_id=?",
            (user_id,),
        )
    ]
    out["territories"] = [
        dict(r)
        for r in db.fetchall(
            "SELECT country, region FROM pilot_territories WHERE pilot_user_id=?",
            (user_id,),
        )
    ]
    out["deliverables"] = list_pilot_deliverables(user_id)
    out["links"] = list_pilot_links(user_id)
    out["certifications"] = list_certifications(user_id)
    out["drones"] = list_drones(user_id)
    out["rating"] = pilot_rating(user_id)
    return out


# ---------------------------------------------------------------------------
# Visibilite d'un pilote : ce qui lui manque concretement pour etre trouve
#
# Deux pilotes sur quatre en production n'ont ni accroche, ni specialite, ni
# brevet : ils n'apparaitront jamais nulle part et ne sauront pas pourquoi.
# Chaque point ci-dessous correspond a un mecanisme reel du site (recherche
# par code postal, pages d'atterrissage par specialite, filtres de confiance),
# pas a un « score de profil » decoratif.
# ---------------------------------------------------------------------------

def pilot_visibility(user_id: int) -> dict:
    """{score, total, done, items:[{key, ok, weight, blocking}]}.

    `blocking` = sans ca, le pilote est absent d'un canal entier (recherche
    geographique, pages specialite) ou ne peut pas etre paye.
    """
    p = get_pilot_profile(user_id)
    if not p:
        return {"score": 0, "total": 0, "done": 0, "items": []}
    certs = p.get("certifications") or []
    ins = insurance_state(p)
    packages = list_pilot_packages(user_id, only_active=True)
    items = [
        # Sans coordonnees, le pilote n'existe pas pour « pres de chez moi »
        # ni pour les pages pays/ville : c'est le premier canal du site.
        ("location", bool(p.get("lat") and p.get("lng")), 3, True),
        ("city", bool((p.get("city") or "").strip()), 2, True),
        # Une specialite = une page d'atterrissage ou le pilote apparait.
        ("specialties", bool(p.get("specialties")), 3, True),
        ("available", bool(p.get("is_available")), 3, True),
        ("headline", bool((p.get("headline") or "").strip()), 2, False),
        ("bio", len((p.get("bio") or "").strip()) >= 80, 2, False),
        ("avatar", bool(p.get("avatar_path")), 1, False),
        ("certification", bool(certs), 2, False),
        # Un brevet sans justificatif reste declaratif : pas de badge.
        ("certification_document", any(c.get("document_path") for c in certs), 3, False),
        ("certification_verified", any(c.get("is_verified") and not c.get("is_expired")
                                       for c in certs), 2, False),
        ("insurance", ins["is_valid"], 2, False),
        ("drone", bool(p.get("drones")), 1, False),
        ("rate", bool(p.get("hourly_rate") or p.get("daily_rate") or packages), 2, False),
        ("portfolio", bool(list_portfolio_items(user_id)), 1, False),
        # Un client cherche « panorama 360 », pas « camera 8K ».
        ("deliverables", bool(p.get("deliverables")), 2, False),
        # Preuves hors plateforme : decisives tant que le reseau est jeune.
        ("links", bool(p.get("links")), 1, False),
    ]
    # Ne reclamer Stripe que si Connect est reellement ouvert cote plateforme :
    # sinon on demanderait au pilote une demarche qui n'aboutit pas.
    if STRIPE_CONNECT_ENABLED:
        items.append(("payouts", bool(p.get("stripe_charges_enabled")), 2, True))
    total = sum(w for _k, _ok, w, _b in items)
    done = sum(w for _k, ok, w, _b in items if ok)
    return {
        "score": round(100 * done / total) if total else 0,
        "total": total, "done": done,
        "items": [{"key": k, "ok": ok, "weight": w, "blocking": b} for k, ok, w, b in items],
        "missing_blocking": [k for k, ok, _w, b in items if b and not ok],
    }


def list_pilot_deliverables(pilot_user_id: int) -> list:
    """Codes des livrables proposes par un pilote, dans l'ordre de
    config.DELIVERABLES. A ne pas confondre avec `list_deliverables`, qui
    liste les fichiers remis sur UNE reservation."""
    rows = {r["deliverable"] for r in db.fetchall(
        "SELECT deliverable FROM pilot_deliverables WHERE pilot_user_id=?",
        (pilot_user_id,))}
    return [c for c in DELIVERABLES if c in rows]


def set_pilot_deliverables(user_id: int, codes: Iterable[str]):
    db.execute("DELETE FROM pilot_deliverables WHERE pilot_user_id=?", (user_id,))
    connus = set(DELIVERABLES)
    for code in {c for c in codes if c in connus}:
        db.execute(
            "INSERT OR IGNORE INTO pilot_deliverables (pilot_user_id, deliverable) "
            "VALUES (?, ?)", (user_id, code))


def _clean_url(raw: str) -> Optional[str]:
    """N'accepte qu'une adresse http(s) : un `javascript:` dans un lien
    affiche sur une page publique serait une injection."""
    url = (raw or "").strip()[:300]
    if not url:
        return None
    schema = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):", url)
    if schema and schema.group(1).lower() not in ("http", "https"):
        return None
    if not schema:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def list_pilot_links(pilot_user_id: int) -> list:
    """[{id, kind, url, label}] dans l'ordre de config.PILOT_LINK_KINDS."""
    labels = dict(PILOT_LINK_KINDS)
    ordre = {k: i for i, (k, _) in enumerate(PILOT_LINK_KINDS)}
    rows = [dict(r) for r in db.fetchall(
        "SELECT id, kind, url FROM pilot_links WHERE pilot_user_id=? ORDER BY id",
        (pilot_user_id,))]
    for r in rows:
        r["label"] = labels.get(r["kind"], r["kind"])
    rows.sort(key=lambda r: ordre.get(r["kind"], 99))
    return rows


def set_pilot_links(user_id: int, pairs: Iterable) -> int:
    """`pairs` = [(kind, url)]. Remplace la liste ; ignore les entrees vides
    ou dont l'adresse n'est pas http(s). Retourne le nombre conserve."""
    valides = []
    for kind, url in pairs:
        if kind not in PILOT_LINK_KIND_CODES:
            continue
        propre = _clean_url(url)
        if propre:
            valides.append((kind, propre))
        if len(valides) >= MAX_PILOT_LINKS:
            break
    db.execute("DELETE FROM pilot_links WHERE pilot_user_id=?", (user_id,))
    for kind, url in valides:
        db.execute("INSERT INTO pilot_links (pilot_user_id, kind, url) VALUES (?, ?, ?)",
                   (user_id, kind, url))
    return len(valides)


def set_pilot_specialties(user_id: int, codes: Iterable[str]):
    db.execute("DELETE FROM pilot_specialties WHERE pilot_user_id=?", (user_id,))
    known = {k for k, _ in MISSION_TYPES}
    for code in {c for c in codes if c in known}:
        db.execute(
            "INSERT OR IGNORE INTO pilot_specialties (pilot_user_id, mission_type) VALUES (?, ?)",
            (user_id, code),
        )


def set_pilot_territories(user_id: int, items: Iterable[dict]):
    db.execute("DELETE FROM pilot_territories WHERE pilot_user_id=?", (user_id,))
    for it in items:
        country = (it.get("country") or "").strip()
        region = (it.get("region") or "").strip()
        if country:
            db.execute(
                "INSERT OR IGNORE INTO pilot_territories (pilot_user_id, country, region) "
                "VALUES (?, ?, ?)",
                (user_id, country, region),
            )


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------

def add_certification(pilot_user_id: int, *, authority: str, title: str,
                      reference: str = "", issued_at: str = "",
                      expires_at: str = "", document_path: str = "") -> int:
    cur = db.execute(
        "INSERT INTO pilot_certifications "
        "(pilot_user_id, authority, title, reference, issued_at, expires_at, document_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pilot_user_id, authority, title, reference, issued_at or None,
         expires_at or None, document_path or None),
    )
    return cur.lastrowid


# Assurance verifiee et non echue : la meme condition qu'en SQL, cote Python.
INSURED_VERIFIED_SQL = (
    "p.insurance_status = 'verified' AND (p.insurance_expires_at IS NULL "
    "OR p.insurance_expires_at = '' OR p.insurance_expires_at >= date('now'))"
)


def cert_is_expired(expires_at) -> bool:
    """True si la date d'expiration (YYYY-MM-DD) est passee."""
    from datetime import date
    raw = str(expires_at or "").strip()[:10]
    return bool(raw) and raw < date.today().isoformat()


def _decorate_cert(row) -> dict:
    d = dict(row)
    d["is_expired"] = cert_is_expired(d.get("expires_at"))
    d.setdefault("review_status", "verified" if d.get("is_verified") else "pending")
    return d


def list_certifications(pilot_user_id: int) -> list:
    return [
        _decorate_cert(r) for r in db.fetchall(
            "SELECT * FROM pilot_certifications WHERE pilot_user_id=? ORDER BY issued_at DESC",
            (pilot_user_id,),
        )
    ]


def delete_certification(cert_id: int, owner_user_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM pilot_certifications WHERE id=? AND pilot_user_id=?",
        (cert_id, owner_user_id),
    )
    if cur.rowcount > 0:
        # Supprimer un brevet verifie peut faire tomber le badge profil.
        refresh_user_verified(owner_user_id)
    return cur.rowcount > 0


def get_certification(cert_id: int) -> Optional[dict]:
    row = db.fetchone("SELECT * FROM pilot_certifications WHERE id=?", (cert_id,))
    return _decorate_cert(row) if row else None


CERT_REVIEW_STATUSES = ("pending", "verified", "rejected")

_CERT_REVIEW_SELECT = (
    "SELECT c.*, u.full_name AS pilot_full_name, u.username, "
    "       u.country AS pilot_country, u.city AS pilot_city, "
    "       u.is_verified AS pilot_is_verified, u.created_at AS pilot_since, "
    "       p.insurance AS pilot_insurance, p.insurance_company AS pilot_insurance_company, "
    "       p.insurance_policy AS pilot_insurance_policy, p.business_name AS pilot_business_name, "
    "       p.stripe_account_id AS pilot_stripe_account_id, "
    "       p.stripe_charges_enabled AS pilot_stripe_kyc, "
    "       (SELECT COUNT(*) FROM name_change_requests n "
    "         WHERE n.user_id=c.pilot_user_id AND n.status='pending') AS pilot_name_change_pending, "
    "       (SELECT COUNT(*) FROM pilot_certifications x "
    "         WHERE x.pilot_user_id=c.pilot_user_id AND x.is_verified=1) AS pilot_verified_certs, "
    "       (SELECT COUNT(*) FROM pilot_certifications x "
    "         WHERE x.pilot_user_id=c.pilot_user_id) AS pilot_total_certs, "
    "       a.full_name AS reviewed_by_name "
    "FROM pilot_certifications c "
    "JOIN users u ON u.id = c.pilot_user_id "
    "LEFT JOIN pilot_profiles p ON p.user_id = c.pilot_user_id "
    "LEFT JOIN users a ON a.id = c.reviewed_by "
)


def list_certifications_for_review(status: str = "pending", limit: int = 200) -> list:
    """File de revue admin. `pending` = a verifier ET justificatif present
    (sans document, rien a controler : le brevet reste declaratif) ;
    `verified` / `rejected` = historique ; `all`."""
    q = _CERT_REVIEW_SELECT
    args: list = []
    if status == "pending":
        q += ("WHERE c.review_status='pending' AND c.document_path IS NOT NULL "
              "AND c.document_path <> '' ORDER BY c.created_at ASC LIMIT ?")
    elif status in ("verified", "rejected"):
        q += "WHERE c.review_status=? ORDER BY c.reviewed_at DESC, c.id DESC LIMIT ?"
        args.append(status)
    else:
        q += "ORDER BY c.created_at DESC LIMIT ?"
    args.append(limit)
    return [_decorate_cert(r) for r in db.fetchall(q, args)]


def list_pending_certifications() -> list:
    """Compat : brevets avec justificatif en attente de revue."""
    return list_certifications_for_review("pending")


def count_certifications_pending() -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM pilot_certifications "
        "WHERE review_status='pending' AND document_path IS NOT NULL AND document_path <> ''"
    )
    return int(row["n"]) if row else 0


def refresh_user_verified(user_id: int) -> bool:
    """Badge profil : users.is_verified = 1 ssi au moins un brevet verifie
    par l'admin et non expire. Recalcule a chaque revue / suppression /
    passage du cron (expirations)."""
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM pilot_certifications "
        "WHERE pilot_user_id=? AND is_verified=1 "
        "  AND (expires_at IS NULL OR expires_at='' OR expires_at >= date('now'))",
        (user_id,),
    )
    flag = 1 if row and row["n"] else 0
    db.execute("UPDATE users SET is_verified=? WHERE id=?", (flag, user_id))
    return bool(flag)


# ---------------------------------------------------------------------------
# Assurance RC pro : declaration du pilote + attestation controlee par l'admin
#
# Meme regle que pour les brevets : sans justificatif il n'y a rien a
# controler, donc rien a promettre. Le badge public et le filtre « assuré »
# ne suivent que le verdict admin, et tombent a l'echeance.
# ---------------------------------------------------------------------------

INSURANCE_STATUSES = ("none", "pending", "verified", "rejected")


def insurance_state(profile) -> dict:
    """{status, is_expired, is_valid, declared} pour une ligne pilot_profiles."""
    p = dict(profile or {})
    status = p.get("insurance_status") or "none"
    expired = cert_is_expired(p.get("insurance_expires_at"))
    return {
        "status": status,
        "is_expired": expired,
        "is_valid": status == "verified" and not expired,
        "declared": bool(p.get("insurance")),
        "has_document": bool(p.get("insurance_document_path")),
    }


def submit_insurance(user_id: int, *, company: str = "", policy: str = "",
                     expires_at: str = "", document_path: str = "") -> None:
    """Le pilote depose ou met a jour son attestation : tout nouveau document
    repasse en attente, un controle ne vaut que pour la piece controlee."""
    # SQL explicite plutot qu'upsert_pilot_profile : il faut pouvoir REMETTRE
    # a NULL le verdict precedent, ce que l'upsert (qui ignore les None) ne
    # sait pas faire, et ces colonnes ne doivent pas etre modifiables depuis
    # le formulaire de profil.
    db.execute("INSERT OR IGNORE INTO pilot_profiles (user_id) VALUES (?)", (user_id,))
    champs = [
        ("insurance_company", (company or "").strip()[:120] or None),
        ("insurance_policy", (policy or "").strip()[:80] or None),
        ("insurance_expires_at", (expires_at or "").strip()[:10] or None),
    ]
    if document_path:
        champs.append(("insurance_document_path", document_path))
    sets = [f"{nom} = ?" for nom, _ in champs] + [
        "insurance = 1", "insurance_status = 'pending'", "insurance_note = NULL",
        "insurance_reviewed_at = NULL", "insurance_reviewed_by = NULL",
        "updated_at = datetime('now')",
    ]
    db.execute(f"UPDATE pilot_profiles SET {', '.join(sets)} WHERE user_id = ?",
               [v for _, v in champs] + [user_id])


def review_insurance(user_id: int, admin_user_id: Optional[int],
                     decision: str, note: str = "") -> Optional[dict]:
    """Verdict de l'admin sur l'attestation. Trace dans l'audit et notifie
    le pilote (best effort, comme pour les brevets)."""
    if decision not in ("verified", "rejected", "pending"):
        raise ValueError(f"decision invalide: {decision}")
    profile = db.fetchone("SELECT * FROM pilot_profiles WHERE user_id=?", (user_id,))
    if not profile:
        return None
    note = (note or "").strip()[:1000]
    db.execute(
        "UPDATE pilot_profiles SET insurance_status=?, insurance_note=?, "
        "insurance_reviewed_at=datetime('now'), insurance_reviewed_by=? WHERE user_id=?",
        (decision, note or None, admin_user_id, user_id),
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'insurance_review', ?, ?)",
        (admin_user_id, f"user:{user_id}",
         json.dumps({"decision": decision, "note": note})),
    )
    fresh = db.fetchone("SELECT * FROM pilot_profiles WHERE user_id=?", (user_id,))
    state = insurance_state(fresh)
    if decision in ("verified", "rejected"):
        try:
            import mailer
            pilot = db.fetchone("SELECT id, email, full_name FROM users WHERE id=?", (user_id,))
            if pilot and pilot["email"]:
                mailer.send_insurance_reviewed(
                    pilot=dict(pilot), decision=decision, note=note,
                    company=(fresh["insurance_company"] if fresh else "") or "",
                    is_valid=state["is_valid"],
                )
        except Exception as exc:
            log.warning("email insurance_reviewed failed for user=%s : %s", user_id, exc)
    return {"profile": dict(fresh) if fresh else {}, "state": state}


def list_insurances_for_review(status: str = "pending", limit: int = 200) -> list:
    """File de revue admin. `pending` = attestation deposee et pas encore
    controlee ; sans document il n'y a rien a examiner."""
    q = ("SELECT u.id AS user_id, u.full_name, u.username, u.country, u.city, "
         "       p.insurance, p.insurance_company, p.insurance_policy, "
         "       p.insurance_expires_at, p.insurance_document_path, "
         "       COALESCE(p.insurance_status,'none') AS insurance_status, "
         "       p.insurance_note, p.insurance_reviewed_at "
         "FROM pilot_profiles p JOIN users u ON u.id = p.user_id "
         "WHERE u.deleted_at IS NULL AND p.insurance_document_path IS NOT NULL "
         "  AND p.insurance_document_path <> '' ")
    args: list = []
    if status in ("pending", "verified", "rejected"):
        q += "AND COALESCE(p.insurance_status,'none') = ? "
        args.append(status)
    q += "ORDER BY p.insurance_reviewed_at IS NULL DESC, u.id LIMIT ?"
    args.append(limit)
    out = []
    for r in db.fetchall(q, args):
        d = dict(r)
        d["state"] = insurance_state(d)
        out.append(d)
    return out


def count_insurances_pending() -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM pilot_profiles p JOIN users u ON u.id = p.user_id "
        "WHERE u.deleted_at IS NULL AND COALESCE(p.insurance_status,'none') = 'pending' "
        "  AND p.insurance_document_path IS NOT NULL AND p.insurance_document_path <> ''"
    )
    return int(row["n"]) if row else 0


def refresh_all_user_verified() -> int:
    """Cron : recalcule le badge de tous les pilotes ayant des brevets
    (fait tomber le badge quand le dernier brevet verifie expire).
    Retourne le nombre de profils dont le badge a change."""
    changed = 0
    ids = [r["pilot_user_id"] for r in db.fetchall(
        "SELECT DISTINCT pilot_user_id FROM pilot_certifications")]
    for uid in ids:
        before = db.fetchone("SELECT is_verified FROM users WHERE id=?", (uid,))
        after = refresh_user_verified(uid)
        if before is not None and bool(before["is_verified"]) != after:
            changed += 1
    return changed


def review_certification(cert_id: int, admin_user_id: Optional[int],
                         decision: str, note: str = "") -> Optional[dict]:
    """Revue d'un justificatif : `decision` = verified | rejected | pending
    (pending = revocation / remise en attente). Met a jour le brevet, le
    badge profil, l'audit, et previent le pilote (best effort)."""
    if decision not in CERT_REVIEW_STATUSES:
        raise ValueError(f"decision invalide: {decision}")
    cert = get_certification(cert_id)
    if not cert:
        return None
    note = (note or "").strip()[:1000]
    db.execute(
        "UPDATE pilot_certifications SET is_verified=?, review_status=?, "
        "review_note=?, reviewed_at=datetime('now'), reviewed_by=? WHERE id=?",
        (1 if decision == "verified" else 0, decision, note or None,
         admin_user_id, cert_id),
    )
    profile_verified = refresh_user_verified(cert["pilot_user_id"])
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'certification_review', ?, ?)",
        (admin_user_id, f"cert:{cert_id}",
         json.dumps({"pilot": cert["pilot_user_id"], "decision": decision,
                     "note": note, "profile_verified": profile_verified})),
    )
    if decision in ("verified", "rejected"):
        try:
            import mailer
            pilot = db.fetchone("SELECT id, email, full_name FROM users WHERE id=?",
                                (cert["pilot_user_id"],))
            if pilot:
                mailer.send_certification_reviewed(
                    pilot=dict(pilot), cert=cert, decision=decision, note=note,
                    profile_verified=profile_verified,
                )
        except Exception as exc:
            log.warning("email certification_reviewed failed for cert=%s : %s", cert_id, exc)
    return {"cert": get_certification(cert_id), "profile_verified": profile_verified}


def set_certification_verified(cert_id: int, verified: bool) -> bool:
    """Compat (tests / anciens appels) : verifie ou remet en attente."""
    return review_certification(cert_id, None, "verified" if verified else "pending") is not None


def is_identity_locked(user_id: int) -> bool:
    """True des qu'au moins un brevet/justificatif a ete uploade.
    Le nom officiel devient alors non modifiable sans demande validee
    par un admin (ref. name_change_requests)."""
    row = db.fetchone(
        "SELECT 1 FROM pilot_certifications "
        "WHERE pilot_user_id=? AND document_path IS NOT NULL AND document_path <> '' "
        "LIMIT 1",
        (user_id,),
    )
    return bool(row)


# ---------------------------------------------------------------------------
# Demandes de changement de nom
# ---------------------------------------------------------------------------

def create_name_change_request(*, user_id: int, current_name: str,
                               requested_name: str, reason: str = "",
                               justif_path: str = "") -> int:
    cur = db.execute(
        "INSERT INTO name_change_requests "
        "(user_id, current_name, requested_name, reason, justif_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, current_name, requested_name, reason or None,
         justif_path or None),
    )
    return cur.lastrowid


def get_name_change_request(req_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT n.*, u.full_name AS user_full_name, u.username "
        "FROM name_change_requests n JOIN users u ON u.id = n.user_id "
        "WHERE n.id=?",
        (req_id,),
    )
    return dict(row) if row else None


def list_pending_name_changes() -> list:
    return [dict(r) for r in db.fetchall(
        "SELECT n.*, u.full_name AS user_full_name, u.username "
        "FROM name_change_requests n JOIN users u ON u.id = n.user_id "
        "WHERE n.status='pending' ORDER BY n.created_at ASC"
    )]


def list_name_change_requests_for_user(user_id: int) -> list:
    return [dict(r) for r in db.fetchall(
        "SELECT * FROM name_change_requests WHERE user_id=? ORDER BY created_at DESC",
        (user_id,),
    )]


def has_pending_name_change(user_id: int) -> bool:
    row = db.fetchone(
        "SELECT 1 FROM name_change_requests "
        "WHERE user_id=? AND status='pending' LIMIT 1",
        (user_id,),
    )
    return bool(row)


def approve_name_change(req_id: int, admin_id: int, note: str = "") -> bool:
    req = get_name_change_request(req_id)
    if not req or req["status"] != "pending":
        return False
    db.execute(
        "UPDATE users SET full_name=? WHERE id=?",
        (req["requested_name"], req["user_id"]),
    )
    db.execute(
        "UPDATE name_change_requests "
        "SET status='approved', reviewed_by=?, reviewed_at=datetime('now'), admin_note=? "
        "WHERE id=?",
        (admin_id, note or None, req_id),
    )
    return True


def reject_name_change(req_id: int, admin_id: int, note: str = "") -> bool:
    cur = db.execute(
        "UPDATE name_change_requests "
        "SET status='rejected', reviewed_by=?, reviewed_at=datetime('now'), admin_note=? "
        "WHERE id=? AND status='pending'",
        (admin_id, note or None, req_id),
    )
    return cur.rowcount > 0


def client_can_view_pilot_credentials(viewer_user_id: int, pilot_user_id: int) -> bool:
    """True si le viewer a une raison legitime de voir le PDF du brevet :
    - viewer == pilote lui-meme
    - relation funded en cours ou passee
    - viewer client a une mission sur laquelle le pilote a soumis une bid
      (le client envisage de retenir ce pilote)
    """
    if not viewer_user_id or not pilot_user_id:
        return False
    if viewer_user_id == pilot_user_id:
        return True
    if has_funded_relation(viewer_user_id, pilot_user_id):
        return True
    row = db.fetchone(
        "SELECT 1 FROM bids b "
        "JOIN missions m ON m.id = b.mission_id "
        "WHERE m.client_user_id=? AND b.pilot_user_id=? "
        "AND b.status IN ('pending','accepted') "
        "LIMIT 1",
        (viewer_user_id, pilot_user_id),
    )
    return bool(row)


# ---------------------------------------------------------------------------
# Drones
# ---------------------------------------------------------------------------

def add_drone(pilot_user_id: int, *, category: str, brand: str = "", model: str = "",
              serial_number: str = "", weight_g: Optional[int] = None,
              max_payload_g: Optional[int] = None, flight_time_min: Optional[int] = None,
              capabilities: Iterable[str] = (), notes: str = "",
              photo_path: str = "") -> int:
    cur = db.execute(
        "INSERT INTO pilot_drones "
        "(pilot_user_id, category, brand, model, serial_number, weight_g, max_payload_g, "
        " flight_time_min, capabilities, photo_path, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pilot_user_id, category, brand, model, serial_number, weight_g,
         max_payload_g, flight_time_min, _csv(capabilities), photo_path or None, notes),
    )
    return cur.lastrowid


def list_drones(pilot_user_id: int) -> list:
    rows = db.fetchall(
        "SELECT * FROM pilot_drones WHERE pilot_user_id=? ORDER BY created_at DESC",
        (pilot_user_id,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["capabilities"] = [c for c in (d.get("capabilities") or "").split(",") if c]
        out.append(d)
    return out


def delete_drone(drone_id: int, owner_user_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM pilot_drones WHERE id=? AND pilot_user_id=?",
        (drone_id, owner_user_id),
    )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Recherche pilotes
# ---------------------------------------------------------------------------

def search_pilots(*, country: str = "", city: str = "", mission_type: str = "",
                  capability: str = "", text: str = "", lat: Optional[float] = None,
                  lng: Optional[float] = None, radius_km: int = DEFAULT_SEARCH_RADIUS_KM,
                  min_rating: float = 0, only_available: bool = True,
                  only_verified: bool = False, only_insured: bool = False,
                  authority: str = "", kind: str = "", deliverable: str = "",
                  strict_radius: bool = False, limit: int = 50) -> list:
    """Annuaire pilotes.

    Filtres « confiance » (facon annuaire pro) :
      only_verified -> au moins un brevet controle par l'admin (ou compte verifie)
      only_insured  -> attestation RC pro verifiee par l'admin et non echue
      authority     -> detient un brevet de cette autorite (ex. Transport Canada)
    Chaque resultat expose `verified_authorities` (codes des autorites dont un
    brevet est verifie) et `certs_verified` pour les badges des cartes.
    """
    # PERF : on JOIN un agregat de reviews + un agregat de brevets dans la
    # requete principale au lieu d'appeler pilot_rating() / list_certifications()
    # N fois en Python (1 seule requete).
    _text = (text or "").strip().lower()   # recherche libre (search box / ?q=)
    q = [
        "SELECT u.id, u.username, u.full_name, u.country, u.city, u.lat, u.lng, "
        "       u.is_verified, u.avatar_path, u.bio, "
        "       p.headline, p.hourly_rate, p.daily_rate, p.currency AS p_currency, "
        "       p.travel_radius_km, p.is_available, p.insurance, p.languages, "
        "       COALESCE(p.insurance_status, 'none') AS insurance_status, "
        "       p.insurance_expires_at, "
        f"       CASE WHEN {INSURED_VERIFIED_SQL} THEN 1 ELSE 0 END AS insured_verified, "
        "       COALESCE(p.kind, 'pro') AS kind, p.business_name, p.school_programs, "
        "       COALESCE(r.avg_rating, 0.0) AS rating_avg, "
        "       COALESCE(r.review_count, 0) AS rating_count, "
        "       COALESCE(c.n_certs, 0) AS certs_total, "
        "       COALESCE(c.n_verified, 0) AS certs_verified, "
        "       c.verified_auths AS verified_auths "
        "FROM users u "
        "JOIN pilot_profiles p ON p.user_id = u.id "
        "LEFT JOIN ("
        "  SELECT target_user_id, AVG(rating) AS avg_rating, "
        "         COUNT(*) AS review_count "
        "  FROM reviews GROUP BY target_user_id"
        ") r ON r.target_user_id = u.id "
        "LEFT JOIN ("
        "  SELECT pilot_user_id, COUNT(*) AS n_certs, "
        "         SUM(is_verified) AS n_verified, "
        "         GROUP_CONCAT(DISTINCT CASE WHEN is_verified=1 THEN authority END) "
        "           AS verified_auths "
        "  FROM pilot_certifications GROUP BY pilot_user_id"
        ") c ON c.pilot_user_id = u.id "
        "WHERE u.role IN ('pilot', 'both') AND u.deleted_at IS NULL",
    ]
    args: list = []
    if only_available:
        q.append("AND p.is_available = 1")
    if kind in PROFILE_KIND_CODES:
        q.append("AND COALESCE(p.kind, 'pro') = ?")
        args.append(kind)
    if only_verified:
        q.append("AND (u.is_verified = 1 OR COALESCE(c.n_verified, 0) > 0)")
    if only_insured:
        # « Assuré » filtre sur une attestation CONTROLEE et non echue : une
        # case cochee par le pilote ne suffit pas a le promettre a un client.
        q.append(f"AND {INSURED_VERIFIED_SQL}")
    if authority:
        q.append(
            "AND EXISTS (SELECT 1 FROM pilot_certifications pc "
            "             WHERE pc.pilot_user_id=u.id AND pc.authority=?)"
        )
        args.append(authority)
    if country:
        q.append(
            "AND (u.country = ? OR EXISTS ("
            "  SELECT 1 FROM pilot_territories t WHERE t.pilot_user_id=u.id AND t.country=?"
            "))"
        )
        args.extend([country, country])
    if city:
        q.append("AND lower(u.city) LIKE ?")
        args.append(f"%{city.lower()}%")
    if mission_type:
        q.append(
            "AND EXISTS (SELECT 1 FROM pilot_specialties s "
            "             WHERE s.pilot_user_id=u.id AND s.mission_type=?)"
        )
        args.append(mission_type)
    if deliverable:
        q.append(
            "AND EXISTS (SELECT 1 FROM pilot_deliverables d0 "
            "             WHERE d0.pilot_user_id=u.id AND d0.deliverable=?)"
        )
        args.append(deliverable)
    if capability:
        q.append(
            "AND EXISTS (SELECT 1 FROM pilot_drones d "
            "             WHERE d.pilot_user_id=u.id AND ',' || d.capabilities || ',' LIKE ?)"
        )
        args.append(f"%,{capability},%")
    if _text:
        like = f"%{_text}%"
        q.append(
            "AND (lower(u.full_name) LIKE ? OR lower(u.city) LIKE ? "
            "OR lower(u.country) LIKE ? OR lower(COALESCE(p.headline,'')) LIKE ? "
            "OR lower(COALESCE(p.business_name,'')) LIKE ?)"
        )
        args.extend([like, like, like, like, like])
    if min_rating > 0:
        # filtre note minimum directement en SQL (LEFT JOIN garantit 0 si pas de reviews)
        q.append("AND COALESCE(r.avg_rating, 0.0) >= ?")
        args.append(float(min_rating))
    q.append("ORDER BY u.is_verified DESC, p.is_available DESC")
    # La distance est calculee en Python. Avec des coordonnees, limiter en SQL
    # avant ce tri pouvait eliminer les pilotes les plus proches.
    if lat is None or lng is None:
        q.append("LIMIT ?")
        args.append(limit)
    rows = [dict(r) for r in db.fetchall(" ".join(q), args)]
    radius = max(1, min(radius_km, MAX_SEARCH_RADIUS_KM))
    enriched = []
    for r in rows:
        if lat is not None and lng is not None and r.get("lat") is not None:
            d = db.haversine_km(lat, lng, r["lat"], r["lng"])
            r["distance_km"] = round(d, 1)
            # NON-EXCLUSION par defaut : on calcule la distance (pour le tri)
            # mais on n'exclut que si strict_radius. Un pilote 'de partout'
            # (ou une mission specialisee lointaine) reste donc visible.
            if strict_radius and d > radius:
                continue
        else:
            r["distance_km"] = None
            if strict_radius and lat is not None and lng is not None:
                continue
        # rating est deja dans r["rating_avg"] / r["rating_count"] — on
        # construit l'objet attendu par les callers.
        r["rating"] = {
            "avg": round(float(r.pop("rating_avg") or 0.0), 2),
            "count": int(r.pop("rating_count") or 0),
        }
        auths = r.pop("verified_auths", None) or ""
        r["verified_authorities"] = [a for a in auths.split(",") if a]
        enriched.append(r)
    if lat is not None and lng is not None:
        enriched.sort(key=lambda x: (x.get("distance_km") or 1e9))
    return enriched[:limit]


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------

def create_mission(client_user_id: int, **f) -> int:
    title = str(f.get("title") or "").strip()
    description = str(f.get("description") or "").strip()
    mission_type = str(f.get("mission_type") or "")
    # Alias historique accepte par les anciens clients/API.
    mission_type = {"evenementiel": "evenement"}.get(mission_type, mission_type)
    currency = str(f.get("currency") or DEFAULT_CURRENCY).upper()
    if not title or not description:
        raise ValueError("titre et description requis")
    if mission_type not in {code for code, _label in MISSION_TYPES}:
        raise ValueError("type de mission invalide")
    if currency not in CURRENCIES:
        raise ValueError("devise invalide")

    numeric: dict[str, Optional[float]] = {}
    for name in ("budget_min", "budget_max", "duration_hours", "lat", "lng"):
        raw = f.get(name)
        if raw is None or raw == "":
            numeric[name] = None
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} invalide") from exc
        if not math.isfinite(value):
            raise ValueError(f"{name} invalide")
        numeric[name] = value
    for name in ("budget_min", "budget_max", "duration_hours"):
        if numeric[name] is not None and numeric[name] < 0:
            raise ValueError(f"{name} ne peut pas être négatif")
    if (numeric["budget_min"] is not None and numeric["budget_max"] is not None
            and numeric["budget_min"] > numeric["budget_max"]):
        raise ValueError("le budget minimum dépasse le budget maximum")
    if numeric["lat"] is not None and not -90 <= numeric["lat"] <= 90:
        raise ValueError("latitude invalide")
    if numeric["lng"] is not None and not -180 <= numeric["lng"] <= 180:
        raise ValueError("longitude invalide")

    cur = db.execute(
        "INSERT INTO missions "
        "(client_user_id, title, description, mission_type, country, region, city, "
        " lat, lng, address, budget_min, budget_max, currency, duration_hours, "
        " start_date, end_date, is_urgent, requires_insurance, "
        " requires_certifications, requires_capabilities) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            client_user_id,
            title,
            description,
            mission_type,
            f.get("country") or "",
            f.get("region"),
            f.get("city"),
            numeric["lat"],
            numeric["lng"],
            f.get("address"),
            numeric["budget_min"],
            numeric["budget_max"],
            currency,
            numeric["duration_hours"],
            f.get("start_date"),
            f.get("end_date"),
            1 if f.get("is_urgent") else 0,
            1 if f.get("requires_insurance") else 0,
            _csv(f.get("requires_certifications") or []),
            _csv(f.get("requires_capabilities") or []),
        ),
    )
    return cur.lastrowid


def pilots_for_mission_alert(mission: dict, exclude_user_id: int = 0) -> list:
    """Pilotes disponibles a prevenir qu'une mission vient d'etre publiee
    dans leur rayon de deplacement. Inclut les pilotes acceptant les
    missions hors zone (accepts_remote) quelle que soit la distance, et,
    a defaut de coordonnees, ceux de la meme ville. Chaque pilote renvoye
    porte un champ distance_km (None si la distance est inconnue), trie du
    plus proche au plus loin."""
    rows = db.fetchall(
        "SELECT u.id, u.email, u.full_name, u.city, u.lat, u.lng, "
        "       p.travel_radius_km, p.accepts_remote "
        "FROM users u JOIN pilot_profiles p ON p.user_id = u.id "
        "WHERE u.role IN ('pilot', 'both') AND p.is_available = 1 "
        "  AND u.deleted_at IS NULL AND COALESCE(u.notify_alerts, 1) = 1 "
        "  AND u.id != ?",
        (exclude_user_id,),
    )
    m_lat, m_lng = mission.get("lat"), mission.get("lng")
    m_city = (mission.get("city") or "").strip().lower()
    out: list = []
    for raw in rows:
        r = dict(raw)
        if not r.get("email"):
            continue
        dist = None
        if (m_lat is not None and m_lng is not None
                and r.get("lat") is not None and r.get("lng") is not None):
            dist = round(db.haversine_km(m_lat, m_lng, r["lat"], r["lng"]), 1)
        if r.get("accepts_remote"):
            r["distance_km"] = dist
            out.append(r)
        elif dist is not None:
            radius = r.get("travel_radius_km") or DEFAULT_SEARCH_RADIUS_KM
            if dist <= radius:
                r["distance_km"] = dist
                out.append(r)
        elif m_city and (r.get("city") or "").strip().lower() == m_city:
            r["distance_km"] = None
            out.append(r)
    out.sort(key=lambda x: x["distance_km"] if x["distance_km"] is not None else 1e9)
    return out


def update_mission_status(mission_id: int, status: str, commit: bool = True):
    if status not in MISSION_STATUS:
        raise ValueError(f"statut invalide: {status}")
    db.execute(
        "UPDATE missions SET status=?, updated_at=datetime('now') WHERE id=?",
        (status, mission_id), commit=commit,
    )


# --------------------------------------------------------------------------- #
# Sitemap : URLs publiques indexables (profils pilotes + missions ouvertes)
# --------------------------------------------------------------------------- #

def sitemap_pilots(limit: int = 5000) -> list:
    rows = db.fetchall(
        "SELECT u.id, COALESCE(p.updated_at, u.created_at) AS lastmod "
        "FROM users u JOIN pilot_profiles p ON p.user_id = u.id "
        "WHERE u.role IN ('pilot', 'both') AND u.deleted_at IS NULL ORDER BY u.id LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Pages d'atterrissage de l'annuaire (pays, ville, specialite)
#
# Ce sont les requetes que les gens tapent vraiment (« pilote de drone
# Montréal », « inspection de toiture par drone »). On ne cree une page que
# pour ce qui existe en base : une liste vide indexee nuit a tout le site.
# Memes criteres que la recherche par defaut (pilote disponible, compte actif).
# ---------------------------------------------------------------------------

_LANDING_BASE = (
    "FROM users u JOIN pilot_profiles p ON p.user_id = u.id "
    "WHERE u.role IN ('pilot','both') AND u.deleted_at IS NULL AND p.is_available = 1 "
)


def _group_by_slug(rows, key: str) -> list:
    """« Montréal », « Montreal » et « montréal » sont une seule page : on
    regroupe par slug, l'orthographe la plus frequente sert d'affichage et
    `names` garde toutes les variantes pour retrouver les pilotes."""
    import seo
    groups: dict = {}
    for r in rows:
        slug = seo.slugify(r[key])
        g = groups.setdefault(slug, {"slug": slug, "n": 0, "names": [], "_best": 0,
                                     **{k: v for k, v in r.items() if k not in (key, "n")}})
        g["n"] += r["n"]
        g["names"].append(r[key])
        if r["n"] > g["_best"]:
            g["_best"], g["name"] = r["n"], r[key]
    out = sorted(groups.values(), key=lambda g: (-g["n"], g["name"]))
    for g in out:
        g.pop("_best")
    return out


def landing_countries() -> list:
    """[{name, names, slug, n}] tries par nombre de pilotes puis nom."""
    rows = db.fetchall(
        "SELECT u.country AS name, COUNT(*) AS n " + _LANDING_BASE +
        "AND u.country IS NOT NULL AND u.country <> '' GROUP BY u.country"
    )
    return _group_by_slug([dict(r) for r in rows], "name")


def landing_cities(country: Optional[str] = None) -> list:
    """[{name, names, country, slug, n}] ; `country` (nom en base) restreint."""
    import seo
    q = ("SELECT u.city AS name, u.country AS country, COUNT(*) AS n " + _LANDING_BASE +
         "AND u.city IS NOT NULL AND u.city <> '' AND u.country IS NOT NULL AND u.country <> '' ")
    args: list = []
    if country:
        q += "AND u.country = ? "
        args.append(country)
    q += "GROUP BY u.country, u.city"
    rows = [dict(r) for r in db.fetchall(q, tuple(args))]
    # la ville est identifiee par (pays, slug) : on porte le slug du pays
    for r in rows:
        r["country_slug"] = seo.slugify(r["country"])
    out = []
    by_country: dict = {}
    for r in rows:
        by_country.setdefault(r["country_slug"], []).append(r)
    for cs, group in by_country.items():
        out.extend(_group_by_slug(group, "name"))
    out.sort(key=lambda g: (-g["n"], g["name"]))
    return out


def landing_specialties() -> list:
    """[{code, n}] pour chaque specialite connue ayant au moins un pilote."""
    known = {k for k, _ in MISSION_TYPES}
    rows = db.fetchall(
        "SELECT s.mission_type AS code, COUNT(DISTINCT u.id) AS n "
        "FROM pilot_specialties s "
        "JOIN users u ON u.id = s.pilot_user_id "
        "JOIN pilot_profiles p ON p.user_id = u.id "
        "WHERE u.role IN ('pilot','both') AND u.deleted_at IS NULL AND p.is_available = 1 "
        "GROUP BY s.mission_type ORDER BY n DESC, s.mission_type"
    )
    return [{"code": r["code"], "n": r["n"]} for r in rows if r["code"] in known]


def resolve_country_slug(slug: str) -> Optional[dict]:
    """Slug d'URL -> groupe {name, names, slug, n} (None si aucun pilote)."""
    for c in landing_countries():
        if c["slug"] == slug:
            return c
    return None


def resolve_city_slug(country_slug: str, slug: str) -> Optional[dict]:
    for c in landing_cities():
        if c["country_slug"] == country_slug and c["slug"] == slug:
            return c
    return None


def pilots_in_place(*, country_names, city_slug: str = "", limit: int = 200) -> list:
    """Pilotes d'un pays (toutes orthographes) et, si `city_slug`, de cette
    ville : filtre exact par slug, pas de LIKE (« laval » ne prend pas
    « Lavaltrie »)."""
    import seo
    out: list = []
    for name in country_names:
        out.extend(search_pilots(country=name, limit=limit))
    seen = set()
    uniq = []
    for p in out:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        if city_slug and seo.slugify(p.get("city") or "") != city_slug:
            continue
        uniq.append(p)
    return uniq


def sitemap_missions(limit: int = 5000) -> list:
    rows = db.fetchall(
        "SELECT id, COALESCE(updated_at, created_at) AS lastmod "
        "FROM missions WHERE status = 'open' ORDER BY id LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


def get_mission(mission_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT m.*, u.full_name AS client_name, u.username AS client_username "
        "FROM missions m JOIN users u ON u.id = m.client_user_id "
        "WHERE m.id=?",
        (mission_id,),
    )
    if not row:
        return None
    out = dict(row)
    out["bids"] = list_bids(mission_id)
    return out


def search_missions(*, country: str = "", city: str = "", mission_type: str = "",
                    status: str = "open", lat: Optional[float] = None,
                    lng: Optional[float] = None,
                    radius_km: int = DEFAULT_SEARCH_RADIUS_KM,
                    only_urgent: bool = False, strict_radius: bool = False,
                    limit: int = 100) -> list:
    q = [
        "SELECT m.*, u.full_name AS client_name "
        "FROM missions m JOIN users u ON u.id=m.client_user_id "
        "WHERE 1=1",
    ]
    args: list = []
    if status:
        q.append("AND m.status=?")
        args.append(status)
    if country:
        q.append("AND m.country=?")
        args.append(country)
    if city:
        q.append("AND lower(m.city) LIKE ?")
        args.append(f"%{city.lower()}%")
    if mission_type:
        q.append("AND m.mission_type=?")
        args.append(mission_type)
    if only_urgent:
        q.append("AND m.is_urgent=1")
    q.append("ORDER BY m.is_urgent DESC, m.created_at DESC")
    if lat is None or lng is None:
        q.append("LIMIT ?")
        args.append(limit)
    rows = [dict(r) for r in db.fetchall(" ".join(q), args)]
    radius = max(1, min(radius_km, MAX_SEARCH_RADIUS_KM))
    out = []
    for r in rows:
        if lat is not None and lng is not None and r.get("lat") is not None:
            d = db.haversine_km(lat, lng, r["lat"], r["lng"])
            r["distance_km"] = round(d, 1)
            if strict_radius and d > radius:
                continue
        else:
            r["distance_km"] = None
            if strict_radius and lat is not None and lng is not None:
                continue
        out.append(r)
    if lat is not None and lng is not None:
        # Recherche « autour de » : du plus proche au plus lointain (les
        # missions sans coordonnees passent en fin de liste).
        out.sort(key=lambda x: (x.get("distance_km") is None,
                                x.get("distance_km") or 0, -x["is_urgent"]))
    return out[:limit]


def list_missions_by_client(client_user_id: int) -> list:
    rows = db.fetchall(
        "SELECT m.*, "
        "  (SELECT COUNT(*) FROM bids b WHERE b.mission_id=m.id) AS bid_count "
        "FROM missions m WHERE m.client_user_id=? ORDER BY m.created_at DESC",
        (client_user_id,),
    )
    return [dict(r) for r in rows]


def list_missions_by_pilot(pilot_user_id: int) -> list:
    """Missions auxquelles le pilote a soumissionne ou est assigne."""
    rows = db.fetchall(
        "SELECT m.*, b.price AS my_bid_price, b.status AS my_bid_status "
        "FROM missions m JOIN bids b ON b.mission_id=m.id "
        "WHERE b.pilot_user_id=? ORDER BY m.created_at DESC",
        (pilot_user_id,),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Encheres
# ---------------------------------------------------------------------------

def place_bid(mission_id: int, pilot_user_id: int, *, price: float,
              currency: str = DEFAULT_CURRENCY, eta_hours: Optional[float] = None,
              message: str = "", description: str = "",
              deliverables: str = "", terms: str = "") -> int:
    """Cree ou revise un devis pour une mission.

    - Premier devis : INSERT avec revision_no=1, statut 'pending'.
    - Revision (apres refus client) : snapshot l'ancien devis dans
      bid_revisions, incremente revision_no et repasse en 'pending'.
    - Mise a jour simple d'un devis 'pending' : on ecrase sans creer
      d'entree d'historique (le pilote retouche son brouillon).
    """
    mission = db.fetchone(
        "SELECT client_user_id, status FROM missions WHERE id=?", (mission_id,)
    )
    if not mission:
        raise LookupError("mission introuvable")
    if mission["client_user_id"] == pilot_user_id:
        raise ValueError("vous ne pouvez pas soumissionner sur votre propre mission")
    if mission["status"] != "open":
        raise ValueError("cette mission n'accepte plus de devis")
    existing = db.fetchone(
        "SELECT id, revision_no, price, currency, eta_hours, message, "
        "       description, deliverables, terms, status, client_response "
        "FROM bids WHERE mission_id=? AND pilot_user_id=?",
        (mission_id, pilot_user_id),
    )
    is_new = existing is None
    is_revision = bool(existing and existing["status"] in ("rejected", "withdrawn"))

    if is_revision:
        assert existing is not None  # garanti par is_revision (narrowing)
        # Snapshot de la version refusee avant ecrasement
        db.execute(
            "INSERT INTO bid_revisions "
            "(bid_id, revision_no, price, currency, eta_hours, message, "
            " description, deliverables, terms, status, client_response) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (existing["id"], existing["revision_no"], existing["price"],
             existing["currency"], existing["eta_hours"], existing["message"],
             existing["description"], existing["deliverables"],
             existing["terms"], existing["status"], existing["client_response"]),
        )
        new_rev = int(existing["revision_no"] or 1) + 1
        db.execute(
            "UPDATE bids SET price=?, currency=?, eta_hours=?, message=?, "
            "  description=?, deliverables=?, terms=?, "
            "  status='pending', client_response=NULL, "
            "  revision_no=?, updated_at=datetime('now') "
            "WHERE id=?",
            (price, currency, eta_hours, message, description, deliverables,
             terms, new_rev, existing["id"]),
        )
        bid_id = existing["id"]
    else:
        cur = db.execute(
            "INSERT INTO bids (mission_id, pilot_user_id, price, currency, "
            "  eta_hours, message, description, deliverables, terms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(mission_id, pilot_user_id) DO UPDATE SET "
            "  price=excluded.price, currency=excluded.currency, "
            "  eta_hours=excluded.eta_hours, message=excluded.message, "
            "  description=excluded.description, "
            "  deliverables=excluded.deliverables, terms=excluded.terms, "
            "  status='pending', updated_at=datetime('now')",
            (mission_id, pilot_user_id, price, currency, eta_hours, message,
             description, deliverables, terms),
        )
        bid_id = cur.lastrowid or (existing["id"] if existing else 0)

    if is_new or is_revision:
        try:
            import mailer
            client = db.fetchone(
                "SELECT u.id, u.email, u.full_name FROM users u "
                "JOIN missions m ON m.client_user_id=u.id WHERE m.id=?",
                (mission_id,),
            )
            mission = db.fetchone(
                "SELECT id, title, country, city FROM missions WHERE id=?",
                (mission_id,),
            )
            pilot = db.fetchone(
                "SELECT id, full_name FROM users WHERE id=?",
                (pilot_user_id,),
            )
            bid_payload = {
                "id": bid_id,
                "price": price, "currency": currency,
                "eta_hours": eta_hours, "message": message,
                "description": description, "deliverables": deliverables,
                "terms": terms,
                "revision_no": (int(existing["revision_no"] or 1) + 1) if is_revision else 1,
            }
            if client and mission and pilot:
                if is_revision:
                    mailer.send_bid_revised(
                        client=dict(client), mission=dict(mission),
                        bid=bid_payload, pilot=dict(pilot),
                    )
                else:
                    mailer.send_new_bid(
                        client=dict(client), mission=dict(mission),
                        bid=bid_payload, pilot=dict(pilot),
                    )
        except Exception as exc:
            log.warning("email new/revised bid failed for mission=%s : %s",
                        mission_id, exc)
    return bid_id


def reject_bid(mission_id: int, bid_id: int, client_user_id: int,
               reason: str = "") -> bool:
    """Le client refuse un devis specifique. La mission reste 'open' :
    le pilote peut soumettre une revision. Retourne True si refus
    applique, False sinon."""
    bid = db.fetchone(
        "SELECT b.*, m.client_user_id AS m_client "
        "FROM bids b JOIN missions m ON m.id=b.mission_id "
        "WHERE b.id=? AND b.mission_id=?",
        (bid_id, mission_id),
    )
    if not bid:
        return False
    if bid["m_client"] != client_user_id:
        return False
    if bid["status"] != "pending":
        return False
    db.execute(
        "UPDATE bids SET status='rejected', client_response=?, "
        "  updated_at=datetime('now') WHERE id=?",
        ((reason or "").strip()[:1000], bid_id),
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'reject_bid', ?, ?)",
        (client_user_id, f"bid:{bid_id}",
         json.dumps({"reason": (reason or "")[:200]})),
    )
    try:
        import mailer
        pilot = db.fetchone(
            "SELECT id, email, full_name FROM users WHERE id=?",
            (bid["pilot_user_id"],),
        )
        mission = db.fetchone(
            "SELECT id, title, country, city FROM missions WHERE id=?",
            (mission_id,),
        )
        client = db.fetchone(
            "SELECT id, full_name FROM users WHERE id=?",
            (client_user_id,),
        )
        if pilot and mission and client:
            mailer.send_bid_rejected(
                pilot=dict(pilot), mission=dict(mission),
                bid={"id": bid_id, "price": bid["price"],
                     "currency": bid["currency"],
                     "revision_no": bid["revision_no"]},
                client=dict(client),
                reason=(reason or "").strip(),
            )
    except Exception as exc:
        log.warning("email bid_rejected failed for bid=%s : %s", bid_id, exc)
    return True


def list_bid_revisions(bid_id: int) -> list:
    """Historique des versions precedentes d'un devis, plus recent d'abord."""
    rows = db.fetchall(
        "SELECT * FROM bid_revisions WHERE bid_id=? "
        "ORDER BY revision_no DESC, id DESC",
        (bid_id,),
    )
    return [dict(r) for r in rows]


def list_bids(mission_id: int) -> list:
    # PERF : on replie le rating dans la requete principale (LEFT JOIN sur
    # l'agregat reviews) au lieu d'appeler pilot_rating() une fois par devis.
    # Avant : 1 + N requetes sur la page mission la plus chaude ; apres : 1.
    rows = db.fetchall(
        "SELECT b.*, u.full_name AS pilot_name, u.username AS pilot_username, "
        "       u.is_verified, u.city AS pilot_city, u.country AS pilot_country, "
        "       COALESCE(r.avg_rating, 0.0) AS rating_avg, "
        "       COALESCE(r.review_count, 0) AS rating_count "
        "FROM bids b JOIN users u ON u.id=b.pilot_user_id "
        "LEFT JOIN ("
        "  SELECT target_user_id, AVG(rating) AS avg_rating, "
        "         COUNT(*) AS review_count "
        "  FROM reviews GROUP BY target_user_id"
        ") r ON r.target_user_id = b.pilot_user_id "
        "WHERE b.mission_id=? ORDER BY b.price ASC, b.created_at ASC",
        (mission_id,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["pilot_rating"] = {
            "avg": round(float(d.pop("rating_avg") or 0.0), 2),
            "count": int(d.pop("rating_count") or 0),
        }
        out.append(d)
    return out


def completed_missions_between(client_user_id: int, pilot_user_id: int) -> int:
    """Nombre de missions deja TERMINEES (completed) entre ce client et ce
    pilote : base de la commission degressive."""
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM bookings "
        "WHERE client_user_id=? AND pilot_user_id=? AND status='completed'",
        (client_user_id, pilot_user_id),
    )
    return int(row["n"]) if row else 0


def platform_fee_pct_for(prior_completed: int) -> float:
    """Taux de commission selon PLATFORM_FEE_TIERS [(seuil, taux), ...] :
    le dernier palier dont le seuil est atteint s'applique."""
    pct = float(PLATFORM_FEE_TIERS[0][1])
    for threshold, rate in PLATFORM_FEE_TIERS:
        if prior_completed >= threshold:
            pct = float(rate)
    return pct


def booking_fee_pct(booking: dict) -> float:
    """Taux effectivement applique a un booking. Les bookings anterieurs a la
    commission degressive n'ont pas de colonne renseignee : on le deduit du
    montant stocke (jamais du taux courant, qui a pu changer)."""
    pct = booking.get("platform_fee_pct")
    if pct is not None:
        return float(pct)
    price = float(booking.get("agreed_price") or 0)
    fee = float(booking.get("platform_fee") or 0)
    return round(fee * 100.0 / price, 1) if price else PLATFORM_FEE_PCT


def accept_bid(mission_id: int, bid_id: int, client_user_id: int) -> int:
    """Accepte une enchere : cree booking, ferme les autres encheres,
    passe la mission en 'assigned'. Retourne booking_id.

    Atomic : si 2 clients du meme compte (ou refresh en double-clic)
    acceptent simultanement, l'UPDATE conditionnel ne reussit qu'une
    seule fois — le 2e appel leve ValueError.
    """
    bid = db.fetchone("SELECT * FROM bids WHERE id=? AND mission_id=?", (bid_id, mission_id))
    mission = db.fetchone(
        "SELECT * FROM missions WHERE id=? AND client_user_id=?",
        (mission_id, client_user_id),
    )
    if not bid or not mission:
        raise LookupError("enchere ou mission introuvable")
    if mission["status"] != "open":
        raise ValueError(f"mission deja {mission['status']}")
    if bid["status"] != "pending":
        raise ValueError("ce devis n'est plus disponible")
    if bid["pilot_user_id"] == client_user_id:
        raise ValueError("vous ne pouvez pas accepter votre propre devis")
    # Commission degressive : selon les missions deja terminees entre les deux.
    prior = completed_missions_between(client_user_id, bid["pilot_user_id"])
    fee_pct = platform_fee_pct_for(prior)
    fee = round(bid["price"] * fee_pct / 100.0, 2)
    # Tout-ou-rien : verrou conditionnel + creation booking + cloture des
    # autres devis dans UNE transaction. Si le verrou echoue (rowcount=0),
    # une autre acceptation a deja eu lieu -> rollback complet + ValueError.
    with db.transaction():
        cur_lock = db.execute(
            "UPDATE missions SET status='assigned', updated_at=datetime('now') "
            "WHERE id=? AND client_user_id=? AND status='open'",
            (mission_id, client_user_id), commit=False,
        )
        if cur_lock.rowcount == 0:
            raise ValueError("mission deja attribuee (race detectee)")
        cur = db.execute(
            "INSERT INTO bookings "
            "(mission_id, bid_id, client_user_id, pilot_user_id, agreed_price, currency, "
            " platform_fee, platform_fee_pct, scheduled_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment')",
            (
                mission_id, bid_id, client_user_id, bid["pilot_user_id"],
                bid["price"], bid["currency"], fee, fee_pct, mission["start_date"],
            ), commit=False,
        )
        booking_id = cur.lastrowid
        db.execute("UPDATE bids SET status='accepted' WHERE id=?", (bid_id,),
                   commit=False)
        db.execute(
            "UPDATE bids SET status='rejected' WHERE mission_id=? AND id<>?",
            (mission_id, bid_id), commit=False,
        )
        db.execute(
            "INSERT INTO audit_log (user_id, action, target, payload) "
            "VALUES (?, 'accept_bid', ?, ?)",
            (client_user_id, f"mission:{mission_id}",
             json.dumps({"booking": booking_id, "bid": bid_id,
                         "fee_pct": fee_pct, "prior_completed": prior})), commit=False,
        )
    # Notification email au pilote choisi
    try:
        import mailer
        pilot = db.fetchone(
            "SELECT id, email, full_name FROM users WHERE id=?",
            (bid["pilot_user_id"],),
        )
        client = db.fetchone(
            "SELECT id, full_name FROM users WHERE id=?", (client_user_id,),
        )
        if pilot and client:
            mailer.send_bid_accepted(
                pilot=dict(pilot),
                mission={"id": mission_id, "title": mission["title"],
                         "country": mission["country"], "city": mission["city"]},
                booking={"id": booking_id, "agreed_price": bid["price"],
                         "currency": bid["currency"], "platform_fee": fee,
                         "platform_fee_pct": fee_pct},
                client=dict(client),
            )
    except Exception as exc:
        log.warning("email bid_accepted failed for booking=%s : %s", booking_id, exc)
    return booking_id


def withdraw_bid(bid_id: int, pilot_user_id: int):
    db.execute(
        "UPDATE bids SET status='withdrawn' WHERE id=? AND pilot_user_id=? AND status='pending'",
        (bid_id, pilot_user_id),
    )


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

def get_booking(booking_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT b.*, m.title AS mission_title, m.mission_type, m.country, m.city, "
        "       m.start_date AS mission_start_date, m.end_date AS mission_end_date, "
        "       m.description AS mission_description, "
        "       cu.full_name AS client_name, cu.city AS client_city, "
        "       cu.country AS client_country, "
        "       pu.full_name AS pilot_name, pu.avatar_path AS pilot_avatar, "
        "       pu.email AS pilot_email, pu.phone AS pilot_phone, "
        "       pp.business_name AS pilot_business_name, pp.headline AS pilot_headline, "
        "       pp.portfolio_url AS pilot_portfolio_url, "
        "       bd.description AS bid_description, bd.deliverables AS bid_deliverables, "
        "       bd.terms AS bid_terms, bd.eta_hours AS bid_eta_hours, "
        "       bd.message AS bid_message "
        "FROM bookings b "
        "JOIN missions m ON m.id=b.mission_id "
        "JOIN users cu ON cu.id=b.client_user_id "
        "JOIN users pu ON pu.id=b.pilot_user_id "
        "LEFT JOIN pilot_profiles pp ON pp.user_id=b.pilot_user_id "
        "LEFT JOIN bids bd ON bd.id=b.bid_id "
        "WHERE b.id=?",
        (booking_id,),
    )
    return dict(row) if row else None


def get_booking_by_bid(bid_id: int) -> Optional[dict]:
    """Reservation issue d'un devis (None si le devis n'a pas encore donne
    lieu a une reservation)."""
    row = db.fetchone(
        "SELECT id, status FROM bookings WHERE bid_id=? LIMIT 1", (bid_id,)
    )
    return dict(row) if row else None


def _parse_db_datetime(value) -> Optional["datetime"]:
    """'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM:SS' (SQLite, UTC) -> datetime aware.
    None si vide ou illisible."""
    from datetime import datetime, timezone
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) <= 10:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw.replace(" ", "T"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def compute_cancellation_fee(booking: dict) -> dict:
    """Calcule ce qui se passe si le CLIENT annule maintenant.

    Deux retenues independantes sur le prix convenu :
      - dedommagement PILOTE si annulation tardive
        (< LATE_CANCELLATION_HOURS avant la mission) : LATE_CANCELLATION_FEE_PCT ;
      - frais de service PLATEFORME si le client a deja paye (le contact du
        pilote lui a ete revele) et que la fenetre de grace
        CANCELLATION_GRACE_HOURS apres le paiement est passee :
        CANCELLATION_SERVICE_FEE_PCT, plafonne a CANCELLATION_SERVICE_FEE_CAP.
        Sans `paid_at` (pas encore paye, ou simple simulation) : aucun frais.

    Renvoie : {
      "is_late", "fee_pct", "fee_amount",            # part pilote
      "service_fee_pct", "service_fee_amount",       # part plateforme
      "within_grace", "grace_h", "grace_hours_left",
      "refund_amount",                               # rendu au client
      "hours_until", "preavis_h",
    }
    """
    from datetime import datetime, timezone
    from config import LATE_CANCELLATION_HOURS, LATE_CANCELLATION_FEE_PCT

    now = datetime.now(timezone.utc)
    price = float(booking.get("agreed_price") or 0)
    start_dt = _parse_db_datetime(
        (booking.get("scheduled_at") or booking.get("mission_start_date") or "")
    )
    hours_until = ((start_dt - now).total_seconds() / 3600.0) if start_dt else None

    is_late = hours_until is not None and hours_until < LATE_CANCELLATION_HOURS
    fee_pct = LATE_CANCELLATION_FEE_PCT if is_late else 0.0
    fee_amount = round(price * fee_pct / 100.0, 2)

    paid_dt = _parse_db_datetime(booking.get("paid_at"))
    within_grace = False
    grace_hours_left = None
    service_fee_pct = 0.0
    service_fee_amount = 0.0
    if paid_dt is not None:
        elapsed_h = (now - paid_dt).total_seconds() / 3600.0
        within_grace = elapsed_h < CANCELLATION_GRACE_HOURS
        grace_hours_left = round(max(0.0, CANCELLATION_GRACE_HOURS - elapsed_h), 1)
        if not within_grace:
            service_fee_pct = CANCELLATION_SERVICE_FEE_PCT
            service_fee_amount = round(
                min(price * service_fee_pct / 100.0, CANCELLATION_SERVICE_FEE_CAP), 2,
            )
    refund_amount = round(max(0.0, price - fee_amount - service_fee_amount), 2)
    return {
        "is_late": is_late,
        "fee_pct": fee_pct,
        "fee_amount": fee_amount,
        "service_fee_pct": service_fee_pct,
        "service_fee_amount": service_fee_amount,
        "within_grace": within_grace,
        "grace_h": CANCELLATION_GRACE_HOURS,
        "grace_hours_left": grace_hours_left,
        "refund_amount": refund_amount,
        "hours_until": round(hours_until, 1) if hours_until is not None else None,
        "preavis_h": LATE_CANCELLATION_HOURS,
    }


_PAYMENT_ACTIONS = frozenset({"cancel_client", "cancel_pilot", "complete", "refund"})


def _claim_payment_action(booking_id: int, action: str,
                          allowed_statuses: tuple[str, ...]) -> bool:
    """Reserve atomiquement une action Stripe pour un seul worker."""
    if action not in _PAYMENT_ACTIONS or not allowed_statuses:
        raise ValueError("action de paiement invalide")
    placeholders = ",".join("?" for _ in allowed_statuses)
    cur = db.execute(
        "UPDATE bookings SET payment_action=?, "
        "payment_action_started_at=datetime('now') "
        f"WHERE id=? AND status IN ({placeholders}) AND payment_action IS NULL",
        (action, booking_id, *allowed_statuses),
    )
    return cur.rowcount == 1


def _clear_payment_action(booking_id: int, action: str) -> None:
    db.execute(
        "UPDATE bookings SET payment_action=NULL, payment_action_started_at=NULL "
        "WHERE id=? AND payment_action=?",
        (booking_id, action),
    )


def cancel_booking_by_client(booking_id: int, by_user: int,
                             reason: str = "") -> dict:
    """Annule une reservation a la demande du client. Applique la regle
    de preavis (LATE_CANCELLATION_HOURS / LATE_CANCELLATION_FEE_PCT).

    Renvoie le dict de calcul (compute_cancellation_fee) augmente de
    {"ok": bool, "reason": str|None}.
    """
    booking = get_booking(booking_id)
    if not booking:
        return {"ok": False, "reason": "booking introuvable"}
    if booking["client_user_id"] != by_user:
        return {"ok": False, "reason": "seul le client peut annuler"}
    if booking["status"] not in ("pending_payment", "funded", "in_progress"):
        return {"ok": False,
                "reason": f"statut {booking['status']} non annulable"}
    action = "cancel_client"
    if not _claim_payment_action(
        booking_id, action, ("pending_payment", "funded", "in_progress"),
    ):
        return {"ok": False, "reason": "une opération financière est déjà en cours"}

    # Une ancienne page Checkout ne doit pas rester payable une fois la
    # reservation annulee. En cas de doute, on conserve la reservation.
    if booking["status"] == "pending_payment" and booking.get("stripe_session_id"):
        try:
            import payments
            expired = payments.expire_checkout_session(booking["stripe_session_id"])
        except Exception as exc:
            log.warning("expiration Checkout a echoue : %s", exc)
            expired = False
        if not expired:
            _clear_payment_action(booking_id, action)
            return {"ok": False, "reason": "paiement en cours de vérification; annulation non effectuée"}

    calc = compute_cancellation_fee(booking)
    paid = bool(booking.get("stripe_payment_intent_id")) and \
        booking["status"] in ("funded", "in_progress")
    if not paid:
        # Rien n'a ete encaisse : aucune retenue possible, aucun remboursement.
        calc.update({"fee_amount": 0.0, "service_fee_amount": 0.0,
                     "service_fee_pct": 0.0, "refund_amount": 0.0})

    # 1) Refund Stripe partiel au client. Le reste (dedommagement pilote +
    #    frais de service) demeure sur le solde plateforme.
    refund_done = True
    if paid and calc["refund_amount"] > 0:
        try:
            import payments
            refund_done = bool(payments.refund_payment(
                booking["stripe_payment_intent_id"],
                amount=calc["refund_amount"],
                currency=booking.get("currency", "EUR"),
                reason=f"booking:{booking_id}:cancel_client:late={calc['is_late']}",
            ))
        except Exception as exc:
            log.warning("refund Stripe a echoue : %s", exc)
            refund_done = False
        if not refund_done:
            _clear_payment_action(booking_id, action)
            return {"ok": False, "reason": "le remboursement a échoué; réservation inchangée"}

    # 2) Dedommagement pilote (annulation tardive) : Transfer immediat vers
    #    son compte Connect, cle d'idempotence distincte de la liberation.
    transfer_id = None
    if paid and calc["fee_amount"] > 0:
        pilot_acc = get_pilot_stripe_account(booking["pilot_user_id"])
        if pilot_acc:
            try:
                import payments
                transfer_id = payments.release_to_pilot(
                    booking_id=booking_id, pilot_amount=calc["fee_amount"],
                    currency=booking.get("currency", "EUR"),
                    pilot_account_id=pilot_acc, kind="cancel-compensation",
                )
            except Exception as exc:
                log.warning("transfer dedommagement a echoue : %s", exc)
        if not transfer_id:
            log.error("dedommagement pilote NON vire pour booking=%s (%.2f) : "
                      "a traiter manuellement", booking_id, calc["fee_amount"])
            # Le refund a pu etre accepte. Garder le verrou evite qu'un autre
            # worker execute une action contradictoire avant reconciliation.
            db.execute(
                "INSERT INTO audit_log (user_id, action, target, payload) "
                "VALUES (?, 'booking_reconciliation_required', ?, ?)",
                (by_user, f"booking:{booking_id}", json.dumps({
                    "payment_action": action,
                    "refund_amount": calc["refund_amount"],
                    "missing_transfer_amount": calc["fee_amount"],
                })),
            )
            return {"ok": False, "reason": "remboursement à vérifier; annulation bloquée pour contrôle"}

    with db.transaction():
        cur = db.execute(
            "UPDATE bookings SET status='cancelled', cancelled_by='client', "
            "cancelled_at=datetime('now'), cancellation_fee=?, "
            "cancellation_service_fee=?, "
            "stripe_transfer_id=COALESCE(?, stripe_transfer_id), "
            "payment_action=NULL, payment_action_started_at=NULL "
            "WHERE id=? AND status IN ('pending_payment', 'funded', 'in_progress') "
            "AND payment_action=?",
            (calc["fee_amount"], calc["service_fee_amount"], transfer_id,
             booking_id, action),
            commit=False,
        )
        if cur.rowcount == 0:
            raise ValueError("reservation modifiee pendant l'annulation")
        update_mission_status(booking["mission_id"], "cancelled", commit=False)

    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'booking_cancel_client', ?, ?)",
        (by_user, f"booking:{booking_id}",
         json.dumps({
             "reason": (reason or "")[:200],
             "paid": paid,
             "is_late": calc["is_late"],
             "fee_pct": calc["fee_pct"],
             "fee_amount": calc["fee_amount"],
             "service_fee_pct": calc["service_fee_pct"],
             "service_fee_amount": calc["service_fee_amount"],
             "within_grace": calc["within_grace"],
             "refund_amount": calc["refund_amount"],
             "hours_until": calc["hours_until"],
             "stripe_refund_done": refund_done,
             "compensation_transfer": transfer_id,
         })),
    )
    return {"ok": True, "reason": None, "paid": paid, **calc,
            "stripe_refund_done": refund_done,
            "compensation_transfer": transfer_id}


def cancel_booking_by_pilot(booking_id: int, by_user: int,
                            reason: str = "") -> dict:
    """Le PILOTE se desiste. Ne coute jamais rien au client : remboursement
    integral s'il a paye, devis retire, mission remise en ligne (le client
    peut choisir un autre devis). Trace en audit (desistements repetes ->
    suspension manuelle)."""
    booking = get_booking(booking_id)
    if not booking:
        return {"ok": False, "reason": "booking introuvable"}
    if booking["pilot_user_id"] != by_user:
        return {"ok": False, "reason": "seul le pilote peut se desister"}
    if booking["status"] not in ("pending_payment", "funded", "in_progress"):
        return {"ok": False,
                "reason": f"statut {booking['status']} non annulable"}
    action = "cancel_pilot"
    if not _claim_payment_action(
        booking_id, action, ("pending_payment", "funded", "in_progress"),
    ):
        return {"ok": False, "reason": "une opération financière est déjà en cours"}

    if booking["status"] == "pending_payment" and booking.get("stripe_session_id"):
        try:
            import payments
            expired = payments.expire_checkout_session(booking["stripe_session_id"])
        except Exception as exc:
            log.warning("expiration Checkout a echoue : %s", exc)
            expired = False
        if not expired:
            _clear_payment_action(booking_id, action)
            return {"ok": False, "reason": "paiement en cours de vérification; désistement non effectué"}

    price = float(booking.get("agreed_price") or 0)
    paid = bool(booking.get("stripe_payment_intent_id")) and \
        booking["status"] in ("funded", "in_progress")
    refund_done = True
    if paid and price > 0:
        try:
            import payments
            refund_done = bool(payments.refund_payment(
                booking["stripe_payment_intent_id"], amount=None,
                currency=booking.get("currency", "EUR"),
                reason=f"booking:{booking_id}:cancel_pilot",
            ))
        except Exception as exc:
            log.warning("refund Stripe (desistement pilote) a echoue : %s", exc)
            refund_done = False
        if not refund_done:
            _clear_payment_action(booking_id, action)
            return {"ok": False, "reason": "le remboursement a échoué; réservation inchangée"}

    with db.transaction():
        cur = db.execute(
            "UPDATE bookings SET status='cancelled', cancelled_by='pilot', "
            "cancelled_at=datetime('now'), cancellation_fee=0, "
            "cancellation_service_fee=0, payment_action=NULL, "
            "payment_action_started_at=NULL "
            "WHERE id=? AND status IN ('pending_payment', 'funded', 'in_progress') "
            "AND payment_action=?",
            (booking_id, action), commit=False,
        )
        if cur.rowcount == 0:
            raise ValueError("reservation deja cloturee")
        db.execute(
            "UPDATE bids SET status='withdrawn', updated_at=datetime('now') WHERE id=?",
            (booking["bid_id"],), commit=False,
        )
        # La mission redevient ouverte : nouveaux devis possibles, et les
        # pilotes refuses peuvent resoumettre (revision).
        update_mission_status(booking["mission_id"], "open", commit=False)

    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'booking_cancel_pilot', ?, ?)",
        (by_user, f"booking:{booking_id}",
         json.dumps({"reason": (reason or "")[:200], "paid": paid,
                     "refund_amount": price if paid else 0.0,
                     "stripe_refund_done": refund_done})),
    )
    try:
        import mailer
        client = db.fetchone("SELECT id, email, full_name FROM users WHERE id=?",
                             (booking["client_user_id"],))
        pilot = db.fetchone("SELECT id, full_name FROM users WHERE id=?",
                            (booking["pilot_user_id"],))
        if client and pilot:
            mailer.send_booking_cancelled_by_pilot(
                client=dict(client), pilot=dict(pilot), booking=booking,
                refund_amount=price if paid else 0.0, reason=reason or "",
            )
    except Exception as exc:
        log.warning("email booking_cancelled_by_pilot failed : %s", exc)
    return {"ok": True, "reason": None, "paid": paid,
            "refund_amount": price if paid else 0.0,
            "stripe_refund_done": refund_done}


def list_bookings_for(user_id: int) -> list:
    rows = db.fetchall(
        "SELECT b.*, m.title AS mission_title, m.mission_type, "
        "       cu.full_name AS client_name, pu.full_name AS pilot_name "
        "FROM bookings b "
        "JOIN missions m ON m.id=b.mission_id "
        "JOIN users cu ON cu.id=b.client_user_id "
        "JOIN users pu ON pu.id=b.pilot_user_id "
        "WHERE b.client_user_id=? OR b.pilot_user_id=? "
        "ORDER BY b.created_at DESC",
        (user_id, user_id),
    )
    return [dict(r) for r in rows]


def update_booking_status(booking_id: int, status: str, by_user: int):
    """Machine a etats STRICTE et role-based.

    La SEULE transition autorisee par cette voie est : le pilote signale le
    debut de l'operation (funded -> in_progress). Toutes les autres
    transitions sensibles passent par des fonctions dediees money-safe :
      - funded     : webhook Stripe (mark_booking_funded)
      - completed  : confirm_completion (declenche le Transfer au pilote)
      - cancelled  : cancel_booking_by_client (regle de preavis + refund)
      - refunded   : refund_booking (admin)
      - disputed   : open_dispute
    Empeche un client ou un pilote de forcer un statut et de contourner
    l'escrow (ex: passer 'completed' sans Transfer, ou 'funded' sans payer).
    """
    if status not in BOOKING_STATUS:
        raise ValueError(f"statut booking invalide: {status}")
    booking = get_booking(booking_id)
    if not booking or by_user not in (booking["client_user_id"], booking["pilot_user_id"]):
        raise ValueError("reservation introuvable")
    if status != "in_progress":
        raise ValueError("transition non autorisee par cette voie")
    if by_user != booking["pilot_user_id"]:
        raise ValueError("seul le pilote peut demarrer la mission")
    with db.transaction():
        cur = db.execute(
            "UPDATE bookings SET status='in_progress' "
            "WHERE id=? AND status='funded' AND payment_action IS NULL",
            (booking_id,), commit=False,
        )
        if cur.rowcount == 0:
            raise ValueError("la mission doit etre financee pour pouvoir demarrer")
        update_mission_status(booking["mission_id"], "in_progress", commit=False)


# ---------------------------------------------------------------------------
# Avis & rating
# ---------------------------------------------------------------------------

def add_review(*, booking_id: int, author_user_id: int, target_user_id: int,
               rating: int, comment: str = ""):
    booking = get_booking(booking_id)
    if not booking or booking["status"] != "completed":
        raise ValueError("Un avis ne peut être publié qu'après la mission terminée.")
    parties = {booking["client_user_id"], booking["pilot_user_id"]}
    if {author_user_id, target_user_id} != parties or author_user_id == target_user_id:
        raise ValueError("Participants de l'avis invalides.")
    rating = max(1, min(5, int(rating)))
    db.execute(
        "INSERT INTO reviews (booking_id, author_user_id, target_user_id, rating, comment) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(booking_id, author_user_id) DO UPDATE SET "
        "  rating=excluded.rating, comment=excluded.comment",
        (booking_id, author_user_id, target_user_id, rating, comment),
    )


def pilot_rating(user_id: int) -> dict:
    row = db.fetchone(
        "SELECT AVG(rating) AS avg, COUNT(*) AS n "
        "FROM reviews WHERE target_user_id=?",
        (user_id,),
    )
    avg = float(row["avg"]) if row and row["avg"] is not None else 0.0
    n = int(row["n"]) if row else 0
    return {"avg": round(avg, 2), "count": n}


def reviews_for(user_id: int, limit: int = 20) -> list:
    rows = db.fetchall(
        "SELECT r.*, u.full_name AS author_name "
        "FROM reviews r JOIN users u ON u.id=r.author_user_id "
        "WHERE r.target_user_id=? ORDER BY r.created_at DESC LIMIT ?",
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def reviewable_booking_for(client_user_id: int, pilot_user_id: int) -> Optional[dict]:
    """Retourne une reservation reelle (escrow finance) entre ce client et ce
    pilote, sur laquelle le client peut deposer/modifier un avis — sinon None.

    Le seul fait de pouvoir commenter prouve qu'on a deja travaille avec le
    pilote. On privilegie une reservation pas encore notee ; si toutes le sont
    deja, on renvoie la plus recente avec l'avis existant (rating/comment) pour
    permettre la modification.
    """
    if not client_user_id or not pilot_user_id or client_user_id == pilot_user_id:
        return None
    row = db.fetchone(
        "SELECT b.id, b.status, r.rating AS my_rating, r.comment AS my_comment "
        "FROM bookings b "
        "LEFT JOIN reviews r ON r.booking_id=b.id AND r.author_user_id=? "
        "WHERE b.pilot_user_id=? AND b.client_user_id=? "
        "  AND b.status='completed' "
        "ORDER BY (r.id IS NOT NULL), b.created_at DESC LIMIT 1",
        (client_user_id, pilot_user_id, client_user_id),
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Messagerie
# ---------------------------------------------------------------------------

def send_message(*, mission_id: int, sender_user_id: int,
                 recipient_user_id: int, body: str):
    body = (body or "").strip()
    if not body:
        return
    db.execute(
        "INSERT INTO messages (mission_id, sender_user_id, recipient_user_id, body) "
        "VALUES (?, ?, ?, ?)",
        (mission_id, sender_user_id, recipient_user_id, body),
    )
    # Notification email — throttle simple : pas plus d'un par destinataire
    # toutes les 5 minutes pour la meme mission, sinon on spamme.
    try:
        recent = db.fetchone(
            "SELECT 1 FROM messages WHERE mission_id=? AND recipient_user_id=? "
            "AND read_at IS NULL "
            "AND datetime(created_at) > datetime('now', '-5 minutes') "
            "AND id <> last_insert_rowid()",
            (mission_id, recipient_user_id),
        )
        if recent:
            return  # un message lui a deja ete envoye recemment, on n'en notifie pas un autre
        import mailer
        sender = db.fetchone(
            "SELECT id, full_name FROM users WHERE id=?", (sender_user_id,),
        )
        recipient = db.fetchone(
            "SELECT id, email, full_name FROM users WHERE id=?",
            (recipient_user_id,),
        )
        mission = db.fetchone(
            "SELECT id, title FROM missions WHERE id=?", (mission_id,),
        )
        if sender and recipient and mission:
            mailer.send_new_message(
                recipient=dict(recipient), sender=dict(sender),
                mission=dict(mission), body=body,
            )
    except Exception as exc:
        log.warning("email hook failed: %s", exc)


def can_message(mission_id: int, sender_user_id: int, peer_id: int) -> bool:
    """Autorise la messagerie uniquement entre le client d'une mission et un
    pilote ayant depose un devis sur CETTE mission (peu importe le sens).
    Empeche un tiers d'ecrire sur une mission qui ne le concerne pas, ou un
    pilote d'ouvrir un fil sans avoir soumissionne."""
    mission = db.fetchone(
        "SELECT client_user_id FROM missions WHERE id=?", (mission_id,)
    )
    if not mission:
        return False
    client_id = mission["client_user_id"]
    parties = {sender_user_id, peer_id}
    if client_id not in parties:
        return False
    others = parties - {client_id}
    if not others:
        return False  # client seul (lui-meme) : pas de fil
    pilot_id = others.pop()
    return bool(db.fetchone(
        "SELECT 1 FROM bids WHERE mission_id=? AND pilot_user_id=?",
        (mission_id, pilot_id),
    ))


def thread(mission_id: int, user_id: int, peer_id: int) -> list:
    rows = db.fetchall(
        "SELECT * FROM messages WHERE mission_id=? "
        "AND ((sender_user_id=? AND recipient_user_id=?) OR "
        "     (sender_user_id=? AND recipient_user_id=?)) "
        "ORDER BY created_at ASC",
        (mission_id, user_id, peer_id, peer_id, user_id),
    )
    db.execute(
        "UPDATE messages SET read_at=datetime('now') "
        "WHERE mission_id=? AND recipient_user_id=? AND read_at IS NULL",
        (mission_id, user_id),
    )
    return [dict(r) for r in rows]


def unread_count(user_id: int) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM messages WHERE recipient_user_id=? AND read_at IS NULL",
        (user_id,),
    )
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# Forfaits pilote (catalogue de packages)
# ---------------------------------------------------------------------------

def list_pilot_packages(pilot_user_id: int, only_active: bool = False) -> list:
    q = ["SELECT * FROM pilot_packages WHERE pilot_user_id=?"]
    args: list = [pilot_user_id]
    if only_active:
        q.append("AND is_active=1")
    q.append("ORDER BY sort_order ASC, id ASC")
    rows = db.fetchall(" ".join(q), args)
    return [dict(r) for r in rows]


def get_pilot_package(package_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT p.*, u.full_name AS pilot_name, u.country AS pilot_country, "
        "       u.city AS pilot_city "
        "FROM pilot_packages p JOIN users u ON u.id=p.pilot_user_id "
        "WHERE p.id=?",
        (package_id,),
    )
    return dict(row) if row else None


def create_pilot_package(pilot_user_id: int, *, title: str, description: str,
                         price: float, currency: str = DEFAULT_CURRENCY,
                         mission_type: Optional[str] = None,
                         duration_hours: Optional[float] = None,
                         deliverables: str = "", capabilities: str = "",
                         is_active: bool = True) -> int:
    cur = db.execute(
        "INSERT INTO pilot_packages (pilot_user_id, title, description, "
        "  mission_type, price, currency, duration_hours, deliverables, "
        "  capabilities, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pilot_user_id, title.strip()[:160], description.strip()[:4000],
         mission_type or None, price, currency, duration_hours,
         deliverables.strip()[:2000], capabilities.strip()[:500],
         1 if is_active else 0),
    )
    return cur.lastrowid or 0


def update_pilot_package(package_id: int, pilot_user_id: int, **fields) -> bool:
    pkg = db.fetchone(
        "SELECT id FROM pilot_packages WHERE id=? AND pilot_user_id=?",
        (package_id, pilot_user_id),
    )
    if not pkg:
        return False
    allowed = {"title", "description", "mission_type", "price", "currency",
               "duration_hours", "deliverables", "capabilities", "is_active",
               "sort_order"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return False
    sets.append("updated_at=datetime('now')")
    args.append(package_id)
    db.execute(f"UPDATE pilot_packages SET {', '.join(sets)} WHERE id=?", args)
    return True


def delete_pilot_package(package_id: int, pilot_user_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM pilot_packages WHERE id=? AND pilot_user_id=?",
        (package_id, pilot_user_id),
    )
    return cur.rowcount > 0


def toggle_pilot_package(package_id: int, pilot_user_id: int) -> bool:
    cur = db.execute(
        "UPDATE pilot_packages SET is_active = 1 - is_active, "
        "  updated_at=datetime('now') WHERE id=? AND pilot_user_id=?",
        (package_id, pilot_user_id),
    )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Livrables booking
# ---------------------------------------------------------------------------

_DELIVERABLE_KIND_BY_EXT = {
    "jpg": "image", "jpeg": "image", "png": "image", "webp": "image",
    "heic": "image", "tif": "image", "tiff": "image",
    "raw": "image", "dng": "image", "cr2": "image", "cr3": "image",
    "nef": "image", "arw": "image", "rw2": "image", "orf": "image",
    "mp4": "video", "mov": "video", "mkv": "video", "avi": "video", "m4v": "video",
    "zip": "archive", "7z": "archive", "tar": "archive", "gz": "archive",
    "pdf": "doc", "txt": "doc", "csv": "doc",
    "las": "data", "laz": "data", "obj": "data", "ply": "data",
    "kml": "data", "kmz": "data", "geojson": "data",
}


def deliverable_kind_from_ext(ext: str) -> str:
    return _DELIVERABLE_KIND_BY_EXT.get((ext or "").lower().lstrip("."), "file")


def add_deliverable(*, booking_id: int, uploaded_by_user_id: int,
                    label: str, original_filename: str,
                    stored_filename: str, mime_type: Optional[str],
                    size_bytes: int, kind: str = "file") -> int:
    cur = db.execute(
        "INSERT INTO booking_deliverables "
        "(booking_id, uploaded_by_user_id, label, original_filename, "
        " stored_filename, mime_type, size_bytes, kind) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (booking_id, uploaded_by_user_id, label.strip()[:200] or None,
         original_filename[:255], stored_filename[:255],
         mime_type, int(size_bytes), kind),
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'upload_deliverable', ?, ?)",
        (uploaded_by_user_id, f"booking:{booking_id}",
         json.dumps({"filename": original_filename, "size": size_bytes})),
    )
    return cur.lastrowid or 0


def list_deliverables(booking_id: int) -> list:
    rows = db.fetchall(
        "SELECT d.*, u.full_name AS uploader_name "
        "FROM booking_deliverables d "
        "LEFT JOIN users u ON u.id=d.uploaded_by_user_id "
        "WHERE d.booking_id=? ORDER BY d.created_at ASC",
        (booking_id,),
    )
    return [dict(r) for r in rows]


def get_deliverable(deliverable_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT * FROM booking_deliverables WHERE id=?",
        (deliverable_id,),
    )
    return dict(row) if row else None


def delete_deliverable(deliverable_id: int, user_id: int) -> Optional[dict]:
    """Suppression autorisee uniquement par l'uploader (pilote).
    Retourne le dict du livrable supprime pour que la route puisse
    enlever le fichier disque."""
    d = db.fetchone(
        "SELECT * FROM booking_deliverables WHERE id=? AND uploaded_by_user_id=?",
        (deliverable_id, user_id),
    )
    if not d:
        return None
    db.execute("DELETE FROM booking_deliverables WHERE id=?", (deliverable_id,))
    return dict(d)


# ---------------------------------------------------------------------------
# Conformite linguistique des contrats (Loi 101 / Loi 96 du Quebec)
# ---------------------------------------------------------------------------

# Villes quebecoises principales (suffit pour le matching grossier ;
# si l'utilisateur saisit une ville hors-liste, on retombe sur la
# detection par region/code).
_QUEBEC_CITIES = {
    "montreal", "montréal", "quebec", "québec", "laval", "gatineau",
    "longueuil", "sherbrooke", "saguenay", "levis", "lévis",
    "trois-rivieres", "trois-rivières", "terrebonne", "brossard",
    "saint-jean-sur-richelieu", "repentigny", "drummondville",
    "saint-jerome", "saint-jérôme", "granby", "blainville",
    "saint-hyacinthe", "shawinigan", "rimouski", "chateauguay",
    "châteauguay", "joliette", "rouyn-noranda", "victoriaville",
    "salaberry-de-valleyfield", "sept-iles", "sept-îles",
    "alma", "boucherville", "saint-eustache", "mascouche",
    "mirabel", "dollard-des-ormeaux", "pointe-claire", "kirkland",
    "westmount", "outremont", "verdun", "lasalle", "anjou",
    "saint-leonard", "saint-léonard", "ahuntsic", "rosemont",
    "plateau-mont-royal", "ville-marie", "cote-saint-luc",
    "côte-saint-luc", "hampstead", "mount royal", "mont-royal",
}

_CANADA_CODES = {"ca", "canada"}
_QC_REGION_CODES = {"qc", "quebec", "québec", "province de quebec",
                    "province de québec"}


def is_party_in_quebec(country: Optional[str], city: Optional[str] = None,
                       region: Optional[str] = None) -> bool:
    """True si la partie reside au Quebec (Charte de la langue francaise).

    Heuristique :
      - region/province en {QC, Quebec, Québec} = oui (le plus fiable)
      - country=Canada + city dans la liste des villes quebecoises = oui
      - autres cas = non
    Ne fait jamais de geoloc IP, on travaille sur ce qui est saisi.
    """
    c = (country or "").strip().lower()
    r = (region or "").strip().lower()
    v = (city or "").strip().lower()
    if r in _QC_REGION_CODES:
        return True
    if c in _CANADA_CODES and v in _QUEBEC_CITIES:
        return True
    return False


def contract_french_only(parties: list) -> bool:
    """True si AU MOINS une partie est au Quebec, donc le contrat
    doit etre en francais (Loi 101 + Loi 96).

    `parties` est une liste de dicts user-like avec country/city/region.
    """
    for p in parties or []:
        if not p:
            continue
        if is_party_in_quebec(p.get("country"), p.get("city"),
                              p.get("region")):
            return True
    return False


# ---------------------------------------------------------------------------
# Avatar pilote
# ---------------------------------------------------------------------------

def set_user_avatar(user_id: int, relative_path: str) -> None:
    """relative_path = chemin relatif au repertoire data/ (ex.
    'uploads/avatar_42.jpg'). Stocke dans users.avatar_path."""
    db.execute(
        "UPDATE users SET avatar_path=? WHERE id=?",
        (relative_path, user_id),
    )


def clear_user_avatar(user_id: int) -> Optional[str]:
    """Vide users.avatar_path et retourne l'ancien chemin (pour
    suppression sur disque)."""
    row = db.fetchone("SELECT avatar_path FROM users WHERE id=?", (user_id,))
    if not row or not row["avatar_path"]:
        return None
    old = row["avatar_path"]
    db.execute("UPDATE users SET avatar_path=NULL WHERE id=?", (user_id,))
    return old


# ---------------------------------------------------------------------------
# Portfolio pilote (showreel)
# ---------------------------------------------------------------------------

_PORTFOLIO_VIDEO_EXT = {"mp4", "mov", "webm", "m4v"}


def portfolio_kind_from_ext(ext: str) -> str:
    return "video" if (ext or "").lower().lstrip(".") in _PORTFOLIO_VIDEO_EXT else "image"


def list_portfolio_items(pilot_user_id: int) -> list:
    rows = db.fetchall(
        "SELECT * FROM pilot_portfolio_items WHERE pilot_user_id=? "
        "ORDER BY sort_order ASC, id DESC",
        (pilot_user_id,),
    )
    return [dict(r) for r in rows]


def portfolio_cover(pilot_user_id: int) -> Optional[str]:
    """Chemin media de la premiere PHOTO du portfolio, ou None.

    Sert d'image d'apercu (og:image) quand le pilote n'a pas d'avatar : une
    vraie image aerienne partagee sur LinkedIn vaut mieux que notre logo. On
    ignore les videos, qu'aucun reseau ne sait afficher en apercu."""
    row = db.fetchone(
        "SELECT stored_filename FROM pilot_portfolio_items "
        "WHERE pilot_user_id=? AND kind='image' AND stored_filename <> '' "
        "ORDER BY sort_order ASC, id DESC LIMIT 1",
        (pilot_user_id,),
    )
    return row["stored_filename"] if row else None


def count_portfolio_items(pilot_user_id: int,
                          kind: Optional[str] = None) -> int:
    """Nombre de pieces du portfolio, filtrable par kind ('video' / 'image')."""
    if kind:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM pilot_portfolio_items "
            "WHERE pilot_user_id=? AND kind=?",
            (pilot_user_id, kind),
        )
    else:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM pilot_portfolio_items WHERE pilot_user_id=?",
            (pilot_user_id,),
        )
    return int(row["n"]) if row else 0


def get_portfolio_item(item_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT * FROM pilot_portfolio_items WHERE id=?", (item_id,),
    )
    return dict(row) if row else None


def add_portfolio_item(*, pilot_user_id: int, title: str, description: str,
                       kind: str, original_filename: str,
                       stored_filename: str, mime_type: Optional[str],
                       size_bytes: int,
                       thumb_filename: Optional[str] = None) -> int:
    cur = db.execute(
        "INSERT INTO pilot_portfolio_items "
        "(pilot_user_id, title, description, kind, original_filename, "
        " stored_filename, mime_type, size_bytes, thumb_filename) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pilot_user_id, title.strip()[:200] or None,
         description.strip()[:2000] or None,
         kind, original_filename[:255], stored_filename[:255],
         mime_type, int(size_bytes), thumb_filename),
    )
    return cur.lastrowid or 0


def update_portfolio_item(item_id: int, pilot_user_id: int,
                          title: str, description: str) -> bool:
    cur = db.execute(
        "UPDATE pilot_portfolio_items SET title=?, description=? "
        "WHERE id=? AND pilot_user_id=?",
        (title.strip()[:200] or None, description.strip()[:2000] or None,
         item_id, pilot_user_id),
    )
    return cur.rowcount > 0


def delete_portfolio_item(item_id: int, pilot_user_id: int) -> Optional[dict]:
    item = db.fetchone(
        "SELECT * FROM pilot_portfolio_items WHERE id=? AND pilot_user_id=?",
        (item_id, pilot_user_id),
    )
    if not item:
        return None
    db.execute("DELETE FROM pilot_portfolio_items WHERE id=?", (item_id,))
    return dict(item)


def mark_deliverable_pushed(deliverable_id: int, service: str,
                            url: Optional[str]) -> None:
    if service == "aubedrive":
        db.execute(
            "UPDATE booking_deliverables SET aubedrive_url=?, "
            "  aubedrive_sent_at=datetime('now') WHERE id=?",
            (url, deliverable_id),
        )
    elif service == "aubephotos":
        db.execute(
            "UPDATE booking_deliverables SET aubephotos_url=?, "
            "  aubephotos_sent_at=datetime('now') WHERE id=?",
            (url, deliverable_id),
        )


# ---------------------------------------------------------------------------
# Stats / homepage
# ---------------------------------------------------------------------------

def public_stats() -> dict:
    pilots = db.fetchone(
        "SELECT COUNT(*) AS n FROM users WHERE role IN ('pilot', 'both')"
    )["n"]
    missions = db.fetchone("SELECT COUNT(*) AS n FROM missions WHERE status='open'")["n"]
    countries = db.fetchone(
        "SELECT COUNT(DISTINCT country) AS n FROM users WHERE country IS NOT NULL AND country<>''"
    )["n"]
    completed = db.fetchone(
        "SELECT COUNT(*) AS n FROM bookings WHERE status='completed'"
    )["n"]
    return {
        "pilots": pilots,
        "open_missions": missions,
        "countries": countries,
        "completed_bookings": completed,
    }


def featured_pilots(limit: int = 6) -> list:
    """Pilotes vedettes, avec rating folde dans la requete principale (1 query)."""
    rows = db.fetchall(
        "SELECT u.id, u.full_name, u.country, u.city, u.is_verified, u.avatar_path, "
        "       p.headline, p.hourly_rate, p.currency AS p_currency, "
        "       COALESCE(p.kind, 'pro') AS kind, p.business_name, "
        "       COALESCE(r.avg_rating, 0.0) AS rating_avg, "
        "       COALESCE(r.review_count, 0) AS rating_count "
        "FROM users u JOIN pilot_profiles p ON p.user_id=u.id "
        "LEFT JOIN ("
        "  SELECT target_user_id, AVG(rating) AS avg_rating, COUNT(*) AS review_count "
        "  FROM reviews GROUP BY target_user_id"
        ") r ON r.target_user_id = u.id "
        "WHERE u.role IN ('pilot','both') AND p.is_available=1 AND u.deleted_at IS NULL "
        "ORDER BY u.is_verified DESC, u.last_seen_at DESC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["rating"] = {
            "avg": round(float(d.pop("rating_avg") or 0.0), 2),
            "count": int(d.pop("rating_count") or 0),
        }
        out.append(d)
    return out


def latest_missions(limit: int = 8) -> list:
    return [
        dict(r) for r in db.fetchall(
            "SELECT id, title, mission_type, country, city, budget_min, budget_max, "
            "       currency, is_urgent, created_at "
            "FROM missions WHERE status='open' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    ]


def country_breakdown(limit: int = 12) -> list:
    """Compte par pays : nb pilotes + nb missions ouvertes. Trie par activité totale."""
    pilots_by = {
        r["country"]: r["n"]
        for r in db.fetchall(
            "SELECT country, COUNT(*) AS n FROM users "
            "WHERE country IS NOT NULL AND country<>'' "
            "AND role IN ('pilot','both') "
            "GROUP BY country"
        )
    }
    missions_by = {
        r["country"]: r["n"]
        for r in db.fetchall(
            "SELECT country, COUNT(*) AS n FROM missions "
            "WHERE country IS NOT NULL AND country<>'' "
            "AND status='open' "
            "GROUP BY country"
        )
    }
    countries = set(pilots_by) | set(missions_by)
    rows = [
        {
            "country": c,
            "pilots": pilots_by.get(c, 0),
            "missions": missions_by.get(c, 0),
            "total": pilots_by.get(c, 0) + missions_by.get(c, 0),
        }
        for c in countries
    ]
    rows.sort(key=lambda r: (-r["total"], -r["pilots"], r["country"]))
    return rows[:limit]


def near_geo(lat: float, lng: float, radius_km: int = 100, limit: int = 10) -> dict:
    """Recherche combinée pilotes + missions dans un rayon."""
    pilots = search_pilots(
        lat=lat, lng=lng, radius_km=radius_km, only_available=True,
        strict_radius=True, limit=limit,
    )
    missions = search_missions(
        lat=lat, lng=lng, radius_km=radius_km, status="open",
        strict_radius=True, limit=limit,
    )
    return {"pilots": pilots[:limit], "missions": missions[:limit]}


def _fuzz_coord(value: Optional[float], decimals: int) -> Optional[float]:
    """Floute une coordonnee en l'arrondissant a une grille grossiere.

    decimals=1 -> ~11 km (niveau quartier/ville) : protege l'adresse exacte
    du pilote tout en restant utile pour une carte mondiale.
    """
    if value is None:
        return None
    return round(float(value), decimals)


def map_markers(*, country: str = "", mission_type: str = "", kind: str = "",
                limit: int = 500) -> dict:
    """Marqueurs cartographiques pilotes + missions, coords floutees.

    Coordonnees pilote arrondies (~11 km) pour la confidentialite ; missions
    a ~1 km. Seuls les enregistrements geolocalises sont renvoyes.
    """
    pilots = search_pilots(
        country=country, mission_type=mission_type, kind=kind,
        only_available=True, limit=limit,
    )
    missions = search_missions(
        country=country, mission_type=mission_type,
        status="open", limit=limit,
    )
    p_out = []
    for p in pilots:
        if p.get("lat") is None or p.get("lng") is None:
            continue
        p_out.append({
            "id": p["id"],
            "lat": _fuzz_coord(p["lat"], 1),
            "lng": _fuzz_coord(p["lng"], 1),
            "country": p.get("country"),
            "rating": p.get("rating", {}),
            "verified": bool(p.get("is_verified")),
            "headline": (p.get("headline") or "")[:90],
            "kind": p.get("kind") or "pro",
            # Une ecole est une organisation : son nom n'est pas une donnee
            # personnelle, on l'affiche tel quel sur la carte.
            "name": public_name(p),
        })
    m_out = []
    for m in missions:
        if m.get("lat") is None or m.get("lng") is None:
            continue
        m_out.append({
            "id": m["id"],
            "lat": _fuzz_coord(m["lat"], 2),
            "lng": _fuzz_coord(m["lng"], 2),
            "title": (m.get("title") or "")[:90],
            "city": m.get("city"),
            "country": m.get("country"),
            "mission_type": m.get("mission_type"),
            "is_urgent": bool(m.get("is_urgent")),
            "budget_min": m.get("budget_min"),
            "budget_max": m.get("budget_max"),
            "currency": m.get("currency"),
        })
    return {"pilots": p_out, "missions": m_out}


# Validation des statuts pour les vues
ALL_MISSION_STATUS = MISSION_STATUS
ALL_BID_STATUS = BID_STATUS
ALL_BOOKING_STATUS = BOOKING_STATUS


# ===========================================================================
# Stripe / Paiement / Escrow
# ===========================================================================

_BANNED_RX = [re.compile(p, re.IGNORECASE) for p in MESSAGE_BANNED_PATTERNS]


def message_passes_filter(body: str, booking_funded: bool) -> tuple:
    """Si la mission n'est pas encore fundee, on bloque les coordonnees externes.

    Retourne (ok: bool, reason: str|None).
    """
    if booking_funded:
        return (True, None)
    for rx in _BANNED_RX:
        if rx.search(body or ""):
            return (False, "Coordonnees externes interdites avant paiement de la mission.")
    return (True, None)


def set_pilot_stripe_account(user_id: int, account_id: str,
                             charges_enabled: bool = False,
                             payouts_enabled: bool = False):
    db.execute(
        "UPDATE pilot_profiles SET stripe_account_id=?, "
        "stripe_charges_enabled=?, stripe_payouts_enabled=? WHERE user_id=?",
        (account_id, 1 if charges_enabled else 0,
         1 if payouts_enabled else 0, user_id),
    )


def update_pilot_stripe_status(user_id: int, charges_enabled: bool,
                               payouts_enabled: bool):
    db.execute(
        "UPDATE pilot_profiles SET stripe_charges_enabled=?, "
        "stripe_payouts_enabled=? WHERE user_id=?",
        (1 if charges_enabled else 0, 1 if payouts_enabled else 0, user_id),
    )


def get_pilot_stripe_account(user_id: int) -> Optional[str]:
    row = db.fetchone(
        "SELECT stripe_account_id FROM pilot_profiles WHERE user_id=?",
        (user_id,),
    )
    return row["stripe_account_id"] if row else None


def attach_payment_session(booking_id: int, session_id: str):
    db.execute(
        "UPDATE bookings SET stripe_session_id=? WHERE id=?",
        (session_id, booking_id),
    )


def mark_booking_funded(booking_id: int, payment_intent_id: Optional[str] = None) -> bool:
    """Le client a paye. Booking en escrow (`funded`).

    Idempotent : ne s'applique qu'une fois, depuis 'pending_payment'. Renvoie
    True si la transition a eu lieu, False si deja traite (rejeu de webhook
    Stripe ou double-clic). Bloque ainsi une double-capture / double-notif.
    """
    cur = db.execute(
        "UPDATE bookings SET status='funded', paid_at=datetime('now'), "
        "stripe_payment_intent_id=COALESCE(?, stripe_payment_intent_id) "
        "WHERE id=? AND status='pending_payment' AND payment_action IS NULL",
        (payment_intent_id, booking_id),
    )
    if cur.rowcount == 0:
        return False
    # Notifie le pilote que la mission est financée (une seule fois)
    booking = get_booking(booking_id)
    if booking:
        try:
            import mailer
            pilot = db.fetchone("SELECT id, email, full_name FROM users WHERE id=?",
                                (booking["pilot_user_id"],))
            client = db.fetchone("SELECT id, full_name FROM users WHERE id=?",
                                 (booking["client_user_id"],))
            if pilot and client:
                mailer.send(
                    to=pilot["email"],
                    subject="Mission financée — vous pouvez décoller",
                    template="booking_funded",
                    context={"pilot": dict(pilot), "client": dict(client),
                             "booking": booking},
                )
        except Exception:
            pass
    return True


def mark_booking_settled_offline(booking_id: int, by_user: int) -> bool:
    """Le CLIENT declare avoir regle le pilote en direct.

    Tant que Stripe Connect n'est pas ouvert, une reservation acceptee reste
    bloquee en 'pending_payment' pour toujours : pas de sequestre possible,
    donc pas de mission terminee, pas d'avis. Cette porte de sortie la fait
    avancer, en assumant ce qu'elle coute : **aucune commission** pour la
    plateforme, **aucune protection** pour le client. Rien ne transite par
    Stripe, donc `stripe_payment_intent_id` reste vide et les chemins
    d'annulation et de remboursement n'y toucheront pas.

    Idempotent : ne s'applique que depuis 'pending_payment'.
    """
    booking = get_booking(booking_id)
    if not booking or booking["client_user_id"] != by_user:
        return False
    cur = db.execute(
        "UPDATE bookings SET status='funded', settled_offline=1, "
        "settled_offline_at=datetime('now'), paid_at=datetime('now'), "
        "platform_fee=0, platform_fee_pct=0 "
        "WHERE id=? AND status='pending_payment' AND payment_action IS NULL",
        (booking_id,),
    )
    if cur.rowcount == 0:
        return False
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'booking_settled_offline', ?, ?)",
        (by_user, f"booking:{booking_id}",
         json.dumps({"amount": booking.get("agreed_price"),
                     "currency": booking.get("currency")})),
    )
    try:
        import mailer
        pilot = db.fetchone("SELECT id, email, full_name FROM users WHERE id=?",
                            (booking["pilot_user_id"],))
        if pilot and pilot["email"]:
            mailer.send_booking_settled_offline(pilot=dict(pilot),
                                                booking=get_booking(booking_id))
    except Exception as exc:
        log.warning("email settled_offline booking=%s : %s", booking_id, exc)
    return True


def confirm_completion(booking_id: int, by_user: int) -> bool:
    """Le client confirme la livraison. Declenche le Transfer Stripe au pilote."""
    booking = get_booking(booking_id)
    if not booking or booking["client_user_id"] != by_user:
        return False
    if booking["status"] not in ("funded", "in_progress"):
        return False
    action = "complete"
    if not _claim_payment_action(booking_id, action, ("funded", "in_progress")):
        return False

    # Regle en direct : il n'y a aucun fonds a transferer, la mission se
    # termine simplement. Sans cette branche, l'absence de compte Connect
    # bloquerait la reservation a jamais (et le cron d'auto-liberation
    # tournerait dans le vide).
    if booking.get("settled_offline"):
        with db.transaction():
            cur = db.execute(
                "UPDATE bookings SET status='completed', completed_at=datetime('now'), "
                "payment_action=NULL, payment_action_started_at=NULL "
                "WHERE id=? AND status IN ('funded', 'in_progress') AND payment_action=?",
                (booking_id, action), commit=False,
            )
            if cur.rowcount == 0:
                raise ValueError("reservation modifiee pendant la cloture")
            update_mission_status(booking["mission_id"], "done", commit=False)
        return True

    # Recupere l'account Stripe du pilote
    pilot_acc = get_pilot_stripe_account(booking["pilot_user_id"])
    if not pilot_acc:
        _clear_payment_action(booking_id, action)
        return False

    pilot_amount = booking["agreed_price"] - booking["platform_fee"]
    import payments
    transfer_id = payments.release_to_pilot(
        booking_id=booking["id"],
        pilot_amount=pilot_amount,
        currency=booking["currency"],
        pilot_account_id=pilot_acc,
    )
    # MONEY-SAFE : si le Transfer Stripe echoue (transfer_id None), on NE
    # marque PAS le booking 'completed'. Il reste 'funded'/'in_progress' et
    # sera rejoue (auto-release J+7 via stale_funded_bookings, ou nouvelle
    # validation). Sinon les fonds quitteraient l'escrow sans jamais atteindre
    # le pilote -> perte seche, pilote jamais paye.
    if not transfer_id:
        log.error(
            "release_to_pilot a echoue pour booking=%s : laisse '%s' pour rejeu",
            booking_id, booking["status"],
        )
        _clear_payment_action(booking_id, action)
        return False

    with db.transaction():
        cur = db.execute(
            "UPDATE bookings SET status='completed', completed_at=datetime('now'), "
            "released_at=datetime('now'), stripe_transfer_id=?, "
            "payment_action=NULL, payment_action_started_at=NULL "
            "WHERE id=? AND status IN ('funded', 'in_progress') AND payment_action=?",
            (transfer_id, booking_id, action), commit=False,
        )
        if cur.rowcount == 0:
            raise ValueError("reservation modifiee pendant le versement")
        update_mission_status(booking["mission_id"], "done", commit=False)

    # Email "vous avez ete paye" au pilote
    try:
        import mailer
        pilot = db.fetchone("SELECT id, email, full_name FROM users WHERE id=?",
                            (booking["pilot_user_id"],))
        client = db.fetchone("SELECT id, full_name FROM users WHERE id=?",
                             (booking["client_user_id"],))
        if pilot and client:
            mailer.send(
                to=pilot["email"],
                subject="Paiement libéré — votre mission est terminée",
                template="payout_done",
                context={"pilot": dict(pilot), "client": dict(client),
                         "booking": booking,
                         "amount_pilot": pilot_amount,
                         "amount_total": booking["agreed_price"],
                         "fee": booking["platform_fee"]},
            )
    except Exception as exc:
        log.warning("email hook failed: %s", exc)
    return True


def open_dispute(booking_id: int, by_user: int, reason: str = "") -> bool:
    booking = get_booking(booking_id)
    if not booking or by_user not in (booking["client_user_id"], booking["pilot_user_id"]):
        return False
    if booking["status"] not in ("funded", "in_progress"):
        return False
    cur = db.execute(
        "UPDATE bookings SET status='disputed', dispute_reason=? "
        "WHERE id=? AND status IN ('funded','in_progress') AND payment_action IS NULL",
        ((reason or "")[:1000], booking_id),
    )
    if cur.rowcount == 0:
        return False
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'dispute_open', ?, ?)",
        (by_user, f"booking:{booking_id}",
         json.dumps({"reason": (reason or "")[:200]})),
    )
    return True


def refund_booking(booking_id: int, amount: Optional[float] = None,
                   admin_user: Optional[int] = None) -> bool:
    """Remboursement integral admin, serialise et fail-closed.

    Les remboursements partiels sont refuses tant qu'un ledger de montants
    rembourses n'est pas disponible : marquer alors tout le booking `refunded`
    produirait un etat financier faux.
    """
    if amount is not None:
        return False
    booking = get_booking(booking_id)
    if not booking or booking["status"] not in ("funded", "disputed", "in_progress"):
        return False
    if not booking.get("stripe_payment_intent_id"):
        return False
    action = "refund"
    if not _claim_payment_action(booking_id, action, ("funded", "disputed", "in_progress")):
        return False
    import payments
    ok = payments.refund_payment(
        booking["stripe_payment_intent_id"],
        amount=amount,
        currency=booking["currency"],
        reason=f"booking:{booking_id}:admin:{admin_user}",
    )
    if ok:
        with db.transaction():
            cur = db.execute(
                "UPDATE bookings SET status='refunded', refunded_at=datetime('now'), "
                "payment_action=NULL, payment_action_started_at=NULL "
                "WHERE id=? AND status IN ('funded', 'disputed', 'in_progress') "
                "AND payment_action=?",
                (booking_id, action), commit=False,
            )
            if cur.rowcount == 0:
                raise ValueError("reservation modifiee pendant le remboursement")
            update_mission_status(booking["mission_id"], "cancelled", commit=False)
    else:
        _clear_payment_action(booking_id, action)
    return ok


def mark_booking_fully_refunded(payment_intent_id: str) -> bool:
    """Reconcile un remboursement integral recu par webhook Stripe.

    Ignore les remboursements declenches par une action locale encore en cours
    (elle finalisera son propre etat) et garde booking + mission atomiques.
    """
    booking = db.fetchone(
        "SELECT id, mission_id FROM bookings "
        "WHERE stripe_payment_intent_id=? "
        "AND status IN ('funded','in_progress','disputed') "
        "AND payment_action IS NULL LIMIT 1",
        (payment_intent_id,),
    )
    if not booking:
        return False
    with db.transaction():
        cur = db.execute(
            "UPDATE bookings SET status='refunded', refunded_at=datetime('now') "
            "WHERE id=? AND status IN ('funded','in_progress','disputed') "
            "AND payment_action IS NULL",
            (booking["id"],), commit=False,
        )
        if cur.rowcount == 0:
            return False
        update_mission_status(booking["mission_id"], "cancelled", commit=False)
    return True


def stale_funded_bookings(days: int) -> list:
    """Bookings `funded` ou `in_progress` non confirmes depuis N jours.
    Le client a paye mais n'a pas valide -> auto-release au pilote."""
    rows = db.fetchall(
        "SELECT id FROM bookings WHERE status IN ('funded','in_progress') "
        "AND paid_at IS NOT NULL "
        "AND datetime(paid_at) < datetime('now', '-' || ? || ' days')",
        (days,),
    )
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# Boite de reception /contact (back-office)
# ---------------------------------------------------------------------------

CONTACT_STATUSES = ("new", "replied", "archived")


def create_contact_message(*, name: str, email: str, topic: str, body: str,
                           user_id: Optional[int] = None, ip: str = "") -> int:
    """Enregistre un message du formulaire public. Toujours appele AVANT
    l'envoi de courriel : si le SMTP tombe, rien n'est perdu."""
    cur = db.execute(
        "INSERT INTO contact_messages (name, email, topic, body, user_id, ip) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name.strip()[:120], email.strip()[:200], topic.strip()[:80],
         body.strip()[:5000], user_id, (ip or "")[:64]),
    )
    return int(cur.lastrowid or 0)


def mark_contact_notified(msg_id: int) -> None:
    db.execute("UPDATE contact_messages SET notified_at=datetime('now') WHERE id=?",
               (msg_id,))


def get_contact_message(msg_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT c.*, u.username AS user_username, a.full_name AS replied_by_name "
        "FROM contact_messages c "
        "LEFT JOIN users u ON u.id = c.user_id "
        "LEFT JOIN users a ON a.id = c.replied_by "
        "WHERE c.id=?", (msg_id,),
    )
    return dict(row) if row else None


def list_contact_messages(status: str = "new", limit: int = 200) -> list:
    """`status` = new | replied | archived | all."""
    q = ("SELECT c.*, u.username AS user_username, a.full_name AS replied_by_name "
         "FROM contact_messages c "
         "LEFT JOIN users u ON u.id = c.user_id "
         "LEFT JOIN users a ON a.id = c.replied_by ")
    args: list = []
    if status and status != "all":
        q += "WHERE c.status=? "
        args.append(status)
    q += "ORDER BY c.created_at DESC, c.id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in db.fetchall(q, args)]


def count_contact_messages(status: str = "new") -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM contact_messages WHERE status=?",
                      (status,))
    return int(row["n"]) if row else 0


def set_contact_status(msg_id: int, status: str) -> bool:
    if status not in CONTACT_STATUSES:
        raise ValueError(f"statut contact invalide: {status}")
    cur = db.execute("UPDATE contact_messages SET status=? WHERE id=?", (status, msg_id))
    return cur.rowcount > 0


def reply_contact_message(msg_id: int, admin_user_id: int, reply_body: str) -> dict:
    """Repond a un message : enregistre la reponse, tente l'envoi du courriel
    (synchrone, pour savoir s'il est parti) et passe le message en 'replied'.
    Retourne {"ok", "sent", "reason"} — `sent=False` si le SMTP a echoue :
    la reponse est conservee, l'admin peut l'envoyer a la main (mailto)."""
    msg = get_contact_message(msg_id)
    if not msg:
        return {"ok": False, "sent": False, "reason": "message introuvable"}
    reply_body = (reply_body or "").strip()
    if len(reply_body) < 2:
        return {"ok": False, "sent": False, "reason": "reponse vide"}
    sent = False
    try:
        import mailer
        sent = bool(mailer.send_contact_reply(
            to=msg["email"], name=msg["name"], topic=msg["topic"],
            original_body=msg["body"], reply_body=reply_body,
            original_date=str(msg.get("created_at") or "")[:16],
        ))
    except Exception as exc:
        log.warning("reponse contact #%s : envoi echoue : %s", msg_id, exc)
        sent = False
    db.execute(
        "UPDATE contact_messages SET status='replied', replied_at=datetime('now'), "
        "replied_by=?, reply_body=?, reply_sent=? WHERE id=?",
        (admin_user_id, reply_body[:5000], 1 if sent else 0, msg_id),
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'contact_reply', ?, ?)",
        (admin_user_id, f"contact:{msg_id}", json.dumps({"sent": sent})),
    )
    return {"ok": True, "sent": sent, "reason": None}


# ---------------------------------------------------------------------------
# Parametres du compte (/espace/parametres)
# ---------------------------------------------------------------------------

NOTIFY_KEYS = ("notify_bids", "notify_messages", "notify_alerts", "notify_news")
ACTIVE_BOOKING_STATUSES = ("pending_payment", "funded", "in_progress", "disputed")


def update_account(user_id: int, *, full_name: Optional[str] = None, phone: Optional[str] = None,
                   country: Optional[str] = None, city: Optional[str] = None,
                   lat: Optional[float] = None, lng: Optional[float] = None,
                   lang: Optional[str] = None) -> dict:
    """Identite + base + langue. Le nom n'est modifiable que tant qu'aucun
    justificatif n'a ete televerse (sinon : demande de changement de nom).
    Retourne {"name_locked": bool}."""
    locked = is_identity_locked(user_id)
    fields = {
        "phone": (phone or "").strip()[:40] or None,
        "country": (country or "").strip()[:80] or None,
        "city": (city or "").strip()[:120] or None,
        "lat": lat, "lng": lng,
        "lang": lang if lang in ("fr", "en") else None,
    }
    if full_name is not None and not locked and len(full_name.strip()) >= 2:
        fields["full_name"] = full_name.strip()[:120]
    sets = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), user_id))
    return {"name_locked": locked}


def update_notification_prefs(user_id: int, prefs: dict) -> None:
    sets = ", ".join(f"{k}=?" for k in NOTIFY_KEYS)
    db.execute(f"UPDATE users SET {sets} WHERE id=?",
               (*[1 if prefs.get(k) else 0 for k in NOTIFY_KEYS], user_id))


def wants_notification(user_id: Optional[int], key: str) -> bool:
    """Prefs courriel : par defaut True (anciens comptes / cle inconnue)."""
    if not user_id or key not in NOTIFY_KEYS:
        return True
    row = db.fetchone(f"SELECT {key} AS v, deleted_at FROM users WHERE id=?", (user_id,))
    if not row or row["deleted_at"]:
        return False
    return bool(row["v"]) if row["v"] is not None else True


def list_sessions(user_id: int, current_sid: Optional[str] = None) -> list:
    rows = db.fetchall(
        "SELECT sid, created_at, expires_at, user_agent, ip FROM sessions "
        "WHERE user_id=? AND expires_at > datetime('now') ORDER BY created_at DESC",
        (user_id,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["is_current"] = bool(current_sid) and d["sid"] == current_sid
        d.pop("sid")
        out.append(d)
    return out


def export_user_data(user_id: int) -> dict:
    """Portabilite (Loi 25 / RGPD) : tout ce que la plateforme detient sur le
    compte, en JSON. Les justificatifs (fichiers) ne sont pas inclus, seules
    leurs metadonnees ; les mots de passe ne sont jamais exportes."""
    def rows(sql, args=()):
        return [dict(r) for r in db.fetchall(sql, args)]
    user = db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))
    if not user:
        return {}
    u = dict(user)
    return {
        "exported_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "account": {k: u.get(k) for k in (
            "id", "username", "email", "full_name", "phone", "country", "city", "lat", "lng",
            "role", "bio", "is_verified", "lang", "notify_bids", "notify_messages",
            "notify_alerts", "notify_news", "created_at", "last_seen_at")},
        "pilot_profile": (dict(r) if (r := db.fetchone(
            "SELECT * FROM pilot_profiles WHERE user_id=?", (user_id,))) else None),
        "specialties": [r["mission_type"] for r in rows(
            "SELECT mission_type FROM pilot_specialties WHERE pilot_user_id=?", (user_id,))],
        "territories": rows("SELECT country, region FROM pilot_territories WHERE pilot_user_id=?", (user_id,)),
        "certifications": rows(
            "SELECT id, authority, title, reference, issued_at, expires_at, is_verified, "
            "review_status, review_note, created_at FROM pilot_certifications WHERE pilot_user_id=?",
            (user_id,)),
        "drones": rows("SELECT * FROM pilot_drones WHERE pilot_user_id=?", (user_id,)),
        "packages": rows("SELECT * FROM pilot_packages WHERE pilot_user_id=?", (user_id,)),
        "portfolio": rows("SELECT id, title, description, kind, original_filename, size_bytes, "
                          "created_at FROM pilot_portfolio_items WHERE pilot_user_id=?", (user_id,)),
        "missions_published": rows("SELECT * FROM missions WHERE client_user_id=?", (user_id,)),
        "bids": rows("SELECT * FROM bids WHERE pilot_user_id=?", (user_id,)),
        "bookings": rows("SELECT * FROM bookings WHERE client_user_id=? OR pilot_user_id=?",
                         (user_id, user_id)),
        "reviews_written": rows("SELECT * FROM reviews WHERE author_user_id=?", (user_id,)),
        "reviews_received": rows("SELECT * FROM reviews WHERE target_user_id=?", (user_id,)),
        "messages": rows("SELECT id, mission_id, sender_user_id, recipient_user_id, body, "
                         "read_at, created_at FROM messages WHERE sender_user_id=? OR recipient_user_id=?",
                         (user_id, user_id)),
        "contact_messages": rows("SELECT id, topic, body, status, created_at FROM contact_messages "
                                 "WHERE user_id=?", (user_id,)),
        "sessions": rows("SELECT created_at, expires_at, user_agent, ip FROM sessions WHERE user_id=?",
                         (user_id,)),
    }


def account_deletion_blockers(user_id: int) -> list:
    """Raisons empechant la suppression immediate (argent ou mission en cours)."""
    out = []
    n = db.fetchone(
        "SELECT COUNT(*) AS n FROM bookings WHERE (client_user_id=? OR pilot_user_id=?) "
        "AND status IN ('pending_payment','funded','in_progress','disputed')",
        (user_id, user_id))["n"]
    if n:
        out.append(f"{n} réservation(s) en cours (paiement, mission ou litige non soldé)")
    m = db.fetchone(
        "SELECT COUNT(*) AS n FROM missions WHERE client_user_id=? AND status IN ('assigned','in_progress')",
        (user_id,))["n"]
    if m:
        out.append(f"{m} mission(s) attribuée(s) non terminée(s)")
    return out


def delete_account(user_id: int) -> dict:
    """Suppression (Loi 25 / RGPD) par ANONYMISATION : les missions, devis,
    reservations et avis restent pour l'autre partie et la comptabilite, mais
    plus aucune donnee personnelle ; connexion impossible ; retire de toutes
    les listes. Refuse s'il reste de l'argent ou une mission en cours."""
    blockers = account_deletion_blockers(user_id)
    if blockers:
        return {"ok": False, "blockers": blockers}
    user = db.fetchone("SELECT username, avatar_path FROM users WHERE id=?", (user_id,))
    if not user:
        return {"ok": False, "blockers": ["compte introuvable"]}
    with db.transaction():
        db.execute("UPDATE missions SET status='cancelled', updated_at=datetime('now') "
                   "WHERE client_user_id=? AND status='open'", (user_id,), commit=False)
        db.execute("UPDATE bids SET status='withdrawn' WHERE pilot_user_id=? AND status='pending'",
                   (user_id,), commit=False)
        for table in ("pilot_certifications", "pilot_drones", "pilot_specialties",
                      "pilot_territories", "pilot_packages", "pilot_portfolio_items"):
            db.execute(f"DELETE FROM {table} WHERE pilot_user_id=?", (user_id,), commit=False)
        db.execute("UPDATE pilot_profiles SET headline=NULL, business_name=NULL, insurance_company=NULL, "
                   "insurance_policy=NULL, portfolio_url=NULL, languages=NULL, is_available=0 "
                   "WHERE user_id=?", (user_id,), commit=False)
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,), commit=False)
        db.execute(
            "UPDATE users SET full_name='Compte supprimé', email=?, phone=NULL, bio=NULL, "
            "avatar_path=NULL, lat=NULL, lng=NULL, city=NULL, is_verified=0, is_admin=0, "
            "notify_bids=0, notify_messages=0, notify_alerts=0, notify_news=0, "
            "deleted_at=datetime('now') WHERE id=?",
            (f"deleted-{user_id}@invalid.local", user_id), commit=False,
        )
        db.execute("INSERT INTO audit_log (user_id, action, target, payload) "
                   "VALUES (?, 'account_deleted', ?, '{}')", (user_id, f"user:{user_id}"),
                   commit=False)
    try:
        import auth
        auth.remove_local_password(user["username"])
    except Exception as exc:
        log.warning("suppression mdp local de %s : %s", user["username"], exc)
    if user["avatar_path"]:
        try:
            from config import DATA_DIR
            os.remove(os.path.join(DATA_DIR, user["avatar_path"]))
        except OSError:
            pass
    return {"ok": True, "blockers": []}
