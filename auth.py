"""Authentification AubeDroniste.

Auth PAM (memoire feedback_auth: ne JAMAIS reset les mdp PAM, partages
entre tous les services Aube). En dev / macOS, on bascule sur un check
local degrade (SHA256 + sel) pour que le scaffold marche tout de suite.

Sessions cote serveur (table `sessions`), cookie httpOnly signe par Flask.
"""
import hashlib
import hmac
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import g, redirect, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer

from config import EMAIL_DOMAIN, SECRET_KEY, SESSION_COOKIE_NAME, SESSION_LIFETIME_DAYS
import db


_signer = URLSafeSerializer(SECRET_KEY, salt="aubedroniste-sid")


# ---------------------------------------------------------------------------
# Mots de passe
# ---------------------------------------------------------------------------

def _pam_authenticate(username: str, password: str) -> bool:
    """Tente PAM si dispo (Linux). Sur macOS le module n'est pas installe."""
    try:
        import pam  # type: ignore
    except Exception:
        return False
    try:
        return pam.authenticate(username, password, service="login")
    except Exception:
        return False


# Fallback dev : on stocke un hash dans la table users sous champ
# `username`-style (bio reutilise pour eviter migration). En prod (Linux),
# PAM gere les mdp et on ne touche jamais a /etc/shadow.
_DEV_HASH_FILE = os.path.join(os.path.dirname(__file__), ".dev_passwords")


def _dev_hash(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _dev_load() -> dict:
    out = {}
    if not os.path.exists(_DEV_HASH_FILE):
        return out
    with open(_DEV_HASH_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":", 2)
            if len(parts) == 3:
                out[parts[0]] = (parts[1], parts[2])
    return out


def _dev_save(username: str, password: str):
    salt = secrets.token_hex(8)
    hashed = _dev_hash(password, salt)
    rows = _dev_load()
    rows[username] = (salt, hashed)
    with open(_DEV_HASH_FILE, "w", encoding="utf-8") as f:
        for u, (s, h) in rows.items():
            f.write(f"{u}:{s}:{h}\n")
    os.chmod(_DEV_HASH_FILE, 0o600)


def _dev_check(username: str, password: str) -> bool:
    rows = _dev_load()
    if username not in rows:
        return False
    salt, want = rows[username]
    return hmac.compare_digest(_dev_hash(password, salt), want)


def authenticate(username: str, password: str) -> bool:
    if sys.platform.startswith("linux"):
        if _pam_authenticate(username, password):
            return True
    # En dev / macOS on accepte le fallback local
    return _dev_check(username, password)


def set_dev_password(username: str, password: str):
    """Helper pour creer / faire tourner un mdp local (dev seulement)."""
    _dev_save(username, password)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user_id: int, user_agent: str = "", ip: str = "") -> str:
    sid = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    db.execute(
        "INSERT INTO sessions (sid, user_id, expires_at, user_agent, ip) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, user_id, expires.isoformat(timespec="seconds"), user_agent[:200], ip[:64]),
    )
    return _signer.dumps(sid)


def revoke_session(token: Optional[str]):
    if not token:
        return
    try:
        sid = _signer.loads(token)
    except BadSignature:
        return
    db.execute("DELETE FROM sessions WHERE sid=?", (sid,))


def load_user_from_request() -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        sid = _signer.loads(token)
    except BadSignature:
        return None
    row = db.fetchone(
        "SELECT u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id "
        "WHERE s.sid=? AND s.expires_at > datetime('now')",
        (sid,),
    )
    if not row:
        return None
    db.execute("UPDATE users SET last_seen_at=datetime('now') WHERE id=?", (row["id"],))
    return dict(row)


def attach_user():
    g.user = load_user_from_request()


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None) or not g.user.get("is_admin"):
            return ("Forbidden", 403)
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Inscription
# ---------------------------------------------------------------------------

def normalize_email(username: str, email: Optional[str]) -> str:
    """Force le domaine @aubemail.com (memoire feedback_email_domain)."""
    if email and "@" in email:
        local = email.split("@", 1)[0]
    else:
        local = username
    return f"{local}@{EMAIL_DOMAIN}"


def create_user(*, username: str, password: str, full_name: str,
                role: str = "client", country: Optional[str] = None,
                city: Optional[str] = None, phone: Optional[str] = None,
                lat: Optional[float] = None, lng: Optional[float] = None,
                send_welcome_email: bool = True) -> int:
    email = normalize_email(username, None)
    cur = db.execute(
        "INSERT INTO users (username, email, full_name, phone, country, city, lat, lng, role) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (username.lower().strip(), email, full_name.strip(), phone, country, city, lat, lng, role),
    )
    user_id = cur.lastrowid
    set_dev_password(username.lower().strip(), password)
    if role in ("droniste", "both"):
        db.execute(
            "INSERT INTO pilot_profiles (user_id) VALUES (?)",
            (user_id,),
        )
    if send_welcome_email:
        try:
            import mailer
            mailer.send_welcome({
                "id": user_id, "username": username.lower().strip(),
                "email": email, "full_name": full_name.strip(), "role": role,
            })
        except Exception:  # email ne doit jamais bloquer l'inscription
            pass
    return user_id
