"""Renouvellement glissant des sessions (auth._maybe_extend_session).

Le probleme d'origine : la session (cookie + ligne en base) expirait 30 jours
apres le LOGIN, meme pour un utilisateur actif tous les jours — qui devait
donc se reconnecter. Desormais toute requete authentifiee dont l'echeance est
entamee d'au moins un jour repousse l'expiration en base ET re-pose le cookie
avec un max_age complet (un an depuis la « connexion automatique » du
2026-09-20 ; sans la case, cookie de navigateur et 24 h glissantes).

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

    # cookie re-pose avec un max_age complet (un an)
    cookies = _sid_cookies(resp)
    assert cookies, "le cookie de session doit etre re-pose"
    assert "Max-Age=31536000" in cookies[0]

    # echeance repoussee en base a ~un an
    new_expires = datetime.fromisoformat(_session_row(user["id"])["expires_at"])
    remaining = new_expires - datetime.now(timezone.utc)
    assert remaining > timedelta(days=364)


def test_fresh_session_is_not_rewritten(app_ctx, make_user, auth_client):
    """Session posee a l'instant (un an plein) : aucune ecriture inutile."""
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


# ---------------------------------------------------------------------------
# Connexion automatique (case du formulaire de connexion)
# ---------------------------------------------------------------------------

def _login(app, username, password, remember):
    c = app.test_client()
    data = {"username": username, "password": password}
    if remember:
        data["remember"] = "1"
    return c, c.post("/connexion", data=data)


def test_case_cochee_session_un_an(app, app_ctx, make_user):
    user = make_user("auto_on")
    c, resp = _login(app, user["username"], user["_password"], remember=True)
    assert resp.status_code == 302
    cookie = _sid_cookies(resp)[0]
    assert "Max-Age=31536000" in cookie
    row = _session_row(user["id"])
    assert row["expires_at"] and datetime.fromisoformat(row["expires_at"]) - datetime.now(timezone.utc) > timedelta(days=364)
    import db
    assert db.fetchone("SELECT persistent FROM sessions WHERE sid=?", (row["sid"],))["persistent"] == 1
    assert c.get("/espace").status_code == 200


def test_case_decochee_cookie_de_navigateur_et_24h(app, app_ctx, make_user):
    user = make_user("auto_off")
    c, resp = _login(app, user["username"], user["_password"], remember=False)
    assert resp.status_code == 302
    cookie = _sid_cookies(resp)[0]
    assert "Max-Age" not in cookie and "Expires" not in cookie      # tombe avec le navigateur
    row = _session_row(user["id"])
    remaining = datetime.fromisoformat(row["expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(hours=23) < remaining <= timedelta(hours=24)
    import db
    assert db.fetchone("SELECT persistent FROM sessions WHERE sid=?", (row["sid"],))["persistent"] == 0
    # Renouvellement glissant par heure, toujours sans duree sur le cookie.
    _set_expiry(row["sid"], days_from_now=0.5)
    resp = c.get("/espace")
    assert resp.status_code == 200
    cookies = _sid_cookies(resp)
    assert cookies and "Max-Age" not in cookies[0]
    remaining = datetime.fromisoformat(_session_row(user["id"])["expires_at"]) - datetime.now(timezone.utc)
    assert remaining > timedelta(hours=23)


def test_formulaire_de_connexion_propose_la_case(app):
    html = app.test_client().get("/connexion").data.decode()
    assert 'name="remember" value="1" checked' in html and "Connexion automatique" in html
