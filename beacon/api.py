"""API AubeBeacon.

    POST /api/v1/telemetry                 ingestion (Authorization: Bearer <jeton de balise>)
    GET  /api/v1/beacon/live               balises du compte + dernier point + vol en cours (session)
    GET  /api/v1/beacon/flights/<id>/track trace simplifiée d'un vol (session)
    GET  /api/v1/beacon/ws-ticket          ticket de connexion au hub temps réel (session)

L'ingestion est le chemin chaud : lecture bornée du corps, authentification
HMAC, validation stricte, une transaction, réponse courte. Elle est exemptée
du CSRF (pas de session, un jeton porteur) et ne permet AUCUNE commande vers
le drone : le serveur ne renvoie que des cadences et des accusés.
"""
import json
import logging
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, Response, abort, g, jsonify, request

import security
from config import (
    BEACON_DEVICE_RATE_PER_MIN, BEACON_MAX_BATCH, BEACON_MAX_BATCH_BYTES, BEACON_MAX_BODY_BYTES,
)

from . import devices as _devices
from . import flights as _flights
from . import realtime as _realtime
from . import status as _status
from . import telemetry as _telemetry

log = logging.getLogger("aubepilot.beacon.api")

bp = Blueprint("beacon_api", __name__)

# Cadence par balise (token bucket en mémoire, par worker : même limite que
# security.rate_limit, documentée comme telle).
_rate_lock = threading.Lock()
_rate: "defaultdict[str, deque]" = defaultdict(deque)


def _json_error(status: int, code: str, message: str, **extra) -> Response:
    body = {"success": False, "error": code, "message": message}
    body.update(extra)
    resp = jsonify(body)
    resp.status_code = status
    return resp


def _reject_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _read_json(max_bytes: int):
    """(objet JSON, taille lue) ; 413 avant de lire au-delà de `max_bytes`."""
    if request.content_length is not None and request.content_length > max_bytes:
        abort(_json_error(413, "payload_too_large", f"body larger than {max_bytes} bytes"))
    raw = request.stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        abort(_json_error(413, "payload_too_large", f"body larger than {max_bytes} bytes"))
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant), len(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        abort(_json_error(400, "invalid_json", str(exc)[:200]))


def _bearer() -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _device_rate_ok(device_uid: str, n: int = 1) -> bool:
    now = time.time()
    with _rate_lock:
        stamps = _rate[device_uid]
        while stamps and now - stamps[0] > 60:
            stamps.popleft()
        if len(stamps) + n > BEACON_DEVICE_RATE_PER_MIN:
            return False
        stamps.extend([now] * n)
    return True


@bp.route("/api/v1/telemetry", methods=["POST"])
@security.rate_limit(per_minute=600, per_hour=20000, key="beacon_ingest_ip")
def telemetry_ingest():
    """Un paquet (objet) ou un lot rejoué (tableau, BEACON_MAX_BATCH au plus)."""
    ctype = (request.content_type or "").split(";")[0].strip().lower()
    if ctype and ctype != "application/json":
        return _json_error(415, "unsupported_media_type", "send application/json")
    body, size = _read_json(BEACON_MAX_BATCH_BYTES)
    batch = isinstance(body, list)
    if not batch and size > BEACON_MAX_BODY_BYTES:
        return _json_error(413, "payload_too_large", f"single packet larger than {BEACON_MAX_BODY_BYTES} bytes")
    raw_packets = body if batch else [body]
    if not raw_packets:
        return _json_error(400, "validation", "empty batch")
    if len(raw_packets) > BEACON_MAX_BATCH:
        return _json_error(400, "validation", f"batch larger than {BEACON_MAX_BATCH} packets")

    # Authentification AVANT la validation : un inconnu reçoit toujours 401,
    # jamais le détail du format attendu. Le device_id vient du premier paquet.
    first = raw_packets[0] if isinstance(raw_packets[0], dict) else {}
    uid = first.get("device_id") if isinstance(first.get("device_id"), str) else ""
    ip = security.client_ip()
    device, why = _devices.authenticate(uid, _bearer(), ip=ip)
    if device is None:
        if why == "disabled":
            return _json_error(403, "device_disabled", "this beacon is disabled")
        return _json_error(401, "unauthorized", "unknown device or bad token")
    if not _device_rate_ok(uid, len(raw_packets)):
        return _json_error(429, "rate_limited", f"more than {BEACON_DEVICE_RATE_PER_MIN} packets per minute")

    packets = []
    for i, raw in enumerate(raw_packets):
        try:
            packets.append(_telemetry.parse_packet(raw))
        except _telemetry.TelemetryError as exc:
            return _json_error(400, "validation", exc.message, path=exc.path, packet_index=i)
    if any(p["device_id"] != uid for p in packets):
        return _json_error(400, "validation", "all packets of a batch must share device_id", packet_index=0)

    received_at = _devices.now_iso()
    try:
        results = _telemetry.ingest(device, packets, ip=ip, received_at=received_at)
    except Exception:
        log.exception("[AUBE][TELEMETRY] ingestion %s", uid)
        return _json_error(500, "server_error", "telemetry not stored")
    for r in results:
        if r["event"]:
            _realtime.publish_telemetry(r["event"])

    if not batch:
        r = results[0]
        return jsonify({
            "success": True, "server_time": received_at, "telemetry_id": r["telemetry_id"],
            "duplicate": r["duplicate"], "flight_id": r["flight_id"], "flags": r["flags"],
            "intervals_ms": _telemetry.intervals_ms(),
        })
    return jsonify({
        "success": True, "server_time": received_at,
        "accepted": sum(1 for r in results if not r["duplicate"]),
        "duplicates": sum(1 for r in results if r["duplicate"]),
        "results": [{"packet_id": r["packet_id"], "telemetry_id": r["telemetry_id"],
                     "duplicate": r["duplicate"], "flight_id": r["flight_id"]} for r in results],
        "intervals_ms": _telemetry.intervals_ms(),
    })


# ---------------------------------------------------------------------------
# Lecture pour la carte (session AubePilot)
# ---------------------------------------------------------------------------

def _require_user() -> dict:
    user = getattr(g, "user", None)
    if not user:
        abort(_json_error(401, "login_required", "sign in first"))
    return user


def _scope(user: dict):
    """(propriétaire filtré ou None pour tout voir). Un administrateur voit
    tout avec ?all=1, sinon ses propres balises comme n'importe quel pilote."""
    if user.get("is_admin") and request.args.get("all") == "1":
        return None
    return user["id"]


def device_view(d: dict, now=None) -> dict:
    """Balise → JSON pour la carte (jamais le jeton, jamais l'IP)."""
    state, age = _status.compute(d.get("last_seen_at"), now)
    position = None
    if d.get("last_lat") is not None:
        position = {
            "latitude": d["last_lat"], "longitude": d["last_lng"],
            "relative_altitude_m": d.get("last_alt_rel"), "speed_mps": d.get("last_speed_mps"),
            "heading_deg": d.get("last_heading_deg"), "satellites": d.get("last_satellites"),
            "gnss_fix": bool(d.get("last_gnss_fix")),
            "timestamp": d.get("last_point_device_at") or d.get("last_point_at"),
        }
    drone = None
    if d.get("drone_id"):
        drone = {"id": d["drone_id"], "brand": d.get("drone_brand"), "model": d.get("drone_model"),
                 "category": d.get("drone_category")}
    return {
        "id": d["id"], "uid": d["device_uid"], "label": d.get("label"), "enabled": bool(d.get("enabled")),
        "owner": {"id": d["owner_user_id"], "name": d.get("owner_name")},
        "drone": drone, "status": state, "age_s": None if age is None else round(age, 1),
        "last_seen_at": d.get("last_seen_at"), "state": d.get("last_state"),
        "battery_percent": d.get("last_battery"), "signal_dbm": d.get("last_signal_dbm"),
        "network_type": d.get("last_network_type"), "firmware_version": d.get("firmware_version"),
        "position": position, "room": _realtime.room_for_device(d["id"]),
    }


def flight_view(f: dict) -> dict:
    return {
        "id": f["id"], "status": f["status"], "device_id": f["device_id"], "drone_id": f.get("drone_id"),
        "takeoff_at": f.get("takeoff_at"), "landing_at": f.get("landing_at"), "duration_s": f.get("duration_s"),
        "distance_m": round(f["distance_m"], 1) if f.get("distance_m") is not None else None,
        "max_speed_mps": f.get("max_speed_mps"), "max_relative_altitude_m": f.get("max_relative_altitude_m"),
        "points": f.get("points"), "last_lat": f.get("last_lat"), "last_lng": f.get("last_lng"),
        "last_point_at": f.get("last_point_at"),
    }


def realtime_info() -> dict:
    return {
        "enabled": _realtime.enabled(),
        "url": _realtime.public_url(request.host, request.is_secure or request.headers.get("X-Forwarded-Proto") == "https")
        if _realtime.enabled() else None,
        "ticket_url": "/api/v1/beacon/ws-ticket",
    }


@bp.route("/api/v1/beacon/live")
def beacon_live():
    """Balises visibles par le compte, avec dernier point, vol en cours et sa trace."""
    user = _require_user()
    now_iso = _devices.now_iso()
    _flights.sweep_stale(now_iso)
    out = []
    for d in _devices.list_devices(_scope(user)):
        v = device_view(d)
        flight = _flights.active_flight(d["id"])
        v["flight"] = flight_view(flight) if flight else None
        v["track"] = _flights.track(flight["id"]) if flight else []
        out.append(v)
    return jsonify({"devices": out, "thresholds": _status.thresholds(), "server_time": now_iso,
                    "realtime": realtime_info()})


@bp.route("/api/v1/beacon/flights/<int:flight_id>/track")
def beacon_flight_track(flight_id):
    user = _require_user()
    flight = _flights.get_flight(flight_id, None if user.get("is_admin") else user["id"])
    if not flight:
        return _json_error(404, "not_found", "unknown flight")
    return jsonify({"flight": flight_view(flight), "track": _flights.track(flight_id)})


@bp.route("/api/v1/beacon/ws-ticket")
def beacon_ws_ticket():
    """Ticket signé (60 s) listant les salles que ce compte peut suivre."""
    user = _require_user()
    if not _realtime.enabled():
        return _json_error(503, "realtime_disabled", "realtime hub not configured")
    if user.get("is_admin") and request.args.get("all") == "1":
        rooms = "*"
    else:
        rooms = [_realtime.room_for_device(d["id"]) for d in _devices.list_devices(user["id"])]
    return jsonify({"ticket": _realtime.make_ticket(user["id"], rooms), "rooms": rooms,
                    "url": realtime_info()["url"], "ttl_s": 60})
