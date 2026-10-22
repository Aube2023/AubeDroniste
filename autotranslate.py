"""Traduction automatique des titres de réalisations.

Le pilote nomme ses photos et vidéos dans sa langue (« toiture », « pont
Rem ») ; un visiteur qui lit le site dans une autre langue voit le titre
traduit (« paa », « daraja la Rem »), avec la mention « traduit
automatiquement » et l'original en infobulle. Galerie /realisations, accueil
et fiche du pilote.

Implémentation :
- MyMemory (Translated, Italie), gratuit, sans clé : 5 000 caractères par
  jour, 50 000 avec une adresse de contact (paramètre `de`, la même que le
  User-Agent du géocodage). Traduction machine seule (`onlyprivate=1` :
  sans clé, aucune mémoire partagée n'est consultée) : ces mémoires
  renvoient n'importe quoi, même notées 100 % (« Ua », une fleur, pour
  « photo » en swahili ; « Defi » en chinois ; « Disc. » pour « pont Rem »).
- Jamais d'appel réseau pendant l'affichage d'une page : la page lit le
  cache (table `text_translations`) et confie les titres manquants à un fil
  de fond ; les pages suivantes les montrent traduits. Seules les langues
  réellement visitées sont demandées, ce qui ménage le quota.
- Seul le texte du titre, déjà public, part chez MyMemory, depuis notre
  serveur : ni l'adresse IP du visiteur, ni son compte, ni le nom du pilote.
- Ne lève jamais : panne ou quota épuisé, le titre reste dans sa langue et
  le service se tait un moment (10 min, ou jusqu'au lendemain pour le quota).

Langue de l'original : celle du compte du pilote (users.lang), le français
sinon. `AUBEPILOT_AUTO_TRANSLATE=0` éteint tout (titres d'origine partout).
"""
import html
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import db
import i18n
from config import AUTO_TRANSLATE_ENABLED, MYMEMORY_URL
from geocode import CONTACT, USER_AGENT

log = logging.getLogger("aubepilot.autotranslate")

ENABLED = AUTO_TRANSLATE_ENABLED
TIMEOUT_S = 8.0
MIN_INTERVAL_S = 0.5          # un appel à la fois, une demi-seconde d'écart
BACKOFF_S = 600               # panne réseau : on se tait 10 minutes
MAX_LEN = 200                 # longueur d'un titre (services.add_portfolio_item)
_CODES = {"zh": "zh-CN"}      # codes MyMemory qui diffèrent des nôtres

_lock = threading.Lock()      # protège tout ce qui suit
_queue: deque = deque()       # (src_lang, texte, lang) à traduire
_pending: set = set()         # les mêmes, pour ne pas les demander deux fois
_paused_until = 0.0
_worker: Optional[threading.Thread] = None

_PRUNE_SQL = ("DELETE FROM text_translations WHERE src_text NOT IN "
              "(SELECT title FROM pilot_portfolio_items WHERE title IS NOT NULL)")


class _Unavailable(Exception):
    """MyMemory ne répond pas ou refuse (quota) : se taire `pause` secondes."""

    def __init__(self, pause: float):
        super().__init__(pause)
        self.pause = pause


def _until_tomorrow() -> float:
    """Secondes jusqu'à minuit UTC passé de cinq minutes (remise du quota)."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


def _tidy(src: str, out: str) -> str:
    """MyMemory rend sa traduction en minuscules (« rem bridge ») : on remet
    les mots que le pilote a écrits avec une majuscule (« Rem », « Crémazie »,
    des noms propres) et la casse de la première lettre de l'original."""
    out = html.unescape(out).strip()
    for word in set(re.findall(r"\w+", src)):
        if word != word.lower():
            out = re.sub(r"\b" + re.escape(word.lower()) + r"\b", word, out)
    if out and src[:1].isupper():
        out = out[:1].upper() + out[1:]
    return out[:MAX_LEN]


def _fetch(text: str, src: str, lang: str) -> str:
    """Appel réseau MyMemory. Isolé pour être mocké en test. Retourne la
    traduction, '' quand MyMemory n'en propose pas ; lève _Unavailable en
    cas de panne ou de quota épuisé (on réessaiera plus tard)."""
    params = {"q": text, "langpair": f"{_CODES.get(src, src)}|{_CODES.get(lang, lang)}",
              "mt": "1", "onlyprivate": "1"}
    if CONTACT:
        params["de"] = CONTACT
    req = urllib.request.Request(MYMEMORY_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log.warning("MyMemory HTTP %s pour %s|%s", exc.code, src, lang)
        raise _Unavailable(_until_tomorrow() if exc.code == 429 else BACKOFF_S)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("MyMemory indisponible : %s", exc)
        raise _Unavailable(BACKOFF_S)
    status = str(data.get("responseStatus"))
    if data.get("quotaFinished") or status == "429":
        log.warning("MyMemory : quota du jour épuisé")
        raise _Unavailable(_until_tomorrow())
    if status != "200":
        # 403 : paire de langues refusée, texte invalide. Rien à traduire.
        log.info("MyMemory %s pour %s|%s : %s", status, src, lang, data.get("responseDetails"))
        return ""
    for match in data.get("matches") or []:
        if match.get("created-by") == "MT!" and match.get("translation"):
            return _tidy(text, str(match["translation"]))
    return ""


def _store(src: str, text: str, lang: str, out: str) -> None:
    """Met la traduction en cache ; l'original lui-même quand MyMemory n'a
    rien, pour ne pas le redemander à chaque page."""
    try:
        with db.standalone() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO text_translations (src_lang, src_text, lang, text) "
                "VALUES (?, ?, ?, ?)", (src, text, lang, out or text),
            )
    except Exception as exc:
        log.warning("traduction non mise en cache : %s", exc)


def _drain() -> None:
    """Fil de fond : traduit la file un titre à la fois, puis fait le ménage
    (un titre supprimé pendant sa traduction) et s'arrête."""
    global _paused_until, _worker
    while True:
        with _lock:
            job = _queue.popleft() if _queue else None
            if job is None:
                _worker = None            # une demande arrivée pendant le ménage relance un fil
        if job is None:
            try:
                with db.standalone() as conn:
                    conn.execute(_PRUNE_SQL)
            except Exception as exc:
                log.warning("ménage du cache de traduction : %s", exc)
            return
        src, text, lang = job
        try:
            out = _fetch(text, src, lang)
        except _Unavailable as exc:
            with _lock:
                _paused_until = time.time() + exc.pause
                _queue.clear()
                _pending.clear()
            return
        except Exception as exc:          # jamais de fil mort en silence
            log.warning("traduction de %r en %s : %s", text, lang, exc)
            out = ""
        _store(src, text, lang, out)
        with _lock:
            _pending.discard(job)
        time.sleep(MIN_INTERVAL_S)


def _start() -> None:
    """Lance le fil de fond s'il ne tourne pas déjà (appelé sous _lock)."""
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_drain, name="autotranslate", daemon=True)
        _worker.start()


def _schedule(pairs: set, lang: str) -> None:
    with _lock:
        if time.time() < _paused_until:
            return
        for src, text in pairs:
            job = (src, text, lang)
            if job not in _pending:
                _pending.add(job)
                _queue.append(job)
        if _queue:
            _start()


def _cached(texts: set, lang: str) -> dict:
    """{(src_lang, texte): traduction} déjà en cache pour `lang`."""
    originals = sorted({t for _, t in texts})
    if not originals:
        return {}
    try:
        rows = db.fetchall(
            f"SELECT src_lang, src_text, text FROM text_translations "
            f"WHERE lang=? AND src_text IN ({','.join('?' * len(originals))})", [lang, *originals],
        )
    except Exception as exc:              # table absente (migration pas passée)
        log.warning("cache de traduction illisible : %s", exc)
        return {}
    return {(r["src_lang"], r["src_text"]): r["text"] for r in rows}


def localize_titles(items: list, lang: str, src_lang: Optional[str] = None) -> list:
    """Pose sur chaque pièce de portfolio `title_shown` (le titre dans la
    langue de la page quand il est traduit, sinon l'original), `title_lang`
    (la langue de ce texte, pour l'attribut lang), `src_lang` (celle du
    pilote) et `title_original` (l'original, seulement s'il est traduit).
    La langue du pilote vient de `it['src_lang']`, sinon de `src_lang`."""
    wanted = []
    for it in items:
        title = (it.get("title") or "").strip()
        src = it.get("src_lang") or src_lang
        src = src if src in i18n.SUPPORTED else i18n.DEFAULT
        it.update(title_shown=title, title_lang=src, src_lang=src, title_original="")
        if title and ENABLED and lang in i18n.SUPPORTED and lang != src:
            wanted.append(it)
    if not wanted:
        return items
    found = _cached({(it["src_lang"], it["title_shown"]) for it in wanted}, lang)
    missing = set()
    for it in wanted:
        key = (it["src_lang"], it["title_shown"])
        out = found.get(key)
        if out is None:
            missing.add(key)
        elif out != key[1]:
            it.update(title_shown=out, title_lang=lang, title_original=key[1])
    if missing:
        _schedule(missing, lang)
    return items


def prune() -> None:
    """Oublie les traductions des titres qui n'existent plus (pièce
    supprimée, titre modifié, compte effacé) : rien ne survit à l'original."""
    try:
        db.execute(_PRUNE_SQL)
    except Exception as exc:
        log.warning("ménage du cache de traduction : %s", exc)
