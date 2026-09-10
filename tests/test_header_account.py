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
    assert "js-self-avatar" in html
    assert "/aubemail/api/avatar/" in html


# ---------------------------------------------------------------------------
# Tableau de bord : lisibilité des tuiles
# ---------------------------------------------------------------------------

def test_tableau_de_bord_sans_anglais_ni_valeur_brute(auth_client, make_user, app_ctx):
    import services
    u = make_user("cockpit", role="both", country="Canada", city="Montréal")
    services.upsert_pilot_profile(u["id"], is_available=1)
    html = auth_client(u["id"]).get("/espace").data.decode()
    assert "Bookings" not in html                  # etait en dur dans une page FR
    assert "rôle both" not in html                 # valeur brute de la base
    assert "client et pilote" in html              # role lisible
    assert "⚙" not in html


def test_tuiles_un_seul_chiffre_par_tuile(auth_client, make_user, app_ctx):
    """Chaque tuile portait un compte dans son titre ET un autre en gros,
    souvent contradictoires (« Bookings 1 » au-dessus d'un gros « 0 »)."""
    import services
    u = make_user("cockpit2", role="both")
    services.upsert_pilot_profile(u["id"], is_available=1)
    html = auth_client(u["id"]).get("/espace").data.decode()
    assert "Soumissions 0" not in html and "Missions 0" not in html
    assert "au total" in html                      # les totaux passent en pied
    # la tuile profil montre la completude, pas un tiret muet
    assert 'class="num-big">' in html
    assert "de fiche complète" in html
