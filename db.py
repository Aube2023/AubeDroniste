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
    # PRAGMAs : a executer a chaque ouverture (sauf journal_mode qui est persistant)
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA cache_size=-64000;
        PRAGMA temp_store=MEMORY;
        PRAGMA mmap_size=134217728;
        PRAGMA busy_timeout=30000;
    """)
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


# Migrations additives idempotentes : ajoutent les colonnes manquantes sur une
# base existante (CREATE TABLE IF NOT EXISTS ne modifie pas une table deja la).
_ADD_COLUMNS = [
    ("pilot_profiles", "business_name", "TEXT"),
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


def run_migrations():
    """Applique les migrations additives manquantes (colonnes + index).
    Sûr et idempotent : a lancer a chaque demarrage."""
    with standalone() as c:
        for table, column, decl in _ADD_COLUMNS:
            if not _column_exists(c, table, column):
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                log.info("migration: %s.%s ajoutee", table, column)
        for stmt in _ADD_INDEXES:
            c.execute(stmt)


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
