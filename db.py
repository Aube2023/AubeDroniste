"""Couche d'acces SQLite pour AubeDroniste.

Volontairement minimaliste : on garde les requetes pres du code metier,
sans ORM. Une connexion par requete Flask via `g`.
"""
import math
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterable, Optional

from flask import g

from config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
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


def fetchone(query: str, params: Iterable = ()) -> Optional[sqlite3.Row]:
    cur = get_db().execute(query, tuple(params))
    return cur.fetchone()


def fetchall(query: str, params: Iterable = ()) -> list:
    cur = get_db().execute(query, tuple(params))
    return cur.fetchall()


def execute(query: str, params: Iterable = ()) -> sqlite3.Cursor:
    cur = get_db().execute(query, tuple(params))
    get_db().commit()
    return cur


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
