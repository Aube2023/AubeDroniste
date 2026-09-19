"""Opportunités pour les pros du drone : appels d'offres publics repris de
sources OUVERTES, avec leur licence, leur source et un lien vers l'avis.

Règles (le service est gratuit et doit rester irréprochable) :
- uniquement des jeux de données ouverts publiés par les organismes
  eux-mêmes : CanadaBuys (Licence du gouvernement ouvert, Canada) et le SEAO
  du Québec (Données Québec, CC BY 4.0) ; pas de scraping de sites qui
  l'interdisent, pas de sites d'emploi sans flux officiel ;
- on republie le titre, l'organisme, la région, la date de clôture et un
  court résumé (400 caractères), jamais l'avis complet ; chaque fiche cite
  sa source et renvoie vers l'avis d'origine ;
- une collecte par nuit, avec un User-Agent qui nous identifie.

`collect()` est appelé par scripts/collect_opportunities.py (minuterie
systemd sur le VPS) ; les requêtes de lecture sont dans services.py.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import urllib.request
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

import db
from config import DATA_DIR

log = logging.getLogger("aubepilot.opportunities")

USER_AGENT = "AubePilot-opportunites/1.0 (+https://pilot.aubeetoilee.com/opportunites)"
CANADABUYS_CSV = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"
CANADABUYS_NOTICE_FR = "https://canadabuys.canada.ca/fr/appels-doffres/avis-dappel-doffres/{ref}"
CANADABUYS_NOTICE_EN = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/{ref}"
SEAO_PACKAGE = ("https://www.donneesquebec.ca/recherche/api/3/action/package_show"
                "?id=systeme-electronique-dappel-doffres-seao")
SEAO_WEEKLY_FILES = 6          # fichiers hebdomadaires relus (un avis ouvert reste actif plusieurs semaines)
SUMMARY_CHARS = 400
STATE_FILE = os.path.join(DATA_DIR, "opportunities_state.json")

SOURCES = {
    "canadabuys": {"label": "CanadaBuys", "licence": "Licence du gouvernement ouvert – Canada",
                   "url": "https://canadabuys.canada.ca/fr/a-propos-de-nous/donnees-ouvertes"},
    "seao": {"label": "SEAO (Québec)", "licence": "Données Québec, CC BY 4.0",
             "url": "https://www.donneesquebec.ca/recherche/dataset/systeme-electronique-dappel-doffres-seao"},
}

# Ce qui concerne un professionnel du drone. Le mot « aérien » seul ne suffit
# pas (ravitailleurs, fret aérien...) : il faut un terme métier.
KEYWORDS = re.compile(
    r"\b(drones?|rpas|satp|uavs?|uas|sua|unmanned (?:aircraft|aerial|air) ?(?:system|vehicle)?s?|"
    r"a[ée]ronefs? (?:t[ée]l[ée]pilot|sans [ée]quipage|sans pilote)\w*|t[ée]l[ée]pilot\w*|remotely piloted\w*|"
    r"lidar|photogramm\w*|orthophoto\w*|orthoimage\w*|orthomosa\w*|"
    r"aerial (?:imagery|survey\w*|photograph\w*|mapping|inspection|lidar|thermograph\w*)|"
    r"imagerie a[ée]rienne|relev[ée]s? a[ée]riens?|inspection a[ée]rienne|cartographie a[ée]rienne|"
    r"captation a[ée]rienne|prises? de vues? a[ée]riennes?|thermographie a[ée]rienne)\b",
    re.I,
)

# Mots-clés -> spécialités AubePilot (codes de config.MISSION_TYPES). Règles
# volontairement étroites : mieux vaut aucune spécialité qu'une fausse.
SPECIALTY_RULES = [
    (re.compile(r"\blidar\b|\btopograph\w*|\barpentage\b|lev[ée]s? (?:laser|de terrain|a[ée]riens?)|\bland survey\w*", re.I), ("topographie",)),
    (re.compile(r"photogramm\w*|mod[ée]lisation 3d|\b3d model\w*", re.I), ("3d",)),
    (re.compile(r"orthophoto\w*|orthoimage\w*|orthomosa\w*|cartograph\w*|\bmapping\b|imagerie a[ée]rienne|aerial imagery", re.I), ("mapping",)),
    (re.compile(r"\binspection\b", re.I), ("inspection",)),
    (re.compile(r"thermograph\w*|imagerie thermique|thermal imag\w*", re.I), ("thermographie",)),
    (re.compile(r"\bagricol\w*|\bagricultur\w*|\bndvi\b|\bcrop\b", re.I), ("agriculture",)),
    (re.compile(r"\bforest\w*|\bfor[êe]ts?\b|\bforesterie\b|\bsylvic\w*", re.I), ("foresterie",)),
    (re.compile(r"feux? de for[êe]t|\bwildfire", re.I), ("feux_foret",)),
    (re.compile(r"\bphotographies? a[ée]rienne|\baerial photograph\w*|prises? de vues? a[ée]riennes?", re.I), ("photo",)),
    (re.compile(r"\bvid[ée]o(?:graph\w*)? a[ée]rienne|\baerial (?:video|film)\w*|\btournage\b", re.I), ("video",)),
    (re.compile(r"\bformation (?:de|des|au|aux|en|sur) (?:pilot|t[ée]l[ée]pilot|drone|rpas|satp)|\bdrone (?:pilot )?training\b|\brpas training\b", re.I), ("formation",)),
]

# Termes qui signalent un avis SUR les drones mais pas POUR un pilote (lutte
# anti-drone, véhicules de surface ou terrestres sans équipage).
NOISE = re.compile(r"anti-?drones?|contre les (?:drones|syst[èe]mes d.a[ée]ronef)|counter-?(?:uas|drone)|"
                   r"v[ée]hicule (?:de surface|terrestre) sans [ée]quipage|unmanned (?:surface|ground) vehicle|"
                   r"\bvsse\b|\busv\b|\bugv\b", re.I)

# Provinces et territoires : libellé de référence (français) et variantes.
PROVINCES = {
    "Québec": ("québec", "quebec", "qc"),
    "Ontario": ("ontario", "on"),
    "Colombie-Britannique": ("colombie-britannique", "british columbia", "bc", "c.-b."),
    "Alberta": ("alberta", "ab"),
    "Manitoba": ("manitoba", "mb"),
    "Saskatchewan": ("saskatchewan", "sk"),
    "Nouvelle-Écosse": ("nouvelle-écosse", "nova scotia", "ns", "n.-é."),
    "Nouveau-Brunswick": ("nouveau-brunswick", "new brunswick", "nb", "n.-b."),
    "Île-du-Prince-Édouard": ("île-du-prince-édouard", "prince edward island", "pe", "î.-p.-é."),
    "Terre-Neuve-et-Labrador": ("terre-neuve-et-labrador", "newfoundland and labrador", "nl", "t.-n.-l."),
    "Yukon": ("yukon", "yt"),
    "Territoires du Nord-Ouest": ("territoires du nord-ouest", "northwest territories", "nt", "t.n.-o."),
    "Nunavut": ("nunavut", "nu"),
}
# Villes fréquentes des avis fédéraux -> province (l'avis ne donne parfois que la ville).
CITY_PROVINCE = {
    "montréal": "Québec", "montreal": "Québec", "québec": "Québec", "gatineau": "Québec", "laval": "Québec",
    "ottawa": "Ontario", "toronto": "Ontario", "kingston": "Ontario", "petawawa": "Ontario", "trenton": "Ontario",
    "région de la capitale nationale": "Ontario", "national capital region": "Ontario", "rcn": "Ontario", "ncr": "Ontario",
    "vancouver": "Colombie-Britannique", "victoria": "Colombie-Britannique", "esquimalt": "Colombie-Britannique",
    "agassiz": "Colombie-Britannique", "comox": "Colombie-Britannique",
    "edmonton": "Alberta", "calgary": "Alberta", "wainwright": "Alberta", "cold lake": "Alberta", "suffield": "Alberta",
    "winnipeg": "Manitoba", "shilo": "Manitoba", "regina": "Saskatchewan", "saskatoon": "Saskatchewan",
    "halifax": "Nouvelle-Écosse", "greenwood": "Nouvelle-Écosse", "moncton": "Nouveau-Brunswick",
    "gagetown": "Nouveau-Brunswick", "fredericton": "Nouveau-Brunswick", "charlottetown": "Île-du-Prince-Édouard",
    "st. john's": "Terre-Neuve-et-Labrador", "goose bay": "Terre-Neuve-et-Labrador", "iqaluit": "Nunavut",
    "yellowknife": "Territoires du Nord-Ouest", "whitehorse": "Yukon",
}


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# Journal des modifications en tête des avis fédéraux (« ***MODIFICATION 002 -
# Une pièce jointe a été ajoutée. ... ») : on saute ces paragraphes pour
# résumer le fond de l'avis, pas son historique.
_AMENDMENT_PARA = re.compile(
    r"^\W*(?:modification|amendment|amend\.?|addenda|addendum|erratum|correction|"
    r"prière de noter|please note|q\s*(?:et|and|&)\s*r|q\s*&\s*a)\b", re.I)


def summarize(text: str, limit: int = SUMMARY_CHARS) -> str:
    """Résumé court : premiers paragraphes de fond (journal des modifications
    sauté), espaces normalisés, coupé au mot, jamais l'avis complet."""
    paras = [re.sub(r"\s+", " ", x).strip() for x in re.split(r"\n\s*\n|\r\n\s*\r\n", text or "")]
    paras = [x for x in paras if x]
    body = [x for x in paras if not _AMENDMENT_PARA.match(x) and len(x) > 40]
    text = " ".join(body or paras)
    # certaines lignes de modification sont collées au fond par des « *** »
    text = re.sub(r"(?:\*{2,}\s*(?:modification|amendment)[^*]{0,200}\*{0,3}\s*)+", " ", text, flags=re.I).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


def matches(title: str, body: str = "") -> bool:
    """Pertinent si un terme métier figure dans le titre, ou revient au moins
    deux fois dans le corps ; un avis « anti-drone » ou « véhicule sans
    équipage » (bateau, engin terrestre) est écarté."""
    if NOISE.search(title or ""):
        return False
    if KEYWORDS.search(title or ""):
        return True
    hits = KEYWORDS.findall(body or "")
    return len(hits) >= 2 and not NOISE.search(body or "")


def specialties_for(text: str) -> str:
    found: list = []
    for rx, codes in SPECIALTY_RULES:
        if rx.search(text or ""):
            for c in codes:
                if c not in found:
                    found.append(c)
    return ",".join(found[:4])


def normalize_region(raw: str) -> tuple:
    """(région de référence ou 'Canada', libellé brut nettoyé) à partir d'un
    champ multi-lignes du type « *Canada\\n*Ontario (sauf RCN) »."""
    parts = [p.strip(" *") for p in re.split(r"[\n,;]+", raw or "") if p.strip(" *")]
    clean = ", ".join(parts)
    for p in parts:
        low = p.lower()
        for ref, variants in PROVINCES.items():
            if any(low == v or low.startswith(v + " ") or low.startswith(v + " (") for v in variants):
                return ref, clean
    for p in parts:
        prov = CITY_PROVINCE.get(p.lower().split(" (")[0])
        if prov:
            return prov, clean
    return "Canada", clean


def _iso_date(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return m.group(1) if m else None


def _load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

UPSERT = """
INSERT INTO opportunities (source, source_ref, kind, title_fr, title_en, summary_fr, summary_en,
    org, country, region, regions_raw, city, url_fr, url_en, notice_type, category, specialties,
    published_at, closes_at, status, first_seen_at, last_seen_at)
VALUES (:source, :source_ref, :kind, :title_fr, :title_en, :summary_fr, :summary_en,
    :org, :country, :region, :regions_raw, :city, :url_fr, :url_en, :notice_type, :category, :specialties,
    :published_at, :closes_at, :status, :now, :now)
ON CONFLICT(source, source_ref) DO UPDATE SET
    title_fr=excluded.title_fr, title_en=excluded.title_en, summary_fr=excluded.summary_fr,
    summary_en=excluded.summary_en, org=excluded.org, region=excluded.region,
    regions_raw=excluded.regions_raw, city=excluded.city, url_fr=excluded.url_fr, url_en=excluded.url_en,
    notice_type=excluded.notice_type, category=excluded.category, specialties=excluded.specialties,
    published_at=COALESCE(excluded.published_at, opportunities.published_at),
    closes_at=excluded.closes_at, last_seen_at=excluded.last_seen_at,
    -- une fiche masquée par l'admin le reste ; un avis clos puis revu redevient publié
    status=CASE WHEN opportunities.status='hidden' THEN 'hidden' ELSE excluded.status END
"""


def _upsert_many(conn, items: Iterable[dict]) -> int:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for it in items:
        row = {"summary_fr": "", "summary_en": "", "city": "", "notice_type": "", "category": "", "specialties": "",
               "published_at": None, "closes_at": None, "regions_raw": "", "status": "published",
               "url_en": None, "now": now, **it}
        conn.execute(UPSERT, row)
        n += 1
    return n


# ---------------------------------------------------------------------------
# CanadaBuys (fédéral) : CSV quotidien des avis ouverts
# ---------------------------------------------------------------------------

CATEGORIES = {"SRV": "services", "SRVTGD": "services", "GD": "goods", "CNST": "construction"}


def parse_canadabuys(raw_csv: str) -> list:
    csv.field_size_limit(10 ** 8)
    best: dict = {}   # numero de sollicitation -> ligne la plus recente (une modification republie l'avis)
    for r in csv.DictReader(io.StringIO(raw_csv.lstrip("\ufeff"))):
        title_fr = (r.get("title-titre-fra") or "").strip()
        title_en = (r.get("title-titre-eng") or "").strip()
        desc_fr = r.get("tenderDescription-descriptionAppelOffres-fra") or ""
        desc_en = r.get("tenderDescription-descriptionAppelOffres-eng") or ""
        body = " ".join([desc_fr, desc_en, r.get("gsinDescription-nibsDescription-fra") or "", r.get("unspscDescription-eng") or ""])
        if not matches(title_fr + " " + title_en, body):
            continue
        ref = (r.get("referenceNumber-numeroReference") or "").strip()
        if not ref:
            continue
        key = (r.get("solicitationNumber-numeroSollicitation") or "").strip() or ref
        try:
            amendment = int(r.get("amendmentNumber-numeroModification") or 0)
        except ValueError:
            amendment = 0
        if key in best and best[key][0] >= amendment:
            continue
        region, regions_raw = normalize_region(r.get("regionsOfDelivery-regionsLivraison-fra") or "")
        cats = [c.strip(" *") for c in re.split(r"[\n,]+", r.get("procurementCategory-categorieApprovisionnement") or "") if c.strip(" *")]
        best[key] = (amendment, {
            "source": "canadabuys", "source_ref": key, "kind": "tender",
            "title_fr": title_fr or title_en, "title_en": title_en or title_fr,
            "summary_fr": summarize(desc_fr or desc_en), "summary_en": summarize(desc_en or desc_fr),
            "org": (r.get("contractingEntityName-nomEntitContractante-fra") or r.get("contractingEntityName-nomEntitContractante-eng") or "").strip(),
            "country": "Canada", "region": region, "regions_raw": regions_raw,
            "city": (r.get("contractingEntityAddressCity-entiteContractanteAdresseVille-fra") or "").strip(),
            "url_fr": CANADABUYS_NOTICE_FR.format(ref=ref), "url_en": CANADABUYS_NOTICE_EN.format(ref=ref),
            "notice_type": (r.get("noticeType-avisType-fra") or "").strip(),
            "category": next((CATEGORIES[c] for c in cats if c in CATEGORIES), ""),
            "specialties": specialties_for(title_fr + " " + title_en + " " + desc_fr + " " + desc_en),
            "published_at": _iso_date(r.get("publicationDate-datePublication")),
            "closes_at": _iso_date(r.get("tenderClosingDate-appelOffresDateCloture")),
        })
    return [v[1] for v in best.values()]


def collect_canadabuys(conn) -> dict:
    raw = _fetch(CANADABUYS_CSV).decode("utf-8", "replace")
    items = parse_canadabuys(raw)
    n = _upsert_many(conn, items)
    # Le CSV liste les avis OUVERTS : ceux qui n'y figurent plus sont clos.
    refs = [it["source_ref"] for it in items]
    conn.execute(
        "UPDATE opportunities SET status='closed' WHERE source='canadabuys' AND status='published' "
        "AND source_ref NOT IN (%s)" % ",".join("?" * len(refs)) if refs else
        "UPDATE opportunities SET status='closed' WHERE source='canadabuys' AND status='published'",
        refs,
    )
    return {"items": n}


# ---------------------------------------------------------------------------
# SEAO (Québec) : fichiers hebdomadaires OCDS sur Données Québec
# ---------------------------------------------------------------------------

def parse_seao(payload: dict) -> tuple:
    """(fiches à publier, ocid des avis clos/annulés) d'un fichier OCDS."""
    items, closed = [], []
    for rel in payload.get("releases") or []:
        tender = rel.get("tender") or {}
        tags = rel.get("tag") or []
        if not any(t in ("tender", "tenderUpdate", "tenderCancellation") for t in tags):
            continue
        title = tender.get("title") or ""
        body = " ".join([tender.get("description") or ""] + [i.get("description") or "" for i in tender.get("items") or []])
        haystack = title + " " + body
        if not matches(title, body):
            continue
        ocid = rel.get("ocid") or ""
        if not ocid:
            continue
        if tender.get("status") != "active":
            closed.append(ocid)
            continue
        buyer = next((p for p in rel.get("parties") or [] if "buyer" in (p.get("roles") or [])), {})
        addr = buyer.get("address") or {}
        region, _ = normalize_region(addr.get("region") or "QC")
        docs = tender.get("documents") or []
        url = (docs[0].get("url") if docs else "") or "https://seao.gouv.qc.ca/"
        summary = summarize(tender.get("description") or " · ".join(
            i.get("description") or "" for i in tender.get("items") or []))
        period = tender.get("tenderPeriod") or {}
        items.append({
            "source": "seao", "source_ref": ocid, "kind": "tender",
            "title_fr": tender.get("title") or "", "title_en": tender.get("title") or "",
            "summary_fr": summary, "summary_en": summary,
            "org": (rel.get("buyer") or {}).get("name") or buyer.get("name") or "",
            "country": "Canada", "region": region if region != "Canada" else "Québec",
            "regions_raw": "Québec", "city": addr.get("locality") or "",
            "url_fr": url, "url_en": url,
            "notice_type": tender.get("procurementMethodDetails") or "",
            "category": {"services": "services", "goods": "goods", "works": "construction"}.get(tender.get("mainProcurementCategory") or "", ""),
            "specialties": specialties_for(haystack),
            "published_at": _iso_date(period.get("startDate") or rel.get("date")),
            "closes_at": _iso_date(period.get("endDate")),
        })
    return items, closed


def seao_weekly_resources() -> list:
    """Derniers fichiers hebdomadaires, du plus ancien au plus récent."""
    pkg = json.loads(_fetch(SEAO_PACKAGE, timeout=60).decode("utf-8"))
    res = [r for r in pkg["result"]["resources"] if (r.get("name") or "").startswith("hebdo_")]
    res.sort(key=lambda r: r.get("name"))
    return res[-SEAO_WEEKLY_FILES:]


def collect_seao(conn, state: dict) -> dict:
    done = set(state.get("seao_files") or [])
    n_items = n_files = 0
    for r in seao_weekly_resources():
        name = r["name"]
        if name in done:
            continue
        payload = json.loads(_fetch(r["url"]).decode("utf-8"))
        items, closed = parse_seao(payload)
        n_items += _upsert_many(conn, items)
        for ocid in closed:
            conn.execute("UPDATE opportunities SET status='closed' WHERE source='seao' AND source_ref=? AND status='published'", (ocid,))
        done.add(name)
        n_files += 1
    state["seao_files"] = sorted(done)[-40:]
    return {"items": n_items, "files": n_files}


# ---------------------------------------------------------------------------
# Collecte complète
# ---------------------------------------------------------------------------

def expire(conn) -> int:
    """Un avis dont la date de clôture est passée n'est plus proposé."""
    cur = conn.execute("UPDATE opportunities SET status='closed' WHERE status='published' AND closes_at IS NOT NULL AND closes_at < ?",
                       (date.today().isoformat(),))
    return cur.rowcount


def collect() -> dict:
    """Une passe complète : chaque source dans son propre try, la collecte
    d'une source ne bloque pas l'autre. Retourne un compte-rendu."""
    report: dict = {}
    state = _load_state()
    with db.standalone() as conn:
        for name, fn in (("canadabuys", lambda c: collect_canadabuys(c)), ("seao", lambda c: collect_seao(c, state))):
            try:
                report[name] = fn(conn)
            except Exception as exc:  # une source en panne ne doit pas casser la nuit
                log.warning("opportunites %s: %s", name, exc)
                report[name] = {"error": str(exc)[:200]}
        report["expired"] = expire(conn)
        report["published"] = conn.execute("SELECT COUNT(*) FROM opportunities WHERE status='published'").fetchone()[0]
    state["last_run"] = datetime.utcnow().isoformat(timespec="seconds")
    state["last_report"] = report
    _save_state(state)
    return report


def recent_for_digest(days: int = 7) -> list:
    """Fiches vues pour la première fois depuis `days` jours, encore ouvertes."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return [dict(r) for r in db.fetchall(
        "SELECT * FROM opportunities WHERE status='published' AND first_seen_at >= ? "
        "ORDER BY closes_at IS NULL, closes_at, id DESC LIMIT 40", (since,))]
