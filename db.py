"""Couche d'acces SQLite pour AubePilot.

Volontairement minimaliste : on garde les requetes pres du code metier,
sans ORM. Une connexion par requete Flask via `g`.

PRAGMAs appliques a chaque connexion :
  - foreign_keys=ON     -> integrite referentielle
  - journal_mode=WAL    -> writers et readers en parallele (already in schema.sql,
                            on le re-applique au cas ou)
  - synchronous=NORMAL  -> 2-3x plus rapide que FULL, safe avec WAL
  - cache_size=-64000   -> 64 MiB de cache page (negatif = KiB)
  - temp_store=MEMORY   -> tables temporaires en RAM
  - mmap_size=128MB     -> memory-mapped I/O pour lectures rapides
  - busy_timeout=30000  -> 30s avant 'database is locked' (vs 0 par defaut)
"""
import logging
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterable, Optional

from flask import g

from config import DB_PATH

log = logging.getLogger("aubepilot.db")

# Au-dela de ce seuil, on log un warning (slow query)
SLOW_QUERY_MS = 200


def _connect() -> sqlite3.Connection:
    """Cree une connexion SQLite proprement configuree pour la prod.

    `check_same_thread=False` permet a gunicorn de partager la connexion
    entre les threads d'un meme worker — sqlite3 le supporte tant qu'on
    n'utilise pas la meme connexion depuis 2 threads en MEME TEMPS (ce qui
    ne se produit pas ici : 1 connexion par requete via flask.g).
    """
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=30.0,                      # attente avant 'database is locked'
        check_same_thread=False,           # safe pour gunicorn threads
    )
    conn.row_factory = sqlite3.Row
    # PRAGMAs : a executer a chaque ouverture (sauf journal_mode qui est
    # persistant). busy_timeout EN PREMIER : le passage en WAL demande un
    # verrou exclusif et, sans busy handler, echoue tout de suite en
    # « database is locked » quand un autre worker gunicorn initialise la
    # base en meme temps (premier demarrage). On reessaie quelques fois.
    script = """
        PRAGMA busy_timeout=30000;
        PRAGMA foreign_keys=ON;
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA cache_size=-64000;
        PRAGMA temp_store=MEMORY;
        PRAGMA mmap_size=134217728;
    """
    for attempt in range(40):
        try:
            conn.executescript(script)
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 39:
                conn.close()
                raise
            time.sleep(0.25)
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def standalone():
    """Connexion hors contexte Flask (scripts, fetcher)."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(schema_path: str):
    if not os.path.exists(schema_path):
        raise FileNotFoundError(schema_path)
    with open(schema_path, encoding="utf-8") as f:
        sql = f.read()
    with standalone() as c:
        c.executescript(sql)


def _column_exists(conn, table: str, column: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def schema_ready(path: str = DB_PATH) -> bool:
    """True si la base existe ET contient le schema (table users). Un fichier
    vide cree par un autre worker en pleine initialisation compte pour NON."""
    if not os.path.exists(path):
        return False
    try:
        with standalone() as c:
            return _table_exists(c, "users")
    except sqlite3.Error:
        return False


# Migrations additives idempotentes : ajoutent les colonnes manquantes sur une
# base existante (CREATE TABLE IF NOT EXISTS ne modifie pas une table deja la).
_ADD_COLUMNS = [
    ("pilot_profiles", "business_name", "TEXT"),
    # Commission degressive : taux applique a CE booking (NULL sur les anciens
    # bookings -> deduit de platform_fee / agreed_price, cf. services.booking_fee_pct).
    ("bookings", "platform_fee_pct", "REAL"),
    # Annulation : frais de service retenus par la plateforme + qui a annule.
    ("bookings", "cancellation_service_fee", "REAL NOT NULL DEFAULT 0"),
    ("bookings", "cancelled_by", "TEXT"),
    # Revue des brevets document par document (cf. services.review_certification).
    ("pilot_certifications", "review_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("pilot_certifications", "review_note", "TEXT"),
    ("pilot_certifications", "reviewed_at", "TEXT"),
    ("pilot_certifications", "reviewed_by", "INTEGER"),
    # Parametres du compte (cf. /espace/parametres).
    ("users", "lang", "TEXT"),
    ("users", "notify_bids", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "notify_messages", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "notify_alerts", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "notify_news", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "deleted_at", "TEXT"),
]

# Rattrapage de donnees idempotent, joue apres les colonnes : aligne les
# brevets deja verifies (ancien flag seul) et le badge profil users.is_verified
# (jamais pose par l'ancien code) sur le nouveau modele.
_POST_SQL = [
    "UPDATE pilot_certifications SET review_status='verified' "
    "WHERE is_verified=1 AND review_status='pending'",
    "UPDATE users SET is_verified=1 WHERE is_verified=0 AND id IN ("
    "  SELECT pilot_user_id FROM pilot_certifications WHERE is_verified=1 "
    "  AND (expires_at IS NULL OR expires_at='' OR expires_at >= date('now')))",
]

# Index additifs idempotents. schema.sql n'est execute QUE sur une base neuve
# (bootstrap_db) ; sur la PROD existante, run_migrations() est le SEUL chemin
# rejoue a chaque demarrage. On y (re)cree donc les index manquants
# (CREATE INDEX IF NOT EXISTS = no-op si deja la). Tous ciblent des requetes
# chaudes : ratings replies (search_pilots/list_bids/featured_pilots), devis
# par mission, messagerie, classement de l'accueil.
_ADD_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bid_mission_price ON bids(mission_id, price)",
    "CREATE INDEX IF NOT EXISTS idx_booking_bid ON bookings(bid_id)",
    "CREATE INDEX IF NOT EXISTS idx_review_target_rating ON reviews(target_user_id, rating)",
    "CREATE INDEX IF NOT EXISTS idx_msg_sender ON messages(sender_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_msg_recip_read ON messages(mission_id, recipient_user_id, read_at)",
    "CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen_at)",
]


# Tables additives idempotentes (memes regles que les index : schema.sql ne
# tourne que sur une base neuve, la prod passe par run_migrations()).
_ADD_TABLES = [
    # Boite de reception du formulaire /contact (cf. services.create_contact_message).
    """CREATE TABLE IF NOT EXISTS contact_messages (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        email         TEXT NOT NULL,
        topic         TEXT NOT NULL,
        body          TEXT NOT NULL,
        user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ip            TEXT,
        status        TEXT NOT NULL DEFAULT 'new',
        notified_at   TEXT,
        replied_at    TEXT,
        replied_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reply_body    TEXT,
        reply_sent    INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_contact_status ON contact_messages(status)",
    # Cache de geocodage (code postal / adresse -> lat/lng), cf. geocode.py.
    # `found=0` memorise aussi les echecs pour ne pas re-solliciter Nominatim.
    """CREATE TABLE IF NOT EXISTS geocode_cache (
        q          TEXT PRIMARY KEY,
        lat        REAL,
        lng        REAL,
        label      TEXT,
        country    TEXT,
        found      INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]


def run_migrations():
    """Applique les migrations additives manquantes (colonnes + index).
    Sûr et idempotent : a lancer a chaque demarrage.

    CONCURRENCE : gunicorn demarre plusieurs workers qui importent app.py en
    meme temps -> plusieurs run_migrations() simultanes. Sans verrou, deux
    workers voient la colonne absente, le second ALTER echoue en
    « duplicate column name » et le worker meurt (prod 502 le 2026-08-29).
    On prend donc un verrou d'ecriture (BEGIN IMMEDIATE, busy_timeout 30 s)
    AVANT de verifier les colonnes : le premier worker migre, les suivants
    attendent puis ne voient plus rien a faire. Ceinture et bretelles : un
    « duplicate column » residuel est ignore.
    """
    with standalone() as c:
        c.execute("BEGIN IMMEDIATE")
        for table, column, decl in _ADD_COLUMNS:
            if not _table_exists(c, table):
                # Base neuve en cours de creation par un autre worker : le
                # schema complet (colonne incluse) arrive via init_schema.
                log.info("migration: table %s absente, colonne %s ignoree", table, column)
                continue
            if _column_exists(c, table, column):
                continue
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                log.info("migration: %s.%s ajoutee", table, column)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
                log.info("migration: %s.%s deja presente (course)", table, column)
        for stmt in _ADD_TABLES:
            c.execute(stmt)
        if not _table_exists(c, "users"):
            # Schema pas encore la (course d'initialisation) : index et
            # rattrapages seront rejoues au prochain demarrage.
            return
        for stmt in _ADD_INDEXES:
            c.execute(stmt)
        c.execute("CREATE INDEX IF NOT EXISTS idx_cert_review ON pilot_certifications(review_status)")
        for stmt in _POST_SQL:
            c.execute(stmt)
        # commit par standalone() a la sortie du bloc


def _timed(query: str, params: Iterable, action):
    """Helper : execute `action()` et logue si > SLOW_QUERY_MS."""
    t0 = time.monotonic()
    try:
        return action()
    finally:
        ms = (time.monotonic() - t0) * 1000
        if ms > SLOW_QUERY_MS:
            log.warning("slow query %.0fms : %s", ms, query[:120].replace("\n", " "))


def fetchone(query: str, params: Iterable = ()) -> Optional[sqlite3.Row]:
    return _timed(query, params,
                  lambda: get_db().execute(query, tuple(params)).fetchone())


def fetchall(query: str, params: Iterable = ()) -> list:
    return _timed(query, params,
                  lambda: get_db().execute(query, tuple(params)).fetchall())


def execute(query: str, params: Iterable = (), commit: bool = True) -> sqlite3.Cursor:
    """INSERT / UPDATE / DELETE.

    `commit=True` (defaut) commit immediatement — usage classique.
    `commit=False` permet de batcher plusieurs ecritures dans 1 transaction
    (cf. context manager `transaction()` ci-dessous).
    """
    def _do():
        cur = get_db().execute(query, tuple(params))
        if commit:
            get_db().commit()
        return cur
    return _timed(query, params, _do)


@contextmanager
def transaction():
    """Bloc atomique : tout ou rien.

    Utilisation :
        with db.transaction():
            db.execute("INSERT ...", commit=False)
            db.execute("UPDATE ...", commit=False)
        # commit auto a la sortie, rollback si exception

    Equivalent a un BEGIN/COMMIT explicite. Beaucoup plus rapide que 10
    appels execute() qui chacun commit (10 fsyncs vs 1).
    """
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance approximative en km entre deux coordonnees."""
    if None in (lat1, lng1, lat2, lng2):
        return float("inf")
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
