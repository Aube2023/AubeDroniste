"""État de liaison d'une balise, déduit du dernier paquet accepté.

    silence < BEACON_ONLINE_S   → ONLINE
    silence < BEACON_DEGRADED_S → DEGRADED
    au-delà                     → OFFLINE (« signal perdu », dernière position affichée)
    jamais vue                  → NEVER

Aucun minuteur côté serveur : l'état est calculé à la lecture, et le
navigateur le recalcule chaque seconde avec les mêmes seuils (envoyés par
l'API), donc un drone passe OFFLINE à l'écran même sans nouvelle requête.
"""
from datetime import datetime, timezone
from typing import Optional

from config import BEACON_DEGRADED_S, BEACON_ONLINE_S

ONLINE, DEGRADED, OFFLINE, NEVER = "ONLINE", "DEGRADED", "OFFLINE", "NEVER"


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """ISO 8601 (avec Z, décalage, ou format SQLite 'YYYY-MM-DD HH:MM:SS') → datetime UTC."""
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_seconds(last_seen: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    dt = parse_iso(last_seen)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds())


def compute(last_seen: Optional[str], now: Optional[datetime] = None) -> tuple:
    """(état, âge en secondes ou None)."""
    age = age_seconds(last_seen, now)
    if age is None:
        return NEVER, None
    if age < BEACON_ONLINE_S:
        return ONLINE, age
    if age < BEACON_DEGRADED_S:
        return DEGRADED, age
    return OFFLINE, age


def thresholds() -> dict:
    return {"online_s": BEACON_ONLINE_S, "degraded_s": BEACON_DEGRADED_S}
