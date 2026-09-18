"""Numero public du profil pilote : 1, 2, 3... dans l'ordre de creation des
profils. Un simple compte client n'a pas de numero ; l'id SQLite (troue apres
une purge) n'apparait jamais."""
import db


def _reset(conn):
    conn.execute("DELETE FROM pilot_profiles")
    conn.execute("DELETE FROM users")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='users'")
    conn.commit()


def _user(conn, uid, name, pilot):
    conn.execute("INSERT INTO users (id, username, email, full_name, role) VALUES (?, ?, ?, ?, ?)",
                 (uid, name, f"{name}@x", name.upper(), "pilot" if pilot else "client"))
    if pilot:
        conn.execute("INSERT INTO pilot_profiles (user_id) VALUES (?)", (uid,))


def test_pilot_no_counts_only_pilot_profiles(app_ctx):
    conn = db.get_db()
    _reset(conn)
    _user(conn, 41, "nicolas", True)
    _user(conn, 50, "client_only", False)     # pas de profil : pas de numero
    _user(conn, 57, "b", True)
    _user(conn, 90, "c", True)
    conn.commit()
    rows = conn.execute("SELECT user_id, pilot_no FROM pilot_profiles ORDER BY user_id").fetchall()
    assert [(r["user_id"], r["pilot_no"]) for r in rows] == [(41, 1), (57, 2), (90, 3)]


def test_backfill_numbers_existing_profiles_in_id_order(app_ctx):
    conn = db.get_db()
    _reset(conn)
    _user(conn, 12, "x", True)
    _user(conn, 3, "w", True)
    conn.execute("UPDATE pilot_profiles SET pilot_no = NULL")   # base d'avant la migration
    conn.commit()
    db.run_migrations()
    rows = conn.execute("SELECT user_id, pilot_no FROM pilot_profiles ORDER BY pilot_no").fetchall()
    assert [(r["user_id"], r["pilot_no"]) for r in rows] == [(3, 1), (12, 2)]
    db.run_migrations()   # rejouer ne change rien
    assert conn.execute("SELECT pilot_no FROM pilot_profiles WHERE user_id=12").fetchone()["pilot_no"] == 2
