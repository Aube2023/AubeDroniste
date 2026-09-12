"""Météo en un point, pour la carte : conditions actuelles et radar.

Deux sources ouvertes, sans clé, les mêmes que le service AubeMeteo :
- Open-Meteo (CC BY 4.0) pour les conditions à des coordonnées libres ;
- RainViewer pour l'index des images radar (les tuiles, elles, sont chargées
  par le navigateur comme celles du fond de carte).

L'app relaie Open-Meteo côté serveur : le navigateur du visiteur ne parle qu'à
notre domaine, et une même zone n'est demandée qu'une fois par dix minutes,
quel que soit le nombre de visiteurs. Les coordonnées sont arrondies au
centième de degré (environ un kilomètre) : c'est la résolution du modèle, et
ça évite qu'une position de pilote floutée à 10 km paraisse plus précise
qu'elle ne l'est.

Le « verdict de vol » est une aide, pas une autorisation : les seuils sont ceux
des drones grand public et professionnels légers (vent maximal constructeur
entre 36 et 43 km/h), et rien ici ne remplace la réglementation locale ni le
jugement du pilote.
"""
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger("aubepilot.meteo")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
RAINVIEWER_URL = "https://api.rainviewer.com/public/weather-maps.json"
USER_AGENT = "AubePilot/1.0 (+https://pilot.aubeetoilee.com)"
TIMEOUT_S = 6
TTL_CURRENT_S = 600      # 10 min : cadence de mise à jour d'Open-Meteo
TTL_RADAR_S = 300        # 5 min  : une nouvelle image radar toutes les 10 min

# Seuils du verdict de vol (km/h, mm/h, m, °C). Documentés dans la FAQ.
WIND_NOGO, WIND_CAUTION = 40, 25
GUST_NOGO, GUST_CAUTION = 55, 40
RAIN_NOGO = 0.5
VISIBILITY_CAUTION_M = 3000
TEMP_CAUTION_C = -10

# Codes WMO (Open-Meteo) -> famille de condition, traduite dans i18n._T
# sous « meteo.cond.<famille> ».
_WMO = [
    ((0,), "clear"), ((1, 2), "partly"), ((3,), "cloudy"), ((45, 48), "fog"),
    ((51, 53, 55, 56, 57), "drizzle"), ((61, 63, 65, 66, 67, 80, 81, 82), "rain"),
    ((71, 73, 75, 77, 85, 86), "snow"), ((95, 96, 99), "storm"),
]


def condition_key(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "unknown"
    for codes, key in _WMO:
        if code in codes:
            return key
    return "unknown"


_cache: dict = {}
_lock = threading.Lock()


def _cached(key, ttl, loader):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    if value is not None:
        with _lock:
            _cache[key] = (now, value)
    return value


def _get_json(url: str) -> Optional[dict]:
    """Appel réseau, isolé pour être remplacé en test."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("météo injoignable (%s) : %s", url.split("?", 1)[0], exc)
        return None


# ---------------------------------------------------------------------------
# Conditions actuelles
# ---------------------------------------------------------------------------

def _round(x: float) -> float:
    return round(float(x), 2)


def fetch_current(lat: float, lng: float) -> Optional[dict]:
    params = {
        "latitude": lat, "longitude": lng, "timezone": "auto",
        "current": ",".join([
            "temperature_2m", "precipitation", "weather_code", "cloud_cover",
            "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "visibility",
        ]),
        "wind_speed_unit": "kmh",
    }
    return _get_json(OPEN_METEO_URL + "?" + urllib.parse.urlencode(params))


def assess(cur: dict) -> dict:
    """{level: favorable|caution|nogo, reasons: [cles i18n]} depuis les
    valeurs courantes. Les raisons sont des clés, traduites à l'affichage."""
    wind = float(cur.get("wind_speed_10m") or 0)
    gust = float(cur.get("wind_gusts_10m") or 0)
    rain = float(cur.get("precipitation") or 0)
    vis = cur.get("visibility")
    temp = cur.get("temperature_2m")
    cond = condition_key(cur.get("weather_code"))

    nogo, caution = [], []
    if wind >= WIND_NOGO or gust >= GUST_NOGO:
        nogo.append("wind")
    elif wind >= WIND_CAUTION or gust >= GUST_CAUTION:
        caution.append("wind")
    if cond == "storm":
        nogo.append("storm")
    if cond == "snow":
        nogo.append("snow")
    elif rain >= RAIN_NOGO or cond == "rain":
        nogo.append("rain")
    elif rain > 0 or cond == "drizzle":
        caution.append("rain")
    if cond == "fog" or (vis is not None and float(vis) < VISIBILITY_CAUTION_M):
        caution.append("visibility")
    if temp is not None and float(temp) <= TEMP_CAUTION_C:
        caution.append("cold")

    if nogo:
        return {"level": "nogo", "reasons": nogo + caution}
    if caution:
        return {"level": "caution", "reasons": caution}
    return {"level": "favorable", "reasons": []}


def current(lat: float, lng: float) -> Optional[dict]:
    """Conditions au point (arrondi au centième de degré), avec verdict de vol.
    None si la source ne répond pas : la carte reste utilisable sans météo."""
    lat, lng = _round(lat), _round(lng)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None

    def load():
        raw = fetch_current(lat, lng)
        cur = (raw or {}).get("current")
        if not cur:
            return None
        return {
            "lat": lat, "lng": lng,
            "temp": cur.get("temperature_2m"),
            "wind": cur.get("wind_speed_10m"),
            "gusts": cur.get("wind_gusts_10m"),
            "wind_dir": cur.get("wind_direction_10m"),
            "precip": cur.get("precipitation"),
            "cloud": cur.get("cloud_cover"),
            "visibility_km": (round(float(cur["visibility"]) / 1000, 1)
                              if cur.get("visibility") is not None else None),
            "condition": condition_key(cur.get("weather_code")),
            "flight": assess(cur),
            "observed_at": cur.get("time"),
            "source": "Open-Meteo",
        }

    return _cached(("cur", lat, lng), TTL_CURRENT_S, load)


# ---------------------------------------------------------------------------
# Radar de précipitations
# ---------------------------------------------------------------------------

def radar() -> Optional[dict]:
    """Index RainViewer : hôte des tuiles + dernières images passées.
    Le navigateur compose ensuite {host}{path}/512/{z}/{x}/{y}/2/1_1.png.

    Deux limites du service, vérifiées : le radar n'existe que jusqu'au zoom
    7 (au-delà, une tuile grise portant un message, en HTTP 200, que la carte
    afficherait telle quelle) et 500 tuiles par minute et par adresse IP."""
    def load():
        raw = _get_json(RAINVIEWER_URL)
        if not raw or not raw.get("host"):
            return None
        past = (raw.get("radar") or {}).get("past") or []
        frames = [{"time": f.get("time"), "path": f.get("path")} for f in past if f.get("path")]
        if not frames:
            return None
        return {"host": raw["host"], "frames": frames[-6:], "latest": frames[-1],
                "source": "RainViewer"}
    return _cached(("radar",), TTL_RADAR_S, load)
