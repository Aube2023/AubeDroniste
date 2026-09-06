"""Provisionnement de comptes AubeMail depuis AubePilot (service-a-service).

Utilise quand une ECOLE s'inscrit : AubePilot demande a AubeMail de creer un
vrai compte (le nom de l'ecole devient son display_name). POST vers l'endpoint
interne d'AubeMail avec la cle partagee AUBE_INTERNAL_API_KEY en en-tete
`X-Aube-Internal-Key`. AubeMail cree le compte systeme + Maildir + la ligne DB.

Inerte si AUBE_INTERNAL_API_KEY n'est pas configuree (dev / tests) : l'appelant
saute alors la creation AubeMail.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from config import AUBEMAIL_URL, AUBE_INTERNAL_API_KEY

log = logging.getLogger("aubepilot.aubemail")

_TIMEOUT = (5, 20)  # connect, read (useradd + Maildir peuvent prendre un peu)


def provision_account(*, username: str, password: str,
                      display_name: Optional[str] = None,
                      source: str = "aubepilot",
                      lang: Optional[str] = None) -> dict:
    """Cree (ou confirme) le compte AubeMail correspondant.

    Retourne {ok: bool, created: bool, reason: str|None} :
      - ok=True, created=True  -> compte cree
      - ok=True, created=False -> le compte existait deja (on n'y touche pas)
      - ok=False               -> echec (reason = message, ex. mdp trop faible)
    """
    if not AUBE_INTERNAL_API_KEY:
        return {"ok": False, "created": False, "reason": "cle interne non configuree"}
    try:
        r = requests.post(
            f"{AUBEMAIL_URL.rstrip('/')}/aubemail/api/internal/provision",
            json={
                "username": username,
                "password": password,
                "display_name": display_name,
                "source": source,
                "lang": lang,
            },
            headers={"X-Aube-Internal-Key": AUBE_INTERNAL_API_KEY},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.warning("provision AubeMail injoignable: %s", exc)
        return {"ok": False, "created": False, "reason": "service AubeMail injoignable"}

    if r.status_code in (200, 201):
        try:
            body = r.json()
        except ValueError:
            body = {}
        return {"ok": True, "created": bool(body.get("created")), "reason": None}

    try:
        msg = r.json().get("error")
    except ValueError:
        msg = None
    log.warning("provision AubeMail refuse (%s): %s", r.status_code, msg)
    return {"ok": False, "created": False, "reason": msg or f"refus AubeMail ({r.status_code})"}
