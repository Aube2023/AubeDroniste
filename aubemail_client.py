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
                      lang: Optional[str] = None,
                      client_ip: Optional[str] = None,
                      user_agent: Optional[str] = None,
                      recovery_email: Optional[str] = None) -> dict:
    """Cree (ou confirme) le compte AubeMail correspondant.

    `recovery_email` (optionnel) : adresse de secours du compte AubeMail. Sans
    elle, « mot de passe oublie » ne peut rien envoyer et le compte est perdu
    au premier oubli. AubeMail la valide (format, jetable, deja partagee) AVANT
    de creer quoi que ce soit et repond field="recovery_email" en cas de refus.

    `client_ip` / `user_agent` : la personne derriere le navigateur, telle
    qu AubePilot la voit derriere son nginx. AubeMail ne voit sinon que
    l IP du service et son alerte admin affiche « IP inconnue » : aucune
    trace d origine pour la securite. Toujours les transmettre.

    Retourne {ok: bool, created: bool, reason: str|None, field: str|None,
              recovery_codes: list[str], recovery_verify_sent: bool} :
      - ok=True, created=True  -> compte cree ; recovery_codes = les 10 codes
        de recuperation EN CLAIR, a montrer une fois (AubeMail n'en garde que
        les hash) ; recovery_verify_sent = le lien de confirmation du secours
        est parti
      - ok=True, created=False -> le compte existait deja (on n'y touche pas)
      - ok=False               -> echec (reason = message, ex. mdp trop faible ;
        field = "recovery_email" si c'est l'adresse de secours qui est refusee)
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
                "client_ip": client_ip,
                "user_agent": (user_agent or "")[:300] or None,
                "recovery_email": (recovery_email or "").strip()[:254] or None,
            },
            headers={"X-Aube-Internal-Key": AUBE_INTERNAL_API_KEY},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.warning("provision AubeMail injoignable: %s", exc)
        return {"ok": False, "created": False, "reason": "service AubeMail injoignable",
                "field": None, "recovery_codes": [], "recovery_verify_sent": False}

    if r.status_code in (200, 201):
        try:
            body = r.json()
        except ValueError:
            body = {}
        codes = body.get("recovery_codes") or []
        return {"ok": True, "created": bool(body.get("created")), "reason": None, "field": None,
                "recovery_codes": [str(c) for c in codes if c],
                "recovery_verify_sent": bool(body.get("recovery_verify_sent"))}

    try:
        err = r.json()
    except ValueError:
        err = {}
    msg = err.get("error") if isinstance(err, dict) else None
    field = err.get("field") if isinstance(err, dict) else None
    log.warning("provision AubeMail refuse (%s): %s", r.status_code, msg)
    return {"ok": False, "created": False, "reason": msg or f"refus AubeMail ({r.status_code})",
            "field": field, "recovery_codes": [], "recovery_verify_sent": False}
