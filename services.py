"""Logique metier AubePilot : missions, pilotes, encheres, bookings.

Garde le code SQL ici pour ne pas alourdir app.py. Pas d'ORM, on prefere
voir les requetes a plat. Les fonctions retournent des dicts (sqlite3.Row
converti) pour rester serialisables JSON.
"""
import json
import logging
from typing import Iterable, Optional

log = logging.getLogger("aubepilot.services")

import db
from config import (
    BID_STATUS,
    BOOKING_STATUS,
    DEFAULT_CURRENCY,
    DEFAULT_SEARCH_RADIUS_KM,
    MAX_SEARCH_RADIUS_KM,
    MISSION_STATUS,
    PLATFORM_FEE_PCT,
)


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


def get_pilot_profile(user_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT u.*, p.headline, p.business_name, p.years_experience, p.hourly_rate, p.daily_rate, "
        "p.currency AS p_currency, p.travel_radius_km, p.accepts_remote, p.insurance, "
        "p.insurance_company, p.insurance_policy, p.is_available, p.languages, "
        "p.portfolio_url, p.accepts_urgent, p.updated_at AS pilot_updated_at "
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
    out["certifications"] = list_certifications(user_id)
    out["drones"] = list_drones(user_id)
    out["rating"] = pilot_rating(user_id)
    return out


def set_pilot_specialties(user_id: int, codes: Iterable[str]):
    db.execute("DELETE FROM pilot_specialties WHERE pilot_user_id=?", (user_id,))
    for code in {c for c in codes if c}:
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


def list_certifications(pilot_user_id: int) -> list:
    return [
        dict(r) for r in db.fetchall(
            "SELECT * FROM pilot_certifications WHERE pilot_user_id=? ORDER BY issued_at DESC",
            (pilot_user_id,),
        )
    ]


def delete_certification(cert_id: int, owner_user_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM pilot_certifications WHERE id=? AND pilot_user_id=?",
        (cert_id, owner_user_id),
    )
    return cur.rowcount > 0


def get_certification(cert_id: int) -> Optional[dict]:
    row = db.fetchone("SELECT * FROM pilot_certifications WHERE id=?", (cert_id,))
    return dict(row) if row else None


def list_pending_certifications() -> list:
    """Brevets uploades avec un PDF mais pas encore valides par un admin."""
    return [dict(r) for r in db.fetchall(
        "SELECT c.*, u.full_name AS pilot_full_name, u.username, "
        "       u.country AS pilot_country, u.city AS pilot_city "
        "FROM pilot_certifications c "
        "JOIN users u ON u.id = c.pilot_user_id "
        "WHERE c.is_verified=0 AND c.document_path IS NOT NULL "
        "  AND c.document_path <> '' "
        "ORDER BY c.created_at ASC"
    )]


def set_certification_verified(cert_id: int, verified: bool) -> bool:
    cur = db.execute(
        "UPDATE pilot_certifications SET is_verified=? WHERE id=?",
        (1 if verified else 0, cert_id),
    )
    return cur.rowcount > 0


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
                  strict_radius: bool = False, limit: int = 50) -> list:
    # PERF : on JOIN un agregat de reviews dans la requete principale au
    # lieu d'appeler pilot_rating() N fois en Python (avant : 1 + N requetes,
    # maintenant : 1 seule).
    _text = (text or "").strip().lower()   # recherche libre (search box / ?q=)
    q = [
        "SELECT u.id, u.username, u.full_name, u.country, u.city, u.lat, u.lng, "
        "       u.is_verified, u.avatar_path, u.bio, "
        "       p.headline, p.hourly_rate, p.daily_rate, p.currency AS p_currency, "
        "       p.travel_radius_km, p.is_available, p.insurance, p.languages, "
        "       COALESCE(r.avg_rating, 0.0) AS rating_avg, "
        "       COALESCE(r.review_count, 0) AS rating_count "
        "FROM users u "
        "JOIN pilot_profiles p ON p.user_id = u.id "
        "LEFT JOIN ("
        "  SELECT target_user_id, AVG(rating) AS avg_rating, "
        "         COUNT(*) AS review_count "
        "  FROM reviews GROUP BY target_user_id"
        ") r ON r.target_user_id = u.id "
        "WHERE u.role IN ('pilot', 'both')",
    ]
    args: list = []
    if only_available:
        q.append("AND p.is_available = 1")
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
    q.append("ORDER BY u.is_verified DESC, p.is_available DESC LIMIT ?")
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
        # rating est deja dans r["rating_avg"] / r["rating_count"] — on
        # construit l'objet attendu par les callers.
        r["rating"] = {
            "avg": round(float(r.pop("rating_avg") or 0.0), 2),
            "count": int(r.pop("rating_count") or 0),
        }
        enriched.append(r)
    if lat is not None and lng is not None:
        enriched.sort(key=lambda x: (x.get("distance_km") or 1e9))
    return enriched


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------

def create_mission(client_user_id: int, **f) -> int:
    cur = db.execute(
        "INSERT INTO missions "
        "(client_user_id, title, description, mission_type, country, region, city, "
        " lat, lng, address, budget_min, budget_max, currency, duration_hours, "
        " start_date, end_date, is_urgent, requires_insurance, "
        " requires_certifications, requires_capabilities) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            client_user_id,
            f["title"].strip(),
            f["description"].strip(),
            f["mission_type"],
            f.get("country") or "",
            f.get("region"),
            f.get("city"),
            f.get("lat"),
            f.get("lng"),
            f.get("address"),
            f.get("budget_min"),
            f.get("budget_max"),
            f.get("currency") or DEFAULT_CURRENCY,
            f.get("duration_hours"),
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
        "WHERE u.role IN ('pilot', 'both') ORDER BY u.id LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


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
    q.append("ORDER BY m.is_urgent DESC, m.created_at DESC LIMIT ?")
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
        out.append(r)
    return out


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
    fee = round(bid["price"] * PLATFORM_FEE_PCT / 100.0, 2)
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
            " platform_fee, scheduled_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment')",
            (
                mission_id, bid_id, client_user_id, bid["pilot_user_id"],
                bid["price"], bid["currency"], fee, mission["start_date"],
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
             json.dumps({"booking": booking_id, "bid": bid_id})), commit=False,
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
                         "currency": bid["currency"], "platform_fee": fee},
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


def compute_cancellation_fee(booking: dict) -> dict:
    """Calcule la penalite client en cas d annulation.

    Renvoie : {
      "is_late":   True si annulation < LATE_CANCELLATION_HOURS de la mission,
      "fee_pct":   % du prix verse au pilote (0 si preavis suffisant),
      "fee_amount":   montant verse au pilote,
      "refund_amount": montant rembourse au client,
      "hours_until":  heures restantes avant la mission (ou None),
      "preavis_h":    LATE_CANCELLATION_HOURS de reference,
    }
    """
    from datetime import datetime, timezone
    from config import LATE_CANCELLATION_HOURS, LATE_CANCELLATION_FEE_PCT

    price = float(booking.get("agreed_price") or 0)
    start = (booking.get("scheduled_at") or booking.get("mission_start_date") or "").strip()
    hours_until = None
    if start:
        # Parse "YYYY-MM-DD" ou "YYYY-MM-DD HH:MM:SS"
        try:
            if len(start) <= 10:
                dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                dt = datetime.fromisoformat(start.replace(" ", "T"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            hours_until = (dt - now).total_seconds() / 3600.0
        except (ValueError, TypeError):
            hours_until = None

    is_late = hours_until is not None and hours_until < LATE_CANCELLATION_HOURS
    fee_pct = LATE_CANCELLATION_FEE_PCT if is_late else 0.0
    fee_amount = round(price * fee_pct / 100.0, 2)
    refund_amount = round(price - fee_amount, 2)
    return {
        "is_late": is_late,
        "fee_pct": fee_pct,
        "fee_amount": fee_amount,
        "refund_amount": refund_amount,
        "hours_until": round(hours_until, 1) if hours_until is not None else None,
        "preavis_h": LATE_CANCELLATION_HOURS,
    }


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

    calc = compute_cancellation_fee(booking)

    # Refund Stripe (si paye en escrow). Le 25 % reste dans l'escrow et sera
    # transfere au pilote via un job ulterieur — pour l'MVP on track juste
    # le montant en DB, le transfer effectif est traite cote payments/cron.
    refund_done = True
    if (booking.get("stripe_payment_intent_id")
            and booking["status"] in ("funded", "in_progress")
            and calc["refund_amount"] > 0):
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

    with db.transaction():
        db.execute(
            "UPDATE bookings SET status='cancelled', "
            "cancelled_at=datetime('now'), cancellation_fee=? "
            "WHERE id=? AND status IN ('pending_payment', 'funded', 'in_progress')",
            (calc["fee_amount"], booking_id), commit=False,
        )
        update_mission_status(booking["mission_id"], "cancelled", commit=False)

    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'booking_cancel_client', ?, ?)",
        (by_user, f"booking:{booking_id}",
         json.dumps({
             "reason": (reason or "")[:200],
             "is_late": calc["is_late"],
             "fee_pct": calc["fee_pct"],
             "fee_amount": calc["fee_amount"],
             "refund_amount": calc["refund_amount"],
             "hours_until": calc["hours_until"],
             "stripe_refund_done": refund_done,
         })),
    )
    return {"ok": True, "reason": None, **calc,
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
            "WHERE id=? AND status='funded'",
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
        "  AND b.status IN ('funded','in_progress','completed','disputed') "
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
        "       COALESCE(r.avg_rating, 0.0) AS rating_avg, "
        "       COALESCE(r.review_count, 0) AS rating_count "
        "FROM users u JOIN pilot_profiles p ON p.user_id=u.id "
        "LEFT JOIN ("
        "  SELECT target_user_id, AVG(rating) AS avg_rating, COUNT(*) AS review_count "
        "  FROM reviews GROUP BY target_user_id"
        ") r ON r.target_user_id = u.id "
        "WHERE u.role IN ('pilot','both') AND p.is_available=1 "
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
        lat=lat, lng=lng, radius_km=radius_km, only_available=True, limit=limit,
    )
    missions = search_missions(
        lat=lat, lng=lng, radius_km=radius_km, status="open", limit=limit,
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


def map_markers(*, country: str = "", mission_type: str = "",
                limit: int = 500) -> dict:
    """Marqueurs cartographiques pilotes + missions, coords floutees.

    Coordonnees pilote arrondies (~11 km) pour la confidentialite ; missions
    a ~1 km. Seuls les enregistrements geolocalises sont renvoyes.
    """
    pilots = search_pilots(
        country=country, mission_type=mission_type,
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

import re

from config import MESSAGE_BANNED_PATTERNS


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
        "WHERE id=? AND status='pending_payment'",
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


def confirm_completion(booking_id: int, by_user: int) -> bool:
    """Le client confirme la livraison. Declenche le Transfer Stripe au pilote."""
    booking = get_booking(booking_id)
    if not booking or booking["client_user_id"] != by_user:
        return False
    if booking["status"] not in ("funded", "in_progress"):
        return False

    # Recupere l'account Stripe du pilote
    pilot_acc = get_pilot_stripe_account(booking["pilot_user_id"])
    if not pilot_acc:
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
        return False

    with db.transaction():
        db.execute(
            "UPDATE bookings SET status='completed', completed_at=datetime('now'), "
            "released_at=datetime('now'), stripe_transfer_id=? "
            "WHERE id=? AND status IN ('funded', 'in_progress')",
            (transfer_id, booking_id), commit=False,
        )
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
    if booking["status"] in ("completed", "refunded", "cancelled"):
        return False
    db.execute(
        "UPDATE bookings SET status='disputed', dispute_reason=? WHERE id=?",
        ((reason or "")[:1000], booking_id),
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'dispute_open', ?, ?)",
        (by_user, f"booking:{booking_id}",
         json.dumps({"reason": (reason or "")[:200]})),
    )
    return True


def refund_booking(booking_id: int, amount: Optional[float] = None,
                   admin_user: Optional[int] = None) -> bool:
    """Refund total ou partiel. Si amount=None, refund full."""
    booking = get_booking(booking_id)
    if not booking or booking["status"] not in ("funded", "disputed", "in_progress"):
        return False
    if not booking.get("stripe_payment_intent_id"):
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
            db.execute(
                "UPDATE bookings SET status='refunded', refunded_at=datetime('now') "
                "WHERE id=? AND status IN ('funded', 'disputed', 'in_progress')",
                (booking_id,), commit=False,
            )
            update_mission_status(booking["mission_id"], "cancelled", commit=False)
    return ok


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
