"""Géocodage : code postal / adresse / ville -> lat, lng.

Objectif produit : « cherche par code postal » comme un annuaire local, mais
partout dans le monde (H2X 1Y4, 75011, G5Y, SW1A 1AA, Casablanca…).

Implémentation :
- Nominatim (OpenStreetMap), gratuit, sans clé. Politique d'usage respectée :
  User-Agent identifiant + contact, au plus 1 requête/s (throttle global),
  résultats mis en cache (table `geocode_cache`) — y compris les échecs, pour
  ne jamais re-frapper le service avec la même saisie.
- Appel côté serveur (pas de dépendance navigateur, pas de CSP à élargir).
- Ne lève jamais : en cas de panne réseau on renvoie None et la recherche
  continue sans tri par distance.

- Codes postaux canadiens à 3 caractères (RTA / FSA, ex. « G5Y ») : Nominatim
  ne les connaît pas. Table hors-ligne `geodata/ca_fsa.csv` (1 657 centroïdes,
  GeoNames, CC BY 4.0) consultée en premier — zéro réseau, réponse immédiate.

Surchargeable via env `NOMINATIM_URL` (instance auto-hébergée) et
`AUBEPILOT_GEOCODE_CONTACT` (email du User-Agent).
"""
import csv
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import db
from config import BASE_DIR, SITE_URL

log = logging.getLogger("aubepilot.geocode")

NOMINATIM_URL = os.environ.get(
    "NOMINATIM_URL", "https://nominatim.openstreetmap.org/search",
).strip()
CONTACT = os.environ.get("AUBEPILOT_GEOCODE_CONTACT", "rprp@aubemail.com").strip()
USER_AGENT = f"AubePilot/1.0 (+{SITE_URL}; {CONTACT})"
TIMEOUT_S = 5.0
MIN_INTERVAL_S = 1.0          # politique Nominatim : <= 1 req/s
MAX_QUERY_LEN = 120

_lock = threading.Lock()
_last_call = 0.0

CA_FSA_PATH = os.path.join(BASE_DIR, "geodata", "ca_fsa.csv")
_CA_FSA_RE = re.compile(r"^([A-Za-z]\d[A-Za-z])\s*(\d[A-Za-z]\d)?$")
_CA_PROVINCES = {
    "AB": "Alberta", "BC": "Colombie-Britannique", "MB": "Manitoba",
    "NB": "Nouveau-Brunswick", "NL": "Terre-Neuve-et-Labrador",
    "NS": "Nouvelle-Écosse", "NT": "Territoires du Nord-Ouest", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Île-du-Prince-Édouard", "QC": "Québec",
    "SK": "Saskatchewan", "YT": "Yukon",
}
_ca_fsa_table: Optional[dict] = None


def _ca_fsa() -> dict:
    """Charge (une fois) la table FSA -> (lat, lng, nom, province)."""
    global _ca_fsa_table
    if _ca_fsa_table is None:
        table = {}
        try:
            with open(CA_FSA_PATH, encoding="utf-8", newline="") as f:
                for row in csv.reader(l for l in f if not l.startswith("#")):
                    if len(row) < 5:
                        continue
                    try:
                        table[row[0].upper()] = (float(row[3]), float(row[4]), row[1], row[2])
                    except ValueError:
                        continue
        except OSError as exc:
            log.warning("table FSA Canada illisible (%s) : %s", CA_FSA_PATH, exc)
        _ca_fsa_table = table
    return _ca_fsa_table


def canada_fsa(query: str) -> Optional[dict]:
    """Résolution hors-ligne d'un code postal canadien (RTA seule « G5Y » ou
    code complet « G5Y 0A1 » -> centroïde de sa RTA). None si non canadien."""
    m = _CA_FSA_RE.match(" ".join((query or "").split()))
    if not m:
        return None
    hit = _ca_fsa().get(m.group(1).upper())
    if not hit:
        return None
    lat, lng, name, prov = hit
    label = f"{m.group(1).upper()} {name}, {_CA_PROVINCES.get(prov, prov)}, Canada"
    return {"lat": round(lat, 4), "lng": round(lng, 4), "label": label,
            "country": "Canada", "approx": True}


def normalize(query: str) -> str:
    """Clé de cache : espaces compactés, minuscules, longueur bornée."""
    return " ".join((query or "").split()).strip().lower()[:MAX_QUERY_LEN]


def looks_like_postal_code(query: str) -> bool:
    """Heuristique d'affichage : code postal FR/BE/CH (4-5 chiffres), CA
    (A1A 1A1 ou A1A), US (5 chiffres), UK (mix court)."""
    q = normalize(query).replace(" ", "")
    if not q or len(q) > 8:
        return False
    return any(ch.isdigit() for ch in q) and all(ch.isalnum() for ch in q)


def _result_matches_postal_query(query: str, item: dict) -> bool:
    """Pour une saisie qui ressemble a un code postal, exige que le resultat
    porte un code postal commencant par la saisie (espaces ignores) ou soit
    lui-meme de type `postcode`. Les saisies libres (villes, adresses) ne
    sont pas filtrees."""
    if not looks_like_postal_code(query):
        return True
    wanted = normalize(query).replace(" ", "")
    got = normalize((item.get("address") or {}).get("postcode") or "").replace(" ", "")
    if got and got.startswith(wanted):
        return True
    return (item.get("type") == "postcode") and not got


def _short_label(item: dict, fallback: str) -> str:
    """Libellé court et lisible (« 75011 Paris, France ») plutôt que le
    display_name Nominatim (10 segments)."""
    addr = item.get("address") or {}
    locality = (addr.get("city") or addr.get("town") or addr.get("village")
                or addr.get("municipality") or addr.get("county") or "")
    postcode = addr.get("postcode") or ""
    country = addr.get("country") or ""
    parts = [p for p in [" ".join(x for x in [postcode, locality] if x), country] if p]
    return ", ".join(parts) or (item.get("display_name") or fallback)[:80]


def _fetch(query: str, country: str = "", lang: str = "fr") -> Optional[dict]:
    """Appel réseau Nominatim (1 résultat). Isolé pour être mocké en test."""
    global _last_call
    q = query if not country else f"{query}, {country}"
    params = {
        "q": q, "format": "jsonv2", "limit": 1,
        "addressdetails": 1, "accept-language": lang or "fr",
    }
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("geocode indisponible pour %r : %s", q, exc)
            return None
    if not data:
        return None
    item = data[0]
    if not _result_matches_postal_query(query, item):
        # Nominatim renvoie parfois un lieu sans rapport pour un code postal
        # inconnu (ex. « H2X 0A1 » -> un hameau au bout du monde). Mieux vaut
        # « introuvable » qu'un faux centre de recherche.
        log.info("geocode: resultat rejete pour %r (%s)", q, item.get("display_name", "")[:60])
        return None
    try:
        lat, lng = float(item["lat"]), float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "lat": round(lat, 5), "lng": round(lng, 5),
        "label": _short_label(item, q),
        "country": (item.get("address") or {}).get("country") or "",
    }


def lookup(query: str, country: str = "", lang: str = "fr") -> Optional[dict]:
    """Géocode `query` (avec `country` en indice facultatif). Cache DB
    (positif ET négatif). Retourne {lat, lng, label, country} ou None."""
    key = normalize(query)
    if len(key) < 2:
        return None
    cache_key = f"{key}|{normalize(country)}" if country else key
    try:
        row = db.fetchone(
            "SELECT lat, lng, label, country, found FROM geocode_cache WHERE q=?",
            (cache_key,),
        )
    except Exception as exc:          # table absente (migration pas passée)
        log.warning("geocode cache illisible : %s", exc)
        row = None
    if row is not None:
        if not row["found"]:
            return None
        return {"lat": row["lat"], "lng": row["lng"],
                "label": row["label"], "country": row["country"]}

    fsa = canada_fsa(query)
    if fsa and len(normalize(query).replace(" ", "")) == 3:
        # RTA seule (« G5Y ») : Nominatim ne sait pas, la table locale si.
        res = fsa
    else:
        res = _fetch(query.strip(), country.strip(), lang)
        if res is None and country:
            # Repli : sans l'indice pays (utilisateur qui a laissé un pays
            # incohérent avec sa saisie, ex. « Bruxelles » + pays France).
            res = _fetch(query.strip(), "", lang)
        if res is None and fsa:
            # Code complet inconnu d'OSM -> centroïde de sa RTA (approx.).
            res = fsa
    try:
        db.execute(
            "INSERT OR REPLACE INTO geocode_cache "
            "(q, lat, lng, label, country, found) VALUES (?,?,?,?,?,?)",
            (cache_key,
             res["lat"] if res else None, res["lng"] if res else None,
             res["label"] if res else None, res["country"] if res else None,
             1 if res else 0),
        )
    except Exception as exc:
        log.warning("geocode cache non écrit : %s", exc)
    return res
