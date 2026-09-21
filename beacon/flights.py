"""Sessions de vol AubeBeacon : ouverture, suivi, clôture, statistiques, trace.

La balise annonce un état (cf. beacon/telemetry.py : TAKEOFF, FLYING, LANDING,
LANDED, READY...). Le serveur en déduit une session :

    TAKEOFF               → session PREPARING (créée si aucune en cours)
    FLYING / LANDING      → session ACTIVE (créée si la balise a redémarré en l'air)
    LANDED / READY        → session FINISHED (ACTIVE) ou ABORTED (PREPARING : faux départ)
    GPS_LOST, NETWORK_LOST, CONNECTED, OFFLINE → sans effet sur la session
    silence > BEACON_FLIGHT_ABORT_AFTER_S → ABORTED (coupure sans atterrissage annoncé)

Les statistiques (distance, vitesse et hauteur maximales, nombre de points,
dernière position) sont tenues à jour point par point ; la durée est fixée à
la clôture. Les points marqués « jump » (saut impossible) ne comptent ni dans
la distance ni dans la trace.
"""
import logging
import math
from datetime import timedelta
from typing import Optional

import db
from config import BEACON_FLIGHT_ABORT_AFTER_S, BEACON_TRACK_EPSILON_M, BEACON_TRACK_MAX_POINTS

from . import status as _status

log = logging.getLogger("aubepilot.beacon.flights")

PREPARING, ACTIVE, FINISHED, ABORTED = "PREPARING", "ACTIVE", "FINISHED", "ABORTED"
OPEN_STATUSES = (PREPARING, ACTIVE)
AIRBORNE_STATES = ("FLYING", "LANDING")
LANDING_STATES = ("LANDED", "READY")


# ---------------------------------------------------------------------------
# Géométrie
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlng = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def simplify(points: list, epsilon_m: float = BEACON_TRACK_EPSILON_M,
             max_points: int = BEACON_TRACK_MAX_POINTS) -> list:
    """Douglas-Peucker sur une projection locale en mètres, puis plafond de
    points (sous-échantillonnage régulier, premier et dernier conservés).
    `points` : liste de [lng, lat, ...] ; les champs supplémentaires suivent."""
    n = len(points)
    if n <= 2:
        return list(points)
    lat0 = math.radians(points[0][1])
    kx = 111320.0 * math.cos(lat0)
    ky = 110540.0
    xy = [((p[0] - points[0][0]) * kx, (p[1] - points[0][1]) * ky) for p in points]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, best_i = 0.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if norm == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dx * (ay - py) - (ax - px) * dy) / norm
            if d > best:
                best, best_i = d, i
        if best > epsilon_m and best_i > 0:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    out = [p for p, k in zip(points, keep) if k]
    if len(out) > max_points:
        stride = (len(out) - 1) / float(max_points - 1)
        idx = sorted({int(round(i * stride)) for i in range(max_points)} | {0, len(out) - 1})
        out = [out[i] for i in idx]
    return out


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

_SELECT = """
SELECT f.*, d.device_uid, d.label AS device_label, p.brand AS drone_brand, p.model AS drone_model,
       u.full_name AS owner_name
FROM beacon_flights f
JOIN beacon_devices d ON d.id = f.device_id
LEFT JOIN pilot_drones p ON p.id = f.drone_id
JOIN users u ON u.id = f.owner_user_id
"""


def active_flight(device_id: int) -> Optional[dict]:
    row = db.fetchone(
        "SELECT * FROM beacon_flights WHERE device_id=? AND status IN (?, ?) ORDER BY id DESC LIMIT 1",
        (device_id, PREPARING, ACTIVE),
    )
    return dict(row) if row else None


def get_flight(flight_id: int, owner_user_id: Optional[int]) -> Optional[dict]:
    if owner_user_id:
        row = db.fetchone(_SELECT + "WHERE f.id=? AND f.owner_user_id=?", (flight_id, owner_user_id))
    else:
        row = db.fetchone(_SELECT + "WHERE f.id=?", (flight_id,))
    return dict(row) if row else None


def list_flights(owner_user_id: Optional[int], device_id: Optional[int] = None, limit: int = 50) -> list:
    clauses, params = [], []
    if owner_user_id:
        clauses.append("f.owner_user_id=?")
        params.append(owner_user_id)
    if device_id:
        clauses.append("f.device_id=?")
        params.append(device_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.fetchall(_SELECT + where + " ORDER BY f.id DESC LIMIT ?", params + [limit])
    return [dict(r) for r in rows]


def track(flight_id: int, simplified: bool = True) -> list:
    """Trace d'un vol : [[lng, lat, hauteur relative, horodatage, vitesse], ...],
    sans les points marqués « jump », simplifiée pour l'affichage."""
    rows = db.fetchall(
        "SELECT longitude, latitude, relative_altitude_m, COALESCE(device_timestamp, server_timestamp) AS ts, "
        "speed_mps FROM beacon_telemetry WHERE flight_id=? AND (flags IS NULL OR flags NOT LIKE '%jump%') "
        "ORDER BY id",
        (flight_id,),
    )
    pts = [[r["longitude"], r["latitude"], r["relative_altitude_m"], r["ts"], r["speed_mps"]] for r in rows]
    return simplify(pts) if simplified else pts


# ---------------------------------------------------------------------------
# Machine d'état
# ---------------------------------------------------------------------------

def _open(device: dict, ts: str, status: str) -> dict:
    cur = db.execute(
        "INSERT INTO beacon_flights (device_id, drone_id, owner_user_id, status, takeoff_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (device["id"], device.get("drone_id"), device["owner_user_id"], status, ts, ts),
        commit=False,
    )
    log.info("[AUBE][FLIGHT] balise %s : session %s ouverte (%s)", device["device_uid"], cur.lastrowid, status)
    return {"id": cur.lastrowid, "device_id": device["id"], "status": status, "takeoff_at": ts,
            "distance_m": 0.0, "max_speed_mps": None, "max_relative_altitude_m": None, "points": 0,
            "last_lat": None, "last_lng": None, "last_point_at": None}


def _set_status(flight: dict, status: str, ts: str) -> None:
    db.execute("UPDATE beacon_flights SET status=?, updated_at=? WHERE id=?", (status, ts, flight["id"]),
               commit=False)
    flight["status"] = status


def _close(flight: dict, status: str, ts: str) -> None:
    landing = ts if status == FINISHED else (flight.get("last_point_at") or ts)
    t0 = _status.parse_iso(flight.get("takeoff_at"))
    t1 = _status.parse_iso(landing)
    duration = int((t1 - t0).total_seconds()) if t0 and t1 and t1 >= t0 else None
    db.execute(
        "UPDATE beacon_flights SET status=?, landing_at=?, duration_s=?, updated_at=? WHERE id=?",
        (status, landing, duration, ts, flight["id"]),
        commit=False,
    )
    flight.update(status=status, landing_at=landing, duration_s=duration)
    log.info("[AUBE][FLIGHT] session %s close : %s (%s s, %.0f m)", flight["id"], status,
             duration if duration is not None else -1, flight.get("distance_m") or 0.0)


def _is_stale(flight: dict, now_iso: str) -> bool:
    ref = _status.parse_iso(flight.get("last_point_at") or flight.get("takeoff_at"))
    now = _status.parse_iso(now_iso)
    return bool(ref and now and now - ref > timedelta(seconds=BEACON_FLIGHT_ABORT_AFTER_S))


def _add_point(flight: dict, point: dict, ts: str, jump: bool) -> None:
    dist = flight.get("distance_m") or 0.0
    if not jump and flight.get("last_lat") is not None:
        dist += haversine_m(flight["last_lat"], flight["last_lng"], point["latitude"], point["longitude"])
    vmax = flight.get("max_speed_mps")
    if point.get("speed_mps") is not None and (vmax is None or point["speed_mps"] > vmax):
        vmax = point["speed_mps"]
    hmax = flight.get("max_relative_altitude_m")
    if point.get("relative_altitude_m") is not None and (hmax is None or point["relative_altitude_m"] > hmax):
        hmax = point["relative_altitude_m"]
    flight["points"] = (flight.get("points") or 0) + 1
    flight.update(distance_m=dist, max_speed_mps=vmax, max_relative_altitude_m=hmax)
    if not jump:
        flight.update(last_lat=point["latitude"], last_lng=point["longitude"], last_point_at=ts)
    db.execute(
        "UPDATE beacon_flights SET points=?, distance_m=?, max_speed_mps=?, max_relative_altitude_m=?, "
        "last_lat=?, last_lng=?, last_point_at=?, updated_at=? WHERE id=?",
        (flight["points"], dist, vmax, hmax, flight.get("last_lat"), flight.get("last_lng"),
         flight.get("last_point_at"), ts, flight["id"]),
        commit=False,
    )


def process(device: dict, *, state: str, ts: str, point: Optional[dict], jump: bool = False) -> Optional[int]:
    """Applique l'état annoncé par la balise à sa session en cours et y rattache
    le point. Retourne l'id de session à lier au point (None hors vol).
    À appeler dans la transaction d'ingestion (aucun commit ici)."""
    flight = active_flight(device["id"])
    if flight and _is_stale(flight, ts):
        _close(flight, ABORTED, ts)
        flight = None
    if state == "TAKEOFF":
        if flight is None:
            flight = _open(device, ts, PREPARING)
    elif state in AIRBORNE_STATES:
        if flight is None:
            flight = _open(device, ts, ACTIVE)
        elif flight["status"] == PREPARING:
            _set_status(flight, ACTIVE, ts)
    if flight is None:
        return None
    if point is not None:
        _add_point(flight, point, ts, jump)
    if state in LANDING_STATES:
        _close(flight, FINISHED if flight["status"] == ACTIVE else ABORTED, ts)
    return flight["id"]


def sweep_stale(now_iso: str) -> int:
    """Abandonne les sessions ouvertes muettes depuis trop longtemps (appelé
    par la lecture « en direct », pas de tâche planifiée à installer)."""
    rows = db.fetchall("SELECT * FROM beacon_flights WHERE status IN (?, ?)", (PREPARING, ACTIVE))
    n = 0
    with db.transaction():
        for r in rows:
            flight = dict(r)
            if _is_stale(flight, now_iso):
                _close(flight, ABORTED, now_iso)
                n += 1
    return n
