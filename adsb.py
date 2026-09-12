"""Trafic aérien en direct (ADS-B) autour d'un point, pour la carte.

Source : adsb.lol, réseau communautaire de récepteurs, données en CC0
(usage commercial permis, pas d'attribution exigée ; on le crédite quand
même). Pas de clé. L'app relaie côté serveur avec un cache court par zone :
le navigateur ne parle qu'à notre domaine, et dix visiteurs sur la même
ville ne coûtent qu'une requête toutes les dix secondes.

Ce qu'un pilote de drone cherche ici, c'est la **situation réelle** : qui
vole près de sa zone, à quelle hauteur. Les avions de ligne à 11 000 m ne le
concernent pas ; un hélicoptère à 300 m, si. On expose donc l'altitude en
mètres ET en pieds (l'aviation compte en pieds), et un indicateur « bas »
sous 1 500 m. Attention : l'altitude ADS-B est barométrique, par rapport au
niveau de la mer, pas au sol.

Couverture : celle des récepteurs bénévoles. Excellente en Europe et en
Amérique du Nord, lacunaire ailleurs et en mer ; les aéronefs sans
transpondeur ADS-B (ULM, ballons, une partie de l'aviation légère) n'y sont
pas. Ce n'est pas un radar de contrôle aérien, et l'écran le dit.
"""
import json
import logging
import threading
import time
import urllib.request
from typing import Optional

log = logging.getLogger("aubepilot.adsb")

ADSB_URL = "https://api.adsb.lol/v2/lat/{lat:.4f}/lon/{lng:.4f}/dist/{nm}"
USER_AGENT = "AubePilot/1.0 (+https://pilot.aubeetoilee.com)"
TIMEOUT_S = 6
TTL_S = 10               # les positions ADS-B changent toutes les secondes ;
                         # 10 s suffisent à une carte, et ménagent la source
MIN_NM, MAX_NM = 10, 150 # rayon demandé, borné (1 nm = 1,852 km)
LOW_ALT_M = 1500         # « bas » : la tranche qui concerne un drone
MAX_AIRCRAFT = 400       # au-delà, la carte devient illisible de toute façon

FT_TO_M = 0.3048
KT_TO_KMH = 1.852
FPM_TO_MS = 0.00508

# Catégories d'émetteur ADS-B (DO-260B) -> famille, traduite par l'interface.
CATEGORIES = {
    "A1": "light", "A2": "small", "A3": "large", "A4": "large", "A5": "heavy",
    "A6": "highperf", "A7": "rotorcraft", "B1": "glider", "B2": "balloon",
    "B4": "ultralight", "B6": "uav", "B7": "space", "C1": "vehicle", "C2": "vehicle",
}
EMERGENCY_SQUAWKS = {"7500": "hijack", "7600": "radio", "7700": "emergency"}

# Beaucoup d'helicopteres legers n'emettent pas leur categorie (« ? ») : on
# reconnait alors le type ICAO. Liste des designateurs courants, pas
# exhaustive ; un type inconnu reste « aeronef ».
HELICOPTER_TYPES = {
    "A109", "A119", "A129", "A139", "A169", "A189", "AS32", "AS3B", "AS50", "AS55",
    "AS65", "B06", "B06T", "B105", "B222", "B230", "B407", "B412", "B429", "B430",
    "B47G", "B505", "BK17", "EC20", "EC25", "EC30", "EC35", "EC45", "EC55", "EC75",
    "EH10", "EN28", "EN48", "EXPL", "G2CA", "H12T", "H125", "H135", "H145", "H160",
    "H175", "H225", "H500", "H53", "H60", "H64", "HUCO", "KMAX", "LYNX", "MD52",
    "MD60", "MI17", "MI24", "MI26", "MI8", "NH90", "PUMA", "R22", "R44", "R66",
    "S330", "S51", "S61", "S64", "S65C", "S76", "S92", "SUCO", "UH1", "UH1Y", "V22",
}

_cache: dict = {}
_lock = threading.Lock()


def _get_json(url: str) -> Optional[dict]:
    """Appel réseau, isolé pour être remplacé en test."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("ADS-B injoignable : %s", exc)
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize(a: dict) -> Optional[dict]:
    """Un aéronef adsb.lol -> ce que la carte affiche. None sans position."""
    lat, lng = _num(a.get("lat")), _num(a.get("lon"))
    if lat is None or lng is None:
        return None
    alt_raw = a.get("alt_baro")
    on_ground = alt_raw == "ground"
    alt_ft = None if on_ground else _num(alt_raw)
    if alt_ft is None and not on_ground:
        alt_ft = _num(a.get("alt_geom"))
    gs_kt = _num(a.get("gs"))
    rate_fpm = _num(a.get("baro_rate"))
    if rate_fpm is None:
        rate_fpm = _num(a.get("geom_rate"))
    squawk = str(a.get("squawk") or "").strip()
    emergency = a.get("emergency")
    emerg = EMERGENCY_SQUAWKS.get(squawk) or (emergency if emergency and emergency != "none" else None)
    category = CATEGORIES.get(a.get("category") or "", "unknown")
    if category == "unknown" and str(a.get("t") or "").upper() in HELICOPTER_TYPES:
        category = "rotorcraft"
    return {
        "hex": a.get("hex"),
        "callsign": (a.get("flight") or "").strip() or None,
        "reg": a.get("r") or None,
        "type": a.get("t") or None,
        "desc": a.get("desc") or None,
        "category": category,
        "lat": round(lat, 4), "lng": round(lng, 4),
        "on_ground": on_ground,
        "alt_ft": int(round(alt_ft)) if alt_ft is not None else None,
        "alt_m": int(round(alt_ft * FT_TO_M)) if alt_ft is not None else None,
        "low": bool(on_ground or (alt_ft is not None and alt_ft * FT_TO_M <= LOW_ALT_M)),
        "speed_kmh": int(round(gs_kt * KT_TO_KMH)) if gs_kt is not None else None,
        "speed_kt": int(round(gs_kt)) if gs_kt is not None else None,
        "track": round(_num(a.get("track")) or 0, 1) if _num(a.get("track")) is not None else None,
        "vrate_ms": round(rate_fpm * FPM_TO_MS, 1) if rate_fpm is not None else None,
        "squawk": squawk or None,
        "emergency": emerg,
        "seen_s": _num(a.get("seen")),
    }


def nearby(lat: float, lng: float, radius_nm: int = 50) -> Optional[dict]:
    """Aéronefs dans `radius_nm` milles nautiques autour du point. Cache par
    zone (dixième de degré) et rayon, 10 s. None si la source ne répond pas."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    nm = int(min(MAX_NM, max(MIN_NM, int(radius_nm or 50))))
    key = ("adsb", round(lat, 1), round(lng, 1), nm)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL_S:
            return hit[1]
    raw = _get_json(ADSB_URL.format(lat=lat, lng=lng, nm=nm))
    if raw is None:
        return None
    aircraft = []
    for a in raw.get("ac") or []:
        n = normalize(a)
        if n:
            aircraft.append(n)
    # les plus bas d'abord : ce sont ceux qui comptent pour un drone
    aircraft.sort(key=lambda x: (x["alt_m"] if x["alt_m"] is not None else 10**6))
    out = {
        "center": {"lat": round(lat, 4), "lng": round(lng, 4)}, "radius_nm": nm,
        "count": len(aircraft), "low_count": sum(1 for x in aircraft if x["low"]),
        "aircraft": aircraft[:MAX_AIRCRAFT],
        "truncated": len(aircraft) > MAX_AIRCRAFT,
        "at": int(time.time()), "source": "adsb.lol",
    }
    with _lock:
        _cache[key] = (now, out)
        # ne garde pas des zones abandonnees en memoire
        if len(_cache) > 500:
            for k in [k for k, v in _cache.items() if now - v[0] > TTL_S]:
                _cache.pop(k, None)
    return out
