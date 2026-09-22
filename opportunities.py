"""Opportunités pour les pros du drone : appels d'offres publics et offres
d'emploi repris de sources OUVERTES, avec leur licence, leur source et un lien
vers l'avis (colonne `kind` : tender | job).

Règles (le service est gratuit et doit rester irréprochable) :
- uniquement des jeux de données ouverts publiés par les organismes
  eux-mêmes : CanadaBuys (Licence du gouvernement ouvert, Canada) et le SEAO
  du Québec (Données Québec, CC BY 4.0) ; pas de scraping de sites qui
  l'interdisent, pas de sites d'emploi sans flux ou API officiels (emplois :
  flux Atom du Guichet-Emplois du Canada, API Adzuna sur clé) ;
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

# Forme conventionnelle des robots déclarés (comme Googlebot ou Bingbot) :
# le pare-feu d'AusTender refuse tout agent qui ne commence pas par Mozilla.
USER_AGENT = "Mozilla/5.0 (compatible; AubePilot-opportunites/1.0; +https://pilot.aubeetoilee.com/opportunites)"
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
    "austender": {"label": "AusTender (Australie)", "licence": "Creative Commons BY 3.0 AU (flux officiel)",
                  "url": "https://www.tenders.gov.au/"},
    "gets": {"label": "GETS (Nouvelle-Zélande)", "licence": "Flux officiel des appels ouverts, New Zealand Government",
             "url": "https://www.gets.govt.nz/"},
    "secop": {"label": "SECOP II (Colombie)", "licence": "Datos Abiertos Colombia (Licencia CC BY-SA)",
              "url": "https://www.datos.gov.co/"},
    "samgov": {"label": "SAM.gov (États-Unis)", "licence": "U.S. Government Work, données publiques",
               "url": "https://sam.gov/"},
    # Emplois (kind = job)
    "jobbank": {"label": "Guichet-Emplois / Job Bank (Canada)", "licence": "Flux Atom officiel, reproduction non commerciale avec mention de la source",
                "url": "https://www.guichetemplois.gc.ca/", "kind": "job"},
    "adzuna": {"label": "Adzuna (19 pays)", "licence": "API officielle, attribution « Jobs by Adzuna »",
               "url": "https://www.adzuna.com/", "kind": "job"},
    "usajobs": {"label": "USAJOBS (États-Unis, emplois fédéraux)", "licence": "API officielle de l'Office of Personnel Management, données publiques",
                "url": "https://www.usajobs.gov/", "kind": "job"},
    "employers": {"label": "Pages carrières d'employeurs du drone (Volatus Aerospace, Zipline, Skydio, Auterion, Elroy Air, Flyability, Pix4D, Wingcopter, Delair, Anduril, Shield AI)",
                  "licence": "flux publics de leurs systèmes de recrutement (Greenhouse, Ashby, Lever, Workable, Personio, Teamtailor, BambooHR), lien vers l'offre chez l'employeur",
                  "url": "https://pilot.aubeetoilee.com/emplois", "kind": "job"},
}
for _s in SOURCES.values():
    _s.setdefault("kind", "tender")
AUSTENDER_RSS = "https://www.tenders.gov.au/public_data/rss/rss.xml"
GETS_RSS = "https://www.gets.govt.nz/ExternalRSSFeed.htm"
SECOP_API = ("https://www.datos.gov.co/resource/p6dx-8zbt.json?$limit=200&$order=fecha_de_publicacion_del%20DESC"
             "&$where=fecha_de_recepcion_de%20%3E%3D%20'{today}'%20AND%20fecha_de_publicacion_del%20%3E%3D%20'{since}'%20AND%20"
             "fase%20in('Fase%20de%20ofertas','Presentaci%C3%B3n%20de%20oferta','Fase%20de%20Selecci%C3%B3n%20(Presentaci%C3%B3n%20de%20ofertas)','Manifestaci%C3%B3n%20de%20inter%C3%A9s%20(Menor%20Cuant%C3%ADa)')"
             "%20AND%20(upper(nombre_del_procedimiento)%20like%20'%25DRON%25'%20OR%20upper(nombre_del_procedimiento)%20like%20'%25LIDAR%25'"
             "%20OR%20upper(nombre_del_procedimiento)%20like%20'%25FOTOGRAMETR%25'%20OR%20upper(nombre_del_procedimiento)%20like%20'%25ORTOFOTO%25'"
             "%20OR%20upper(descripci_n_del_procedimiento)%20like%20'%25DRON%25')")
_SECOP_API_OLD = ("https://www.datos.gov.co/resource/p6dx-8zbt.json?$limit=200&$order=fecha_de_publicacion_del%20DESC"
             "&$where=fase%20in('Fase%20de%20ofertas','Presentaci%C3%B3n%20de%20oferta','Fase%20de%20Selecci%C3%B3n%20(Presentaci%C3%B3n%20de%20ofertas)','Manifestaci%C3%B3n%20de%20inter%C3%A9s%20(Menor%20Cuant%C3%ADa)')"
             "%20AND%20(upper(nombre_del_procedimiento)%20like%20'%25DRON%25'%20OR%20upper(nombre_del_procedimiento)%20like%20'%25LIDAR%25'"
             "%20OR%20upper(nombre_del_procedimiento)%20like%20'%25FOTOGRAMETR%25'%20OR%20upper(nombre_del_procedimiento)%20like%20'%25ORTOFOTO%25'"
             "%20OR%20upper(descripci_n_del_procedimiento)%20like%20'%25DRON%25')")
SAMGOV_API = ("https://api.sam.gov/opportunities/v2/search?limit=100&api_key={key}&postedFrom={frm}&postedTo={to}"
              "&ptype=o,k,p&keywords={kw}")
SAMGOV_KEY = os.environ.get("SAMGOV_API_KEY", "").strip()
# Emplois. Guichet-Emplois : flux Atom officiel de la recherche (lien « RSS »
# de la page de résultats). Il ne couvre que les offres des 3 derniers jours,
# d'où la collecte nocturne, et cherche dans les titres (mot entier) : on
# interroge quelques termes, en anglais et en français, et on fusionne par
# numéro d'offre (le même dans les deux langues).
JOBBANK_FEED_EN = "https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed?searchstring={q}&sort=D&rows=100"
JOBBANK_FEED_FR = "https://www.guichetemplois.gc.ca/jobsearch/feed/jobSearchRSSfeed?searchstring={q}&sort=D&rows=100"
# Les intitulés sont filtrés ensuite par job_matches() : un terme large ne
# ramène du bruit que dans la page lue, jamais sur le site. Chaque terme est
# interrogé dans les deux langues (le flux FR indexe les intitulés français).
JOBBANK_TERMS = ("drone", "drones", "UAV", "UAS", "RPAS", "télépilote", "aerial", "aérien",
                 "lidar", "photogrammétrie", "arpentage", "cartographie", "géomatique", "topographie")
JOBBANK_PAUSE = 2.0            # le portail ralentit un client trop pressé
JOB_DAYS = 30                  # une offre sans date de fin est proposée 30 jours après sa publication
# Intitulés d'emploi à écarter : « RPAS » d'Oracle Retail (Retail Predictive
# Application Server), annonces de mise en relation Cronoshare (« Drones e
# Fotografia Aérea em <ville> » : des demandes de particuliers, pas des postes).
JOB_NOISE = re.compile(r"oracle retail|\bsiocs\b|\bmfcs\b|cronoshare|drones? e fotografia a[ée]rea em\b", re.I)


def job_matches(title: str) -> bool:
    """Un intitulé de poste qui parle vraiment de drone (titre seul : le
    descriptif d'une offre cite « drone » pour un rien)."""
    return bool(title) and not JOB_NOISE.search(title) and matches(title)
# Adzuna : API officielle (clé gratuite sur developer.adzuna.com), un appel
# par pays et par nuit ; sans clé la source est sautée proprement.
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()
ADZUNA_API = ("https://api.adzuna.com/v1/api/jobs/{cc}/search/{page}?app_id={app_id}&app_key={app_key}"
              "&results_per_page=50&{query}&max_days_old=30&sort_by=date&content-type=application/json")
# Deux balayages par pays, les plus récentes d'abord, jusqu'à ADZUNA_PAGES
# pages de 50 chacun :
# - `title_only=<terme>` (mot dans l'intitulé, racinisé : « drone » trouve
#   « Drones ») : aux États-Unis, 223 offres « drone » contre 3 retenues sur la
#   première page du balayage large ; « UAS » y ramène aussi « UA » (filtré) ;
# - `what_or=…` (large, descriptif compris) : garde le Canada, où title_only
#   « drone » répond 0 alors que des intitulés « Drone Pilot » existent.
# Les appels sont espacés (accès d'essai : 25/min) et plafonnés par nuit.
ADZUNA_TITLE_TERMS = ("drone", "UAV", "UAS", "RPAS")
ADZUNA_EXTRA_TERMS = {"fr": ("télépilote",), "be": ("télépilote",), "ch": ("télépilote", "Drohne"), "ca": ("télépilote",),
                      "de": ("Drohne",), "at": ("Drohne",), "es": ("dron",), "mx": ("dron",), "pl": ("dron",)}
ADZUNA_WIDE = "what_or=drone%20drones%20UAV%20RPAS%20t%C3%A9l%C3%A9pilote%20Drohne%20dron"
ADZUNA_PAGES = 5
ADZUNA_PAUSE = 1.5
ADZUNA_MAX_CALLS = 220
# USAJOBS : API officielle (clé gratuite, demandée sur
# developer.usajobs.gov/apirequest/ et envoyée par courriel ; l'adresse
# inscrite sert de User-Agent, la clé d'en-tête Authorization-Key).
# `PositionTitle` cherche dans l'intitulé (« contains »), pas dans tout l'avis :
# c'est l'équivalent du title_only d'Adzuna, sans le bruit. DatePosted vaut au
# plus 60 jours, ResultsPerPage au plus 500 (doc de l'API).
USAJOBS_API = ("https://data.usajobs.gov/api/search?PositionTitle={kw}&ResultsPerPage=500"
               "&DatePosted=60&SortField=opendate&SortDirection=Desc")
USAJOBS_KEY = os.environ.get("USAJOBS_API_KEY", "").strip()
USAJOBS_EMAIL = os.environ.get("USAJOBS_EMAIL", "").strip()
USAJOBS_TERMS = ("drone", "UAS", "UAV", "unmanned aircraft", "remotely piloted", "aerial survey")
# Employeurs du drone : leurs pages carrières exposent un flux public prévu
# pour la republication (API « job board » de leur outil de recrutement).
# strict=True (industriels de défense aux milliers de postes) : seul un
# intitulé qui parle de drone est gardé ; sinon, aussi les métiers de terrain
# et d'aéronef (vol, essais, maintenance, opérations) hors fonctions support.
EMPLOYER_BOARDS = (
    # (clé, ATS, identifiant chez l'ATS, employeur, strict, pays par défaut si le lieu ne le dit pas)
    ("zipline", "greenhouse", "flyzipline", "Zipline", False, "États-Unis"),
    ("auterion", "greenhouse", "auterion", "Auterion", False, "Suisse"),
    ("anduril", "greenhouse", "andurilindustries", "Anduril Industries", True, "États-Unis"),
    ("skydio", "ashby", "skydio", "Skydio", False, "États-Unis"),
    ("elroyair", "lever", "elroyair", "Elroy Air", False, "États-Unis"),
    ("shieldai", "lever", "shieldai", "Shield AI", True, "États-Unis"),
    ("flyability", "workable", "flyability", "Flyability", False, "Suisse"),
    ("pix4d", "workable", "pix4d", "Pix4D", False, "Suisse"),
    ("wingcopter", "personio", "wingcopter", "Wingcopter", False, "Allemagne"),
    ("delair", "teamtailor", "delair", "Delair", False, "France"),
    ("volatus", "bamboohr", "volatus", "Volatus Aerospace", False, "Canada"),
)
ATS_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}",
    "personio": "https://{slug}.jobs.personio.de/xml",
    "teamtailor": "https://{slug}.teamtailor.com/jobs.rss",
    "bamboohr": "https://{slug}.bamboohr.com/careers/list",
}
EMPLOYER_ROLE = re.compile(
    r"\b(pilot\w*|pilote\w*|t[ée]l[ée]pilot\w*|flight\w*|vol en|"
    r"test pilot|aircraft|a[ée]ronef\w*|a[ée]rospat\w*|aerospace|airframe|avionic\w*|payload\w*|propulsion|"
    r"technician\w*|technicien\w*|maintenance\w*|assembl\w*|"
    r"field (?:service|support|technician|engineer|operations?|ops)\w*|mission (?:operator|specialist|planner)\w*|operator\w*|"
    r"composite\w*|assembly technician|inspection\w*|survey\w*|mapping|lidar|photogramm\w*|geomat\w*|"
    r"instructor\w*|training specialist|site lead|remote operations?|ground (?:control|station)|gcs)\b", re.I)
EMPLOYER_EXCLUDE = re.compile(
    r"recruit\w*|business operations|revenue|marketing|\bsales\b|account (?:executive|manager)|finance|accountant|legal|counsel|"
    r"people operations|human resources|\bHR\b|payroll|talent|customer success|software engineer|data (?:engineer|scientist)|"
    r"machine learning|perception|product manager|designer|procurement|supply chain|buyer|planner\b(?! *\))|"
    r"security clearance officer|facilities|receptionist|executive assistant|operations (?:analyst|program|manager|lead)", re.I)
ADZUNA_COUNTRIES = {"ca": "Canada", "us": "États-Unis", "gb": "Royaume-Uni", "fr": "France", "de": "Allemagne",
                    "es": "Espagne", "it": "Italie", "nl": "Pays-Bas", "be": "Belgique", "ch": "Suisse", "at": "Autriche",
                    "pl": "Pologne", "au": "Australie", "nz": "Nouvelle-Zélande", "br": "Brésil", "mx": "Mexique",
                    "in": "Inde", "sg": "Singapour", "za": "Afrique du Sud"}
US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
             "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
             "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
             "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
             "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
             "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
             "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
             "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}
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
    r"\b(drones?|dron|drones|drohnen?|rpas|satp|uavs?|uas|unmanned (?:aircraft|aerial|air) ?(?:system|vehicle)?s?|"
    r"aeronaves? (?:no tripulada|remotamente pilotada)s?|fotogrametr[ií]a|ortofoto\w*|levantamiento a[ée]reo|"
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
    (re.compile(r"\blidar\b|\btopograf\w*|\btopograph\w*|\barpentage\b|lev[ée]s? (?:laser|de terrain|a[ée]riens?)|levantamiento\w*|\bland survey\w*|vermessung|rilievo\w*", re.I), ("topographie",)),
    (re.compile(r"photogramm\w*|fotogram\w*|mod[ée]lisation 3d|modelo 3d|\b3d model\w*", re.I), ("3d",)),
    (re.compile(r"orthophoto\w*|ortho-?image\w*|orthomosa\w*|ortofoto\w*|cartograph\w*|cartograf\w*|\bmapping\b|imagerie a[ée]rienne|aerial imagery|luftbild\w*", re.I), ("mapping",)),
    (re.compile(r"\binspection\b|\binspecci[óo]n\b|\bispezion\w*|\binspektion\w*", re.I), ("inspection",)),
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
NOISE = re.compile(r"anti-?drones?|contre les (?:drones|syst[èe]mes d.a[ée]ronef)|counter-?\s?(?:uas|drone|uav)|c[-‑–]uas|"
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


_ACRONYMS = {"DJI", "RTK", "PPK", "LIDAR", "UAV", "UAS", "RPAS", "SATP", "GNSS", "GPS", "NDVI", "BVLOS", "DAA", "EASA", "DGAC",
             "FAA", "CNRC", "NRC", "SPAC", "PSPC", "MPO", "DFO", "ONF", "DIR", "DIMAR", "AMN", "FSI", "GHT", "DGA", "MDN", "DND",
             "SIC", "ISC", "IDD", "USV", "UGV", "VTOL", "FPV", "RGB", "IR", "3D", "2D", "4K", "HD", "PDF", "GIS", "SIG", "CAD",
             "BIM", "IA", "AI", "RF", "VHF", "UHF", "ADS-B", "NOTAM", "ULC", "CAA", "EU", "UE", "USA", "UK", "NRW", "IDM"}


def sentence_case(text: str) -> str:
    """Un titre ou un résumé publié TOUT EN CAPITALES devient une phrase
    normale (première lettre haute), sigles et codes conservés."""
    t = (text or "").strip()
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 12 or sum(1 for c in letters if c.isupper()) / len(letters) < 0.85:
        return t
    words = []
    for w in t.split(" "):
        core = w.strip("(),.;:'\"«»")
        if core in _ACRONYMS or (core.isupper() and any(ch.isdigit() for ch in core) and len(core) <= 12):
            words.append(w)
        else:
            words.append(w.lower())
    out = " ".join(words)
    # majuscule en debut de phrase
    return re.sub(r"(^|[.!?]\s+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), out)


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
    text = sentence_case(re.sub(r"\s+", " ", text).strip())
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


_EMPTY_VALUES = {"no definido", "no aplica", "n/a", "na", "none", "null", "-", "—", "sin definir"}


def _clean(value: str) -> str:
    v = (value or "").strip()
    return "" if v.lower() in _EMPTY_VALUES else v


def _upsert_many(conn, items: Iterable[dict]) -> int:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for it in items:
        it["title_fr"] = sentence_case(it.get("title_fr") or "")
        it["title_en"] = sentence_case(it.get("title_en") or "")
        it["org"] = sentence_case(_clean(it.get("org") or ""))
        it["city"] = _clean(it.get("city") or "")
        if not _clean(it.get("region") or ""):
            it["region"] = it.get("country") or ""
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
    downloaded = []   # (nom, fiches, ocid clos) : tout le réseau d'abord, la base ensuite
    for r in seao_weekly_resources():
        name = r["name"]
        if name in done:
            continue
        payload = json.loads(_fetch(r["url"]).decode("utf-8"))
        downloaded.append((name, *parse_seao(payload)))
    n_items = 0
    for name, items, closed in downloaded:
        n_items += _upsert_many(conn, items)
        for ocid in closed:
            conn.execute("UPDATE opportunities SET status='closed' WHERE source='seao' AND source_ref=? AND status='published'", (ocid,))
        done.add(name)
    state["seao_files"] = sorted(done)[-40:]
    return {"items": n_items, "files": len(downloaded)}


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
# Flux RSS officiels : AusTender (Australie) et GETS (Nouvelle-Zélande)
# ---------------------------------------------------------------------------

def _rss_items(raw: bytes) -> list:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)
    out = []
    for it in root.iter("item"):
        d: dict = {}
        for c in it:
            d[c.tag.split("}")[-1]] = (c.text or "").strip()
        out.append(d)
    return out


def _strip_html(text: str) -> str:
    import html as _html
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _html.unescape(text or ""))).strip()


def _rss_date(value: str) -> Optional[str]:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except Exception:
        return _iso_date(value)


AU_STATES = ("New South Wales", "Victoria", "Queensland", "Western Australia", "South Australia", "Tasmania",
             "Australian Capital Territory", "Northern Territory")


def parse_austender(items: list) -> list:
    out = []
    for it in items:
        title = it.get("title") or ""
        body = _strip_html(it.get("description") or "")
        link = it.get("link") or it.get("guid") or ""
        if not title or not link or not matches(title, body):
            continue
        m = re.search(r"(?:Close|Closing) (?:Date|date)[^A-Za-z0-9]*([0-9]{1,2}[- ][A-Za-z]{3,9}[- ][0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})", body)
        closes = None
        if m:
            raw = m.group(1).replace("-", " ")
            for fmt in ("%d %B %Y", "%d %b %Y"):
                try:
                    closes = datetime.strptime(raw, fmt).date().isoformat(); break
                except ValueError:
                    continue
            closes = closes or _iso_date(m.group(1))
        # Résumé sans l'en-tête « Agency: … » ni la ligne de clôture.
        overview = re.sub(r"(?:Agency|Organisation)\s*:?\s*[^.]{3,80}\.\s*", "", body, count=1)
        overview = re.sub(r"(?:Close|Closing) (?:Date|date).*$", "", overview).strip()
        region = next((st for st in AU_STATES if st.lower() in body.lower()), "Australie")
        org = ""
        mo = re.search(r"(?:Agency|Organisation)\s*:?\s*([^|.]{3,80})", body)
        if mo:
            org = mo.group(1).strip()
        out.append({
            "source": "austender", "source_ref": link.rsplit("/", 1)[-1], "kind": "tender",
            "title_fr": title, "title_en": title, "summary_fr": summarize(overview), "summary_en": summarize(overview),
            "org": org, "country": "Australie", "region": region, "regions_raw": region, "city": "",
            "url_fr": link, "url_en": link, "notice_type": "Approach to market", "category": "",
            "specialties": specialties_for(title + " " + body),
            "published_at": _rss_date(it.get("pubDate") or ""), "closes_at": closes,
        })
    return out


def collect_austender(conn) -> dict:
    items = parse_austender(_rss_items(_fetch(AUSTENDER_RSS, timeout=90)))
    n = _upsert_many(conn, items)
    refs = [it["source_ref"] for it in items]
    conn.execute("UPDATE opportunities SET status='closed' WHERE source='austender' AND status='published'"
                 + (" AND source_ref NOT IN (%s)" % ",".join("?" * len(refs)) if refs else ""), refs)
    return {"items": n}


def parse_gets(items: list) -> list:
    out = []
    for it in items:
        title = it.get("title") or ""
        body = _strip_html(it.get("description") or "")
        link = it.get("link") or it.get("guid") or ""
        if not title or not link or not matches(title, body):
            continue
        m = re.search(r"Close date:\s*[A-Za-z]+,\s*([0-9]{1,2} [A-Za-z]+ [0-9]{4})", body)
        closes = None
        if m:
            try:
                closes = datetime.strptime(m.group(1), "%d %B %Y").date().isoformat()
            except ValueError:
                closes = None
        mr = re.search(r"Region:\s*([A-Za-z' /-]{3,40}?)\s*(?:Overview|Categories|Open date|$)", body)
        region = (mr.group(1).strip() if mr else "") or "Nouvelle-Zélande"
        mo = re.search(r"Organisation:\s*(.{3,80}?)\s*(?:Open date|Close date|Categories)", body)
        overview = body.split("Overview:", 1)[1] if "Overview:" in body else body
        out.append({
            "source": "gets", "source_ref": (re.search(r"id=(\d+)", link) or [None, link])[1], "kind": "tender",
            "title_fr": title, "title_en": title, "summary_fr": summarize(overview), "summary_en": summarize(overview),
            "org": (mo.group(1).strip() if mo else it.get("creator") or ""), "country": "Nouvelle-Zélande",
            "region": region, "regions_raw": region, "city": "",
            "url_fr": link.replace("//", "/").replace("https:/", "https://"), "url_en": link.replace("//", "/").replace("https:/", "https://"),
            "notice_type": "Open tender", "category": "", "specialties": specialties_for(title + " " + body),
            "published_at": _iso_date(it.get("date") or "") or _rss_date(it.get("pubDate") or ""), "closes_at": closes,
        })
    return out


def collect_gets(conn) -> dict:
    items = parse_gets(_rss_items(_fetch(GETS_RSS, timeout=120)))
    n = _upsert_many(conn, items)
    refs = [it["source_ref"] for it in items]
    conn.execute("UPDATE opportunities SET status='closed' WHERE source='gets' AND status='published'"
                 + (" AND source_ref NOT IN (%s)" % ",".join("?" * len(refs)) if refs else ""), refs)
    return {"items": n}


# ---------------------------------------------------------------------------
# SECOP II (Colombie) : API Datos Abiertos (Socrata)
# ---------------------------------------------------------------------------

def parse_secop(rows: list) -> list:
    out = []
    for r in rows or []:
        title = (r.get("nombre_del_procedimiento") or "").strip()
        body = (r.get("descripci_n_del_procedimiento") or "")
        ref = r.get("id_del_proceso") or r.get("referencia_del_proceso") or ""
        if not title or not ref or not matches(title, body):
            continue
        if (r.get("estado_del_procedimiento") or "").lower() in ("cancelado", "terminado anormalmente después de convocado", "suspendido"):
            continue
        if not KEYWORDS.search(title):
            continue   # SECOP : le mot métier doit être dans l'objet même, pas seulement dans la description
        url = r.get("urlproceso") or {}
        url = url.get("url") if isinstance(url, dict) else str(url)
        if not url or "Login" in url:
            url = "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID=" + ref
        out.append({
            "source": "secop", "source_ref": ref, "kind": "tender",
            "title_fr": title, "title_en": title, "summary_fr": summarize(body), "summary_en": summarize(body),
            "org": (r.get("entidad") or "").strip(), "country": "Colombie",
            "region": (r.get("departamento_entidad") or "Colombie").strip(), "regions_raw": r.get("departamento_entidad") or "",
            "city": (r.get("ciudad_entidad") or "").strip(),
            "url_fr": url, "url_en": url, "notice_type": r.get("modalidad_de_contratacion") or "", "category": "",
            "specialties": specialties_for(title + " " + body),
            "published_at": _iso_date(r.get("fecha_de_publicacion_del") or ""),
            "closes_at": _iso_date(r.get("fecha_de_recepcion_de") or ""),
        })
    return out


def collect_secop(conn) -> dict:
    url = SECOP_API.format(today=date.today().isoformat() + "T00:00:00",
                           since=(date.today() - timedelta(days=60)).isoformat() + "T00:00:00")
    items = parse_secop(json.loads(_fetch(url, timeout=120).decode("utf-8")))
    n = _upsert_many(conn, items)
    return {"items": n}


# ---------------------------------------------------------------------------
# SAM.gov (États-Unis) : API officielle, clé gratuite requise (SAMGOV_API_KEY)
# ---------------------------------------------------------------------------

def parse_samgov(data: dict) -> list:
    out = []
    for o in (data or {}).get("opportunitiesData") or []:
        title = o.get("title") or ""
        body = o.get("description") if isinstance(o.get("description"), str) else ""
        ref = o.get("noticeId") or ""
        if not title or not ref or not matches(title, body or title):
            continue
        if o.get("active") not in (None, "Yes", True):
            continue
        pop = o.get("placeOfPerformance") or {}
        state = ((pop.get("state") or {}).get("code") or "") if isinstance(pop.get("state"), dict) else (pop.get("state") or "")
        out.append({
            "source": "samgov", "source_ref": ref, "kind": "tender",
            "title_fr": title, "title_en": title, "summary_fr": summarize(body), "summary_en": summarize(body),
            "org": (o.get("fullParentPathName") or o.get("department") or "").split(".")[0].strip(), "country": "États-Unis",
            "region": US_STATES.get(state, "États-Unis"), "regions_raw": state,
            "city": ((pop.get("city") or {}).get("name") or "") if isinstance(pop.get("city"), dict) else "",
            "url_fr": o.get("uiLink") or f"https://sam.gov/opp/{ref}/view", "url_en": o.get("uiLink") or f"https://sam.gov/opp/{ref}/view",
            "notice_type": o.get("type") or "", "category": "", "specialties": specialties_for(title + " " + (body or "")),
            "published_at": _iso_date(o.get("postedDate") or ""), "closes_at": _iso_date(o.get("responseDeadLine") or ""),
        })
    return out


def collect_samgov(conn) -> dict:
    if not SAMGOV_KEY:
        return {"skipped": "SAMGOV_API_KEY absente"}
    frm = (date.today() - timedelta(days=60)).strftime("%m/%d/%Y")
    to = date.today().strftime("%m/%d/%Y")
    items: list = []
    for kw in ("drone", "UAS", "unmanned aircraft", "lidar", "photogrammetry", "aerial survey"):
        data = json.loads(_fetch(SAMGOV_API.format(key=SAMGOV_KEY, frm=frm, to=to, kw=kw.replace(" ", "%20")), timeout=90).decode("utf-8"))
        items.extend(parse_samgov(data))
    seen, uniq = set(), []
    for it in items:
        if it["source_ref"] not in seen:
            seen.add(it["source_ref"]); uniq.append(it)
    n = _upsert_many(conn, uniq)
    return {"items": n}


# ---------------------------------------------------------------------------
# Emplois : Guichet-Emplois / Job Bank (Canada), flux Atom officiel
# ---------------------------------------------------------------------------

def _atom_entries(raw: bytes) -> list:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)
    out = []
    for e in root.iter("{http://www.w3.org/2005/Atom}entry"):
        d: dict = {}
        for c in e:
            tag = c.tag.split("}")[-1]
            if tag == "link":
                d["link"] = c.get("href") or ""
            else:
                d[tag] = (c.text or "").strip()
        out.append(d)
    return out


def _jobbank_ref(link: str) -> str:
    m = re.search(r"/jobposting/(\d+)", link or "")
    return m.group(1) if m else ""


_JB_LABELS = {"job number": "number", "numéro de l’offre": "number", "numéro de l'offre": "number",
              "location": "location", "emplacement": "location", "employer": "org", "employeur": "org",
              "salary": "salary", "salaire": "salary"}


def _jobbank_fields(summary_html: str) -> dict:
    """« <strong>Location:</strong> Alma (QC) <br /><strong>Employer:</strong> … »
    -> {location: 'Alma (QC)', org: '…', salary: 'Salary: $…'} (le salaire
    garde son libellé, dans la langue du flux)."""
    out: dict = {}
    for part in re.split(r"<br\s*/?>", summary_html or ""):
        m = re.match(r"\s*<strong>(.*?)</strong>(.*)", part, re.S)
        if not m:
            continue
        label = _strip_html(m.group(1)).rstrip(" :").lower()
        key = _JB_LABELS.get(label)
        if key == "salary":
            out[key] = _strip_html(part)
        elif key:
            out[key] = _strip_html(m.group(2))
    return out


def _plus_days(day: Optional[str], days: int) -> Optional[str]:
    try:
        return (date.fromisoformat(day) + timedelta(days=days)).isoformat() if day else None
    except ValueError:
        return None


def parse_jobbank(entries_en: list, entries_fr: list) -> list:
    """Fusionne les entrées anglaises et françaises par numéro d'offre ; ne
    garde que les titres qui parlent de drone (mot métier dans le titre)."""
    en = {_jobbank_ref(e.get("link")): e for e in entries_en if _jobbank_ref(e.get("link"))}
    fr = {_jobbank_ref(e.get("link")): e for e in entries_fr if _jobbank_ref(e.get("link"))}
    items = []
    for ref in sorted(set(en) | set(fr), key=int):
        e_en, e_fr = en.get(ref) or {}, fr.get(ref) or {}
        # le Guichet publie les intitulés tout en minuscules (« drone technician »)
        title_en, title_fr = (t[:1].upper() + t[1:] for t in (e_en.get("title") or "", e_fr.get("title") or ""))
        if not (job_matches(title_en) or job_matches(title_fr)):
            continue
        f_en, f_fr = _jobbank_fields(e_en.get("summary") or ""), _jobbank_fields(e_fr.get("summary") or "")
        location = f_fr.get("location") or f_en.get("location") or ""
        m = re.match(r"(.*?)\s*\(([A-Z]{2})\)\s*$", location)
        city, code = (m.group(1), m.group(2)) if m else (location, "")
        region = normalize_region(code)[0] if code else "Canada"
        url_en = e_en.get("link") or f"https://www.jobbank.gc.ca/jobsearch/jobposting/{ref}"
        url_fr = e_fr.get("link") or f"https://www.guichetemplois.gc.ca/jobsearch/jobposting/{ref}"
        published = _iso_date(e_en.get("updated") or e_fr.get("updated") or "")
        items.append({
            "source": "jobbank", "source_ref": ref, "kind": "job",
            "title_fr": title_fr or title_en, "title_en": title_en or title_fr,
            "summary_fr": f_fr.get("salary") or f_en.get("salary") or "",
            "summary_en": f_en.get("salary") or f_fr.get("salary") or "",
            "org": f_en.get("org") or f_fr.get("org") or "", "country": "Canada", "region": region,
            "regions_raw": location, "city": city if code else "",
            "url_fr": url_fr, "url_en": url_en, "notice_type": "", "category": "job",
            "specialties": specialties_for(title_en + " " + title_fr),
            "published_at": published, "closes_at": _plus_days(published, JOB_DAYS),
        })
    return items


def collect_jobbank(conn) -> dict:
    import time
    import urllib.parse
    entries_en: list = []
    entries_fr: list = []
    errors = calls = 0
    for term in JOBBANK_TERMS:
        q = urllib.parse.quote(term)
        for store, url in ((entries_en, JOBBANK_FEED_EN.format(q=q)), (entries_fr, JOBBANK_FEED_FR.format(q=q))):
            if calls:
                time.sleep(JOBBANK_PAUSE)
            calls += 1
            try:
                store.extend(_atom_entries(_fetch(url, timeout=60)))
            except Exception as exc:
                log.warning("jobbank %s: %s", url, exc)
                errors += 1
    items = parse_jobbank(entries_en, entries_fr)
    return {"items": _upsert_many(conn, items), "seen": len(entries_en) + len(entries_fr), "errors": errors}


# ---------------------------------------------------------------------------
# Emplois : Adzuna (API officielle sur clé, 19 pays)
# ---------------------------------------------------------------------------

def parse_adzuna(data: dict, country: str) -> list:
    items = []
    for r in (data or {}).get("results") or []:
        title = re.sub(r"\s+", " ", _strip_html(r.get("title") or "")).strip()
        desc = _strip_html(r.get("description") or "")
        url = (r.get("redirect_url") or "").strip()
        if not url or not job_matches(title):
            continue
        loc = r.get("location") or {}
        area = [a for a in (loc.get("area") or []) if a]
        region = area[1] if len(area) > 1 else ""
        city = area[-1] if len(area) > 2 else ""
        if country == "Canada":
            region = normalize_region(region or city)[0]
        summary = summarize(desc)
        if r.get("salary_min") and not r.get("salary_is_predicted") in (1, "1", True):
            lo, hi = int(r["salary_min"]), int(r.get("salary_max") or r["salary_min"])
            summary = (summary + " " if summary else "") + (f"({lo:,}–{hi:,})" if hi != lo else f"({lo:,})")
        published = _iso_date(r.get("created") or "")
        items.append({
            "source": "adzuna", "source_ref": str(r.get("id") or url), "kind": "job",
            "title_fr": title, "title_en": title, "summary_fr": summary, "summary_en": summary,
            "org": (r.get("company") or {}).get("display_name") or "", "country": country,
            "region": region or country, "regions_raw": loc.get("display_name") or "", "city": city,
            "url_fr": url, "url_en": url,
            "notice_type": ", ".join(str(x).replace("_", " ") for x in (r.get("contract_type"), r.get("contract_time")) if x),
            "category": "job", "specialties": specialties_for(title + " " + desc),
            "published_at": published, "closes_at": _plus_days(published, JOB_DAYS),
        })
    return items


def collect_adzuna(conn) -> dict:
    """Tous les téléchargements d'abord, une seule écriture ensuite : la
    connexion garde le verrou SQLite dès la première insertion jusqu'au
    commit, et ~100 appels espacés durent plusieurs minutes."""
    import time
    import urllib.parse
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        return {"skipped": "ADZUNA_APP_ID / ADZUNA_APP_KEY absents"}
    items: list = []
    errors = calls = 0
    for cc, country in ADZUNA_COUNTRIES.items():
        queries = ["title_only=" + urllib.parse.quote(t) for t in ADZUNA_TITLE_TERMS + ADZUNA_EXTRA_TERMS.get(cc, ())]
        queries.append(ADZUNA_WIDE)
        for query in queries:
            for page in range(1, ADZUNA_PAGES + 1):
                if calls >= ADZUNA_MAX_CALLS:
                    break
                url = ADZUNA_API.format(cc=cc, page=page, query=query, app_id=ADZUNA_APP_ID, app_key=ADZUNA_APP_KEY)
                if calls:
                    time.sleep(ADZUNA_PAUSE)
                calls += 1
                try:
                    data = json.loads(_fetch(url, timeout=60).decode("utf-8"))
                except Exception as exc:
                    log.warning("adzuna %s %s p%d: %s", cc, query[:24], page, exc)
                    errors += 1
                    time.sleep(ADZUNA_PAUSE * 4)   # 500/503 passagers côté Adzuna : on souffle avant la suite
                    break
                items.extend(parse_adzuna(data, country))
                if len(data.get("results") or []) < 50:
                    break
    seen: set = set()
    unique = [it for it in items if not (it["source_ref"] in seen or seen.add(it["source_ref"]))]
    return {"items": _upsert_many(conn, unique), "countries": len(ADZUNA_COUNTRIES), "calls": calls, "errors": errors}


# ---------------------------------------------------------------------------
# Emplois : USAJOBS (fédéral américain, API officielle sur clé)
# ---------------------------------------------------------------------------

def _fetch_headers(url: str, headers: dict, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_usajobs(data: dict) -> list:
    items = []
    for it in ((data or {}).get("SearchResult") or {}).get("SearchResultItems") or []:
        d = it.get("MatchedObjectDescriptor") or {}
        title = re.sub(r"\s+", " ", d.get("PositionTitle") or "").strip()
        url = (d.get("PositionURI") or "").strip()
        if not url or not job_matches(title):
            continue
        locs = d.get("PositionLocation") or []
        first = locs[0] if locs else {}
        state = first.get("CountrySubDivisionCode") or ""
        region = US_STATES.get(state, state) if len(locs) == 1 else "États-Unis"
        pay = (d.get("PositionRemuneration") or [{}])[0]
        salary = ""
        if pay.get("MinimumRange"):
            try:
                salary = f" ({int(float(pay['MinimumRange'])):,}–{int(float(pay.get('MaximumRange') or pay['MinimumRange'])):,} USD)"
            except ValueError:
                salary = ""
        summary = summarize(((d.get("UserArea") or {}).get("Details") or {}).get("JobSummary") or "")
        notice = ", ".join(x.get("Name", "") for x in (d.get("PositionSchedule") or []) + (d.get("PositionOfferingType") or []) if x.get("Name"))
        items.append({
            "source": "usajobs", "source_ref": str(it.get("MatchedObjectId") or d.get("PositionID") or url), "kind": "job",
            "title_fr": title, "title_en": title, "summary_fr": summary + salary, "summary_en": summary + salary,
            "org": d.get("OrganizationName") or d.get("DepartmentName") or "", "country": "États-Unis",
            "region": region or "États-Unis", "regions_raw": d.get("PositionLocationDisplay") or "",
            "city": first.get("CityName") or "" if len(locs) == 1 else "",
            "url_fr": url, "url_en": url, "notice_type": notice, "category": "job",
            "specialties": specialties_for(title + " " + summary),
            "published_at": _iso_date(d.get("PublicationStartDate") or ""),
            "closes_at": _iso_date(d.get("ApplicationCloseDate") or "") or _plus_days(_iso_date(d.get("PublicationStartDate") or ""), JOB_DAYS),
        })
    return items


def collect_usajobs(conn) -> dict:
    import urllib.parse
    if not (USAJOBS_KEY and USAJOBS_EMAIL):
        return {"skipped": "USAJOBS_API_KEY / USAJOBS_EMAIL absents"}
    headers = {"Host": "data.usajobs.gov", "User-Agent": USAJOBS_EMAIL, "Authorization-Key": USAJOBS_KEY}
    items: list = []
    errors = 0
    for term in USAJOBS_TERMS:
        try:
            data = json.loads(_fetch_headers(USAJOBS_API.format(kw=urllib.parse.quote(term)), headers).decode("utf-8"))
        except Exception as exc:
            log.warning("usajobs %s: %s", term, exc)
            errors += 1
            continue
        items.extend(parse_usajobs(data))
    seen: set = set()
    unique = [it for it in items if not (it["source_ref"] in seen or seen.add(it["source_ref"]))]
    return {"items": _upsert_many(conn, unique), "errors": errors}


# ---------------------------------------------------------------------------
# Emplois : pages carrières d'employeurs du drone (flux publics de leur ATS)
# ---------------------------------------------------------------------------

_COUNTRY_ALIASES = {"usa": "États-Unis", "us": "États-Unis", "u.s.": "États-Unis", "united states": "États-Unis",
                    "united states of america": "États-Unis", "uk": "Royaume-Uni", "united kingdom": "Royaume-Uni",
                    "england": "Royaume-Uni", "scotland": "Royaume-Uni", "deutschland": "Allemagne", "germany": "Allemagne",
                    "schweiz": "Suisse", "switzerland": "Suisse", "españa": "Espagne", "spain": "Espagne", "italia": "Italie",
                    "italy": "Italie", "brasil": "Brésil", "brazil": "Brésil", "nederland": "Pays-Bas", "netherlands": "Pays-Bas",
                    "österreich": "Autriche", "austria": "Autriche", "côte d’ivoire": "Côte d'Ivoire", "ivory coast": "Côte d'Ivoire",
                    "rwanda": "Rwanda", "ghana": "Ghana", "nigeria": "Nigeria", "kenya": "Kenya", "japan": "Japon", "finland": "Finlande",
                    "singapore": "Singapour", "taiwan": "Taïwan", "france": "France", "canada": "Canada", "belgium": "Belgique",
                    "australia": "Australie", "india": "Inde", "mexico": "Mexique", "poland": "Pologne"}
_US_STATE_NAMES = {v.lower(): v for v in US_STATES.values()}


def _country_from_location(loc: str, hint: str = "") -> tuple:
    """(pays de référence, région) d'après « Houston, Texas, USA », « Byron,
    CA » + hint « US », « Zurich, Switzerland », « Arlington, Virginia »."""
    import i18n
    names = {v.lower(): k for k, v in i18n._COUNTRY_NAMES.get("en", {}).items()}
    names.update({k.lower(): k for k in i18n._COUNTRY_NAMES.get("en", {})})
    names.update(_COUNTRY_ALIASES)
    parts = [p.strip() for p in re.split(r"[;,/·]", loc or "") if p.strip()]
    country, region = "", ""
    for p in reversed(parts):
        low = p.lower()
        if low in names:
            country = names[low]
            break
    for p in parts:
        low = p.lower()
        if low in _US_STATE_NAMES:
            country, region = "États-Unis", _US_STATE_NAMES[low]
            break
        if p.upper() in US_STATES and (country == "États-Unis" or (hint or "").upper() == "US"):
            country, region = "États-Unis", US_STATES[p.upper()]
            break
    if not country and hint:
        import geoip
        country = names.get(hint.lower()) or (geoip.country_fr(hint) if len(hint) == 2 and geoip.country_fr(hint) != hint.upper() else "")
    if country == "Canada":
        region = normalize_region(loc)[0]
    return country, region


def _employer_keep(title: str, strict: bool) -> bool:
    if strict:
        return job_matches(title)
    if not title or JOB_NOISE.search(title) or NOISE.search(title):
        return False
    return bool(KEYWORDS.search(title) or (EMPLOYER_ROLE.search(title) and not EMPLOYER_EXCLUDE.search(title)))


def parse_employer_board(ats: str, raw: bytes, board: str, employer: str, strict: bool, default_country: str = "") -> list:
    """Normalise le flux d'un ATS en fiches : (titre, lieu, pays hint, lien,
    date, type de contrat)."""
    rows: list = []
    if ats == "greenhouse":
        for j in (json.loads(raw.decode("utf-8")).get("jobs") or []):
            rows.append((j.get("title"), (j.get("location") or {}).get("name"), "", j.get("absolute_url"),
                         j.get("first_published") or j.get("updated_at"), "", str(j.get("id"))))
    elif ats == "ashby":
        for j in (json.loads(raw.decode("utf-8")).get("jobs") or []):
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            loc = j.get("location") or ""
            rows.append((j.get("title"), loc, addr.get("addressCountry") or "", j.get("jobUrl"), j.get("publishedAt"),
                         (j.get("employmentType") or "").replace("FullTime", "full time").replace("PartTime", "part time"), j.get("id")))
    elif ats == "lever":
        for j in json.loads(raw.decode("utf-8")):
            cats = j.get("categories") or {}
            created = j.get("createdAt")
            when = datetime.utcfromtimestamp(created / 1000).date().isoformat() if isinstance(created, (int, float)) else ""
            rows.append((j.get("text"), cats.get("location") or "", j.get("country") or "", j.get("hostedUrl"), when,
                         cats.get("commitment") or "", j.get("id")))
    elif ats == "workable":
        for j in (json.loads(raw.decode("utf-8")).get("jobs") or []):
            loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
            rows.append((j.get("title"), loc, j.get("country") or "", j.get("url"), j.get("published_on"),
                         (j.get("employment_type") or "").lower(), j.get("shortcode")))
    elif ats == "personio":
        import xml.etree.ElementTree as ET
        for pos in ET.fromstring(raw).iter("position"):
            pid = (pos.findtext("id") or "").strip()
            rows.append((pos.findtext("name"), pos.findtext("office") or "", "", f"https://{board}.jobs.personio.de/job/{pid}",
                         pos.findtext("createdAt"), ", ".join(x for x in (pos.findtext("employmentType"), pos.findtext("schedule")) if x), pid))
    elif ats == "bamboohr":
        # Liste publique du tableau de bord carrières : pas de date de parution,
        # la fiche vit tant qu'elle reste dans le flux.
        for j in (json.loads(raw.decode("utf-8")).get("result") or []):
            loc = j.get("location") or {}
            place = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
            rows.append((j.get("jobOpeningName"), place, (j.get("atsLocation") or {}).get("country") or "",
                         f"https://{board}.bamboohr.com/careers/{j.get('id')}", "",
                         (j.get("employmentStatusLabel") or "").lower(), str(j.get("id"))))
    elif ats == "teamtailor":
        import xml.etree.ElementTree as ET
        ns = "{https://teamtailor.com/locations}"
        for it in ET.fromstring(raw).iter("item"):
            loc = ", ".join(x for x in (it.findtext(f"{ns}locations/{ns}location/{ns}city"), it.findtext(f"{ns}locations/{ns}location/{ns}country")) if x)
            remote = it.findtext("remoteStatus") or ""
            rows.append((it.findtext("title"), loc, it.findtext(f"{ns}locations/{ns}location/{ns}country") or "", it.findtext("link"),
                         _rss_date(it.findtext("pubDate") or ""), remote if remote in ("onsite", "hybrid", "remote") else "",
                         it.findtext("guid") or it.findtext("link")))
    items = []
    for title, loc, hint, url, when, contract, ref in rows:
        title = re.sub(r"\s+", " ", title or "").strip()
        if not url or not ref or not _employer_keep(title, strict):
            continue
        country, region = _country_from_location(loc or "", hint)
        anywhere = bool(re.search(r"remote|international|anywhere", loc or "", re.I))
        if not country:
            country = "International" if anywhere else (default_country or "International")
            if country == "Canada" and not anywhere:
                region = normalize_region(loc or "")[0]
        published = _iso_date(when or "")
        city = (loc or "").split(",")[0].strip() if loc and not anywhere else ""
        items.append({
            "source": "employers", "source_ref": f"{board}:{ref}", "kind": "job",
            "title_fr": title, "title_en": title, "summary_fr": "", "summary_en": "",
            "org": employer, "country": country, "region": region or country,
            "regions_raw": loc or "", "city": city if city.lower() != country.lower() else "",
            "url_fr": url, "url_en": url, "notice_type": contract or "", "category": "job",
            "specialties": specialties_for(title),
            # une page carrières ne liste que des postes ouverts : pas de date de
            # fin, la fiche est close quand elle disparaît du flux (collect_employers)
            "published_at": published, "closes_at": None,
        })
    return items


def collect_employers(conn) -> dict:
    items: list = []
    errors = 0
    per_board: dict = {}
    for board, ats, slug, employer, strict, default_country in EMPLOYER_BOARDS:
        try:
            raw = _fetch(ATS_URLS[ats].format(slug=slug), timeout=60)
            got = parse_employer_board(ats, raw, board, employer, strict, default_country)
        except Exception as exc:
            log.warning("employeur %s (%s): %s", board, ats, exc)
            errors += 1
            continue
        per_board[board] = len(got)
        items.extend(got)
    seen = {it["source_ref"] for it in items}
    n = _upsert_many(conn, items)
    # un poste retiré de la page carrières (flux lu sans erreur) est clos
    closed = 0
    for board in per_board:
        for (opp_id, ref) in conn.execute("SELECT id, source_ref FROM opportunities WHERE source='employers' AND source_ref LIKE ? "
                                          "AND status='published'", (board + ":%",)).fetchall():
            if ref not in seen:
                conn.execute("UPDATE opportunities SET status='closed' WHERE id=?", (opp_id,)); closed += 1
    return {"items": n, "boards": per_board, "closed": closed, "errors": errors}


# ---------------------------------------------------------------------------
# Vérification des liens : un avis dont la page ne répond pas n'est pas montré
# ---------------------------------------------------------------------------

LINK_OK = (200, 202, 203, 301, 302, 303, 307, 308)
LINK_TIMEOUT = 20
# Adzuna répond 403 à tout robot sur ses pages de redirection : l'API (30 jours
# glissants) et la date de fin de la fiche font foi, pas le contrôle du lien.
LINK_CHECK_SKIP = ("adzuna",)


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


def verify_links(limit: int = 120) -> dict:
    """Contrôle les liens des fiches publiées non encore vérifiées (et
    revérifie les plus anciennes) : lien mort -> fiche retirée (status
    'broken'), ou bascule sur le lien anglais s'il répond. Les requêtes
    HTTP se font sans verrou sur la base ; l'écriture est une transaction
    courte à la fin. Borné par nuit pour rester poli avec les portails."""
    with db.standalone() as conn:
        rows = conn.execute(
            "SELECT id, url_fr, url_en FROM opportunities WHERE status='published' "
            f"AND source NOT IN ({','.join('?' * len(LINK_CHECK_SKIP))}) "
            "ORDER BY link_checked_at IS NOT NULL, link_checked_at LIMIT ?", (*LINK_CHECK_SKIP, limit)).fetchall()
    results = []
    for oid, url_fr, url_en in rows:
        code = link_status(url_fr)
        if code in LINK_OK:
            results.append((oid, "ok", code, None)); continue
        if url_en and url_en != url_fr and link_status(url_en) in LINK_OK:
            results.append((oid, "swap", 200, url_en)); continue
        results.append((oid, "broken", code, None))
        log.warning("opportunite %s: lien mort (%s) %s", oid, code, url_fr)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    counts = {"checked": len(rows), "ok": 0, "swapped": 0, "broken": 0}
    with db.standalone() as conn:
        for oid, kind, code, new_url in results:
            if kind == "ok":
                conn.execute("UPDATE opportunities SET link_checked_at=?, link_status=? WHERE id=?", (now, code, oid)); counts["ok"] += 1
            elif kind == "swap":
                conn.execute("UPDATE opportunities SET url_fr=?, link_checked_at=?, link_status=200 WHERE id=?", (new_url, now, oid)); counts["swapped"] += 1
            else:
                conn.execute("UPDATE opportunities SET status='broken', link_checked_at=?, link_status=? WHERE id=?", (now, code, oid)); counts["broken"] += 1
    return counts


# ---------------------------------------------------------------------------
# Collecte complète
# ---------------------------------------------------------------------------

def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()[:80]


SECONDARY_SOURCES = ("ted", "adzuna")   # agrégateurs : le doublon cède devant la source nationale


def dedupe_cross_sources(conn) -> int:
    """Un marché français est publié à la fois au BOAMP et au TED, une offre
    canadienne au Guichet-Emplois et chez Adzuna : on garde la fiche de la
    source nationale (lien direct, région) et on ferme le doublon de
    l'agrégateur. Pour un emploi, même titre ET même employeur."""
    rows = conn.execute("SELECT id, source, country, title_fr, kind, org FROM opportunities WHERE status='published'").fetchall()
    seen: dict = {}
    closed = 0
    for r in sorted(rows, key=lambda r: (r[1] in SECONDARY_SOURCES,)):   # sources nationales d'abord
        # employeur réduit à son premier mot : « Anduril » chez Adzuna, « Anduril Industries » sur sa page carrières
        key = (r[2], _norm_title(r[3]), _norm_title(r[5]).split(" ")[0] if r[4] == "job" else "")
        if key in seen and r[1] in SECONDARY_SOURCES:
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
    """Une passe complète. Les téléchargements et les contrôles de liens se
    font HORS transaction : chaque source ouvre sa propre connexion courte,
    validée aussitôt, pour ne jamais retenir le verrou SQLite pendant le
    réseau (une première version bloquait le site 30 s : les compteurs de
    visites attendaient derrière la collecte). Une source en panne ne bloque
    pas les autres."""
    report: dict = {}
    state = _load_state()
    for name, fn in (("canadabuys", lambda c: collect_canadabuys(c)), ("seao", lambda c: collect_seao(c, state)),
                     ("ted", collect_ted), ("boamp", collect_boamp), ("contractsfinder", collect_contractsfinder),
                     ("austender", collect_austender), ("gets", collect_gets), ("secop", collect_secop),
                     ("samgov", collect_samgov), ("jobbank", collect_jobbank), ("adzuna", collect_adzuna),
                     ("usajobs", collect_usajobs), ("employers", collect_employers)):
        try:
            with db.standalone() as conn:
                report[name] = fn(conn)
        except Exception as exc:
            log.warning("opportunites %s: %s", name, exc)
            report[name] = {"error": str(exc)[:200]}
    with db.standalone() as conn:
        report["dedup"] = dedupe_cross_sources(conn)
        report["expired"] = expire(conn)
    if verify:
        report["links"] = verify_links()
    with db.standalone() as conn:
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
