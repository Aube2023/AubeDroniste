"""Logique metier AubeDroniste : missions, dronistes, encheres, bookings.

Garde le code SQL ici pour ne pas alourdir app.py. Pas d'ORM, on prefere
voir les requetes a plat. Les fonctions retournent des dicts (sqlite3.Row
converti) pour rester serialisables JSON.
"""
import json
import logging
from typing import Iterable, Optional

log = logging.getLogger("aubedroniste.services")

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
# Profil droniste
# ---------------------------------------------------------------------------

def upsert_pilot_profile(user_id: int, **fields) -> Optional[dict]:
    existing = db.fetchone("SELECT 1 FROM pilot_profiles WHERE user_id=?", (user_id,))
    allowed = {
        "headline", "years_experience", "hourly_rate", "daily_rate",
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


def get_pilot_profile(user_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT u.*, p.headline, p.years_experience, p.hourly_rate, p.daily_rate, "
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
# Recherche dronistes
# ---------------------------------------------------------------------------

def search_pilots(*, country: str = "", city: str = "", mission_type: str = "",
                  capability: str = "", lat: Optional[float] = None,
                  lng: Optional[float] = None, radius_km: int = DEFAULT_SEARCH_RADIUS_KM,
                  min_rating: float = 0, only_available: bool = True,
                  limit: int = 50) -> list:
    q = [
        "SELECT u.id, u.username, u.full_name, u.country, u.city, u.lat, u.lng, "
        "       u.is_verified, u.avatar_path, u.bio, "
        "       p.headline, p.hourly_rate, p.daily_rate, p.currency AS p_currency, "
        "       p.travel_radius_km, p.is_available, p.insurance, p.languages "
        "FROM users u "
        "JOIN pilot_profiles p ON p.user_id = u.id "
        "WHERE u.role IN ('droniste', 'both')",
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
    q.append("ORDER BY u.is_verified DESC, p.is_available DESC LIMIT ?")
    args.append(limit)
    rows = [dict(r) for r in db.fetchall(" ".join(q), args)]
    radius = max(1, min(radius_km, MAX_SEARCH_RADIUS_KM))
    enriched = []
    for r in rows:
        if lat is not None and lng is not None and r.get("lat") is not None:
            d = db.haversine_km(lat, lng, r["lat"], r["lng"])
            r["distance_km"] = round(d, 1)
            if d > radius:
                continue
        else:
            r["distance_km"] = None
        r["rating"] = pilot_rating(r["id"])
        if r["rating"]["avg"] < min_rating:
            continue
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


def update_mission_status(mission_id: int, status: str):
    if status not in MISSION_STATUS:
        raise ValueError(f"statut invalide: {status}")
    db.execute(
        "UPDATE missions SET status=?, updated_at=datetime('now') WHERE id=?",
        (status, mission_id),
    )


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


def search_missions(*, country: str = "", mission_type: str = "",
                    status: str = "open", lat: Optional[float] = None,
                    lng: Optional[float] = None,
                    radius_km: int = DEFAULT_SEARCH_RADIUS_KM,
                    only_urgent: bool = False, limit: int = 100) -> list:
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
            if d > radius:
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
              message: str = "") -> int:
    # On regarde si c'est une nouvelle enchere (pas un update) avant l'INSERT
    is_new = not db.fetchone(
        "SELECT 1 FROM bids WHERE mission_id=? AND pilot_user_id=?",
        (mission_id, pilot_user_id),
    )
    cur = db.execute(
        "INSERT INTO bids (mission_id, pilot_user_id, price, currency, eta_hours, message) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(mission_id, pilot_user_id) DO UPDATE SET "
        "  price=excluded.price, currency=excluded.currency, "
        "  eta_hours=excluded.eta_hours, message=excluded.message, status='pending'",
        (mission_id, pilot_user_id, price, currency, eta_hours, message),
    )
    bid_id = cur.lastrowid or 0
    if is_new:
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
            if client and mission and pilot:
                mailer.send_new_bid(
                    client=dict(client), mission=dict(mission),
                    bid={"price": price, "currency": currency,
                         "eta_hours": eta_hours, "message": message},
                    pilot=dict(pilot),
                )
        except Exception as exc:
            log.warning("email new_bid failed for mission=%s : %s", mission_id, exc)
    return bid_id


def list_bids(mission_id: int) -> list:
    rows = db.fetchall(
        "SELECT b.*, u.full_name AS pilot_name, u.username AS pilot_username, "
        "       u.is_verified, u.city AS pilot_city, u.country AS pilot_country "
        "FROM bids b JOIN users u ON u.id=b.pilot_user_id "
        "WHERE b.mission_id=? ORDER BY b.price ASC, b.created_at ASC",
        (mission_id,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["pilot_rating"] = pilot_rating(d["pilot_user_id"])
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
    # Verrou atomique : on tente de passer la mission en 'assigned'
    # uniquement si elle est encore 'open'. Si rowcount=0, c'est qu'un
    # autre processus a deja accepte une enchere -> on refuse.
    cur_lock = db.execute(
        "UPDATE missions SET status='assigned', updated_at=datetime('now') "
        "WHERE id=? AND client_user_id=? AND status='open'",
        (mission_id, client_user_id),
    )
    if cur_lock.rowcount == 0:
        raise ValueError("mission deja attribuee (race detectee)")
    fee = round(bid["price"] * PLATFORM_FEE_PCT / 100.0, 2)
    cur = db.execute(
        "INSERT INTO bookings "
        "(mission_id, bid_id, client_user_id, pilot_user_id, agreed_price, currency, "
        " platform_fee, scheduled_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')",
        (
            mission_id, bid_id, client_user_id, bid["pilot_user_id"],
            bid["price"], bid["currency"], fee, mission["start_date"],
        ),
    )
    booking_id = cur.lastrowid
    db.execute("UPDATE bids SET status='accepted' WHERE id=?", (bid_id,))
    db.execute(
        "UPDATE bids SET status='rejected' WHERE mission_id=? AND id<>?",
        (mission_id, bid_id),
    )
    # mission deja 'assigned' via le verrou plus haut.
    # Statut explicite : en attente de paiement client
    db.execute(
        "UPDATE bookings SET status='pending_payment' WHERE id=?",
        (booking_id,),
    )
    db.execute(
        "INSERT INTO audit_log (user_id, action, target, payload) "
        "VALUES (?, 'accept_bid', ?, ?)",
        (client_user_id, f"mission:{mission_id}",
         json.dumps({"booking": booking_id, "bid": bid_id})),
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
        "       cu.full_name AS client_name, pu.full_name AS pilot_name "
        "FROM bookings b "
        "JOIN missions m ON m.id=b.mission_id "
        "JOIN users cu ON cu.id=b.client_user_id "
        "JOIN users pu ON pu.id=b.pilot_user_id "
        "WHERE b.id=?",
        (booking_id,),
    )
    return dict(row) if row else None


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
    if status not in BOOKING_STATUS:
        raise ValueError(f"statut booking invalide: {status}")
    extra = ""
    if status == "completed":
        extra = ", completed_at=datetime('now')"
    db.execute(
        f"UPDATE bookings SET status=?{extra} "
        "WHERE id=? AND (client_user_id=? OR pilot_user_id=?)",
        (status, booking_id, by_user, by_user),
    )
    if status == "completed":
        b = get_booking(booking_id)
        if b:
            update_mission_status(b["mission_id"], "done")
    elif status == "in_progress":
        b = get_booking(booking_id)
        if b:
            update_mission_status(b["mission_id"], "in_progress")
    elif status == "cancelled":
        b = get_booking(booking_id)
        if b:
            update_mission_status(b["mission_id"], "cancelled")


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
# Stats / homepage
# ---------------------------------------------------------------------------

def public_stats() -> dict:
    pilots = db.fetchone(
        "SELECT COUNT(*) AS n FROM users WHERE role IN ('droniste', 'both')"
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
    rows = db.fetchall(
        "SELECT u.id, u.full_name, u.country, u.city, u.is_verified, u.avatar_path, "
        "       p.headline, p.hourly_rate, p.currency AS p_currency "
        "FROM users u JOIN pilot_profiles p ON p.user_id=u.id "
        "WHERE u.role IN ('droniste','both') AND p.is_available=1 "
        "ORDER BY u.is_verified DESC, datetime(u.last_seen_at) DESC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["rating"] = pilot_rating(d["id"])
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
    """Compte par pays : nb dronistes + nb missions ouvertes. Trie par activité totale."""
    pilots_by = {
        r["country"]: r["n"]
        for r in db.fetchall(
            "SELECT country, COUNT(*) AS n FROM users "
            "WHERE country IS NOT NULL AND country<>'' "
            "AND role IN ('droniste','both') "
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


def mark_booking_funded(booking_id: int, payment_intent_id: Optional[str] = None):
    """Le client a paye. Booking en escrow (`funded`)."""
    db.execute(
        "UPDATE bookings SET status='funded', paid_at=datetime('now'), "
        "stripe_payment_intent_id=COALESCE(?, stripe_payment_intent_id) "
        "WHERE id=? AND status IN ('pending_payment', 'in_progress')",
        (payment_intent_id, booking_id),
    )
    # Notifie le pilote que la mission est financée
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

    db.execute(
        "UPDATE bookings SET status='completed', completed_at=datetime('now'), "
        "released_at=datetime('now'), stripe_transfer_id=? WHERE id=?",
        (transfer_id, booking_id),
    )
    update_mission_status(booking["mission_id"], "done")

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
        db.execute(
            "UPDATE bookings SET status='refunded', refunded_at=datetime('now') WHERE id=?",
            (booking_id,),
        )
        update_mission_status(booking["mission_id"], "cancelled")
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
