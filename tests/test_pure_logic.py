"""Tests de logique pure (ROI rapide) sur services.py.

Cible des fonctions deterministes a faible cout :
- mask_full_name        : anonymisation prenom + initiale
- compute_cancellation_fee : penalite d'annulation selon le preavis
- has_funded_relation   : un viewer voit-il la fiche pilote en clair
- is_party_in_quebec / contract_french_only : detection Quebec (Loi 101/96)

DB partagee : on n'asserte que sur les ids qu'on cree soi-meme.
"""
from datetime import datetime, timedelta, timezone

from config import LATE_CANCELLATION_HOURS, LATE_CANCELLATION_FEE_PCT
import services


# ---------------------------------------------------------------------------
# mask_full_name
# ---------------------------------------------------------------------------

def test_mask_full_name_prenom_et_initiale():
    assert services.mask_full_name("Amine Benali") == "Amine B."
    assert services.mask_full_name("Sophie Tremblay") == "Sophie T."


def test_mask_full_name_garde_premier_nom_de_famille():
    # Avec plusieurs noms de famille, on ne garde que l'initiale du premier.
    assert services.mask_full_name("Marie Dubois Pellerin") == "Marie D."


def test_mask_full_name_mononyme_inchange():
    assert services.mask_full_name("Cher") == "Cher"


def test_mask_full_name_vide_ou_none():
    assert services.mask_full_name("") == ""
    assert services.mask_full_name(None) == ""
    # Espaces seuls -> traite comme vide.
    assert services.mask_full_name("   ") == ""


def test_mask_full_name_initiale_majuscule():
    # L'initiale du nom est forcee en majuscule.
    assert services.mask_full_name("amine benali") == "amine B."


# ---------------------------------------------------------------------------
# compute_cancellation_fee
# ---------------------------------------------------------------------------

def _future_iso(hours: float) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def test_cancellation_fee_preavis_suffisant_pas_de_penalite():
    # Mission tres loin dans le futur -> preavis OK, aucune penalite.
    booking = {
        "agreed_price": 1000,
        "scheduled_at": _future_iso(LATE_CANCELLATION_HOURS + 48),
    }
    res = services.compute_cancellation_fee(booking)
    assert res["is_late"] is False
    assert res["fee_pct"] == 0.0
    assert res["fee_amount"] == 0.0
    assert res["refund_amount"] == 1000.0
    assert res["preavis_h"] == LATE_CANCELLATION_HOURS


def test_cancellation_fee_tardif_applique_le_pourcentage():
    # Mission dans 1h (< preavis) -> annulation tardive, penalite appliquee.
    price = 1000
    booking = {
        "agreed_price": price,
        "scheduled_at": _future_iso(1),
    }
    res = services.compute_cancellation_fee(booking)
    expected_fee = round(price * LATE_CANCELLATION_FEE_PCT / 100.0, 2)
    assert res["is_late"] is True
    assert res["fee_pct"] == LATE_CANCELLATION_FEE_PCT
    assert res["fee_amount"] == expected_fee
    assert res["refund_amount"] == round(price - expected_fee, 2)
    # fee + remboursement = prix complet.
    assert round(res["fee_amount"] + res["refund_amount"], 2) == float(price)


def test_cancellation_fee_sans_date_pas_late():
    # Pas de date exploitable -> hours_until None -> jamais tardif.
    res = services.compute_cancellation_fee({"agreed_price": 500})
    assert res["hours_until"] is None
    assert res["is_late"] is False
    assert res["fee_amount"] == 0.0
    assert res["refund_amount"] == 500.0


# ---------------------------------------------------------------------------
# has_funded_relation  (necessite app_ctx : lit la DB via flask.g)
# ---------------------------------------------------------------------------

def test_has_funded_relation_self_toujours_true(app_ctx, pilot_user):
    # Un pilote voit toujours sa propre fiche en clair.
    pid = pilot_user["id"]
    assert services.has_funded_relation(pid, pid) is True


def test_has_funded_relation_client_sans_booking_false(app_ctx, make_user, pilot_user):
    # Un client sans aucun booking finance ne voit pas la fiche en clair.
    stranger = make_user("stranger_no_booking", role="client")
    assert services.has_funded_relation(stranger["id"], pilot_user["id"]) is False


def test_has_funded_relation_ids_manquants_false(app_ctx):
    assert services.has_funded_relation(None, 1) is False
    assert services.has_funded_relation(1, None) is False
    assert services.has_funded_relation(0, 0) is False


# ---------------------------------------------------------------------------
# is_party_in_quebec / contract_french_only
# ---------------------------------------------------------------------------

def test_is_party_in_quebec_par_region():
    assert services.is_party_in_quebec(None, None, "QC") is True
    assert services.is_party_in_quebec("Canada", "Montreal", None) is True
    # Region prioritaire, casse/accents toleres.
    assert services.is_party_in_quebec(None, None, "Québec") is True


def test_is_party_in_quebec_hors_quebec():
    assert services.is_party_in_quebec("France", "Paris", None) is False
    # Canada mais ville non quebecoise.
    assert services.is_party_in_quebec("Canada", "Toronto", None) is False
    assert services.is_party_in_quebec(None, None, None) is False


def test_contract_french_only_au_moins_une_partie_quebec():
    france = {"country": "France", "city": "Paris"}
    quebec = {"country": "Canada", "city": "Montreal"}
    assert services.contract_french_only([france, quebec]) is True
    assert services.contract_french_only([france, france]) is False
    # Liste vide ou entrees None -> pas de contrainte francophone.
    assert services.contract_french_only([]) is False
    assert services.contract_french_only([None]) is False
