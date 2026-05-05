"""Configuration AubeDroniste.

Marketplace souveraine type Uber pour pilotes de drone (dronistes) et
clients qui publient des missions. Auth PAM partagee avec les autres
services Aube. SQLite local pour demarrer ; PostgreSQL possible plus tard.
"""
import os

PORT = int(os.environ.get("AUBEDRONISTE_PORT", "5034"))
HOST = os.environ.get("AUBEDRONISTE_HOST", "0.0.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Toutes les donnees runtime (DB, uploads, mail dev) vivent dans data/
# pour separer code (versionne) et etat (gitignore).
DATA_DIR = os.environ.get("AUBEDRONISTE_DATA", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "aubedroniste.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
MAIL_DUMP_DIR = os.path.join(DATA_DIR, "mail")

# URL publique du site (utilisee dans les emails et metas)
SITE_URL = os.environ.get("SITE_URL", f"http://localhost:{PORT}")

for _d in (DATA_DIR, UPLOAD_DIR, MAIL_DUMP_DIR):
    os.makedirs(_d, exist_ok=True)

SECRET_KEY = os.environ.get("AUBEDRONISTE_SECRET", "change-me-in-prod-aubedroniste-2026")
SESSION_COOKIE_NAME = "aubedroniste_sid"
SESSION_LIFETIME_DAYS = 30

EMAIL_DOMAIN = "aubemail.com"

# Fichiers
MAX_UPLOAD_MB = 10
ALLOWED_DOC_EXT = {"pdf", "png", "jpg", "jpeg", "webp"}

# Devises supportees par defaut
CURRENCIES = ["EUR", "CAD", "USD", "MAD", "TND", "DZD", "XOF", "CHF"]
DEFAULT_CURRENCY = "EUR"

# Recherche geo : rayon par defaut (km)
DEFAULT_SEARCH_RADIUS_KM = 50
MAX_SEARCH_RADIUS_KM = 500

# Commission plateforme (pourcent), preleve a la confirmation booking.
# 30% : choix Nicolas — le materiel drone est cher, la mission a plus de valeur ajoutee.
PLATFORM_FEE_PCT = 30.0

# Statuts
MISSION_STATUS = ("open", "assigned", "in_progress", "done", "cancelled")
BID_STATUS = ("pending", "accepted", "rejected", "withdrawn")
BOOKING_STATUS = ("scheduled", "in_progress", "completed", "cancelled", "disputed")

# Types de missions (FR)
MISSION_TYPES = [
    ("photo",        "Photographie aerienne"),
    ("video",        "Video aerienne / clip"),
    ("immobilier",   "Immobilier (visite, prises de vues)"),
    ("mariage",      "Mariage / evenement prive"),
    ("evenement",    "Evenement public / sport"),
    ("mapping",      "Cartographie / orthophoto"),
    ("3d",           "Modelisation 3D / photogrammetrie"),
    ("inspection",   "Inspection technique (toiture, panneaux, eolienne)"),
    ("agriculture",  "Agriculture (NDVI, epandage, suivi parcelle)"),
    ("btp",          "BTP / suivi de chantier"),
    ("surveillance", "Surveillance / securite privee"),
    ("livraison",    "Livraison (charges legeres)"),
    ("recherche",    "Recherche & sauvetage"),
    ("formation",    "Formation / accompagnement vol"),
    ("autre",        "Autre"),
]

# Types de drones (catalogue)
DRONE_CATEGORIES = [
    ("micro",        "Micro / sous 250 g"),
    ("loisir",       "Loisir / FPV"),
    ("pro_camera",   "Pro camera (Mavic 3 Pro, Air 3, Inspire)"),
    ("cinema",       "Cinema (Inspire 3, Alta X, FreeFly)"),
    ("rtk",          "Cartographie RTK (P4 RTK, M350, WingtraOne)"),
    ("agri",         "Agriculture (T40, AGRAS, MG-1)"),
    ("inspection",   "Inspection (M30T, M350 + L2/H20T)"),
    ("livraison",    "Livraison cargo"),
    ("vtol",         "VTOL longue endurance"),
]

# Capacites optionnelles d'un drone
DRONE_CAPABILITIES = [
    "camera_4k", "camera_6k", "camera_8k",
    "thermique", "lidar", "rtk", "multispectrale",
    "zoom_optique", "haut_parleur", "projecteur",
    "largage", "epandage",
]

# Autorites de certification (catalogue indicatif)
LICENCE_AUTHORITIES = [
    ("DGAC",     "DGAC (France)"),
    ("EASA",     "EASA - A1/A2/A3 / STS (UE)"),
    ("Transport Canada", "Transport Canada (avance / de base)"),
    ("FAA",      "FAA Part 107 (USA)"),
    ("DGAC_MA",  "DGAC Maroc"),
    ("ANAC_TN",  "ANAC Tunisie"),
    ("DACM_DZ",  "DACM Algerie"),
    ("ASECNA",   "ASECNA (Afrique de l'Ouest)"),
    ("OFAC",     "OFAC (Suisse)"),
    ("autre",    "Autre / declarative"),
]

# Pays vedettes pour le selecteur (le reste reste libre)
FEATURED_COUNTRIES = [
    "France", "Canada", "Belgique", "Suisse", "Luxembourg",
    "Maroc", "Algerie", "Tunisie", "Senegal", "Cote d'Ivoire",
    "Cameroun", "Mali", "Burkina Faso", "Niger", "Madagascar",
    "Liban", "Quebec",
]
