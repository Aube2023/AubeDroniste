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
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

import db
from config import DATA_DIR

log = logging.getLogger("aubepilot.opportunities")

USER_AGENT = "AubePilot-opportunites/1.0 (+https://pilot.aubeetoilee.com/opportunites)"
CANADABUYS_CSV = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"
CANADABUYS_NOTICE_FR = "https://canadabuys.canada.ca/fr/occasions-de-marche/appels-d-offres/{ref}"
CANADABUYS_NOTICE_EN = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/{ref}"
SEAO_PACKAGE = ("https://www.donneesquebec.ca/recherche/api/3/action/package_show"
                "?id=systeme-electronique-dappel-doffres-seao")
SEAO_WEEKLY_FILES = 6          # fichiers hebdomadaires relus (un avis ouvert reste actif plusieurs semaines)
SUMMARY_CHARS = 400
STATE_FILE = os.path.join(DATA_DIR, "opportunities_state.json")

SOURCES = {
    "canadabuys": {"label": "CanadaBuys", "licence": "Licence du gouvernement ouvert – Canada",
                   "url": "https://canadabuys.canada.ca/en/support/open-data"},
    "seao": {"label": "SEAO (Québec)", "licence": "Données Québec, CC BY 4.0",
             "url": "https://www.donneesquebec.ca/recherche/dataset/systeme-electronique-dappel-doffres-seao"},
    "ted": {"label": "TED (Union européenne)", "licence": "Réutilisation libre des données TED (décision 2011/833/UE)",
            "url": "https://ted.europa.eu/"},
    "boamp": {"label": "BOAMP (France)", "licence": "Licence Ouverte / Open Licence (Etalab)",
              "url": "https://www.boamp.fr/pages/donnees-ouvertes"},
    "contractsfinder": {"label": "Contracts Finder (Royaume-Uni)", "licence": "Open Government Licence v3.0",
                        "url": "https://www.contractsfinder.service.gov.uk/"},
}
TED_SEARCH = "https://api.ted.europa.eu/v3/notices/search"
TED_QUERY = ('(FT~"drone" OR FT~"drones" OR FT~"dron" OR FT~"drohne*" OR FT~"UAV" OR FT~"UAS" OR FT~"RPAS" '
             'OR FT~"lidar" OR FT~"photogramm*" OR FT~"fotogramm*" OR FT~"orthophoto*" OR FT~"ortofoto*" '
             'OR FT~"aerial imagery" OR FT~"aerial survey*" OR FT~"aerial inspection*" OR FT~"imagerie aérienne" '
             'OR FT~"prises de vues aériennes" OR FT~"inspection aérienne" OR FT~"télépilot*" OR FT~"luftbild*" '
             'OR FT~"unmanned aircraft" OR FT~"sistema aéreo no tripulado" OR FT~"aeromobile a pilotaggio remoto") '
             'AND notice-type IN (cn-standard cn-social cn-desg pin-only)')
BOAMP_API = ("https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
             "?where={where}&limit=100&offset={offset}&select=idweb,objet,nomacheteur,dateparution,datelimitereponse,"
             "code_departement,nature_libelle,type_marche,url_avis,descripteur_libelle,donnees")
BOAMP_WHERE = ('(search("drone") OR search("drones") OR search("lidar") OR search("photogrammétrie") OR search("orthophoto") '
               'OR search("aéronef télépiloté") OR search("imagerie aérienne")) AND datelimitereponse>=now()')
CONTRACTSFINDER_API = ("https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
                       "?publishedFrom={since}T00:00:00&stages=tender&limit=100")

# Pays de l'Union (code ISO-3 TED -> nom francais de reference, celui de la base users.country)
TED_COUNTRIES = {
    "AUT": "Autriche", "BEL": "Belgique", "BGR": "Bulgarie", "HRV": "Croatie", "CYP": "Chypre", "CZE": "Tchéquie",
    "DNK": "Danemark", "EST": "Estonie", "FIN": "Finlande", "FRA": "France", "DEU": "Allemagne", "GRC": "Grèce",
    "HUN": "Hongrie", "IRL": "Irlande", "ITA": "Italie", "LVA": "Lettonie", "LTU": "Lituanie", "LUX": "Luxembourg",
    "MLT": "Malte", "NLD": "Pays-Bas", "POL": "Pologne", "PRT": "Portugal", "ROU": "Roumanie", "SVK": "Slovaquie",
    "SVN": "Slovénie", "ESP": "Espagne", "SWE": "Suède", "NOR": "Norvège", "ISL": "Islande", "CHE": "Suisse",
    "GBR": "Royaume-Uni", "LIE": "Liechtenstein",
}
# Langues du site -> code de langue TED (ISO 639-3) pour le titre et le lien
TED_LANGS = {"fr": "fra", "en": "eng", "es": "spa", "de": "deu", "it": "ita", "pt": "por", "nl": "nld", "pl": "pol",
             "ro": "ron", "el": "ell", "sw": "eng", "hu": "hun", "sv": "swe", "da": "dan", "fi": "fin", "cs": "ces"}
TED_HTML_LANG = {"fra": "fr", "eng": "en", "spa": "es", "deu": "de", "ita": "it", "por": "pt", "nld": "nl", "pol": "pl", "ron": "ro", "ell": "el"}

# Ce qui concerne un professionnel du drone. Le mot « aérien » seul ne suffit
# pas (ravitailleurs, fret aérien...) : il faut un terme métier.
KEYWORDS = re.compile(
    r"\b(drones?|dron|drohnen?|rpas|satp|uavs?|uas|unmanned (?:aircraft|aerial|air) ?(?:system|vehicle)?s?|"
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
NOISE = re.compile(r"anti-?drones?|contre les (?:drones|syst[èe]mes d.a[ée]ronef)|counter-?\s?(?:uas|drone|uav)|c-uas|"
                   r"drone remote id|remote id|d[ée]tection (?:et neutralisation )?de drones|neutralisation de drones|brouill\w+|"
                   r"v[ée]hicule (?:de surface|terrestre) sans [ée]quipage|unmanned (?:surface|ground) vehicle|drones? navals?|"
                   r"\bvsse\b|\busv\b|\bugv\b|munitions?|lõhkepea|minendetektion|mine ?detection|"
                   r"drohnenabwehr|antidron|contradron|anti-uav|dronebestrijding|drone ?afvær|"
                   r"p[óo]lizas? de seguro|seguros? de vida|contrat d.assurance|insurance polic|spectacle|pyrotechni|feu d.artifice|light show|"
                   r"uav[- ]?gc|uav 2012|uav 1989", re.I)   # NL : « UAV » = Uniforme Administratieve Voorwaarden (conditions de marché), pas un drone

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
    r"prière de noter|please note|question(?:s)? (?:et|and|&) r[ée]ponse(?:s)?|q\s*(?:et|and|&)\s*r|q\s*&\s*a|"
    r"attention\s*[:\u00a0]|note\s*:|la présente|this amendment|cette modification)", re.I)
# Traits de séparation (« ______ », « ------ », « ====== »), tirets décoratifs.
_RULES = re.compile(r"[_\-=*~]{4,}")


def summarize(text: str, limit: int = SUMMARY_CHARS) -> str:
    """Résumé court : premiers paragraphes de fond (journal des modifications
    sauté), espaces normalisés, coupé au mot, jamais l'avis complet."""
    text = _RULES.sub("\n\n", text or "")   # un trait de séparation vaut un saut de paragraphe
    paras = [re.sub(r"\s+", " ", x).strip() for x in re.split(r"\n\s*\n|\r\n\s*\r\n", text)]
    paras = [x for x in paras if x]
    body = [x for x in paras if not _AMENDMENT_PARA.match(x) and len(x) > 40]
    if body and len(" ".join(body)) < 60 and len(paras) > len(body):
        body = []   # trop peu de fond une fois les avertissements retirés : on garde tout
    text = " ".join(body or paras)
    # certaines lignes de modification sont collées au fond par des « *** »
    text = re.sub(r"(?:\*{2,}\s*(?:modification|amendment)[^*]{0,200}\*{0,3}\s*)+", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
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
    published_at, closes_at, status, first_seen_at, last_seen_at, i18n)
VALUES (:source, :source_ref, :kind, :title_fr, :title_en, :summary_fr, :summary_en,
    :org, :country, :region, :regions_raw, :city, :url_fr, :url_en, :notice_type, :category, :specialties,
    :published_at, :closes_at, :status, :now, :now, :i18n)
ON CONFLICT(source, source_ref) DO UPDATE SET
    title_fr=excluded.title_fr, title_en=excluded.title_en, summary_fr=excluded.summary_fr,
    summary_en=excluded.summary_en, org=excluded.org, region=excluded.region,
    regions_raw=excluded.regions_raw, city=excluded.city, url_fr=excluded.url_fr, url_en=excluded.url_en,
    notice_type=excluded.notice_type, category=excluded.category, specialties=excluded.specialties, i18n=excluded.i18n,
    published_at=COALESCE(excluded.published_at, opportunities.published_at),
    closes_at=excluded.closes_at, last_seen_at=excluded.last_seen_at,
    -- une fiche masquée par l'admin le reste ; un lien mort reste retiré tant que l'URL ne change pas ;
    -- un avis clos puis revu redevient publié
    status=CASE WHEN opportunities.status='hidden' THEN 'hidden'
                WHEN opportunities.status='broken' AND opportunities.url_fr=excluded.url_fr THEN 'broken'
                ELSE excluded.status END,
    link_checked_at=CASE WHEN opportunities.url_fr=excluded.url_fr THEN opportunities.link_checked_at ELSE NULL END
"""


def _upsert_many(conn, items: Iterable[dict]) -> int:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for it in items:
        row = {"summary_fr": "", "summary_en": "", "city": "", "notice_type": "", "category": "", "specialties": "",
               "published_at": None, "closes_at": None, "regions_raw": "", "status": "published",
               "url_en": None, "now": now, **it}
        row["i18n"] = json.dumps({"titles": it.get("titles") or {}, "urls": it.get("urls") or {}}, ensure_ascii=False) \
            if (it.get("titles") or it.get("urls")) else ""
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
# TED (Union européenne) : API publique de recherche, avis actifs
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v or ""


def parse_ted(notices: list) -> list:
    out = []
    for n in notices or []:
        pub = n.get("publication-number") or ""
        titles = n.get("notice-title") or {}
        if not pub or not titles:
            continue
        title_en = titles.get("eng") or next(iter(titles.values()), "")
        title_fr = titles.get("fra") or title_en
        desc = n.get("description-lot") or {}
        desc_txt = " ".join(_first(v) if isinstance(v, list) else str(v) for v in (desc.values() if isinstance(desc, dict) else []))
        if not matches(title_en + " " + title_fr, desc_txt + " " + " ".join(str(v) for v in titles.values())):
            continue
        cc = _first(n.get("buyer-country"))
        country = TED_COUNTRIES.get(cc, "")
        if not country:
            continue
        buyer = n.get("buyer-name") or {}
        org = _first(next(iter(buyer.values()), "")) if isinstance(buyer, dict) else str(buyer)
        html = (n.get("links") or {}).get("html") or {}
        url_en = html.get("ENG") or f"https://ted.europa.eu/en/notice/-/detail/{pub}"
        url_fr = html.get("FRA") or url_en
        # Le titre TED commence par « Pays – Objet CPV – Intitulé » : on garde l'intitulé.
        def strip(t):
            parts = [x.strip() for x in t.split(" – ")]
            return parts[-1] if len(parts) >= 3 else t
        if not _first(n.get("deadline-receipt-tender-date-lot")):
            continue   # sans date limite : avis d'information ou attribution, pas une chose à laquelle répondre
        out.append({
            "source": "ted", "source_ref": pub, "kind": "tender",
            "title_fr": strip(title_fr), "title_en": strip(title_en),
            "summary_fr": summarize(desc.get("fra", [""])[0] if isinstance(desc.get("fra"), list) else desc_txt),
            "summary_en": summarize(desc.get("eng", [""])[0] if isinstance(desc.get("eng"), list) else desc_txt),
            "org": org, "country": country, "region": country, "regions_raw": country, "city": "",
            "url_fr": url_fr, "url_en": url_en,
            "notice_type": {"cn-standard": "Avis de marché", "cn-social": "Avis de marché (services sociaux)",
                            "cn-desg": "Concours", "pin-only": "Avis de préinformation"}.get(n.get("notice-type") or "", ""),
            "category": "", "specialties": specialties_for(title_en + " " + title_fr + " " + desc_txt),
            "published_at": _iso_date(str(n.get("publication-date") or "")),
            "closes_at": _iso_date(_first(n.get("deadline-receipt-tender-date-lot"))),
            "titles": {TED_HTML_LANG[k]: strip(v) for k, v in titles.items() if k in TED_HTML_LANG},
            "urls": {TED_HTML_LANG[k.lower()]: v for k, v in html.items() if k.lower() in TED_HTML_LANG},
        })
    return out


def collect_ted(conn) -> dict:
    since = (date.today() - timedelta(days=120)).strftime("%Y%m%d")
    notices, page, total = [], 1, None
    while page <= 4:
        payload = {"query": f"{TED_QUERY} AND PD>={since}", "scope": "ACTIVE", "limit": 250, "page": page,
                   "fields": ["publication-number", "notice-title", "buyer-name", "buyer-country",
                              "deadline-receipt-tender-date-lot", "description-lot", "publication-date", "notice-type", "links"]}
        data = _post_json(TED_SEARCH, payload)
        batch = data.get("notices") or []
        notices.extend(batch)
        total = data.get("totalNoticeCount")
        if len(batch) < 250:
            break
        page += 1
    items = parse_ted(notices)
    n = _upsert_many(conn, items)
    refs = [it["source_ref"] for it in items]
    # Un avis actif ce mois-ci qui n'est plus renvoye est clos.
    if refs:
        conn.execute("UPDATE opportunities SET status='closed' WHERE source='ted' AND status='published' "
                     "AND source_ref NOT IN (%s)" % ",".join("?" * len(refs)), refs)
    return {"items": n, "total": total}


# ---------------------------------------------------------------------------
# BOAMP (France) : API ouverte Opendatasoft, avis de marché en cours
# ---------------------------------------------------------------------------

FR_DEPT_REGION = {  # departement -> region (nom court)
    **{d: "Île-de-France" for d in ("75", "77", "78", "91", "92", "93", "94", "95")},
    **{d: "Auvergne-Rhône-Alpes" for d in ("01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74")},
    **{d: "Provence-Alpes-Côte d'Azur" for d in ("04", "05", "06", "13", "83", "84")},
    **{d: "Occitanie" for d in ("09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82")},
    **{d: "Nouvelle-Aquitaine" for d in ("16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87")},
    **{d: "Hauts-de-France" for d in ("02", "59", "60", "62", "80")},
    **{d: "Grand Est" for d in ("08", "10", "51", "52", "54", "55", "57", "67", "68", "88")},
    **{d: "Bretagne" for d in ("22", "29", "35", "56")},
    **{d: "Pays de la Loire" for d in ("44", "49", "53", "72", "85")},
    **{d: "Normandie" for d in ("14", "27", "50", "61", "76")},
    **{d: "Bourgogne-Franche-Comté" for d in ("21", "25", "39", "58", "70", "71", "89", "90")},
    **{d: "Centre-Val de Loire" for d in ("18", "28", "36", "37", "41", "45")},
    "2A": "Corse", "2B": "Corse", "20": "Corse",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane", "974": "La Réunion", "976": "Mayotte",
}


def _boamp_description(record: dict) -> str:
    """Le texte de l'avis est dans `donnees` (JSON imbriqué) : on prend les
    champs description / caractéristiques, sans les renseignements pratiques."""
    raw = record.get("donnees")
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return ""
    found: list = []
    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, str) and key in ("objet", "principales", "description", "descriptionMarche") and len(o) > 20:
            found.append(o)
    walk(data)
    return " ".join(dict.fromkeys(found))


def parse_boamp(records: list) -> list:
    out = []
    for r in records or []:
        ref = r.get("idweb") or ""
        title = (r.get("objet") or "").strip()
        desc = _boamp_description(r)
        body = " ".join([desc, " ".join(r.get("descripteur_libelle") or [])])
        if not ref or not title or not matches(title, body):
            continue
        depts = [str(d).zfill(2) if len(str(d)) < 2 else str(d) for d in (r.get("code_departement") or [])]
        region = next((FR_DEPT_REGION[d] for d in depts if d in FR_DEPT_REGION), "France")
        cat = {"SERVICES": "services", "FOURNITURES": "goods", "TRAVAUX": "construction"}.get(_first(r.get("type_marche")), "")
        out.append({
            "source": "boamp", "source_ref": ref, "kind": "tender",
            "title_fr": title, "title_en": title,
            "summary_fr": summarize(desc), "summary_en": summarize(desc),
            "org": (r.get("nomacheteur") or "").strip(), "country": "France", "region": region,
            "regions_raw": ", ".join(depts), "city": "",
            "url_fr": r.get("url_avis") or f"https://www.boamp.fr/pages/avis/?q=idweb:{ref}", "url_en": None,
            "notice_type": r.get("nature_libelle") or "", "category": cat,
            "specialties": specialties_for(title + " " + body),
            "published_at": _iso_date(r.get("dateparution") or ""),
            "closes_at": _iso_date(r.get("datelimitereponse") or ""),
        })
    return out


def collect_boamp(conn) -> dict:
    from urllib.parse import quote
    items, offset = [], 0
    while offset < 1000:
        data = json.loads(_fetch(BOAMP_API.format(where=quote(BOAMP_WHERE), offset=offset), timeout=90).decode("utf-8"))
        batch = data.get("results") or []
        items.extend(parse_boamp(batch))
        offset += 100
        if len(batch) < 100:
            break
    n = _upsert_many(conn, items)
    refs = [it["source_ref"] for it in items]
    if refs:
        conn.execute("UPDATE opportunities SET status='closed' WHERE source='boamp' AND status='published' "
                     "AND source_ref NOT IN (%s)" % ",".join("?" * len(refs)), refs)
    return {"items": n}


# ---------------------------------------------------------------------------
# Contracts Finder (Royaume-Uni) : API OCDS, avis en phase d'appel d'offres
# ---------------------------------------------------------------------------

UK_REGIONS = {"UKC": "North East", "UKD": "North West", "UKE": "Yorkshire and the Humber", "UKF": "East Midlands",
              "UKG": "West Midlands", "UKH": "East of England", "UKI": "London", "UKJ": "South East",
              "UKK": "South West", "UKL": "Wales", "UKM": "Scotland", "UKN": "Northern Ireland"}


def parse_contractsfinder(releases: list) -> list:
    out = []
    for rel in releases or []:
        tender = rel.get("tender") or {}
        title = tender.get("title") or ""
        body = tender.get("description") or ""
        ocid = rel.get("ocid") or ""
        if not ocid or not title or not matches(title, body):
            continue
        if tender.get("status") not in (None, "active", "planned"):
            continue
        addr = ((tender.get("items") or [{}])[0].get("deliveryAddresses") or [{}])[0] if tender.get("items") else {}
        reg = (addr.get("region") or "")
        region = next((v for k, v in UK_REGIONS.items() if reg.upper().startswith(k)), reg or "Royaume-Uni")
        docs = tender.get("documents") or []
        url = next((d.get("url") for d in docs if d.get("url")), "") or f"https://www.contractsfinder.service.gov.uk/Search/Results?keyword={ocid}"
        out.append({
            "source": "contractsfinder", "source_ref": ocid, "kind": "tender",
            "title_fr": title, "title_en": title,
            "summary_fr": summarize(body), "summary_en": summarize(body),
            "org": (rel.get("buyer") or {}).get("name") or "", "country": "Royaume-Uni", "region": region,
            "regions_raw": reg, "city": addr.get("locality") or "",
            "url_fr": url, "url_en": url,
            "notice_type": tender.get("procurementMethodDetails") or "",
            "category": {"services": "services", "goods": "goods", "works": "construction"}.get(tender.get("mainProcurementCategory") or "", ""),
            "specialties": specialties_for(title + " " + body),
            "published_at": _iso_date(rel.get("date") or ""),
            "closes_at": _iso_date((tender.get("tenderPeriod") or {}).get("endDate") or ""),
        })
    return out


def collect_contractsfinder(conn) -> dict:
    since = (date.today() - timedelta(days=90)).isoformat()
    items, url, pages = [], CONTRACTSFINDER_API.format(since=since), 0
    while url and pages < 40:
        data = json.loads(_fetch(url, timeout=120).decode("utf-8"))
        rel = data.get("releases") or []
        items.extend(parse_contractsfinder(rel))
        pages += 1
        url = (data.get("links") or {}).get("next") or "" if rel else ""
    n = _upsert_many(conn, items)
    return {"items": n, "pages": pages}


# ---------------------------------------------------------------------------
# Vérification des liens : un avis dont la page ne répond pas n'est pas montré
# ---------------------------------------------------------------------------

LINK_OK = (200, 202, 203, 301, 302, 303, 307, 308)
LINK_TIMEOUT = 20


def link_status(url: str) -> int:
    """Code HTTP de la page d'un avis (HEAD, puis GET si le portail refuse
    HEAD). 0 = injoignable."""
    if not url:
        return 0
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT,
                                                                       "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=LINK_TIMEOUT) as resp:
                code = resp.getcode()
        except urllib.error.HTTPError as exc:
            code = exc.code
        except Exception:
            code = 0
        if method == "HEAD" and code in (403, 405, 0):
            continue   # certains portails refusent HEAD : on retente en GET
        return code
    return code


def verify_links(conn, limit: int = 120) -> dict:
    """Contrôle les liens des fiches publiées non encore vérifiées (et
    revérifie les plus anciennes) : lien mort -> fiche retirée (status
    'broken'), ou bascule sur le lien anglais s'il répond. Borné par nuit
    pour rester poli avec les portails."""
    rows = conn.execute(
        "SELECT id, url_fr, url_en FROM opportunities WHERE status='published' "
        "ORDER BY link_checked_at IS NOT NULL, link_checked_at LIMIT ?", (limit,)).fetchall()
    ok = broken = swapped = 0
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for oid, url_fr, url_en in rows:
        code = link_status(url_fr)
        if code in LINK_OK:
            conn.execute("UPDATE opportunities SET link_checked_at=?, link_status=? WHERE id=?", (now, code, oid)); ok += 1
            continue
        if url_en and url_en != url_fr and link_status(url_en) in LINK_OK:
            conn.execute("UPDATE opportunities SET url_fr=url_en, link_checked_at=?, link_status=200 WHERE id=?", (now, oid)); swapped += 1
            continue
        conn.execute("UPDATE opportunities SET status='broken', link_checked_at=?, link_status=? WHERE id=?", (now, code, oid)); broken += 1
        log.warning("opportunite %s: lien mort (%s) %s", oid, code, url_fr)
    return {"checked": len(rows), "ok": ok, "swapped": swapped, "broken": broken}


# ---------------------------------------------------------------------------
# Collecte complète
# ---------------------------------------------------------------------------

def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()[:80]


def dedupe_cross_sources(conn) -> int:
    """Un marché français est publié à la fois au BOAMP et au TED : on garde
    la fiche nationale (lien direct, région) et on ferme le doublon TED."""
    rows = conn.execute("SELECT id, source, country, title_fr FROM opportunities WHERE status='published'").fetchall()
    seen: dict = {}
    closed = 0
    for r in sorted(rows, key=lambda r: (r[1] == "ted",)):   # sources nationales d'abord
        key = (r[2], _norm_title(r[3]))
        if key in seen and r[1] == "ted":
            conn.execute("UPDATE opportunities SET status='closed' WHERE id=?", (r[0],)); closed += 1
        else:
            seen.setdefault(key, r[0])
    return closed


def expire(conn) -> int:
    """Un avis dont la date de clôture est passée n'est plus proposé."""
    cur = conn.execute("UPDATE opportunities SET status='closed' WHERE status='published' AND closes_at IS NOT NULL AND closes_at < ?",
                       (date.today().isoformat(),))
    return cur.rowcount


def collect(verify: bool = True) -> dict:
    """Une passe complète : chaque source dans son propre try, la collecte
    d'une source ne bloque pas l'autre ; puis contrôle des liens. Retourne
    un compte-rendu."""
    report: dict = {}
    state = _load_state()
    with db.standalone() as conn:
        for name, fn in (("canadabuys", lambda c: collect_canadabuys(c)), ("seao", lambda c: collect_seao(c, state)),
                         ("ted", collect_ted), ("boamp", collect_boamp), ("contractsfinder", collect_contractsfinder)):
            try:
                report[name] = fn(conn)
            except Exception as exc:  # une source en panne ne doit pas casser la nuit
                log.warning("opportunites %s: %s", name, exc)
                report[name] = {"error": str(exc)[:200]}
        report["dedup"] = dedupe_cross_sources(conn)
        report["expired"] = expire(conn)
        if verify:
            report["links"] = verify_links(conn)
        report["published"] = conn.execute("SELECT COUNT(*) FROM opportunities WHERE status='published'").fetchone()[0]
    state["last_run"] = datetime.utcnow().isoformat(timespec="seconds")
    state["last_report"] = report
    _save_state(state)
    return report


def recent_for_digest(days: int = 7, country: str = "") -> list:
    """Fiches vues pour la première fois depuis `days` jours, encore ouvertes,
    pour un pays (ou tous)."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    q = "SELECT * FROM opportunities WHERE status='published' AND first_seen_at >= ?"
    args = [since]
    if country:
        q += " AND country=?"; args.append(country)
    return [dict(r) for r in db.fetchall(q + " ORDER BY closes_at IS NULL, closes_at, id DESC LIMIT 40", args)]


def digest_countries() -> list:
    """Pays ayant des fiches ouvertes : un courriel par pays de pilote."""
    return [r["country"] for r in db.fetchall("SELECT DISTINCT country FROM opportunities WHERE status='published'")]
