"""Onboarding mondial AubePilot.

Couvre :
- les normaliseurs auth.normalize_username / auth.normalize_email ;
- l'inscription directe (POST /inscription) d'un pilote etranger -> compte
  local cree (users + pilot_profiles) + session posee (cookie aubepilot_sid) ;
- le garde-fou password != confirm (page re-rendue, aucun user cree) ;
- la bascule client -> pilote via POST /espace/pilote (champ become_pilot).

NB import : on importe `auth` / `db` / `config` UNIQUEMENT dans les
fonctions (pas au niveau module). La fixture conftest `isolated_data_dir`
(autouse) place AUBEPILOT_DATA dans un temp dir AVANT le premier import de
`config`/`db` (declenche par la fixture `_app`). Importer ces modules au
niveau module d'un fichier de test gele `db.DB_PATH` sur la vraie base du
projet pendant la collecte pytest, et pollue la DB de dev. On reste donc en
imports paresseux, comme conftest.py.

DB de test PARTAGEE : on n'asserte QUE les ids/usernames qu'on cree ici, via
des usernames uniques (uuid). Les ecritures passent par les routes (contexte
de requete) ou par make_user (sous app_ctx).
"""
import uuid


def _uniq(base):
    """Username globalement unique (DB de test partagee + persistante)."""
    return f"{base}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Normaliseurs (fonctions pures)
# ---------------------------------------------------------------------------

def test_normalize_username_strips_email_and_lowers():
    import auth
    assert auth.normalize_username("Nicolas@aubemail.com") == "nicolas"
    assert auth.normalize_username("  ALICE ") == "alice"
    # robustesse : entrees vides
    assert auth.normalize_username("") == ""
    assert auth.normalize_username(None) == ""


def test_normalize_email_forces_domain():
    import auth
    from config import EMAIL_DOMAIN
    assert EMAIL_DOMAIN == "aubemail.com"
    assert auth.normalize_email("bob", None) == f"bob@{EMAIL_DOMAIN}"
    assert auth.normalize_email("bob", None).endswith(f"@{EMAIL_DOMAIN}")
    # email fourni : seule la partie locale est gardee, domaine force
    assert auth.normalize_email("ignored", "carol@gmail.com") == f"carol@{EMAIL_DOMAIN}"


# ---------------------------------------------------------------------------
# Inscription mondiale : POST /inscription
# ---------------------------------------------------------------------------

def test_register_foreign_pilot_creates_account_and_session(client, app_ctx):
    """Un pilote japonais s'inscrit en direct (REQUIRE_AUBEMAIL off sur macOS) :
    302 vers le dashboard, cookie de session pose, et les lignes users +
    pilot_profiles existent en base.
    """
    import auth
    import db
    from config import EMAIL_DOMAIN, SESSION_COOKIE_NAME

    username = _uniq("yuki")
    resp = client.post(
        "/inscription",
        data={
            "username": username,
            "password": "demo1234",
            "confirm": "demo1234",
            "full_name": "Yuki Tanaka",
            "role": "pilot",
            "country": "Japon",
            "city": "Tokyo",
        },
    )

    # Redirection + cookie de session pose
    assert resp.status_code == 302
    assert SESSION_COOKIE_NAME in resp.headers.get("Set-Cookie", "")

    # normalize_username -> minuscule ; on relit la ligne reellement creee
    norm = auth.normalize_username(username)
    user = db.fetchone("SELECT * FROM users WHERE username=?", (norm,))
    assert user is not None
    assert user["role"] == "pilot"
    assert user["country"] == "Japon"
    assert user["email"] == f"{norm}@{EMAIL_DOMAIN}"

    # Un role pilote cree forcement une ligne pilot_profiles
    profile = db.fetchone(
        "SELECT * FROM pilot_profiles WHERE user_id=?", (user["id"],)
    )
    assert profile is not None


def test_register_password_mismatch_rerenders_and_creates_nothing(client, app_ctx):
    """password != confirm : la page se re-rend (200) et AUCUN user n'est cree."""
    import auth
    import db
    from config import SESSION_COOKIE_NAME

    username = _uniq("mismatch")
    norm = auth.normalize_username(username)

    resp = client.post(
        "/inscription",
        data={
            "username": username,
            "password": "demo1234",
            "confirm": "PASdupareil",
            "full_name": "Sans Compte",
            "role": "client",
            "country": "France",
            "city": "Lyon",
        },
    )

    assert resp.status_code == 200
    assert SESSION_COOKIE_NAME not in resp.headers.get("Set-Cookie", "")
    # aucun user cree avec cet identifiant
    assert db.fetchone("SELECT 1 FROM users WHERE username=?", (norm,)) is None


# ---------------------------------------------------------------------------
# Bascule client -> pilote : POST /espace/pilote (become_pilot)
# ---------------------------------------------------------------------------

def test_become_pilot_promotes_client_to_both(make_user, auth_client, app_ctx):
    """Un client connecte poste become_pilot=1 -> role 'both' + pilot_profiles."""
    import db

    user = make_user(_uniq("topilot"), role="client")
    assert user["role"] == "client"
    # un client n'a pas de pilot_profiles au depart
    assert db.fetchone(
        "SELECT 1 FROM pilot_profiles WHERE user_id=?", (user["id"],)
    ) is None

    c = auth_client(user["id"])
    resp = c.post("/espace/pilote", data={"become_pilot": "1"})

    # la route redirige vers pilot_edit apres bascule
    assert resp.status_code == 302

    refreshed = db.fetchone("SELECT * FROM users WHERE id=?", (user["id"],))
    assert refreshed["role"] == "both"
    assert db.fetchone(
        "SELECT 1 FROM pilot_profiles WHERE user_id=?", (user["id"],)
    ) is not None


def test_pilot_become_page_renders_for_client(make_user, auth_client):
    """GET /espace/pilote pour un pur client -> page d'invitation a devenir
    pilote (200), sans bascule de role (pas de POST become_pilot).
    """
    user = make_user(_uniq("stayclient"), role="client")
    c = auth_client(user["id"])
    resp = c.get("/espace/pilote")
    assert resp.status_code == 200
