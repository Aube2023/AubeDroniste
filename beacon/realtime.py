"""Temps réel AubeBeacon côté AubePilot : publication vers le hub et tickets.

Le hub WebSocket (AubeBeacon/server/websocket/hub.py) est un processus à
part : gunicorn (2 workers × 4 fils bloquants) ne peut pas tenir des
connexions longues, et un paquet reçu par un worker doit atteindre les
navigateurs connectés à l'autre. AubePilot ne fait donc que :

1. publier chaque événement au hub par un POST local (127.0.0.1:5137),
   depuis une file et un fil d'arrière-plan : l'ingestion répond à la balise
   sans attendre le hub, et un hub arrêté ne casse rien (la carte se rabat
   sur le sondage) ;
2. signer des tickets de connexion : le navigateur demande un ticket
   (session AubePilot), le présente au hub, qui vérifie la signature HMAC et
   n'abonne le client qu'aux salles listées. Permissions calculées ici, où
   vivent les comptes ; le hub n'a besoin que du secret partagé.

Format d'un ticket : base64url(JSON) + "." + base64url(HMAC-SHA256(secret, base64url(JSON)))
JSON : {"u": user_id, "r": ["beacon:12", ...] ou "*", "exp": unix, "n": nonce}
Le hub porte la même vérification (une trentaine de lignes, sans dépendance) ;
un vecteur de test partagé vit dans AubeBeacon/docs/api/ticket-vector.json.
"""
import base64
import hashlib
import hmac
import json
import logging
import queue
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from config import BEACON_HUB_PUBLIC_URL, BEACON_HUB_SECRET, BEACON_HUB_URL, BEACON_TICKET_TTL_S

log = logging.getLogger("aubepilot.beacon.realtime")

EVENT_TELEMETRY = "drone:telemetry"
EVENT_DEVICE = "drone:device"        # changements de balise (association, désactivation)

_QUEUE_MAX = 2000
_queue: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAX)
_worker_lock = threading.Lock()
_worker: Optional[threading.Thread] = None
_dropped = 0


def enabled() -> bool:
    return bool(BEACON_HUB_SECRET)


def public_url(page_host: str, secure: bool) -> str:
    """Adresse WebSocket annoncée au navigateur."""
    if BEACON_HUB_PUBLIC_URL:
        return BEACON_HUB_PUBLIC_URL
    return ("wss://" if secure else "ws://") + page_host + "/ws/beacon"


def room_for_device(device_id: int) -> str:
    return f"beacon:{int(device_id)}"


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign_ticket(payload: dict, secret: str) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + _b64(sig)


def verify_ticket(ticket: str, secret: str, now: Optional[float] = None) -> Optional[dict]:
    """Charge du ticket si la signature est bonne et qu'il n'est pas expiré, sinon None."""
    try:
        body, sig = ticket.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            return None
        payload = json.loads(_unb64(body).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or (now or time.time()) > float(payload.get("exp", 0)):
        return None
    return payload


def make_ticket(user_id: int, rooms, *, ttl_s: int = BEACON_TICKET_TTL_S) -> str:
    """`rooms` : liste de salles autorisées, ou "*" (administrateur)."""
    payload = {"u": int(user_id), "r": rooms if rooms == "*" else sorted(set(rooms)),
               "exp": int(time.time()) + int(ttl_s), "n": secrets.token_hex(6)}
    return sign_ticket(payload, BEACON_HUB_SECRET)


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------

def _post(room: str, event: str, data: dict) -> bool:
    body = json.dumps({"room": room, "event": event, "data": data}, default=str).encode("utf-8")
    req = urllib.request.Request(
        BEACON_HUB_URL + "/publish", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Beacon-Key": BEACON_HUB_SECRET},
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("hub injoignable : %s", exc)
        return False


def _run():
    while True:
        room, event, data = _queue.get()
        try:
            _post(room, event, data)
        except Exception as exc:       # jamais de mort du fil de publication
            log.warning("publication temps réel : %s", exc)
        finally:
            _queue.task_done()


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="aubebeacon-publish", daemon=True)
            _worker.start()


def publish(room: str, event: str, data: dict) -> bool:
    """Dépose l'événement dans la file (non bloquant). False si le temps réel
    est désactivé ou si la file déborde (le hub est en retard : on jette,
    la carte rattrapera par sondage)."""
    global _dropped
    if not enabled():
        return False
    _ensure_worker()
    try:
        _queue.put_nowait((room, event, data))
        return True
    except queue.Full:
        _dropped += 1
        if _dropped % 100 == 1:
            log.warning("file de publication pleine (%d événements jetés)", _dropped)
        return False


def publish_telemetry(event: dict) -> bool:
    return publish(room_for_device(event["device_id"]), EVENT_TELEMETRY, event)


def publish_device(device_id: int, data: dict) -> bool:
    return publish(room_for_device(device_id), EVENT_DEVICE, dict(data, device_id=int(device_id)))


def flush(timeout: float = 2.0) -> None:
    """Attend que la file soit vidée (tests, scripts)."""
    deadline = time.time() + timeout
    while not _queue.empty() and time.time() < deadline:
        time.sleep(0.02)
