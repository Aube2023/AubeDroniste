"""Pays d'un visiteur, en local, sans service externe ni traceur.

Le VPS a deja la base pays de MaxMind (paquet Debian `geoip-database`,
/usr/share/GeoIP/GeoIP.dat) et la bibliotheque C `libGeoIP`. On l'appelle par
ctypes : pas de dependance Python a installer, pas d'appel reseau, l'IP ne
sort jamais de la machine.

Ce que l'app en fait (voir app._count_visit) : elle resout le pays a la volee
et n'incremente qu'un compteur `pays -> nombre de pages vues`. **Aucune IP
n'est stockee**, rien ne permet de reconnaitre un visiteur. C'est ce qui
distingue ce comptage d'un traqueur.

Sans la bibliotheque ni la base (poste de dev macOS, image minimale), tout
retourne None et l'app continue : le pays vaut simplement « inconnu ».
"""
import ctypes
import ctypes.util
import ipaddress
import json
import logging
import os
import threading
from typing import Optional

log = logging.getLogger("aubepilot.geoip")

# Emplacements Debian/Ubuntu du paquet geoip-database.
DB_PATHS = (
    os.environ.get("GEOIP_DB", ""),
    "/usr/share/GeoIP/GeoIP.dat",
    "/var/lib/GeoIP/GeoIP.dat",
)
GEOIP_MEMORY_CACHE = 1

_lib = None
_handle = None
_lock = threading.Lock()
_loaded = False


def _load():
    """Ouvre la base une fois. Toute absence est silencieuse (mode degrade)."""
    global _lib, _handle, _loaded
    if _loaded:
        return
    _loaded = True
    path = next((p for p in DB_PATHS if p and os.path.exists(p)), None)
    if not path:
        log.info("GeoIP : aucune base trouvee, pays des visiteurs desactive")
        return
    name = ctypes.util.find_library("GeoIP")
    if not name:
        log.info("GeoIP : libGeoIP absente, pays des visiteurs desactive")
        return
    try:
        lib = ctypes.CDLL(name)
        lib.GeoIP_open.restype = ctypes.c_void_p
        lib.GeoIP_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.GeoIP_country_code_by_addr.restype = ctypes.c_char_p
        lib.GeoIP_country_code_by_addr.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        handle = lib.GeoIP_open(path.encode(), GEOIP_MEMORY_CACHE)
        if not handle:
            log.warning("GeoIP : ouverture de %s impossible", path)
            return
        _lib, _handle = lib, handle
        log.info("GeoIP : base %s chargee", path)
    except Exception as exc:                       # bibliotheque incompatible
        log.warning("GeoIP indisponible : %s", exc)


def available() -> bool:
    _load()
    return _handle is not None


def country_code(ip: str) -> Optional[str]:
    """Code ISO2 majuscule, ou None (IP privee, inconnue, base absente).

    IPv6 : la base v4 ne repond pas, sauf pour les adresses mappees
    (::ffff:1.2.3.4) qu'on ramene a leur forme v4.
    """
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    # Machine locale, reseau interne : rien a geolocaliser.
    if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
        return None
    if addr.version != 4:
        return None
    _load()
    if _handle is None or _lib is None:
        return None
    try:
        with _lock:                                # libGeoIP n'est pas reentrante
            code = _lib.GeoIP_country_code_by_addr(
                ctypes.c_void_p(_handle), str(addr).encode())
    except Exception as exc:
        log.warning("GeoIP : echec sur une adresse (%s)", exc)
        return None
    if not code:
        return None
    code = code.decode("ascii", "ignore").upper()
    return code or None


# ---------------------------------------------------------------------------
# Noms de pays : ISO2 -> nom francais (geodata/country_iso2.json, genere depuis
# libGeoIP). i18n.country_name() traduit ensuite dans la langue de la page.
# ---------------------------------------------------------------------------

_NAMES: dict = {}
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "geodata", "country_iso2.json"), encoding="utf-8") as _f:
        _NAMES = json.load(_f)
except (OSError, ValueError):
    _NAMES = {}


def country_fr(code: str) -> str:
    """Nom francais du pays, ou le code lui-meme s'il est inconnu."""
    return _NAMES.get((code or "").upper(), code or "")


def flag(code: str) -> str:
    """Drapeau emoji depuis le code ISO2 (indicateurs regionaux Unicode)."""
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return "🌐"
    return chr(0x1F1E6 + ord(code[0]) - 65) + chr(0x1F1E6 + ord(code[1]) - 65)
