"""Jeton de lecture de la flotte : AubeControl (téléphone ou PC) affiche les
drones d'un pilote en direct avec lui.

Un seul jeton par pilote, créé ou remplacé sur la page des balises, montré une
fois puis gardé haché (même HMAC que les jetons de balise). Il ne permet que
deux lectures, toujours limitées aux balises de son propriétaire, même pour un
administrateur : GET /api/v1/beacon/live et GET /api/v1/beacon/flights/<id>/track
(cf. beacon/api.py, `_require_reader`). Il n'envoie rien, ne commande rien, et
n'ouvre ni la page, ni le hub temps réel, ni AubeLink.

Préfixe « apr_ » : AubeControl et un relecteur reconnaissent le jeton, et un
jeton de balise collé par erreur est refusé sans requête en base.
"""
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import db

from . import devices as _devices

PREFIX = "apr_"
TOKEN_BYTES = 32
# last_used_at : une écriture par minute au plus (AubeControl lit toutes les 3 s).
TOUCH_EVERY_S = 60


def generate() -> str:
    return PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def get(owner_user_id: int) -> Optional[dict]:
    row = db.fetchone("SELECT id, owner_user_id, created_at, last_used_at FROM beacon_read_tokens "
                      "WHERE owner_user_id=?", (owner_user_id,))
    return dict(row) if row else None


def create(owner_user_id: int) -> str:
    """Crée le jeton du pilote ou remplace l'ancien, qui cesse aussitôt de marcher."""
    token = generate()
    now = _devices.now_iso()
    db.execute("DELETE FROM beacon_read_tokens WHERE owner_user_id=?", (owner_user_id,))
    db.execute("INSERT INTO beacon_read_tokens (owner_user_id, token_hash, created_at) VALUES (?, ?, ?)",
               (owner_user_id, _devices.hash_token(token), now))
    return token


def revoke(owner_user_id: int) -> bool:
    had = get(owner_user_id) is not None
    db.execute("DELETE FROM beacon_read_tokens WHERE owner_user_id=?", (owner_user_id,))
    return had


def authenticate(token: str) -> Optional[dict]:
    """Compte du propriétaire (même forme que g.user), ou None. Un compte supprimé ne lit plus rien."""
    if not token or not token.startswith(PREFIX) or len(token) > 100:
        return None
    digest = _devices.hash_token(token)
    row = db.fetchone("SELECT id, owner_user_id, token_hash, last_used_at FROM beacon_read_tokens "
                      "WHERE token_hash=?", (digest,))
    if not row or not hmac.compare_digest(row["token_hash"], digest):
        return None
    user = db.fetchone("SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (row["owner_user_id"],))
    if not user:
        return None
    if _stale(row["last_used_at"]):
        db.execute("UPDATE beacon_read_tokens SET last_used_at=? WHERE id=?", (_devices.now_iso(), row["id"]))
    return dict(user)


def _stale(last_used_at: Optional[str]) -> bool:
    if not last_used_at:
        return True
    try:
        seen = datetime.fromisoformat(last_used_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - seen > timedelta(seconds=TOUCH_EVERY_S)
