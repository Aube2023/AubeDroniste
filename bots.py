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

Rien n'est conserve : la fonction rend un verdict, le compteur s'incremente,
les en-tetes sont oublies. Pas de cookie, pas d'adresse IP, pas d'empreinte.
"""
import re
from typing import Mapping, Optional

# Robots connus, avoues par leur agent (GPTBot, Semrush, curl...).
UA_MARKERS = ("bot", "crawler", "spider", "slurp", "curl", "wget", "python-requests",
              "headlesschrome", "monitor", "preview", "scan", "http-client",
              "facebookexternalhit", "aubestatus", "go-http", "okhttp", "java/",
              "libwww", "httpx", "aiohttp", "scrapy", "node-fetch", "axios")

# Chromium qui n'envoient pas (ou pas surement) les Client Hints : WebView
# Android (« wv »), notre application, navigateurs a noyau modifie.
_NO_CLIENT_HINTS = ("wv)", "aubepilotmobile", "ucbrowser", "miuibrowser", "opera mini",
                    "mqqbrowser", "electron")

_CHROME = re.compile(r"Chrome/(\d+)")
_FIREFOX = re.compile(r"Firefox/(\d+)")


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

    chrome = _major(_CHROME, ua)
    firefox = _major(_FIREFOX, ua)
    if (chrome >= 76 or firefox >= 90) and not mode:
        return "no-sec-fetch"
    if chrome >= 89 and not headers.get("Sec-CH-UA") \
            and not any(m in low for m in _NO_CLIENT_HINTS):
        return "no-client-hints"
    return None


def is_bot(headers: Mapping[str, str]) -> bool:
    return reason(headers) is not None
