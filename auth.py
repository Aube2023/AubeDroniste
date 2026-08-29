"""Authentification AubePilot.

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

from config import (
    EMAIL_DOMAIN,
    REQUIRE_AUBEMAIL,
    SECRET_KEY,
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME_DAYS,
)
import db


_signer = URLSafeSerializer(SECRET_KEY, salt="aubepilot-sid")


# ---------------------------------------------------------------------------
# Mots de passe
# ---------------------------------------------------------------------------

def _pam_authenticate(username: str, password: str) -> bool:
    """Authentification PAM via le service `aube` (partage entre tous les
    services L'Aube Etoilee). Le service `aube` est defini dans
    /etc/pam.d/aube et ne fait que auth+account via pam_unix, sans check
    de shell — donc tolere les comptes /usr/sbin/nologin (cas typique des
    boites mail AubeMail).

    Sur macOS / dev : le module pam n'est pas installe, on retourne False
    et l'auth bascule sur le fallback dev_passwords.
    """
    try:
        import pam  # type: ignore
    except Exception:
        return False
    try:
        # API moderne (python-pam >= 2.0)
        if hasattr(pam, "PamAuthenticator"):
            p = pam.PamAuthenticator()
            return bool(p.authenticate(username, password, service="aube"))
        # API legacy (python-pam 1.x)
        return bool(pam.authenticate(username, password, service="aube"))
    except Exception:
        return False


def system_user_exists(username: str) -> bool:
    """Verifie qu'un compte systeme (= compte AubeMail) existe.

    Sur Linux prod, tous les services Aube partagent /etc/passwd via PAM.
    Un compte cree dans AubeMail apparait donc immediatement ici.
    Sur macOS / dev, on retourne True (le fallback dev gere tout).
    """
    if not sys.platform.startswith("linux"):
        return True
    try:
        import pwd
        pwd.getpwnam(username)
        return True
    except (KeyError, ImportError):
        return False


# Fallback dev : on stocke un hash dans .dev_passwords (gitignore, chmod 600).
# En prod (Linux), PAM gere les mdp et on ne touche jamais a /etc/shadow.
#
# Format des lignes : `<username>:<scheme>:<salt_hex>:<hash_hex>`
#   - scheme = "scrypt"  (nouveau, defaut)
#   - scheme = "sha256"  (legacy, lecture seule, re-hash automatique au prochain login)
_DEV_HASH_FILE = os.path.join(os.path.dirname(__file__), ".dev_passwords")

# Parametres scrypt : ~50ms de hash, 16 MiB de RAM par tentative -> bruteforce
# 10 000x plus cher que SHA-256 si .dev_passwords leak.
_SCRYPT_N = 2 ** 14   # 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def _scrypt_hash(password: str, salt: bytes) -> str:
    h = hashlib.scrypt(
        password=password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return h.hex()


def _sha256_hash(password: str, salt_hex: str) -> str:
    """Legacy SHA-256+sel — uniquement pour lire l'ancien format."""
    return hashlib.sha256(f"{salt_hex}:{password}".encode("utf-8")).hexdigest()


def _dev_load() -> dict:
    """Retourne {username: (scheme, salt_hex, hash_hex)}.

    Tolere l'ancien format `username:salt:sha256` (3 champs) ET le nouveau
    `username:scheme:salt:hash` (4 champs).
    """
    out = {}
    if not os.path.exists(_DEV_HASH_FILE):
        return out
    with open(_DEV_HASH_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":", 3)
            if len(parts) == 4:
                out[parts[0]] = (parts[1], parts[2], parts[3])
            elif len(parts) == 3:
                # legacy : on injecte scheme=sha256
                out[parts[0]] = ("sha256", parts[1], parts[2])
    return out


def _dev_save_all(rows: dict):
    """Reecrit le fichier complet (overwrite atomique via tmp + rename)."""
    tmp = _DEV_HASH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for u, (scheme, salt, h) in rows.items():
            f.write(f"{u}:{scheme}:{salt}:{h}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, _DEV_HASH_FILE)  # atomic


def _dev_save(username: str, password: str):
    """Hash le mdp avec scrypt et l'ecrit dans .dev_passwords."""
    salt = secrets.token_bytes(16)
    hashed = _scrypt_hash(password, salt)
    rows = _dev_load()
    rows[username] = ("scrypt", salt.hex(), hashed)
    _dev_save_all(rows)


def _dev_check(username: str, password: str) -> bool:
    """Verifie le mdp contre .dev_passwords. Si l'entree est en SHA-256 legacy
    ET que le mdp matche, on re-hash en scrypt automatiquement (silent upgrade).
    """
    rows = _dev_load()
    if username not in rows:
        return False
    scheme, salt_hex, want = rows[username]

    if scheme == "scrypt":
        try:
            got = _scrypt_hash(password, bytes.fromhex(salt_hex))
        except ValueError:
            return False
        return hmac.compare_digest(got, want)

    if scheme == "sha256":
        ok = hmac.compare_digest(_sha256_hash(password, salt_hex), want)
        if ok:
            # Migration silencieuse vers scrypt
            _dev_save(username, password)
        return ok

    return False


def authenticate(username: str, password: str) -> bool:
    if sys.platform.startswith("linux"):
        if _pam_authenticate(username, password):
            return True
    # En dev / macOS on accepte le fallback local
    return _dev_check(username, password)


def set_dev_password(username: str, password: str):
    """Helper pour creer / faire tourner un mdp local (dev seulement)."""
    _dev_save(username, password)


def password_managed_by_aubemail(username: str) -> bool:
    """True si le mot de passe est celui du compte systeme PAM (AubeMail) :
    il ne se change pas ici, mais sur AubeMail."""
    return sys.platform.startswith("linux") and system_user_exists(username)


def change_password(username: str, current: str, new: str):
    """Change le mot de passe LOCAL apres verification de l'actuel.
    Retourne (ok, raison) — raison : 'aubemail' (gere ailleurs),
    'wrong' (actuel incorrect), 'weak' (trop court)."""
    if password_managed_by_aubemail(username):
        return False, "aubemail"
    if len(new or "") < 8:
        return False, "weak"
    if not _dev_check(username, current or ""):
        return False, "wrong"
    _dev_save(username, new)
    return True, None


def remove_local_password(username: str) -> None:
    """Supprime l'entree locale (compte supprime) : plus aucune connexion."""
    rows = _dev_load()
    if username in rows:
        del rows[username]
        _dev_save_all(rows)


def revoke_all_sessions(user_id: int, keep_sid: Optional[str] = None) -> int:
    """Deconnecte tous les appareils (sauf la session courante si keep_sid)."""
    if keep_sid:
        cur = db.execute("DELETE FROM sessions WHERE user_id=? AND sid<>?", (user_id, keep_sid))
    else:
        cur = db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return cur.rowcount


def current_sid() -> Optional[str]:
    """sid (non signe) de la session courante, pour la distinguer des autres."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        return _signer.loads(token)
    except BadSignature:
        return None


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
        "SELECT u.*, s.expires_at AS session_expires_at FROM sessions s "
        "JOIN users u ON u.id = s.user_id "
        "WHERE s.sid=? AND s.expires_at > datetime('now') AND u.deleted_at IS NULL",
        (sid,),
    )
    if not row:
        return None
    user = dict(row)
    _maybe_extend_session(sid, token, user.pop("session_expires_at", None))
    db.execute("UPDATE users SET last_seen_at=datetime('now') WHERE id=?", (user["id"],))
    return user


def _maybe_extend_session(sid: str, token: str, expires_at) -> None:
    """Renouvellement glissant : tant que l'utilisateur revient, sa session ne
    meurt jamais (sinon elle expire 30 jours apres le LOGIN, meme pour un
    utilisateur actif tous les jours — et il doit se reconnecter).

    Au plus une extension par jour (des que l'echeance a ete entamee d'un
    jour), pour ne pas ecrire en base a chaque requete. Le cookie est re-pose
    cote reponse par app._refresh_session_cookie via g.refreshed_session_token.
    """
    if not expires_at:
        return
    try:
        current = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if current - now > timedelta(days=SESSION_LIFETIME_DAYS - 1):
        return  # session posee/renouvelee il y a moins d'un jour
    new_expires = now + timedelta(days=SESSION_LIFETIME_DAYS)
    db.execute(
        "UPDATE sessions SET expires_at=? WHERE sid=?",
        (new_expires.isoformat(timespec="seconds"), sid),
    )
    g.refreshed_session_token = token


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

def normalize_username(raw: str) -> str:
    """Accepte 'nicolas' ou 'nicolas@aubemail.com' -> retourne 'nicolas'."""
    s = (raw or "").strip().lower()
    if "@" in s:
        s = s.split("@", 1)[0]
    return s


def normalize_email(username: str, email: Optional[str]) -> str:
    """Force le domaine @aubemail.com (memoire feedback_email_domain)."""
    if email and "@" in email:
        local = email.split("@", 1)[0]
    else:
        local = username
    return f"{local.strip().lower()}@{EMAIL_DOMAIN}"


class AubeMailRequiredError(Exception):
    """Le compte systeme (PAM/AubeMail) n'existe pas en prod."""


def create_user(*, username: str, password: str, full_name: str,
                role: str = "client", country: Optional[str] = None,
                city: Optional[str] = None, phone: Optional[str] = None,
                lat: Optional[float] = None, lng: Optional[float] = None,
                send_welcome_email: bool = True, kind: str = "pro") -> int:
    """Cree le profil AubePilot local. En prod Linux, exige que le compte
    AubeMail (= compte systeme PAM) existe au prealable — l'inscription au
    sens credentiel se fait sur AubeMail, pas ici. Si le compte systeme est
    absent, leve `AubeMailRequiredError`.
    """
    username = username.lower().strip()
    # Compte PAM reel = uniquement sur Linux avec une entree /etc/passwd
    # (system_user_exists renvoie True sur macOS par commodite, ce n'est PAS
    # un vrai compte PAM). Inscription mondiale : on n'exige un compte
    # AubeMail prealable que si REQUIRE_AUBEMAIL est explicitement active.
    on_linux = sys.platform.startswith("linux")
    has_pam_account = on_linux and system_user_exists(username)
    if on_linux and not has_pam_account and REQUIRE_AUBEMAIL:
        raise AubeMailRequiredError(username)
    email = normalize_email(username, None)
    cur = db.execute(
        "INSERT INTO users (username, email, full_name, phone, country, city, lat, lng, role) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (username, email, full_name.strip(), phone, country, city, lat, lng, role),
    )
    user_id = cur.lastrowid
    # Mot de passe local : provisionne sauf si un vrai compte PAM gere deja le
    # mdp (prod Linux avec compte AubeMail — on ne touche jamais /etc/shadow).
    # Un pilote etranger inscrit en direct (pas de compte PAM) obtient donc un
    # mdp local et peut se connecter via le fallback dev de authenticate().
    if not has_pam_account:
        set_dev_password(username, password)
    if role in ("pilot", "both"):
        from config import PROFILE_KIND_CODES
        if kind not in PROFILE_KIND_CODES:
            kind = "pro"
        db.execute(
            "INSERT INTO pilot_profiles (user_id, kind) VALUES (?, ?)",
            (user_id, kind),
        )
    if send_welcome_email:
        try:
            import mailer
            mailer.send_welcome({
                "id": user_id, "username": username,
                "email": email, "full_name": full_name.strip(), "role": role,
            })
        except Exception as exc:  # email ne doit jamais bloquer l'inscription
            import logging
            logging.getLogger("aubepilot.auth").warning(
                "welcome email failed for user_id=%s : %s", user_id, exc,
            )
    return user_id
