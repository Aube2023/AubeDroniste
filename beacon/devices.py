"""Balises AubeBeacon : identité, jeton, association à un drone, journal.

Une balise appartient à un compte (`owner_user_id`) et peut être associée à
un appareil de sa flotte (`pilot_drones`). Toutes les fonctions de lecture
et d'écriture prennent le propriétaire en paramètre et l'appliquent dans la
requête : un compte ne voit et ne touche que ses balises. `owner_user_id=None`
lève la restriction (réservé aux vues administrateur).

Jeton : 32 octets aléatoires, remis UNE fois au pilote (pour le fichier
secrets.h du micrologiciel), puis seul son HMAC-SHA256 (clé dérivée du
secret de l'application) est conservé. Une fuite de la base ne donne donc
aucun jeton utilisable, et la vérification reste immédiate à chaque paquet.
"""
import fnmatch
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

import db
from config import SECRET_KEY

log = logging.getLogger("aubepilot.beacon.devices")

UID_PREFIX = "AUBE-BCN-"
# Suffixe : chiffres (série) ou lettres+chiffres (balises simulées « SIM001 »).
UID_RE = re.compile(r"^AUBE-BCN-[A-Z0-9]{3,12}$")
# Série automatique (`next_uid`) : AUBE-BCN- suivi d'exactement 6 chiffres.
SERIAL_DIGITS = 6
_SERIAL_GLOB = UID_PREFIX + "[0-9]" * SERIAL_DIGITS
TOKEN_BYTES = 32
LABEL_MAX = 80

_PEPPER = hashlib.sha256(b"aubebeacon-token|" + SECRET_KEY.encode("utf-8")).digest()


class DeviceError(ValueError):
    """Erreur métier (identifiant pris, drone étranger...) : message pour l'écran."""


def now_iso() -> str:
    """Horodatage serveur ISO 8601 UTC à la milliseconde (format des colonnes télémétrie)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def hash_token(token: str) -> str:
    return hmac.new(_PEPPER, token.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def normalize_uid(raw: str) -> str:
    """Identifiant saisi par le pilote → forme canonique, ou DeviceError."""
    uid = (raw or "").strip().upper()
    if uid and not uid.startswith(UID_PREFIX):
        uid = UID_PREFIX + uid
    if not UID_RE.match(uid):
        raise DeviceError("Identifiant invalide : AUBE-BCN- suivi de 3 à 12 lettres ou chiffres.")
    return uid


def _serial_top() -> int:
    """Plus grand numéro de la série déjà attribué, balises supprimées comprises (0 sinon)."""
    row = db.fetchone(
        "SELECT MAX(device_uid) AS top FROM ("
        "SELECT device_uid FROM beacon_devices WHERE device_uid GLOB ? "
        "UNION SELECT device_uid FROM beacon_retired_uids WHERE device_uid GLOB ?)",
        (_SERIAL_GLOB, _SERIAL_GLOB),
    )
    # Même longueur partout : le plus grand texte est le plus grand numéro.
    return int(row["top"][len(UID_PREFIX):]) if row and row["top"] else 0


def next_uid() -> str:
    """AUBE-BCN-000001, 000002... : suite du plus grand numéro de série
    automatique attribué, balises supprimées comprises (`beacon_retired_uids`).
    Un numéro n'est donc jamais réattribué : AubeLink peut encore l'associer à
    un drone si la dissociation n'a pas pu lui parvenir à la suppression.

    Seuls comptent les numéros de la série (exactement SERIAL_DIGITS
    chiffres). Une saisie manuelle (SIM001, numéro matériel de 12 chiffres
    tapé par erreur), gardée à jamais dans les numéros retirés une fois la
    balise supprimée, ne décale donc pas la série et ne la fait pas déborder
    de UID_RE. DeviceError quand la série est épuisée."""
    top = _serial_top()
    uid = f"{UID_PREFIX}{top + 1:0{SERIAL_DIGITS}d}"
    if top + 1 >= 10 ** SERIAL_DIGITS or not UID_RE.match(uid):
        raise DeviceError("Numérotation automatique épuisée : saisissez l'identifiant imprimé sur la balise.")
    return uid


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

_SELECT = """
SELECT d.*, p.brand AS drone_brand, p.model AS drone_model, p.category AS drone_category,
       t.latitude AS last_lat, t.longitude AS last_lng, t.relative_altitude_m AS last_alt_rel,
       t.speed_mps AS last_speed_mps, t.heading_deg AS last_heading_deg,
       t.satellites AS last_satellites, t.gnss_fix AS last_gnss_fix,
       t.flight_id AS last_flight_id, t.server_timestamp AS last_point_at,
       t.device_timestamp AS last_point_device_at,
       u.full_name AS owner_name, u.username AS owner_username, u.email AS owner_email,
       u.deleted_at AS owner_deleted_at
FROM beacon_devices d
LEFT JOIN pilot_drones p ON p.id = d.drone_id
LEFT JOIN beacon_telemetry t ON t.id = d.last_telemetry_id
JOIN users u ON u.id = d.owner_user_id
"""


def list_devices(owner_user_id: Optional[int]) -> list:
    where, params = ("WHERE d.owner_user_id=?", (owner_user_id,)) if owner_user_id else ("", ())
    rows = db.fetchall(_SELECT + where + " ORDER BY d.created_at DESC, d.id DESC", params)
    return [dict(r) for r in rows]


def get_device(device_id: int, owner_user_id: Optional[int]) -> Optional[dict]:
    if owner_user_id:
        row = db.fetchone(_SELECT + "WHERE d.id=? AND d.owner_user_id=?", (device_id, owner_user_id))
    else:
        row = db.fetchone(_SELECT + "WHERE d.id=?", (device_id,))
    return dict(row) if row else None


def get_by_uid(device_uid: str) -> Optional[dict]:
    row = db.fetchone("SELECT * FROM beacon_devices WHERE device_uid=?", (device_uid,))
    return dict(row) if row else None


def count_for_owner(owner_user_id: int) -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM beacon_devices WHERE owner_user_id=?", (owner_user_id,))
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------

def _owned_drone(owner_user_id: int, drone_id: Optional[int]) -> Optional[int]:
    """Le drone doit être dans la flotte du propriétaire, sinon DeviceError."""
    if not drone_id:
        return None
    row = db.fetchone("SELECT id FROM pilot_drones WHERE id=? AND pilot_user_id=?", (drone_id, owner_user_id))
    if not row:
        raise DeviceError("Ce drone n'est pas dans votre flotte.")
    return int(row["id"])


def create_device(owner_user_id: int, *, label: str = "", drone_id: Optional[int] = None,
                  device_uid: Optional[str] = None, user_id: Optional[int] = None,
                  ip: str = "") -> tuple:
    """Crée une balise et retourne (balise, jeton en clair). Le jeton n'est
    jamais réaffiché : le pilote le copie maintenant ou le régénère."""
    uid = normalize_uid(device_uid) if device_uid else next_uid()
    if not UID_RE.match(uid):       # jamais un identifiant que la balise ne pourrait pas présenter
        raise DeviceError("Identifiant invalide : AUBE-BCN- suivi de 3 à 12 lettres ou chiffres.")
    # Un numéro de la série saisi à la main ne peut être que celui d'une balise déjà
    # numérotée (réenregistrée). Au-delà du plus grand numéro attribué, il décalerait
    # la numérotation automatique pour toujours (il resterait dans les numéros retirés).
    if device_uid and fnmatch.fnmatchcase(uid, _SERIAL_GLOB) and int(uid[len(UID_PREFIX):]) > _serial_top():
        raise DeviceError(f"Le numéro {uid} n'a pas encore été attribué : laissez le champ vide "
                          "pour recevoir le numéro suivant, ou saisissez celui imprimé sur la balise.")
    if get_by_uid(uid):
        raise DeviceError(f"L'identifiant {uid} est déjà utilisé.")
    drone = _owned_drone(owner_user_id, drone_id)
    token = generate_token()
    cur = db.execute(
        "INSERT INTO beacon_devices (device_uid, owner_user_id, drone_id, label, token_hash, token_rotated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, owner_user_id, drone, (label or "").strip()[:LABEL_MAX] or None, hash_token(token), now_iso()),
    )
    device_id = cur.lastrowid
    log_event(device_id, "created", uid, ip=ip, user_id=user_id or owner_user_id)
    if drone:
        log_event(device_id, "attached", f"drone {drone}", ip=ip, user_id=user_id or owner_user_id)
    return get_device(device_id, None), token


def rotate_token(device_id: int, owner_user_id: Optional[int], *, user_id: Optional[int] = None,
                 ip: str = "") -> str:
    """Nouveau jeton (l'ancien cesse de fonctionner immédiatement)."""
    token = generate_token()
    params = [hash_token(token), now_iso(), now_iso(), device_id]
    sql = "UPDATE beacon_devices SET token_hash=?, token_rotated_at=?, updated_at=? WHERE id=?"
    if owner_user_id:
        sql += " AND owner_user_id=?"
        params.append(owner_user_id)
    if db.execute(sql, params).rowcount == 0:
        raise DeviceError("Balise introuvable.")
    log_event(device_id, "token_rotated", None, ip=ip, user_id=user_id)
    return token


def _update_owned(device_id: int, owner_user_id: Optional[int], assignments: str, values: tuple) -> None:
    sql = f"UPDATE beacon_devices SET {assignments}, updated_at=? WHERE id=?"
    params = list(values) + [now_iso(), device_id]
    if owner_user_id:
        sql += " AND owner_user_id=?"
        params.append(owner_user_id)
    if db.execute(sql, params).rowcount == 0:
        raise DeviceError("Balise introuvable.")


def set_enabled(device_id: int, owner_user_id: Optional[int], enabled: bool, *,
                user_id: Optional[int] = None, ip: str = "") -> None:
    _update_owned(device_id, owner_user_id, "enabled=?", (1 if enabled else 0,))
    log_event(device_id, "enabled" if enabled else "disabled", None, ip=ip, user_id=user_id)


def rename(device_id: int, owner_user_id: Optional[int], label: str) -> None:
    _update_owned(device_id, owner_user_id, "label=?", ((label or "").strip()[:LABEL_MAX] or None,))


def attach_drone(device_id: int, owner_user_id: int, drone_id: int, *,
                 user_id: Optional[int] = None, ip: str = "") -> None:
    drone = _owned_drone(owner_user_id, drone_id)
    if not drone:
        raise DeviceError("Choisissez un drone.")
    _update_owned(device_id, owner_user_id, "drone_id=?", (drone,))
    log_event(device_id, "attached", f"drone {drone}", ip=ip, user_id=user_id)


def detach_drone(device_id: int, owner_user_id: Optional[int], *, user_id: Optional[int] = None,
                 ip: str = "") -> None:
    _update_owned(device_id, owner_user_id, "drone_id=NULL", ())
    log_event(device_id, "detached", None, ip=ip, user_id=user_id)


def delete_device(device_id: int, owner_user_id: Optional[int]) -> bool:
    """Supprime la balise, ses vols et ses points (ON DELETE CASCADE). Son
    numéro rejoint `beacon_retired_uids` : `next_uid` ne le rendra plus. La
    saisie manuelle du même numéro reste permise (même balise physique
    réenregistrée)."""
    where = "WHERE id=?"
    params = [device_id]
    if owner_user_id:
        where += " AND owner_user_id=?"
        params.append(owner_user_id)
    db.execute("INSERT OR IGNORE INTO beacon_retired_uids (device_uid, retired_at) "
               f"SELECT device_uid, ? FROM beacon_devices {where}", [now_iso()] + params, commit=False)
    return db.execute(f"DELETE FROM beacon_devices {where}", params).rowcount > 0


# ---------------------------------------------------------------------------
# Authentification d'une balise (chemin chaud : un appel par paquet)
# ---------------------------------------------------------------------------

def authenticate(device_uid: str, token: str, ip: str = "") -> tuple:
    """(balise, None) si le jeton est le bon et la balise active, sinon
    (None, code) avec code ∈ {unknown, bad_token, disabled}. Un échec sur une
    balise existante est journalisé (jamais le jeton envoyé)."""
    if not device_uid or not token or not UID_RE.match(device_uid):
        return None, "unknown"
    device = get_by_uid(device_uid)
    if not device:
        return None, "unknown"
    if not hmac.compare_digest(device["token_hash"], hash_token(token)):
        log_event(device["id"], "auth_failed", "jeton refusé", ip=ip)
        return None, "bad_token"
    if not device["enabled"]:
        log_event(device["id"], "auth_failed", "balise désactivée", ip=ip)
        return None, "disabled"
    return device, None


def touch(device_id: int, *, seen_at: str, state: Optional[str], battery: Optional[int],
          signal_dbm: Optional[int], network_type: Optional[str], firmware: Optional[str],
          ip: str, telemetry_id: Optional[int], first_seen: bool) -> None:
    """Dernier signe de vie et derniers relevés (appelé après chaque paquet accepté)."""
    db.execute(
        "UPDATE beacon_devices SET last_seen_at=?, last_ip=?, last_state=COALESCE(?, last_state), "
        "last_battery=COALESCE(?, last_battery), last_signal_dbm=COALESCE(?, last_signal_dbm), "
        "last_network_type=COALESCE(?, last_network_type), firmware_version=COALESCE(?, firmware_version), "
        "last_telemetry_id=COALESCE(?, last_telemetry_id), updated_at=? WHERE id=?",
        (seen_at, ip or None, state, battery, signal_dbm, network_type, firmware, telemetry_id, seen_at, device_id),
        commit=False,
    )
    if first_seen:
        log_event(device_id, "first_seen", firmware or None, ip=ip, commit=False)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def log_event(device_id: Optional[int], kind: str, detail: Optional[str], *, ip: str = "",
              user_id: Optional[int] = None, commit: bool = True) -> None:
    try:
        db.execute(
            "INSERT INTO beacon_device_events (device_id, kind, detail, ip, user_id) VALUES (?, ?, ?, ?, ?)",
            (device_id, kind[:32], (detail or "")[:200] or None, (ip or "")[:64] or None, user_id),
            commit=commit,
        )
    except Exception as exc:   # le journal n'interrompt jamais le flux
        log.error("journal balise : %s", exc)


def list_events(device_id: int, limit: int = 30) -> list:
    rows = db.fetchall(
        "SELECT * FROM beacon_device_events WHERE device_id=? ORDER BY id DESC LIMIT ?",
        (device_id, limit),
    )
    return [dict(r) for r in rows]
