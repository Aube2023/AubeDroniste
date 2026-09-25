"""Client AubeLink : le drone AubeLink qui porte chaque balise AubeBeacon.

AubeLink (réseau de liaison de données drone-sol, dépôt `AubeLink/`) tient
SEUL l'association balise ↔ drone AubeLink : AubePilot n'ajoute aucune
colonne et ne garde qu'un cache mémoire. La clé de jointure est le
`device_uid` de la balise (AUBE-BCN-…), qui est le `beaconId` d'AubeLink,
normalisé des deux côtés par la même règle (`devices.normalize_uid`).
Cette association est indépendante de celle d'une balise à un drone du
catalogue AubePilot (`pilot_drones`).

Trois routes AubeLink seulement, de serveur à serveur :

    GET    /api/v1/beacon-status              drones du délégué ; pour chaque drone associé,
                                              5 alertes actives et 3 derniers messages au plus
    PUT    /api/v1/drones/{droneId}/beacon    associer, corps {"beaconId": "AUBE-BCN-…"}
    DELETE /api/v1/drones/{droneId}/beacon    dissocier (204)

Chaque appel porte la clé d'intégration (`Authorization: Bearer alp_…`,
portées fleet:read, beacon:write et act_as_user) et `X-Acting-User` :
l'adresse @aubemail.com du PROPRIÉTAIRE de la balise, jamais celle de
l'administrateur qui regarde. AubeLink limite alors tout aux drones dont ce
compte est propriétaire, même s'il est administrateur chez AubeLink.

Robustesse. `_request` est la seule fonction réseau (remplacée en test). Une
lecture réussie est gardée AUBELINK_CACHE_S secondes par délégué. Une panne
met le client en pause, sans aucun appel de lecture : AUBELINK_FAIL_CACHE_S
après une erreur réseau, un délai dépassé ou un 5xx ; `Retry-After` (60 s à
défaut) après un 429 ; 300 s après une clé refusée (401). Un 403 ou un 404
en lecture est gardé en cache négatif pour ce délégué. Les actions (associer,
dissocier, lecture fraîche) ignorent cache et pause. Cache et pause sont
propres à chaque worker gunicorn.

Pages (`panels`) : un appel part toujours avec son délai entier, et seulement
si le budget de temps restant le couvre ; sinon le propriétaire est servi au
sondage suivant (`deferred`). Un délai raccourci au budget qui expirerait
passerait pour une panne et poserait la pause de tout le worker, alors
qu'AubeLink répond. Par worker, MAX_CONCURRENT_READS lectures au plus
attendent AubeLink en même temps ; au-delà, `deferred` sans attendre, pour
qu'un AubeLink lent n'occupe jamais tous les fils de gunicorn.

Le journal ne contient jamais la clé : `aubelink.request_failed` porte la
méthode, le chemin, le statut, le code AubeLink et la durée.

Ce que les pages reçoivent (`panels`) est une liste blanche : jamais
`ownerId`, l'UUID du drone, sa télémétrie (donc aucune coordonnée), la clé ni
l'adresse du délégué. Les textes aussi : le compte rendu de position
(catégorie POSITION_REPORT, « POSITION 45.50170N 073.56730W … ») perd son
texte, et toute coordonnée écrite en clair dans un message ou une alerte est
masquée.

Compte supprimé (`users.deleted_at`) : AubePilot n'agit plus au nom de ce
propriétaire, AubeLink n'est pas appelé pour ses balises.

TODO (hors V1) : temps réel AubeLink (une clé d'intégration n'obtient pas de
ticket /ws : la page sonde toutes les 15 s) ; correspondance des vols
AubeBeacon et AubeLink ; portée messages:write (bouton STATUS REQUEST).
"""
import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone
from typing import Optional

import auth
import config

log = logging.getLogger("aubepilot.beacon.aubelink")

USER_AGENT = "AubePilot"
STATUS_PATH = "/api/v1/beacon-status"
# Identifiant de drone AubeLink : motif ADLP DRONE_ID_PATTERN (AE-XR-001).
DRONE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,30}[A-Z0-9]$")
MAX_BODY_BYTES = 2 * 1024 * 1024     # beacon-status : 200 drones au plus
CACHE_MAX = 500                      # délégués gardés en mémoire (par worker)
KEY_REJECTED_PAUSE_S = 300           # clé refusée (401) : AubeLink n'est plus sollicité
RATE_LIMITED_DEFAULT_S = 60          # 429 sans Retry-After exploitable
CALL_MARGIN_S = 0.2                  # un budget de `panels` couvre toujours un appel entier et cette marge
MAX_CONCURRENT_READS = 2             # lectures de `panels` qui attendent AubeLink en même temps (par worker)
MAX_ALERTS, MAX_MESSAGES = 5, 3

# Codes d'erreur côté AubePilot : AubeLinkError.code et `reason` de la route JSON.
UNREACHABLE = "unreachable"          # réseau, délai, 5xx, réponse illisible
RATE_LIMITED = "rate_limited"        # 429
MISCONFIGURED = "misconfigured"      # 401 : clé refusée, ou fonction non configurée
REFUSED = "refused"                  # 403 (portée, délégué), 404 en lecture, autre 4xx
NOT_FOUND = "not_found"              # 404 sur PUT : drone absent de ce compte
NOT_LINKED = "not_linked"            # 404 sur DELETE : aucune balise sur ce drone
ALREADY_LINKED = "already_linked"    # 409 BEACON_ALREADY_LINKED
INVALID = "invalid"                  # 400 INVALID_BEACON_ID, INVALID_DRONE_ID, VALIDATION_FAILED
DEFERRED = "deferred"                # budget de temps ou lectures simultanées épuisés : servi au sondage suivant
OWNER_DELETED = "owner_deleted"      # compte du propriétaire supprimé : aucun appel

# Catégories de message dont le texte est une position (apps/node/src/reports.ts
# d'AubeLink) : seule la catégorie est montrée.
POSITION_CATEGORIES = frozenset({"POSITION_REPORT"})
# Coordonnées écrites en clair dans un texte : degrés décimaux (45.50170N,
# -73.5673, 45,50170) ou degrés et minutes (45°30'06"N, 45° 30.1' N).
_COORD_RE = re.compile(
    r"[-+]?\d{1,3}(?:\.\d{3,}|,\d{4,})(?:\s*°)?(?:\s*[NSEOW](?![A-Za-z]))?"
    r"|\d{1,3}\s*°\s*\d{1,2}(?:[.,]\d+)?\s*['′](?:\s*\d{1,2}(?:[.,]\d+)?\s*[\"″])?(?:\s*[NSEOW](?![A-Za-z]))?"
)
COORD_MASK = "[…]"

Resp = namedtuple("Resp", "status data retry_after")


class AubeLinkError(Exception):
    """Échec d'un appel AubeLink. `code` : un des codes ci-dessus ; `status` :
    statut HTTP (0 sans réponse exploitable) ; `details` : `error.details`
    d'AubeLink (par exemple {"droneId": …} sur un 409) ; `remote_code` : le
    code d'erreur d'AubeLink (SCOPE_MISSING, BEACON_LINK_VIA_AUBEPILOT…)."""

    def __init__(self, code: str, status: int = 0, details: Optional[dict] = None,
                 remote_code: Optional[str] = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = dict(details or {})
        self.remote_code = remote_code

    def copy(self) -> "AubeLinkError":
        return AubeLinkError(self.code, self.status, self.details, self.remote_code)


# Horloge monotone, remplaçable en test (pauses, durée de vie du cache).
_now = time.monotonic

_lock = threading.Lock()
_cache: dict = {}                      # délégué -> (expire, rangé à, données | AubeLinkError)
_down: Optional[tuple] = None          # (jusqu'à, AubeLinkError) : pause après une panne
# Jetons des lectures de `panels` (pris sans attendre : sans jeton, `deferred`).
_reads = threading.BoundedSemaphore(MAX_CONCURRENT_READS)


# ---------------------------------------------------------------------------
# Configuration (relue à chaque appel : monkeypatch en test, pas de copie)
# ---------------------------------------------------------------------------

def enabled() -> bool:
    """Fonction active : URL http(s) posée et clé d'intégration `alp_…`."""
    url = config.AUBELINK_URL or ""
    return url.startswith(("http://", "https://")) and (config.AUBELINK_KEY or "").startswith("alp_")


def _timeout_s() -> float:
    return max(0.1, min(30.0, (config.AUBELINK_TIMEOUT_MS or 2500) / 1000))


def acting_email(username: str, email: Optional[str] = None) -> str:
    """Adresse déléguée (X-Acting-User) : <local>@aubemail.com du propriétaire.

    Tirée de l'identifiant, qui est la partie locale AubeMail et ne change
    jamais (`users.email` en est la copie normalisée, sauf après une
    suppression de compte, qui le réécrit en deleted-<id>@invalid.local).
    `email` ne sert qu'à défaut d'identifiant."""
    local = (username or "").strip()
    if local:
        return auth.normalize_email(local, None)
    if email and "@" in email and email.split("@", 1)[0].strip():
        return auth.normalize_email("", email)
    return ""


def device_acting(device: dict) -> Optional[str]:
    """Délégué pour une balise (ligne de devices._SELECT) : son propriétaire,
    jamais l'administrateur qui regarde. None si le compte du propriétaire
    est supprimé : AubePilot n'agit plus en son nom."""
    if device.get("owner_deleted_at"):
        return None
    return acting_email(device.get("owner_username") or "", device.get("owner_email")) or None


def public_url() -> Optional[str]:
    url = config.AUBELINK_PUBLIC_URL or ""
    return url if url.startswith(("http://", "https://")) else None


# ---------------------------------------------------------------------------
# Réseau
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Une redirection n'est jamais suivie : elle emporterait la clé ailleurs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Ni redirection ni mandataire de l'environnement (http_proxy) : la clé ne
# part que vers AUBELINK_URL.
_opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))


def _request(method: str, path: str, acting: str, body: Optional[dict] = None,
             timeout: Optional[float] = None) -> Resp:
    """Seul appel réseau du module (remplacé en test).

    Rend Resp(status, data, retry_after). Une réponse d'erreur HTTP est lue
    comme du JSON ; une erreur de réseau, un délai dépassé ou une réponse qui
    n'est pas du JSON donnent status 0. `path` arrive avec ses segments déjà
    encodés (`_seg`)."""
    if not acting:
        raise ValueError("X-Acting-User est obligatoire")
    headers = {
        "Authorization": "Bearer " + (config.AUBELINK_KEY or ""),
        "X-Acting-User": acting,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(config.AUBELINK_URL + path, data=data, method=method, headers=headers)
    try:
        try:
            with _opener.open(req, timeout=timeout or _timeout_s()) as resp:
                status = resp.status
                retry = resp.headers.get("Retry-After")
                raw = resp.read(MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry = exc.headers.get("Retry-After") if exc.headers else None
            raw = exc.read(MAX_BODY_BYTES + 1) if exc.fp else b""
            exc.close()
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return Resp(0, None, None)
    if len(raw) > MAX_BODY_BYTES:
        return Resp(0, None, None)
    if not raw.strip():
        return Resp(status, None, retry)          # 204 de DELETE
    try:
        return Resp(status, json.loads(raw.decode("utf-8")), retry)
    except (ValueError, UnicodeDecodeError):
        return Resp(0, None, None)


def _seg(value: str) -> str:
    """Segment de chemin encodé (un identifiant ne sort jamais de son segment)."""
    return urllib.parse.quote(str(value), safe="")


def _call(method: str, path: str, acting: str, body: Optional[dict] = None) -> tuple:
    """(Resp, durée en ms). Fonction inactive = aucun appel. Le délai est
    toujours le délai entier (`_timeout_s`) : un statut 0 veut donc dire
    qu'AubeLink n'a pas répondu à temps, jamais que l'appelant était pressé."""
    if not enabled():
        raise AubeLinkError(MISCONFIGURED)
    t0 = _now()
    resp = _request(method, path, acting, body)
    return resp, int(max(0.0, _now() - t0) * 1000)


def _retry_after_s(value) -> int:
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return RATE_LIMITED_DEFAULT_S
    return max(1, min(seconds, 3600))


def _fail(resp: Resp, method: str, path: str, ms: int) -> AubeLinkError:
    """Réponse en échec → AubeLinkError, pause éventuelle, journal (sans la clé)."""
    global _down
    status = resp.status
    err = resp.data.get("error") if isinstance(resp.data, dict) else None
    err = err if isinstance(err, dict) else {}
    remote = err.get("code") if isinstance(err.get("code"), str) else None
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    if status == 0 or status >= 500 or status < 400:
        code = UNREACHABLE
    elif status == 429:
        code = RATE_LIMITED
    elif status == 401:
        code = MISCONFIGURED
    elif status == 404 and method == "PUT":
        code = NOT_FOUND
    elif status == 404 and method == "DELETE":
        code = NOT_LINKED
    elif status == 409:
        code = ALREADY_LINKED
    elif status == 400:
        code = INVALID
    else:
        code = REFUSED
    error = AubeLinkError(code, status, details, (remote or "")[:64] or None)

    pause = None
    if code == UNREACHABLE:
        pause = max(0, config.AUBELINK_FAIL_CACHE_S)
    elif code == RATE_LIMITED:
        pause = _retry_after_s(resp.retry_after)
    elif code == MISCONFIGURED:
        pause = KEY_REJECTED_PAUSE_S
    if pause:
        with _lock:
            _down = (_now() + pause, error.copy())

    if code in (MISCONFIGURED, REFUSED):
        level = logging.ERROR
    elif code in (UNREACHABLE, RATE_LIMITED):
        level = logging.WARNING
    else:                                   # réponse métier attendue (409, 404…)
        level = logging.INFO
    log.log(level, "aubelink.request_failed method=%s path=%s status=%s code=%s ms=%d",
            method, path, status, remote or code, ms)
    if code == MISCONFIGURED:
        log.error("aubelink.key_rejected status=%s pause_s=%d", status, KEY_REJECTED_PAUSE_S)
    return error


def _resume() -> None:
    """Un appel a réussi : fin de la pause."""
    global _down
    with _lock:
        _down = None


def _paused(now: float) -> Optional[AubeLinkError]:
    with _lock:
        if _down and _down[0] > now:
            return _down[1].copy()
    return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_get(acting: str, now: float):
    with _lock:
        hit = _cache.get(acting)
        if hit and hit[0] > now:
            return hit[2]
    return None


def _cache_put(acting: str, value, ttl_s: float) -> None:
    now = _now()
    with _lock:
        if acting not in _cache and len(_cache) >= CACHE_MAX:
            for key in [k for k, v in _cache.items() if v[0] <= now]:
                del _cache[key]
            while len(_cache) >= CACHE_MAX:
                del _cache[min(_cache, key=lambda k: _cache[k][1])]
        _cache[acting] = (now + max(0.0, ttl_s), now, value)


def _stored_at(acting: str) -> float:
    """Heure de la dernière lecture gardée pour ce délégué (-inf s'il n'a jamais été lu)."""
    with _lock:
        hit = _cache.get(acting)
    return hit[1] if hit else float("-inf")


def _invalidate(acting: str) -> None:
    with _lock:
        _cache.pop(acting, None)


def clear_cache() -> None:
    """Oublie cache et pause (tests)."""
    global _down
    with _lock:
        _cache.clear()
        _down = None


def _from_cache(value) -> dict:
    if isinstance(value, AubeLinkError):
        raise value.copy()
    return value


# ---------------------------------------------------------------------------
# Appels
# ---------------------------------------------------------------------------

def _valid_status(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("drones"), list)


def beacon_status(acting: str, fresh: bool = False) -> dict:
    """BeaconStatusResponse d'AubeLink pour ce délégué ({serverTime, drones:
    [{drone, alerts, lastMessages}], truncated}). Lève AubeLinkError.
    `fresh=True` ignore cache et pause (avant une action)."""
    if not acting:
        raise ValueError("X-Acting-User est obligatoire")
    if not fresh:
        now = _now()
        hit = _cache_get(acting, now)
        if hit is not None:
            return _from_cache(hit)
        down = _paused(now)
        if down:
            raise down
    resp, ms = _call("GET", STATUS_PATH, acting)
    if resp.status == 200 and _valid_status(resp.data):
        _resume()
        _cache_put(acting, resp.data, config.AUBELINK_CACHE_S)
        return resp.data
    error = _fail(resp, "GET", STATUS_PATH, ms)
    if error.status in (403, 404):
        _cache_put(acting, error.copy(), config.AUBELINK_FAIL_CACHE_S)
    raise error


def link(acting: str, drone_id: str, uid: str) -> dict:
    """Associe la balise `uid` au drone AubeLink `drone_id` du délégué.
    Rend le BeaconLinkItem d'AubeLink ; lève AubeLinkError."""
    path = f"/api/v1/drones/{_seg(drone_id)}/beacon"
    try:
        resp, ms = _call("PUT", path, acting, {"beaconId": uid})
    finally:
        _invalidate(acting)
    if resp.status in (200, 201):
        _resume()
        return resp.data if isinstance(resp.data, dict) else {}
    raise _fail(resp, "PUT", path, ms)


def unlink(acting: str, drone_id: str) -> None:
    """Retire la balise du drone AubeLink `drone_id` (204) ; lève AubeLinkError."""
    path = f"/api/v1/drones/{_seg(drone_id)}/beacon"
    try:
        resp, ms = _call("DELETE", path, acting)
    finally:
        _invalidate(acting)
    if 200 <= resp.status < 300:
        _resume()
        return
    raise _fail(resp, "DELETE", path, ms)


def beacon_on_drone(acting: str, drone_id: str) -> Optional[str]:
    """Balise que porte le drone AubeLink `drone_id` du délégué, d'après une
    lecture fraîche ; None si le drone n'en porte aucune ou n'est pas dans la
    réponse (drone inconnu : le PUT répondra 404 ; au-delà des 200 drones
    d'une réponse tronquée, rien n'est vérifiable). Lève AubeLinkError.

    Le PUT d'AubeLink remplace sans erreur la balise d'un drone qui en porte
    déjà une : l'association vérifie donc d'abord que le drone est libre,
    sans se fier au cache ni au sélecteur de la page (autre onglet,
    association posée entre-temps dans AubeLink, formulaire fabriqué)."""
    for item in _items(beacon_status(acting, fresh=True)):
        if item["drone"]["droneId"] == drone_id:
            return _s(item["drone"].get("beaconId"), 32)
    return None


def unlink_beacon(acting: str, uid: str) -> Optional[str]:
    """Retrouve par une lecture fraîche le drone du délégué qui porte `uid`,
    puis le dissocie. Rend son droneId, ou None si la balise n'est associée
    à aucun drone de ce compte. Lève AubeLinkError."""
    data = beacon_status(acting, fresh=True)
    for item in data.get("drones") or []:
        drone = item.get("drone") if isinstance(item, dict) else None
        if not isinstance(drone, dict) or drone.get("beaconId") != uid:
            continue
        drone_id = drone.get("droneId")
        if not isinstance(drone_id, str) or not DRONE_ID_RE.match(drone_id):
            continue
        try:
            unlink(acting, drone_id)
        except AubeLinkError as exc:
            if exc.code == NOT_LINKED:          # retirée entre la lecture et l'appel
                return None
            raise
        return drone_id
    return None


def flash_key(error: AubeLinkError) -> tuple:
    """(clé i18n, variables) du message à montrer pour cet échec."""
    if error.code == RATE_LIMITED:
        return "aubelink.busy", {}
    if error.code == REFUSED:
        return "aubelink.err.refused", {"code": error.remote_code or str(error.status)}
    if error.code == NOT_FOUND:
        return "aubelink.err.drone_not_found", {}
    if error.code == NOT_LINKED:
        return "aubelink.err.not_linked", {}
    if error.code == ALREADY_LINKED:
        other = error.details.get("droneId")
        if isinstance(other, str) and DRONE_ID_RE.match(other):
            return "aubelink.err.taken_by", {"drone": other}
        return "aubelink.err.taken", {}
    if error.code == INVALID:
        return "aubelink.err.invalid_drone", {}
    return "aubelink.err.unreachable", {}   # injoignable, clé refusée, non configuré


# ---------------------------------------------------------------------------
# Vues pour les pages (liste blanche)
# ---------------------------------------------------------------------------

def _s(value, limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) and value else None


def _masked(value, limit: int) -> Optional[str]:
    """Texte libre sans coordonnée en clair (masquée avant la coupe, pour
    qu'une coordonnée coupée en fin de texte ne passe pas)."""
    if not isinstance(value, str) or not value:
        return None
    return _s(_COORD_RE.sub(COORD_MASK, value), limit)


def _message_text(m: dict) -> Optional[str]:
    """Texte d'un message : aucun pour un compte rendu de position (seule sa
    catégorie est montrée), masqué pour les autres."""
    if m.get("category") in POSITION_CATEGORIES:
        return None
    return _masked(m.get("text"), 300)


def _int(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def _urls(drone_id: Optional[str], flight_id: Optional[str]) -> Optional[dict]:
    base = public_url()
    if not base or not drone_id:
        return None
    return {"drone": f"{base}/app/drones/{_seg(drone_id)}",
            "flight": f"{base}/app/vols/{_seg(flight_id)}" if flight_id else None}


def _drone_view(item: dict) -> dict:
    drone = item["drone"]
    telemetry = drone.get("telemetry") if isinstance(drone.get("telemetry"), dict) else None
    view = {
        "droneId": _s(drone.get("droneId"), 32),
        "name": _s(drone.get("name"), 120),
        "status": _s(drone.get("status"), 32),
        "enabled": bool(drone.get("enabled")),
        "linkState": _s(drone.get("linkState"), 32),
        "flightState": _s(drone.get("flightState"), 32),
        "gnssState": _s(drone.get("gnssState"), 32),
        "lastSeen": _s(drone.get("lastSeen"), 40),
        # TODO : AubeLink rend 0 quand la mesure manque (question ouverte F6).
        "batteryPercent": _int(telemetry.get("batteryPercent")) if telemetry else None,
        "activeAlerts": _int(drone.get("activeAlerts")) or 0,
        "activeFlightId": _s(drone.get("activeFlightId"), 64),
    }
    alerts = [
        {"code": _s(a.get("code"), 40), "severity": _s(a.get("severity"), 16),
         "message": _masked(a.get("message"), 300), "timestamp": _s(a.get("timestamp"), 40)}
        for a in (item.get("alerts") or [])[:MAX_ALERTS] if isinstance(a, dict)
    ]
    # Ni `fields` (latitude, longitude d'un POSITION_REPORT) ni `data` d'alerte.
    messages = [
        {"direction": _s(m.get("direction"), 16), "kind": _s(m.get("kind"), 16),
         "text": _message_text(m), "category": _s(m.get("category"), 40),
         "command": _s(m.get("command"), 40), "status": _s(m.get("status"), 16),
         "createdAt": _s(m.get("createdAt"), 40)}
        for m in (item.get("lastMessages") or [])[:MAX_MESSAGES] if isinstance(m, dict)
    ]
    return {"linked": True, "drone": view, "alerts": alerts, "messages": messages,
            "urls": _urls(view["droneId"], view["activeFlightId"])}


def _items(data: dict) -> list:
    """Entrées valides de beacon-status (drone avec un identifiant ADLP)."""
    out = []
    for item in data.get("drones") or []:
        drone = item.get("drone") if isinstance(item, dict) else None
        if isinstance(drone, dict) and isinstance(drone.get("droneId"), str) and DRONE_ID_RE.match(drone["droneId"]):
            out.append(item)
    return out


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def panels(devices: list, budget_s: float) -> dict:
    """Vue AubeLink des balises `devices` (lignes de devices.list_devices).

    Regroupe les balises par propriétaire et lit beacon-status pour chacun,
    tant que le budget de temps couvre un appel entier (une lecture en cache
    ne compte pas) et qu'un jeton de lecture est libre ; les autres passent en
    `deferred`, sans effet sur `available`. Rend {available, reason,
    server_time, beacons: {id de balise: vue}, drones: {id du propriétaire:
    [{droneId, name, beaconId}]}}.
    Un propriétaire dont le compte est supprimé n'est pas lu (`owner_deleted`,
    sans effet sur `available`)."""
    call_s = _timeout_s()
    budget = max(0.0, budget_s)
    if budget:
        # Un budget couvre toujours au moins un appel entier : sinon un
        # AUBELINK_TIMEOUT_MS plus long que le budget laisserait tout en attente.
        budget = max(budget, call_s + CALL_MARGIN_S)
    deadline = _now() + budget
    owners: dict = {}
    for d in devices:
        owners.setdefault(d["owner_user_id"], (device_acting(d), []))[1].append(d)

    results: dict = {}
    pending = []
    now = _now()
    for owner_id, (acting, _) in owners.items():
        if acting is None:
            results[owner_id] = OWNER_DELETED
            continue
        hit = _cache_get(acting, now)
        if hit is None:
            pending.append(owner_id)
        else:
            results[owner_id] = hit
    # Les moins récemment lus d'abord (jamais lus en tête) : quand le budget ne couvre
    # pas tout le monde, chaque sondage reprend là où le précédent s'est arrêté, et
    # aucun propriétaire ne reste « deferred » indéfiniment en fin de liste.
    pending.sort(key=lambda owner_id: _stored_at(owners[owner_id][0]))
    for owner_id in pending:
        # Jamais de délai raccourci au budget restant : s'il expirait, le
        # statut 0 passerait pour une panne d'AubeLink et poserait la pause de
        # tout le worker. Ce propriétaire attend le sondage suivant.
        if deadline - _now() < call_s:
            results[owner_id] = DEFERRED
            continue
        # Lectures simultanées bornées par worker : sans jeton libre, pas
        # d'attente (un AubeLink lent n'occupe pas tous les fils de gunicorn).
        if not _reads.acquire(blocking=False):
            results[owner_id] = DEFERRED
            continue
        try:
            results[owner_id] = beacon_status(owners[owner_id][0])
        except AubeLinkError as exc:
            results[owner_id] = exc
        finally:
            _reads.release()

    available, reason = True, None
    beacons: dict = {}
    choices: dict = {}
    for owner_id, (_, owned) in owners.items():
        res = results[owner_id]
        if isinstance(res, dict):
            items = _items(res)
            choices[str(owner_id)] = [
                {"droneId": it["drone"]["droneId"], "name": _s(it["drone"].get("name"), 120),
                 "beaconId": _s(it["drone"].get("beaconId"), 32)} for it in items
            ]
            by_uid = {it["drone"].get("beaconId"): it for it in items if it["drone"].get("beaconId")}
            for d in owned:
                hit = by_uid.get(d["device_uid"])
                view = _drone_view(hit) if hit else {"linked": False}
                beacons[str(d["id"])] = {"uid": d["device_uid"], **view}
            continue
        code = res if isinstance(res, str) else res.code
        if code not in (DEFERRED, OWNER_DELETED):
            available = False
            reason = reason or code
        for d in owned:
            beacons[str(d["id"])] = {"uid": d["device_uid"], "linked": None, "reason": code}
    return {"available": available, "reason": reason, "server_time": _iso_now(),
            "beacons": beacons, "drones": choices}
