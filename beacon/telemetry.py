"""Télémétrie AubeBeacon : validation du format v1, plausibilité, ingestion.

Format (contrat complet : AubeBeacon/server/api/telemetry.v1.schema.json) :

    {"version": 1, "device_id": "AUBE-BCN-000001", "packet_id": "3fa2c1e8-42",
     "timestamp": "2026-09-21T08:00:00Z" | null,
     "position": {"latitude", "longitude", "gnss_altitude_m", "barometric_altitude_m",
                  "relative_altitude_m", "speed_mps", "heading_deg"} | null,
     "gnss": {"fix": true, "satellites": 17, "hdop": 0.9},
     "network": {"type": "LTE-M", "signal_dbm": -87},
     "device": {"battery_percent": 83, "firmware": "0.1.0", "uptime_s": 120, "queued": 0},
     "flight": {"state": "FLYING"}}

Règles d'évolution : un ajout de clé optionnelle ne change pas la version
(les clés inconnues sont ignorées) ; une rupture incrémente `version` et
l'ancienne reste acceptée tant qu'elle figure dans SUPPORTED_VERSIONS.

Un paquet sans `position` est un battement de cœur (GPS perdu, au sol sans
fix) : il met à jour la balise (état, batterie, signal, dernier signe de vie)
mais ne crée pas de point. Un paquet positionné crée une ligne de
`beacon_telemetry` ; `UNIQUE(device_id, packet_id)` absorbe les rejeux.
"""
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import db
from config import (
    BEACON_INTERVAL_FLYING_MS, BEACON_INTERVAL_LANDED_MS, BEACON_INTERVAL_READY_MS,
    BEACON_JUMP_SPEED_MPS, BEACON_MAX_SPEED_MPS,
)

from . import devices as _devices
from . import flights as _flights
from . import status as _status

log = logging.getLogger("aubepilot.beacon.telemetry")

SUPPORTED_VERSIONS = (1,)
FLIGHT_STATES = ("OFFLINE", "CONNECTED", "READY", "TAKEOFF", "FLYING", "LANDING", "LANDED",
                 "GPS_LOST", "NETWORK_LOST")
PACKET_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
NETWORK_TYPE_RE = re.compile(r"^[A-Za-z0-9 _.+/-]{1,16}$")
FIRMWARE_RE = re.compile(r"^[A-Za-z0-9 _.+-]{1,32}$")
# Un paquet daté de plus d'une journée dans le futur : horloge fausse, refusé.
FUTURE_TOLERANCE = timedelta(days=1)
# Plus vieux qu'une semaine : accepté (rejeu d'une file) mais marqué « late ».
LATE_AFTER = timedelta(days=7)


class TelemetryError(ValueError):
    """Paquet refusé : `code` court pour la machine, `message` pour l'humain."""

    def __init__(self, code: str, message: str, path: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _obj(value, path: str, required: bool) -> Optional[dict]:
    if value is None:
        if required:
            raise TelemetryError("missing", f"{path} is required", path)
        return None
    if not isinstance(value, dict):
        raise TelemetryError("type", f"{path} must be an object", path)
    return value


def _num(obj: dict, key: str, path: str, lo: float, hi: float, required: bool = False) -> Optional[float]:
    v = obj.get(key)
    if v is None:
        if required:
            raise TelemetryError("missing", f"{path}.{key} is required", f"{path}.{key}")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise TelemetryError("type", f"{path}.{key} must be a finite number", f"{path}.{key}")
    if v < lo or v > hi:
        raise TelemetryError("range", f"{path}.{key} out of range [{lo}, {hi}]", f"{path}.{key}")
    return float(v)


def _int(obj: dict, key: str, path: str, lo: int, hi: int) -> Optional[int]:
    v = obj.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or int(v) != v:
        raise TelemetryError("type", f"{path}.{key} must be an integer", f"{path}.{key}")
    if v < lo or v > hi:
        raise TelemetryError("range", f"{path}.{key} out of range [{lo}, {hi}]", f"{path}.{key}")
    return int(v)


def _str(obj: dict, key: str, path: str, pattern: re.Pattern, required: bool = False) -> Optional[str]:
    v = obj.get(key)
    if v is None:
        if required:
            raise TelemetryError("missing", f"{path}.{key} is required", f"{path}.{key}")
        return None
    if not isinstance(v, str) or not pattern.match(v):
        raise TelemetryError("format", f"{path}.{key} has an invalid format", f"{path}.{key}")
    return v


def _timestamp(value, now: datetime) -> tuple:
    """(ISO normalisé ou None, drapeau 'late' ou None)."""
    if value is None:
        return None, None
    if not isinstance(value, str):
        raise TelemetryError("type", "timestamp must be an ISO 8601 string or null", "timestamp")
    dt = _status.parse_iso(value)
    if dt is None:
        raise TelemetryError("format", "timestamp is not ISO 8601", "timestamp")
    if dt - now > FUTURE_TOLERANCE:
        raise TelemetryError("range", "timestamp is in the future (device clock not set?)", "timestamp")
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return iso, ("late" if now - dt > LATE_AFTER else None)


def parse_packet(raw, now: Optional[datetime] = None) -> dict:
    """Valide un paquet brut (dict issu du JSON) et le normalise. Lève
    TelemetryError à la première anomalie ; les clés inconnues sont ignorées."""
    now = now or datetime.now(timezone.utc)
    p = _obj(raw, "packet", required=True)
    version = p.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in SUPPORTED_VERSIONS:
        raise TelemetryError("version", f"unsupported version (supported: {list(SUPPORTED_VERSIONS)})", "version")
    device_id = p.get("device_id")
    if not isinstance(device_id, str) or not _devices.UID_RE.match(device_id):
        raise TelemetryError("format", "device_id has an invalid format", "device_id")
    packet_id = _str(p, "packet_id", "packet", PACKET_ID_RE, required=True)
    timestamp, late = _timestamp(p.get("timestamp"), now)

    position = None
    pos = _obj(p.get("position"), "position", required=False)
    if pos is not None:
        heading = _num(pos, "heading_deg", "position", 0, 360)
        position = {
            "latitude": _num(pos, "latitude", "position", -90, 90, required=True),
            "longitude": _num(pos, "longitude", "position", -180, 180, required=True),
            "gnss_altitude_m": _num(pos, "gnss_altitude_m", "position", -500, 20000),
            "barometric_altitude_m": _num(pos, "barometric_altitude_m", "position", -500, 20000),
            "relative_altitude_m": _num(pos, "relative_altitude_m", "position", -200, 5000),
            "speed_mps": _num(pos, "speed_mps", "position", 0, BEACON_MAX_SPEED_MPS),
            "heading_deg": (heading % 360.0) if heading is not None else None,
        }

    gnss = _obj(p.get("gnss"), "gnss", required=False) or {}
    fix = gnss.get("fix")
    if fix is not None and not isinstance(fix, bool):
        raise TelemetryError("type", "gnss.fix must be a boolean", "gnss.fix")
    network = _obj(p.get("network"), "network", required=False) or {}
    device = _obj(p.get("device"), "device", required=False) or {}
    flight = _obj(p.get("flight"), "flight", required=True)
    state = flight.get("state")
    if not isinstance(state, str) or state.upper() not in FLIGHT_STATES:
        raise TelemetryError("value", f"flight.state must be one of {list(FLIGHT_STATES)}", "flight.state")

    return {
        "version": version,
        "device_id": device_id,
        "packet_id": packet_id,
        "timestamp": timestamp,
        "position": position,
        "gnss": {
            "fix": bool(fix) if fix is not None else (position is not None),
            "satellites": _int(gnss, "satellites", "gnss", 0, 99),
            "hdop": _num(gnss, "hdop", "gnss", 0, 100),
        },
        "network": {
            "type": _str(network, "type", "network", NETWORK_TYPE_RE),
            "signal_dbm": _int(network, "signal_dbm", "network", -150, 0),
        },
        "device": {
            "battery_percent": _int(device, "battery_percent", "device", 0, 100),
            "firmware": _str(device, "firmware", "device", FIRMWARE_RE),
            "uptime_s": _int(device, "uptime_s", "device", 0, 10 ** 9),
            "queued": _int(device, "queued", "device", 0, 10 ** 6),
        },
        "state": state.upper(),
        "flags": [late] if late else [],
    }


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def intervals_ms() -> dict:
    """Cadences renvoyées à la balise avec chaque réponse."""
    return {"flying": BEACON_INTERVAL_FLYING_MS, "ready": BEACON_INTERVAL_READY_MS,
            "landed": BEACON_INTERVAL_LANDED_MS}


def _last_point(device: dict) -> Optional[dict]:
    if not device.get("last_telemetry_id"):
        return None
    row = db.fetchone(
        "SELECT latitude, longitude, COALESCE(device_timestamp, server_timestamp) AS ts "
        "FROM beacon_telemetry WHERE id=?", (device["last_telemetry_id"],))
    return dict(row) if row else None


def _is_jump(last: Optional[dict], position: dict, ts: str) -> bool:
    """Saut impossible entre le dernier point retenu et celui-ci (vitesse
    implicite > BEACON_JUMP_SPEED_MPS) : le point est gardé mais marqué."""
    if not last:
        return False
    t0, t1 = _status.parse_iso(last["ts"]), _status.parse_iso(ts)
    dt = abs((t1 - t0).total_seconds()) if t0 and t1 else 1.0
    dist = _flights.haversine_m(last["latitude"], last["longitude"], position["latitude"], position["longitude"])
    return dist / max(dt, 1.0) > BEACON_JUMP_SPEED_MPS


def _insert_point(device: dict, packet: dict, ts_eff: str, received_at: str, flags: list) -> tuple:
    """(id, doublon). INSERT OR IGNORE : un rejeu renvoie l'id déjà en base."""
    pos, gnss, net, dev = packet["position"], packet["gnss"], packet["network"], packet["device"]
    cur = db.execute(
        "INSERT OR IGNORE INTO beacon_telemetry (device_id, packet_id, device_timestamp, server_timestamp, "
        "latitude, longitude, gnss_altitude_m, barometric_altitude_m, relative_altitude_m, speed_mps, "
        "heading_deg, satellites, gnss_fix, network_type, network_signal_dbm, battery_percent, flight_state, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (device["id"], packet["packet_id"], packet["timestamp"], received_at,
         pos["latitude"], pos["longitude"], pos["gnss_altitude_m"], pos["barometric_altitude_m"],
         pos["relative_altitude_m"], pos["speed_mps"], pos["heading_deg"], gnss["satellites"],
         1 if gnss["fix"] else 0, net["type"], net["signal_dbm"], dev["battery_percent"], packet["state"],
         ",".join(flags) or None),
        commit=False,
    )
    if cur.rowcount == 0:
        row = db.fetchone("SELECT id FROM beacon_telemetry WHERE device_id=? AND packet_id=?",
                          (device["id"], packet["packet_id"]))
        return (int(row["id"]) if row else None), True
    return cur.lastrowid, False


def ingest(device: dict, packets: list, *, ip: str = "", received_at: Optional[str] = None) -> list:
    """Ingère des paquets validés (parse_packet) d'une même balise, dans une
    seule transaction. Retourne un résultat par paquet :
    {packet_id, telemetry_id, duplicate, flight_id, flags, event}. `event`
    est la charge à diffuser en temps réel (None pour un doublon)."""
    received_at = received_at or _devices.now_iso()
    results = []
    last = _last_point(device)
    first_seen = device.get("last_seen_at") is None
    with db.transaction():
        for packet in packets:
            ts_eff = packet["timestamp"] or received_at
            flags = list(packet["flags"])
            telemetry_id, duplicate, jump = None, False, False
            point = packet["position"]
            if point is not None:
                if last and last["ts"] and ts_eff < last["ts"]:
                    flags.append("late")
                jump = _is_jump(last, point, ts_eff)
                if jump:
                    flags.append("jump")
                telemetry_id, duplicate = _insert_point(device, packet, ts_eff, received_at, flags)
            if duplicate:
                results.append({"packet_id": packet["packet_id"], "telemetry_id": telemetry_id,
                                "duplicate": True, "flight_id": None, "flags": flags, "event": None})
                continue
            flight_id = _flights.process(device, state=packet["state"], ts=ts_eff, point=point, jump=jump)
            if telemetry_id and flight_id:
                db.execute("UPDATE beacon_telemetry SET flight_id=? WHERE id=?", (flight_id, telemetry_id),
                           commit=False)
            _devices.touch(
                device["id"], seen_at=received_at, state=packet["state"],
                battery=packet["device"]["battery_percent"], signal_dbm=packet["network"]["signal_dbm"],
                network_type=packet["network"]["type"], firmware=packet["device"]["firmware"], ip=ip,
                telemetry_id=telemetry_id if not jump else None, first_seen=first_seen,
            )
            first_seen = False
            if point is not None and not jump:
                last = {"latitude": point["latitude"], "longitude": point["longitude"], "ts": ts_eff}
                device["last_telemetry_id"] = telemetry_id
            device["last_state"] = packet["state"]
            results.append({
                "packet_id": packet["packet_id"], "telemetry_id": telemetry_id, "duplicate": False,
                "flight_id": flight_id, "flags": flags,
                "event": live_event(device, packet, ts_eff, flight_id, telemetry_id, flags, received_at),
            })
    return results


def live_event(device: dict, packet: dict, ts_eff: str, flight_id: Optional[int],
               telemetry_id: Optional[int], flags: list, received_at: str) -> dict:
    """Charge diffusée sur la salle beacon:<id> (et renvoyée par /live)."""
    pos = packet["position"] or {}
    return {
        "device_id": device["id"],
        "device_uid": device["device_uid"],
        "drone_id": device.get("drone_id"),
        "flight_id": flight_id,
        "telemetry_id": telemetry_id,
        "latitude": pos.get("latitude"),
        "longitude": pos.get("longitude"),
        "relative_altitude_m": pos.get("relative_altitude_m"),
        "gnss_altitude_m": pos.get("gnss_altitude_m"),
        "speed_mps": pos.get("speed_mps"),
        "heading_deg": pos.get("heading_deg"),
        "state": packet["state"],
        "gnss_fix": packet["gnss"]["fix"],
        "satellites": packet["gnss"]["satellites"],
        "network_type": packet["network"]["type"],
        "signal_dbm": packet["network"]["signal_dbm"],
        "battery_percent": packet["device"]["battery_percent"],
        "flags": flags,
        "timestamp": ts_eff,
        "server_time": received_at,
    }
