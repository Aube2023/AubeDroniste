"""Cycle de vie des devis (bids) + route POST /missions/<id>/enchere.

Couvre la couche services (place_bid / reject_bid / withdraw_bid /
list_bid_revisions) et le garde de la route `bid_place` (gating role,
prix <= 0, description < 30 caracteres).

DB de test PARTAGEE : on n'asserte QUE l'etat/appartenance des ids qu'on
cree soi-meme. Les ecritures services passent sous `app_ctx` (porte par les
seeds open_mission / pilot_user, ou ajoute explicitement).
"""
import pytest

# IMPORTANT : aucun import de `db`/`services`/`app` au niveau module. Ces
# modules importent `config`, qui fige DB_PATH a partir de AUBEPILOT_DATA des
# l'import. Or la fixture session `isolated_data_dir` (conftest) ne pose cette
# variable qu'au moment du *run*, apres la *collection*. Importer ici lierait
# donc la DB de prod reelle. On importe TOUJOURS dans les fonctions/fixtures.


# Description valide (>= 30 caracteres) reutilisee dans les POST de route.
VALID_DESC = "Prestation drone 4K avec reperage, vol et livrables montes."


def _bid_row(mission_id, pilot_user_id):
    """Ligne brute du devis (mission, pilote) ou None s'il n'existe pas."""
    import db
    row = db.fetchone(
        "SELECT id, status, revision_no FROM bids "
        "WHERE mission_id=? AND pilot_user_id=?",
        (mission_id, pilot_user_id),
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Couche services
# ---------------------------------------------------------------------------

def test_place_bid_first_revision_is_one_and_pending(open_mission, pilot_user):
    """Premier devis : revision_no=1, statut 'pending'."""
    import services
    bid_id = services.place_bid(
        open_mission, pilot_user["id"], price=900,
        description="Survol immobilier, photos HD et video 4K du bien.",
        deliverables="Photos + video",
    )
    row = _bid_row(open_mission, pilot_user["id"])
    assert row is not None
    assert row["id"] == bid_id
    assert row["status"] == "pending"
    assert row["revision_no"] == 1


def test_place_bid_on_own_mission_raises(app_ctx, client_user, make_user):
    """Anti auto-soumission : le client ne peut pas soumissionner sur sa
    propre mission -> ValueError. Aucun devis ne doit etre cree."""
    import services
    mid = services.create_mission(
        client_user["id"], title="Inspection toiture",
        description="Controle de toiture par drone", mission_type="inspection",
        country="France", city="Paris", lat=48.8566, lng=2.3522,
        budget_min=300, budget_max=900,
    )
    with pytest.raises(ValueError):
        services.place_bid(
            mid, client_user["id"], price=500,
            description="Je propose une inspection complete avec rapport.",
        )
    assert _bid_row(mid, client_user["id"]) is None


def test_reject_bid_marks_rejected_mission_stays_open(
    open_mission, pending_bid, client_user
):
    """reject_bid -> devis 'rejected', la mission reste 'open'."""
    import db
    import services
    ok = services.reject_bid(open_mission, pending_bid, client_user["id"],
                             reason="Tarif trop eleve")
    assert ok is True

    bid = db.fetchone("SELECT status FROM bids WHERE id=?", (pending_bid,))
    assert bid["status"] == "rejected"

    mission = db.fetchone("SELECT status FROM missions WHERE id=?", (open_mission,))
    assert mission["status"] == "open"


def test_reject_then_resubmit_bumps_revision_and_logs_history(
    open_mission, pending_bid, pilot_user, client_user
):
    """Apres refus, une re-soumission incremente revision_no a 2 et archive
    l'ancienne version dans bid_revisions (exactement 1 ligne)."""
    import services
    assert services.reject_bid(open_mission, pending_bid, client_user["id"]) is True

    new_id = services.place_bid(
        open_mission, pilot_user["id"], price=1100,
        description="Revision : ajout d'un second axe de vol et drone secours.",
        deliverables="Photos + video 4K + plan de vol",
    )
    # Meme devis (UPDATE en place), pas une nouvelle ligne.
    assert new_id == pending_bid

    row = _bid_row(open_mission, pilot_user["id"])
    assert row["status"] == "pending"
    assert row["revision_no"] == 2

    revisions = services.list_bid_revisions(pending_bid)
    assert len(revisions) == 1
    # La version archivee est l'ancienne (revision_no=1, statut refuse).
    assert revisions[0]["revision_no"] == 1
    assert revisions[0]["status"] == "rejected"


def test_withdraw_bid_only_affects_pending(open_mission, pending_bid, pilot_user):
    """withdraw_bid ne retire que les devis 'pending'."""
    import db
    import services
    # 1) Sur un devis pending : passe a 'withdrawn'.
    services.withdraw_bid(pending_bid, pilot_user["id"])
    bid = db.fetchone("SELECT status FROM bids WHERE id=?", (pending_bid,))
    assert bid["status"] == "withdrawn"

    # 2) Re-tenter un retrait sur un devis NON pending ne le modifie pas.
    services.withdraw_bid(pending_bid, pilot_user["id"])
    bid = db.fetchone("SELECT status FROM bids WHERE id=?", (pending_bid,))
    assert bid["status"] == "withdrawn"


def test_withdraw_bid_wrong_pilot_noop(open_mission, pending_bid, make_user):
    """Un autre pilote ne peut pas retirer le devis d'autrui (garde
    pilot_user_id) : le devis reste 'pending'."""
    import db
    import services
    other = make_user("intrus", role="both")
    services.withdraw_bid(pending_bid, other["id"])
    bid = db.fetchone("SELECT status FROM bids WHERE id=?", (pending_bid,))
    assert bid["status"] == "pending"


# ---------------------------------------------------------------------------
# Route POST /missions/<id>/enchere  (endpoint bid_place)
# ---------------------------------------------------------------------------

def test_route_client_role_cannot_bid(
    app, open_mission, client_user, auth_client
):
    """Un user role 'client' qui POST sur /enchere -> redirige (302) et
    AUCUN devis n'est cree (seuls les pilotes soumissionnent)."""
    c = auth_client(client_user["id"])
    resp = c.post(
        f"/missions/{open_mission}/enchere",
        data={"price": "800", "description": VALID_DESC},
    )
    assert resp.status_code == 302
    with app.app_context():
        assert _bid_row(open_mission, client_user["id"]) is None


def test_route_pilot_price_zero_rejected(
    app, make_user, auth_client
):
    """Pilote avec price<=0 -> redirige sans creer de devis."""
    import services
    # Mission d'un autre client (sinon garde anti auto-soumission).
    owner = make_user("owner_pz", role="client")
    pilot = make_user("pilot_pz", role="both")
    with app.app_context():
        mid = services.create_mission(
            owner["id"], title="Cartographie agricole",
            description="Releve par drone d'une parcelle", mission_type="agriculture",
            country="France", city="Paris", lat=48.8566, lng=2.3522,
            budget_min=400, budget_max=1000,
        )

    c = auth_client(pilot["id"])
    resp = c.post(
        f"/missions/{mid}/enchere",
        data={"price": "0", "description": VALID_DESC},
    )
    assert resp.status_code == 302
    with app.app_context():
        assert _bid_row(mid, pilot["id"]) is None


def test_route_pilot_description_too_short_rejected(
    app, make_user, auth_client
):
    """Pilote avec description < 30 caracteres -> redirige sans creer de devis."""
    import services
    owner = make_user("owner_short", role="client")
    pilot = make_user("pilot_short", role="both")
    with app.app_context():
        mid = services.create_mission(
            owner["id"], title="Photo evenement",
            description="Couverture aerienne d'un evenement", mission_type="evenementiel",
            country="France", city="Paris", lat=48.8566, lng=2.3522,
            budget_min=200, budget_max=800,
        )

    c = auth_client(pilot["id"])
    resp = c.post(
        f"/missions/{mid}/enchere",
        data={"price": "750", "description": "Trop court"},  # < 30 car.
    )
    assert resp.status_code == 302
    with app.app_context():
        assert _bid_row(mid, pilot["id"]) is None


def test_route_pilot_valid_creates_bid(app, make_user, auth_client):
    """Pilote avec price>0 et description>=30 -> devis cree (pending)."""
    import services
    owner = make_user("owner_ok", role="client")
    pilot = make_user("pilot_ok", role="both")
    with app.app_context():
        mid = services.create_mission(
            owner["id"], title="Suivi de chantier",
            description="Vues aeriennes mensuelles d'un chantier", mission_type="btp",
            country="France", city="Paris", lat=48.8566, lng=2.3522,
            budget_min=500, budget_max=2000,
        )

    c = auth_client(pilot["id"])
    resp = c.post(
        f"/missions/{mid}/enchere",
        data={"price": "1200", "description": VALID_DESC,
              "deliverables": "Photos + video"},
    )
    assert resp.status_code == 302
    with app.app_context():
        row = _bid_row(mid, pilot["id"])
        assert row is not None
        assert row["status"] == "pending"
        assert row["revision_no"] == 1
