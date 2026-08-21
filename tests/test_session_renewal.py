"""Renouvellement glissant des sessions (auth._maybe_extend_session).

Le probleme d'origine : la session (cookie + ligne en base) expirait 30 jours
apres le LOGIN, meme pour un utilisateur actif tous les jours — qui devait
donc se reconnecter. Desormais toute requete authentifiee dont l'echeance est
entamee d'au moins un jour repousse l'expiration en base ET re-pose le cookie
avec un max_age complet.

NB conftest : jamais d'import db/app/auth au niveau module (la DB temp doit
etre posee avant l'import des modules projet).
"""
from datetime import datetime, timedelta, timezone


def _session_row(user_id):
    import db
    return db.fetchone(
        "SELECT sid, expires_at FROM sessions WHERE user_id=? ORDER BY rowid DESC",
        (user_id,),
    )


def _set_expiry(sid, *, days_from_now):
    import db
    expires = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    db.execute(
        "UPDATE sessions SET expires_at=? WHERE sid=?",
        (expires.isoformat(timespec="seconds"), sid),
    )


def _sid_cookies(resp):
    return [c for c in resp.headers.getlist("Set-Cookie")
            if c.startswith("aubepilot_sid=")]


def test_aging_session_is_extended_and_cookie_reset(app_ctx, make_user, auth_client):
    user = make_user("renew")
    client = auth_client(user["id"])
    row = _session_row(user["id"])
    _set_expiry(row["sid"], days_from_now=10)

    resp = client.get("/espace")
    assert resp.status_code == 200

    # cookie re-pose avec un max_age complet (30 jours)
    cookies = _sid_cookies(resp)
    assert cookies, "le cookie de session doit etre re-pose"
    assert "Max-Age=2592000" in cookies[0]

    # echeance repoussee en base a ~30 jours
    new_expires = datetime.fromisoformat(_session_row(user["id"])["expires_at"])
    remaining = new_expires - datetime.now(timezone.utc)
    assert remaining > timedelta(days=29)


def test_fresh_session_is_not_rewritten(app_ctx, make_user, auth_client):
    """Session posee a l'instant (30 j pleins) : aucune ecriture inutile."""
    user = make_user("fresh")
    client = auth_client(user["id"])
    before = _session_row(user["id"])["expires_at"]

    resp = client.get("/espace")
    assert resp.status_code == 200
    assert not _sid_cookies(resp)
    assert _session_row(user["id"])["expires_at"] == before


def test_expired_session_is_not_resurrected(app_ctx, make_user, auth_client):
    """Une session deja expiree reste morte : redirection vers /connexion."""
    user = make_user("dead")
    client = auth_client(user["id"])
    row = _session_row(user["id"])
    _set_expiry(row["sid"], days_from_now=-1)

    resp = client.get("/espace")
    assert resp.status_code == 302
    assert "/connexion" in resp.headers["Location"]
    assert not _sid_cookies(resp)
