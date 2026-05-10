"""Configuration AubePilot.

Marketplace souveraine type Uber pour pilotes de drone et
clients qui publient des missions. Auth PAM partagee avec les autres
services Aube. SQLite local pour demarrer ; PostgreSQL possible plus tard.
"""
import os

PORT = int(os.environ.get("AUBEPILOT_PORT", "5034"))
HOST = os.environ.get("AUBEPILOT_HOST", "0.0.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Toutes les donnees runtime (DB, uploads, mail dev) vivent dans data/
# pour separer code (versionne) et etat (gitignore).
DATA_DIR = os.environ.get("AUBEPILOT_DATA", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "aubepilot.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
MAIL_DUMP_DIR = os.path.join(DATA_DIR, "mail")

# URL publique du site (utilisee dans les emails et metas)
SITE_URL = os.environ.get("SITE_URL", f"http://localhost:{PORT}")

for _d in (DATA_DIR, UPLOAD_DIR, MAIL_DUMP_DIR):
    os.makedirs(_d, exist_ok=True)

SECRET_KEY = os.environ.get("AUBEPILOT_SECRET", "change-me-in-prod-aubepilot-2026")
# La validation de production (refuse la cle par defaut hors dev) est faite
# dans security.assert_production_ready() au demarrage de l'app.
SESSION_COOKIE_NAME = "aubepilot_sid"
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

# Annulation tardive cote client : preavis exige (heures) avant
# mission.start_date. En dessous de ce preavis, le client dedomage le pilote
# a hauteur de LATE_CANCELLATION_FEE_PCT du prix convenu, et est rembourse
# du reste. Au-dessus, refund integral et aucune penalite.
LATE_CANCELLATION_HOURS = 24
LATE_CANCELLATION_FEE_PCT = 25.0

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

# Autorites de certification (catalogue indicatif, monde entier)
LICENCE_AUTHORITIES = [
    # Europe
    ("EASA",     "EASA - A1/A2/A3 / STS (UE)"),
    ("DGAC",     "DGAC (France)"),
    ("CAA_UK",   "CAA (Royaume-Uni)"),
    ("OFAC",     "OFAC / FOCA (Suisse)"),
    ("LBA_DE",   "LBA (Allemagne)"),
    ("ENAC_IT",  "ENAC (Italie)"),
    ("AESA_ES",  "AESA (Espagne)"),
    # Amerique du Nord
    ("Transport Canada", "Transport Canada (avance / de base)"),
    ("FAA",      "FAA Part 107 (USA)"),
    # Amerique latine
    ("ANAC_BR",  "ANAC (Bresil)"),
    ("DGAC_CL",  "DGAC (Chili)"),
    # Maghreb / Afrique
    ("DGAC_MA",  "DGAC Maroc"),
    ("ANAC_TN",  "ANAC Tunisie"),
    ("DACM_DZ",  "DACM Algerie"),
    ("ASECNA",   "ASECNA (Afrique de l'Ouest)"),
    ("SACAA_ZA", "SACAA (Afrique du Sud)"),
    # Russie / CEI
    ("Rosaviatsia", "Rosaviatsia (Russie)"),
    # Asie-Pacifique
    ("CAAC",     "CAAC (Chine)"),
    ("JCAB",     "JCAB (Japon)"),
    ("KOCA",     "KOCA / MOLIT (Coree du Sud)"),
    ("CASA",     "CASA (Australie)"),
    ("CAANZ",    "CAA (Nouvelle-Zelande)"),
    ("DGCA_IN",  "DGCA (Inde)"),
    ("CAAS_SG",  "CAAS (Singapour)"),
    # Moyen-Orient
    ("GCAA_AE",  "GCAA (Emirats arabes unis)"),
    ("GACA_SA",  "GACA (Arabie saoudite)"),
    ("CAAI_IL",  "CAAI (Israel)"),
    # Generique
    ("autre",    "Autre / declarative"),
]

# Catalogue indicatif des intitules de brevet par autorite. Le pilote peut
# choisir une option suggeree (datalist) ou taper un intitule libre.
# Cle = code autorite, valeur = liste d'intitules pretablis.
LICENCE_TITLES_BY_AUTHORITY = {
    "DGAC": [
        "Categorie Ouverte A1/A3",
        "Categorie Ouverte A2",
        "Scenario STS-01 (vue directe urbain)",
        "Scenario STS-02 (hors vue, hors urbain)",
        "Scenario S1 (legacy <2024)",
        "Scenario S2 (legacy)",
        "Scenario S3 (legacy)",
        "Scenario S4 (legacy hors vue)",
        "Brevet theorique telepilote DGAC",
    ],
    "EASA": [
        "Categorie Ouverte A1/A3",
        "Categorie Ouverte A2 (CATS)",
        "Scenario standard STS-01",
        "Scenario standard STS-02",
        "Categorie Specifique - LUC",
        "Categorie Certifiee",
    ],
    "Transport Canada": [
        "Operations de base (RPAS)",
        "Operations avancees (RPAS)",
        "Operations en vol au-dela visibilite directe (BVLOS)",
        "Pilote certifie operations specialisees",
    ],
    "FAA": [
        "Part 107 - Remote Pilot Certificate",
        "Part 107 - Waiver / sUAS specifique",
        "Part 61 - Manned + sUAS endorsement",
    ],
    "DGAC_MA": [
        "Telepilote scenarios S1-S2-S3",
        "Telepilote scenario S4 (hors vue)",
        "Operateur drone professionnel - DGAC Maroc",
    ],
    "ANAC_TN": [
        "Telepilote categorie A (loisir)",
        "Telepilote categorie B (professionnel)",
        "Operateur drone agree ANAC",
    ],
    "DACM_DZ": [
        "Telepilote loisir",
        "Telepilote professionnel",
        "Operateur drone certifie DACM",
    ],
    "ASECNA": [
        "Telepilote ASECNA categorie standard",
        "Telepilote ASECNA categorie specifique",
        "Operateur certifie ASECNA",
    ],
    "OFAC": [
        "Categorie Ouverte A1/A3",
        "Categorie Ouverte A2",
        "Categorie Specifique (LUC)",
    ],
    "CAA_UK": [
        "A2 CofC (A2 Certificate of Competency)",
        "GVC (General VLOS Certificate)",
        "Operational Authorisation (PDRA)",
        "BVLOS Operational Authorisation",
    ],
    "LBA_DE": [
        "EU-Kompetenznachweis A1/A3",
        "EU-Fernpilotenzeugnis A2",
        "STS-DE-01 / STS-DE-02",
        "Spezielle Betriebsgenehmigung (LUC)",
    ],
    "ENAC_IT": [
        "Attestato pilota A1/A3",
        "Attestato pilota A2",
        "Scenario standard STS-IT-01",
        "Autorizzazione LUC",
    ],
    "AESA_ES": [
        "Curso A1/A3 (categoria abierta)",
        "Curso A2 (categoria abierta)",
        "Escenario estandar STS-ES",
        "Certificado LUC",
    ],
    "ANAC_BR": [
        "Piloto remoto Classe 3 (RPA <25 kg, VLOS)",
        "Piloto remoto Classe 2 (25-150 kg)",
        "Piloto remoto Classe 1 (>150 kg)",
        "Codigo ANAC RPA",
    ],
    "DGCA_CL": [
        "Operador remoto categoria abierta",
        "Operador remoto categoria especifica",
    ],
    "SACAA_ZA": [
        "RPL (Remote Pilot Licence)",
        "ROC (RPAS Operator Certificate)",
        "BVLOS Authorisation",
    ],
    "Rosaviatsia": [
        "Vneshnij pilot - kategoriya A (do 30 kg)",
        "Vneshnij pilot - kategoriya B (BVLOS / >30 kg)",
        "Sertifikat ekspluatanta BVS",
    ],
    "CAAC": [
        "AOPA-China multirotor (sub-7 kg)",
        "AOPA-China multirotor (7-25 kg)",
        "AOPA-China BVLOS / large UAV",
        "UTC Trainer rating",
    ],
    "JCAB": [
        "JCAB Level 1 - basic skill",
        "JCAB Level 2 - DID / nuit / BVLOS",
        "JCAB Level 3 - ops habites",
        "JCAB Level 4 - urbain / habites",
    ],
    "KOCA": [
        "Telepilote categorie 1 (<2 kg)",
        "Telepilote categorie 2 (2-7 kg)",
        "Telepilote categorie 3 (7-25 kg)",
        "Telepilote categorie 4 (>25 kg)",
    ],
    "CASA": [
        "RePL (Remote Pilot Licence)",
        "ReOC (Remote Operator's Certificate)",
        "BVLOS Approval",
        "Excluded category - sub-2 kg commercial",
    ],
    "CAANZ": [
        "Part 101 - sub-25 kg unmanned",
        "Part 102 - certified UAV operations",
        "BVLOS Authorisation",
    ],
    "DGCA_IN": [
        "Small UAS Pilot (RPC) - sub-25 kg",
        "Medium UAS Pilot (25-150 kg)",
        "Large UAS Pilot (>150 kg)",
        "BVLOS Permit",
    ],
    "CAAS_SG": [
        "UAPL (Unmanned Aircraft Pilot Licence)",
        "Operator Permit (OP)",
        "Activity Permit",
    ],
    "GCAA_AE": [
        "UAS Pilot - Class A (sub-7 kg)",
        "UAS Pilot - Class B (7-25 kg)",
        "Commercial Operator Authorization",
    ],
    "GACA_SA": [
        "UAS pilot certificate - Cat 1 (sub-7 kg)",
        "UAS pilot certificate - Cat 2 (7-25 kg)",
        "Commercial UAS operator licence",
    ],
    "CAAI_IL": [
        "Commercial UAV Operator Licence",
        "Recreational UAV Permit",
    ],
    "autre": [
        "Brevet theorique pilote (PPL/CPL)",
        "Formation interne entreprise",
        "Autodidacte declare",
    ],
}

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
AUTO_RELEASE_DAYS = int(os.environ.get("AUBEPILOT_AUTO_RELEASE_DAYS", "7"))

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
