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
# La validation de production (refuse la cle par defaut hors dev) est faite
# dans security.assert_production_ready() au demarrage de l'app.
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
BOOKING_STATUS = (
    "pending_payment",  # bid accepte, en attente paiement client
    "funded",           # client a paye, fonds en escrow plateforme
    "in_progress",      # pilote en operation
    "completed",        # client a valide, fonds libérés au pilote
    "cancelled",        # annulee avant paiement
    "disputed",         # mediation en cours
    "refunded",         # remboursee au client
)

# Types de missions (FR) — calque sur l'offre reelle des marches francais
# (DGAC) et canadien (Transport Canada). Les codes restent stables : seuls
# les libelles peuvent etre traduits via i18n. Backward-compat conservee
# pour photo / video / immobilier / mariage / evenement / mapping / 3d /
# inspection / agriculture / btp / surveillance / livraison / recherche /
# formation / autre.
MISSION_TYPES = [
    # ----- Médias & cinéma -----
    ("photo",          "Photographie aérienne"),
    ("video",          "Vidéo aérienne / clip"),
    ("cinema",         "Cinéma & FPV (cascades, racing)"),
    ("reportage",      "Reportage / documentaire"),
    ("mariage",        "Mariage / événement privé"),
    ("evenement",      "Événement public / sport"),

    # ----- Immobilier & patrimoine -----
    ("immobilier",     "Immobilier résidentiel & commercial"),
    ("patrimoine",     "Patrimoine & monuments historiques"),

    # ----- Cartographie & mesure -----
    ("mapping",        "Cartographie / orthophoto (RTK / PPK)"),
    ("3d",             "Modélisation 3D / photogrammétrie"),
    ("topographie",    "Topographie & levés de terrain"),
    ("volumes",        "Mesure de volumes (stocks, carrières)"),

    # ----- Inspection technique -----
    ("inspection",     "Inspection technique (général)"),
    ("toiture",        "Inspection toiture & façade"),
    ("ouvrage_art",    "Inspection ouvrages d'art (ponts, viaducs)"),
    ("eolienne",       "Inspection éolienne"),
    ("photovoltaique", "Inspection panneaux solaires"),
    ("ligne_ht",       "Inspection lignes électriques HT"),
    ("pipeline",       "Inspection pipelines / oléoducs"),
    ("ferroviaire",    "Inspection ferroviaire"),
    ("industriel",     "Inspection industrielle (silos, cheminées)"),
    ("thermographie",  "Thermographie énergétique / bâtiment"),

    # ----- Construction & BTP -----
    ("btp",            "BTP / suivi de chantier"),

    # ----- Agriculture & foresterie -----
    ("agriculture",    "Agriculture (NDVI, suivi parcelle)"),
    ("epandage",       "Pulvérisation & épandage agricole"),
    ("foresterie",     "Foresterie & inventaire forestier"),
    ("feux_foret",     "Détection / suivi feux de forêt"),

    # ----- Sécurité & urgence -----
    ("surveillance",   "Surveillance / sécurité privée"),
    ("sdis",           "Sécurité civile / SDIS / pompiers"),
    ("recherche",      "Recherche & sauvetage (SAR)"),
    ("sinistre",       "Constat d'assurance / sinistre"),

    # ----- Environnement -----
    ("environnement",  "Suivi environnemental / pollution"),

    # ----- Logistique & autres -----
    ("livraison",      "Livraison (charges légères)"),
    ("formation",      "Formation / accompagnement vol"),
    ("autre",          "Autre / sur mesure"),
]

# Groupes pour l'affichage en optgroup / sections de chips
MISSION_TYPE_GROUPS = [
    ("Médias & cinéma",          ["photo", "video", "cinema", "reportage", "mariage", "evenement"]),
    ("Immobilier & patrimoine",  ["immobilier", "patrimoine"]),
    ("Cartographie & mesure",    ["mapping", "3d", "topographie", "volumes"]),
    ("Inspection technique",     ["inspection", "toiture", "ouvrage_art", "eolienne",
                                  "photovoltaique", "ligne_ht", "pipeline", "ferroviaire",
                                  "industriel", "thermographie"]),
    ("Construction & BTP",       ["btp"]),
    ("Agriculture & foresterie", ["agriculture", "epandage", "foresterie", "feux_foret"]),
    ("Sécurité & urgence",       ["surveillance", "sdis", "recherche", "sinistre"]),
    ("Environnement",            ["environnement"]),
    ("Logistique & autres",      ["livraison", "formation", "autre"]),
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

# --- Stripe Connect ---------------------------------------------------------
# Si STRIPE_SECRET_KEY est vide, l'app fonctionne en "fake mode" :
# l'onboarding genere un account_id "acct_fake_<uid>", le paiement passe
# par une page interne qui simule la reussite. Permet de demoler le flow
# de bout en bout sans clé Stripe. En prod, poser ces 3 variables d'env :
#   STRIPE_SECRET_KEY=sk_live_...
#   STRIPE_PUBLISHABLE_KEY=pk_live_...
#   STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_LIVE_MODE       = STRIPE_SECRET_KEY.startswith("sk_live_")
STRIPE_FAKE_MODE       = not STRIPE_SECRET_KEY  # mode demo sans cle

# Auto-libération si le client n'a pas validé après ce delai (jours)
AUTO_RELEASE_DAYS = int(os.environ.get("AUBEDRONISTE_AUTO_RELEASE_DAYS", "7"))

# Filtre anti-bypass : regex bloquees dans la messagerie avant `funded`
MESSAGE_BANNED_PATTERNS = [
    r"[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}",          # email
    r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]?\d{2,4}){2,4}",  # tel
    r"\b(?:whatsapp|telegram|signal|wechat|instagram|insta|messenger|fb)\b",
    r"\b(?:wa\.me|t\.me|m\.me|ig|@\w{3,})\b",
]

# Pays vedettes pour le selecteur (le reste reste libre)
FEATURED_COUNTRIES = [
    "France", "Canada", "Belgique", "Suisse", "Luxembourg",
    "Maroc", "Algerie", "Tunisie", "Senegal", "Cote d'Ivoire",
    "Cameroun", "Mali", "Burkina Faso", "Niger", "Madagascar",
    "Liban", "Quebec",
]
