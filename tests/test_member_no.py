"""Numero de membre public : 1, 2, 3... dans l'ordre d'inscription, quel que
soit l'id SQLite (qui ne repart pas a 1 apres une purge)."""
import db


def _reset(conn):
    conn.execute("DELETE FROM users")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='users'")
    conn.commit()


def test_member_no_is_sequential_from_one(app_ctx):
    conn = db.get_db()
    _reset(conn)
    # Ids volontairement troues : le numero de membre, lui, suit 1, 2, 3.
    conn.execute("INSERT INTO users (id, username, email, full_name) VALUES (41, 'nicolas', 'n@x', 'N')")
    conn.execute("INSERT INTO users (id, username, email, full_name) VALUES (57, 'b', 'b@x', 'B')")
    conn.execute("INSERT INTO users (id, username, email, full_name) VALUES (90, 'c', 'c@x', 'C')")
    conn.commit()
    rows = conn.execute("SELECT id, member_no FROM users ORDER BY id").fetchall()
    assert [(r["id"], r["member_no"]) for r in rows] == [(41, 1), (57, 2), (90, 3)]


def test_backfill_numbers_existing_accounts_in_id_order(app_ctx):
    conn = db.get_db()
    _reset(conn)
    conn.execute("INSERT INTO users (id, username, email, full_name) VALUES (12, 'x', 'x@x', 'X')")
    conn.execute("INSERT INTO users (id, username, email, full_name) VALUES (3, 'w', 'w@x', 'W')")
    conn.execute("UPDATE users SET member_no = NULL")   # base d'avant la migration
    conn.commit()
    db.run_migrations()
    rows = conn.execute("SELECT id, member_no FROM users ORDER BY member_no").fetchall()
    assert [(r["id"], r["member_no"]) for r in rows] == [(3, 1), (12, 2)]
    # Rejouer la migration ne change rien.
    db.run_migrations()
    assert conn.execute("SELECT member_no FROM users WHERE id=12").fetchone()["member_no"] == 2
