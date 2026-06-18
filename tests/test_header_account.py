"""Header : indicateur de connexion (pastille « mon compte »).

Régression UX : une fois connecté, l'utilisateur ne voyait ni son nom ni un
accès clair à son compte — impossible de savoir si on était connecté ou pas.
Le header affiche désormais une pastille (avatar + nom) liée à /espace quand
on est connecté, et les liens Connexion/Rejoindre sinon.
"""

# IMPORTANT : pas d'import db/services/app au niveau module (cf conftest).


def test_header_anonymous_shows_login_not_account(client):
    """Anonyme : pas de pastille compte, mais un accès Connexion."""
    html = client.get("/").get_data(as_text=True)
    assert "account-chip" not in html
    assert "Connexion" in html


def test_header_logged_in_shows_name_and_account_link(make_user, auth_client):
    """Connecté : la pastille montre le nom et pointe vers l'espace compte."""
    u = make_user("headeruser", role="client", full_name="Bob Header")
    c = auth_client(u["id"])
    html = c.get("/").get_data(as_text=True)
    assert "account-chip" in html
    assert "Bob Header" in html
    assert "/espace" in html
    # avatar unifié : la pastille est câblée sur l'avatar AubeMail (source de vérité)
    assert 'id="account-avatar"' in html
    assert "/aubemail/api/avatar/" in html
