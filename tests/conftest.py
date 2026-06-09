"""Pytest configuration : DB temporaire isolee + fixtures/factories partagees.

- `isolated_data_dir` (session, autouse) place DATA_DIR dans un temp dir AVANT
  l'import de l'app.
- `app` / `app_ctx` / `client` : l'app Flask de test, un app_context, un client.
- `make_user` : factory de comptes (sous app_context, sans email de bienvenue).
- `auth_client` : connecte le test_client comme un user donne (cookie session).
- seeds : `client_user`, `pilot_user`, `open_mission`, `pending_bid`,
  `funded_booking` pour les tests metier (devis / escrow).

DB de test PARTAGEE entre les tests d'un meme process : on n'asserte donc QUE
l'appartenance des ids crees, jamais des comptes exacts.
"""
import itertools
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ISOLATION DB ROBUSTE — POSEE AVANT TOUT IMPORT DE MODULE PROJET.
# config.py fige DB_PATH/DATA_DIR a l'import (lecture de AUBEPILOT_DATA). Un
# fichier de test qui importe db/services/config/app au niveau module le fait
# pendant la COLLECTION pytest ; il faut donc que le temp dir soit deja en
# place ICI — conftest.py est importe AVANT tout module de test — sinon la
# suite ecrirait dans data/aubepilot.db (la base dev). setdefault respecte un
# AUBEPILOT_DATA fourni de l'exterieur (CI / orchestrateur).
os.environ.setdefault("AUBEPILOT_DATA", tempfile.mkdtemp(prefix="aubepilot-test-"))

# DB de test partagee dans un meme process : compteur global garantissant
# l'unicite des usernames/emails crees (sinon UNIQUE users.email).
_uid_seq = itertools.count(1)


@pytest.fixture(scope="session", autouse=True)
def isolated_data_dir():
    """Le temp dir DATA est deja pose au chargement de ce module (ci-dessus).
    On expose juste le chemin ; pas de cleanup (relecture des eml dumps)."""
    yield os.environ["AUBEPILOT_DATA"]


# ---------------------------------------------------------------------------
# App / contexte / client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _app(isolated_data_dir):
    """App Flask configuree pour les tests (DB isolee, schema + migrations)."""
    import auth
    from app import app as flask_app, bootstrap_db
    import db
    # Isolation du fichier de mots de passe dev : auth._DEV_HASH_FILE pointe par
    # defaut sur la racine du projet (.dev_passwords). On le redirige vers le
    # temp dir pour ne pas polluer le vrai fichier quand les tests creent des
    # comptes (create_user -> set_dev_password sur macOS / Linux sans PAM).
    auth._DEV_HASH_FILE = os.path.join(isolated_data_dir, ".dev_passwords")
    if not os.path.exists(db.DB_PATH):
        bootstrap_db()
    else:
        db.run_migrations()
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        # SERVER_NAME : permet a url_for() de construire les liens dans les
        # templates d'email declenches par la couche service (sinon warning).
        SERVER_NAME="localhost.localdomain",
    )
    return flask_app


@pytest.fixture()
def app(_app):
    return _app


@pytest.fixture()
def app_ctx(_app):
    """Pousse un app_context (db.get_db depend de flask.g)."""
    with _app.app_context():
        yield


@pytest.fixture()
def client(_app):
    with _app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

@pytest.fixture()
def make_user(app_ctx):
    """Factory de users (sous app_context). Retourne le dict user + _password.

    Usage : u = make_user("alice", role="both", country="Japon", lat=35.6, lng=139.7)
    """
    import auth
    import db

    def _make(username, *, role="client", password="demo1234",
              full_name=None, country="France", city="Paris",
              lat=None, lng=None):
        # suffixe unique : la DB de test est partagee entre les tests
        username = f"{username}{next(_uid_seq)}"
        uid = auth.create_user(
            username=username, password=password,
            full_name=full_name or username.title(),
            role=role, country=country, city=city, lat=lat, lng=lng,
            send_welcome_email=False,
        )
        row = dict(db.fetchone("SELECT * FROM users WHERE id=?", (uid,)))
        row["_password"] = password
        return row

    return _make


@pytest.fixture()
def auth_client(client, _app):
    """Connecte le test_client comme `user_id`. Retourne le client authentifie."""
    import auth

    def _login(user_id):
        with _app.app_context():
            token = auth.create_session(user_id, "pytest", "127.0.0.1")
        # Werkzeug 3.x : set_cookie(key, value)
        client.set_cookie("aubepilot_sid", token, domain="localhost.localdomain")
        return client

    return _login


# ---------------------------------------------------------------------------
# Seeds metier
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_user(make_user):
    return make_user("seed_client", role="client")


@pytest.fixture()
def pilot_user(make_user):
    """Pilote 'both' avec un compte Stripe (fake) charges_enabled -> payable."""
    import services
    u = make_user("seed_pilot", role="both", country="France",
                  city="Paris", lat=48.8566, lng=2.3522)
    services.set_pilot_stripe_account(u["id"], f"acct_fake_{u['id']}",
                                      charges_enabled=True, payouts_enabled=True)
    return u


@pytest.fixture()
def open_mission(app_ctx, client_user):
    """Mission ouverte publiee par client_user. Retourne (mission_id, client)."""
    import services
    mid = services.create_mission(
        client_user["id"], title="Tournage immobilier",
        description="Survol et photos d'une residence", mission_type="immobilier",
        country="France", city="Paris", lat=48.8566, lng=2.3522,
        budget_min=500, budget_max=1500,
    )
    return mid


@pytest.fixture()
def pending_bid(app_ctx, open_mission, pilot_user):
    """Devis 'pending' depose par pilot_user sur open_mission. Retourne bid_id."""
    import services
    return services.place_bid(
        open_mission, pilot_user["id"], price=1000,
        description="Prestation drone 4K complete avec livrables et montage",
        deliverables="Photos HD + video 4K",
    )


@pytest.fixture()
def funded_booking(app_ctx, open_mission, pending_bid, client_user, pilot_user):
    """Booking finance (escrow) issu de pending_bid. Retourne booking_id."""
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    services.mark_booking_funded(booking_id, payment_intent_id="pi_fake_seed")
    return booking_id
