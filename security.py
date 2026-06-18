"""Couche securite AubePilot.

- CSRF tokens (par session, valides sur POST/PUT/DELETE/PATCH)
- Rate limiting per IP / route (token bucket en memoire)
- Security headers HTTP (CSP, HSTS, X-Frame-Options, etc.)
- Anti-redirection ouverte sur le parametre `next`
- Helpers d'audit

Ne dependent que de la stdlib + Flask. Pas de Redis requis pour le rate
limit — token bucket en memoire (suffit pour 1 worker, suffisant pour
demarrer ; passer a Redis si on scale au-dela d'1 process gunicorn).
"""
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from typing import Optional
from urllib.parse import urlparse

from flask import abort, current_app, request, session
from markupsafe import Markup

log = logging.getLogger("aubepilot.security")


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

CSRF_EXEMPT_ROUTES = {
    "stripe_webhook",   # Stripe signe la requete differemment
}


def csrf_token() -> str:
    """Token CSRF par session (genere a la 1ere demande)."""
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


def csrf_input() -> Markup:
    """A appeler dans les templates : `{{ csrf_input() }}` injecte le hidden input."""
    return Markup(
        f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'
    )


def validate_csrf():
    """before_request hook : refuse les POST sans token valide."""
    if current_app.config.get("TESTING"):
        return
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return
    if request.endpoint in CSRF_EXEMPT_ROUTES:
        return
    expected = session.get("_csrf")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not sent or not secrets.compare_digest(str(expected), str(sent)):
        log.warning("CSRF refusé : endpoint=%s ip=%s", request.endpoint, _client_ip())
        abort(403, description="Jeton CSRF invalide. Rafraîchissez la page.")


# ---------------------------------------------------------------------------
# Rate limiting (token bucket en memoire)
# ---------------------------------------------------------------------------

_buckets = defaultdict(deque)  # type: ignore[var-annotated]
_lock = threading.Lock()


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def rate_limit(per_minute: int = 10, per_hour: int = 100, key: Optional[str] = None):
    """Decorateur : limite N hits / fenetre par IP (et par endpoint)."""
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_app.config.get("TESTING"):
                return view(*args, **kwargs)
            ip = _client_ip()
            bucket_key = (ip, key or request.endpoint)
            now = time.time()
            with _lock:
                stamps = _buckets[bucket_key]
                # Drop trop vieux (>1h)
                while stamps and now - stamps[0] > 3600:
                    stamps.popleft()
                count_minute = sum(1 for t in stamps if now - t < 60)
                if count_minute >= per_minute or len(stamps) >= per_hour:
                    log.warning(
                        "rate_limit hit : ip=%s endpoint=%s minute=%d hour=%d",
                        ip, request.endpoint, count_minute, len(stamps),
                    )
                    abort(429, description="Trop de requêtes. Réessayez dans quelques minutes.")
                stamps.append(now)
            return view(*args, **kwargs)
        return wrapped
    return deco


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

def apply_security_headers(resp):
    """after_request hook : pose les en-tetes de securite recommandes."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-XSS-Protection", "0")
    resp.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(self), camera=(), microphone=(), payment=(self)",
    )
    # CSP — autorise Google Fonts (typo), Stripe (frames + js) et MapLibre GL
    # (carte interactive : JS/CSS via unpkg, tuiles demotiles.maplibre.org,
    # web workers via blob:).
    resp.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://js.stripe.com https://unpkg.com https://aubemail.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https:; "
        "worker-src 'self' blob:; "
        "child-src 'self' blob:; "
        "frame-src https://js.stripe.com https://hooks.stripe.com; "
        "connect-src 'self' https://api.stripe.com https://demotiles.maplibre.org https://aubemail.com; "
        "form-action 'self' https://checkout.stripe.com https://connect.stripe.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self';"
    ))
    # HSTS uniquement si on est servi en HTTPS (declenche par SITE_URL)
    site_url = current_app.config.get("SITE_URL", "")
    if site_url.startswith("https://"):
        resp.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return resp


# ---------------------------------------------------------------------------
# Open redirect
# ---------------------------------------------------------------------------

def safe_next(next_url: Optional[str], fallback: str = "/") -> str:
    """Refuse une redirection externe (header `next` malveillant)."""
    if not next_url:
        return fallback
    parsed = urlparse(next_url)
    # Url relative -> OK
    if not parsed.netloc and not parsed.scheme:
        return next_url
    # Meme host -> OK
    if parsed.netloc == request.host:
        return next_url
    log.warning("open redirect bloque : %r (host=%s)", next_url, request.host)
    return fallback


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------

def audit(user_id: Optional[int], action: str, target: Optional[str] = None,
          payload: Optional[dict] = None):
    """Log une action sensible dans la table audit_log."""
    try:
        import json
        import db
        db.execute(
            "INSERT INTO audit_log (user_id, action, target, payload) "
            "VALUES (?, ?, ?, ?)",
            (user_id, action[:64], (target or "")[:128],
             json.dumps(payload or {}, default=str)[:2000]),
        )
    except Exception as exc:
        log.error("audit failed: %s", exc)


# ---------------------------------------------------------------------------
# Verifications de demarrage en prod
# ---------------------------------------------------------------------------

DEFAULT_SECRET = "change-me-in-prod-aubepilot-2026"


def assert_production_ready(app):
    """A appeler depuis app.py au boot. Verifie qu'on n'oublie pas de
    durcir l'app en prod. Ne fait rien si on est en debug/macOS."""
    import os
    import sys

    is_dev = (
        app.debug
        or app.config.get("TESTING")
        or sys.platform == "darwin"
        or os.environ.get("FLASK_DEBUG") == "1"
    )
    if is_dev:
        return

    secret = app.secret_key
    if not secret or secret == DEFAULT_SECRET:
        raise RuntimeError(
            "AUBEPILOT_SECRET non defini en prod. Genere une cle "
            "(python -c 'import secrets; print(secrets.token_urlsafe(48))') "
            "et expose-la via la variable d'environnement."
        )

    site_url = app.config.get("SITE_URL", "")
    if not site_url.startswith("https://"):
        log.warning(
            "SITE_URL=%s n'est pas https — HSTS desactive et cookies non secure. "
            "Mets SITE_URL=https://pilot.aubeetoilee.com en prod.",
            site_url,
        )
