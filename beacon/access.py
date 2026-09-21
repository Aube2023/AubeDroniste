"""Qui voit AubeBeacon.

Tant que le module physique n'est pas construit, la fonction reste
confidentielle : administrateurs et comptes de `AUBEBEACON_USERS` seulement.
Pour tout autre compte, la page, les liens et les routes JSON n'existent pas
(404, jamais 403 : rien ne doit trahir la fonction). `AUBEBEACON_PUBLIC=1`
l'ouvre à tous les pilotes le jour venu, sans changer le code. L'ingestion
(`POST /api/v1/telemetry`) n'est pas concernée : une balise s'authentifie
par son jeton, pas par un compte.
"""
from typing import Optional

import config


def allowed(user: Optional[dict]) -> bool:
    if not user:
        return False
    if config.BEACON_PUBLIC:
        return True
    if user.get("is_admin"):
        return True
    return (user.get("username") or "").lower() in config.BEACON_ALLOWED_USERS
