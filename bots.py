"""Visiteur ou robot ? Le tri qui alimente la frequentation (admin/visites).

Le User-Agent seul ne suffit plus : le 13 septembre 2026, deux collecteurs
se sont presentes comme Chrome (deux mille adresses, une page chacune,
dix agents pris au hasard, Referer « google.com » de complaisance) et ont
gonfle les « visiteurs » a cinq mille dans la journee. On regarde donc ce
qu'un vrai navigateur envoie TOUJOURS avec une page :

- Accept-Language : aucun navigateur ne l'omet ;
- Accept qui demande du HTML (un client HTTP nu envoie « */* » ou rien) ;
- Sec-Fetch-Mode / Sec-Fetch-Dest : Chrome >= 76, Firefox >= 90 ;
- Sec-CH-UA : tout Chromium >= 89 (Chrome, Edge, Opera, Brave, Samsung).

Le collecteur le plus soigne envoie tout cela (HTTP/2 compris). Il se trahit
ailleurs : un Referer « https://pilot.aubeetoilee.com » sans la barre finale
(un navigateur serialise toujours le chemin), un Referer qui est notre propre
redirection /lang/xx (apres un 302 un navigateur garde la page d'origine
comme referent, jamais l'URL intermediaire), et des Client Hints qui ne
racontent pas la meme histoire que le User-Agent (version, plateforme,
mobile) parce que l'agent est tire au sort a chaque requete.

Rien n'est conserve : la fonction rend un verdict, le compteur s'incremente,
les en-tetes sont oublies. Pas de cookie, pas d'adresse IP, pas d'empreinte.
"""
import re
from typing import Mapping, Optional
from urllib.parse import urlsplit

# Robots connus, avoues par leur agent (GPTBot, Semrush, curl...).
UA_MARKERS = ("bot", "crawler", "spider", "slurp", "curl", "wget", "python-requests",
              "headlesschrome", "monitor", "preview", "scan", "http-client",
              "facebookexternalhit", "aubestatus", "go-http", "okhttp", "java/",
              "libwww", "httpx", "aiohttp", "scrapy", "node-fetch", "axios")

# Chromium qui n'envoient pas (ou pas surement) les Client Hints : WebView
# Android (« wv »), notre application, navigateurs a noyau modifie.
_NO_CLIENT_HINTS = ("wv)", "aubepilotmobile", "ucbrowser", "miuibrowser", "opera mini",
                    "mqqbrowser", "electron")

# Sec-CH-UA-Platform -> ce que le User-Agent doit alors contenir.
_PLATFORM_MARKS = {
    "windows": ("Windows",),
    "macos": ("Mac OS X", "Macintosh"),
    "android": ("Android",),
    "chrome os": ("CrOS",),
}

_CHROME = re.compile(r"Chrome/(\d+)")
_FIREFOX = re.compile(r"Firefox/(\d+)")
_CH_VERSIONS = re.compile(r'v="(\d+)')
_ORIGIN = re.compile(r"^https?://[^/?#]+$", re.I)


def _major(rx, ua: str) -> int:
    m = rx.search(ua)
    return int(m.group(1)) if m else 0


def reason(headers: Mapping[str, str]) -> Optional[str]:
    """Pourquoi cette requete de page n'est pas un visiteur, ou None si elle
    en a tout l'air. `headers` : les en-tetes de la requete (insensibles a
    la casse, comme `flask.request.headers`)."""
    ua = (headers.get("User-Agent") or "").strip()
    low = ua.lower()
    if not ua:
        return "no-ua"
    if any(m in low for m in UA_MARKERS):
        return "ua"

    # Prefetch / prerender : le navigateur charge d'avance, personne ne lit.
    purpose = (headers.get("Sec-Purpose") or headers.get("Purpose") or "").lower()
    if "prefetch" in purpose or "prerender" in purpose:
        return "prefetch"

    if not (headers.get("Accept-Language") or "").strip():
        return "no-accept-language"
    accept = (headers.get("Accept") or "").lower()
    if "text/html" not in accept:
        return "accept"

    # Metadonnees de navigation : presentes, elles doivent decrire une page.
    mode = (headers.get("Sec-Fetch-Mode") or "").lower()
    dest = (headers.get("Sec-Fetch-Dest") or "").lower()
    if (mode and mode != "navigate") or (dest and dest != "document"):
        return "not-navigation"

    # Referent impossible pour un navigateur (voir en tete de module).
    ref = (headers.get("Referer") or "").strip()
    own = (headers.get("Host") or "").lower().split(":")[0]
    # Sec-Fetch-Site: none = navigation tapee ou favori : un navigateur
    # n'y joint jamais de Referer. Le collecteur du 13 septembre le fait.
    if ref and (headers.get("Sec-Fetch-Site") or "").lower() == "none":
        return "referer-with-site-none"
    if ref and own:
        try:
            parts = urlsplit(ref)
        except ValueError:
            parts = None
        if parts and (parts.hostname or "").lower() == own:
            if _ORIGIN.match(ref):
                return "referer-origin"
            if parts.path.startswith("/lang/"):
                return "referer-redirect"

    chrome = _major(_CHROME, ua)
    firefox = _major(_FIREFOX, ua)
    if (chrome >= 76 or firefox >= 90) and not mode:
        return "no-sec-fetch"
    ch_ua = headers.get("Sec-CH-UA") or ""
    if chrome >= 89 and not ch_ua and not any(m in low for m in _NO_CLIENT_HINTS):
        return "no-client-hints"

    # Client Hints et User-Agent doivent raconter la meme histoire.
    if chrome and ch_ua:
        versions = {int(v) for v in _CH_VERSIONS.findall(ch_ua)}
        if versions and chrome not in versions:
            return "client-hints-mismatch"
    platform = (headers.get("Sec-CH-UA-Platform") or "").strip('"').lower()
    if platform and platform in _PLATFORM_MARKS \
            and not any(m in ua for m in _PLATFORM_MARKS[platform]):
        return "platform-mismatch"
    mobile = (headers.get("Sec-CH-UA-Mobile") or "").strip()
    if mobile == "?1" and "Mobile" not in ua and "Android" not in ua:
        return "mobile-mismatch"
    return None


def is_bot(headers: Mapping[str, str]) -> bool:
    return reason(headers) is not None


_DESCRIBE = ("User-Agent", "Accept", "Accept-Language", "Accept-Encoding", "Referer",
             "Sec-Fetch-Mode", "Sec-Fetch-Dest", "Sec-Fetch-Site", "Sec-Fetch-User",
             "Sec-CH-UA", "Sec-CH-UA-Mobile", "Sec-CH-UA-Platform", "Upgrade-Insecure-Requests",
             "Priority", "Cache-Control", "DNT")


def describe(headers: Mapping[str, str]) -> str:
    """Les en-tetes qui servent au verdict, pour le journal quand on etudie un
    collecteur : ni adresse, ni cookie, ni rien qui designe une personne."""
    out = [f"{k}={headers.get(k)!r}" for k in _DESCRIBE if headers.get(k)]
    if headers.get("Cookie"):
        names = sorted({c.split("=", 1)[0].strip() for c in headers["Cookie"].split(";") if c.strip()})
        out.append("Cookie-names=" + ",".join(names))
    return " ".join(out)
