"""Controle d'acces / IDOR sur les ressources sensibles.

On verifie que :
- la fiche reservation + le devis PDF sont reserves aux deux parties du
  booking (403 pour un tiers authentifie) ;
- la messagerie pre-booking (`can_message`) n'autorise qu'un pilote ayant
  soumissionne <-> le client de la mission ;
- le filtre anti-desintermediation (`message_passes_filter`) bloque les
  coordonnees externes tant que la mission n'est pas financee ;
- les routes /admin/* renvoient 403 a un utilisateur non admin authentifie.

DB partagee : on n'asserte que l'etat/l'appartenance des ids qu'on cree.
Le backend fait foi (signatures lues dans app.py / services.py / auth.py).
"""
# Isolation DB AVANT tout import projet : config.DATA_DIR/DB_PATH sont
# resolus a l'import (config.py lit AUBEPILOT_DATA au chargement). Si on
# laisse la valeur par defaut, ce fichier ecrirait dans le vrai data/ et
# entrerait en collision (UNIQUE users.email) avec les seeds des autres
# process / des runs precedents. On force donc un repertoire temporaire
# DEDIE a ce fichier de test, avant `import services`.
import os as _os
import tempfile as _tempfile

_os.environ.setdefault(
    "AUBEPILOT_DATA",
    _tempfile.mkdtemp(prefix="aubepilot-test-access-"),
)

import services


# ---------------------------------------------------------------------------
# IDOR sur la reservation : un tiers ne voit ni la fiche ni le devis PDF
# ---------------------------------------------------------------------------

def test_booking_detail_forbidden_for_outsider(
    auth_client, make_user, funded_booking
):
    """Un user qui n'est ni le client ni le pilote du booking -> 403."""
    outsider = make_user("outsider", role="both")
    c = auth_client(outsider["id"])
    resp = c.get(f"/reservations/{funded_booking}")
    assert resp.status_code == 403


def test_booking_devis_pdf_forbidden_for_outsider(
    auth_client, make_user, funded_booking
):
    """Le devis PDF d'un booking n'est servi qu'aux deux parties."""
    outsider = make_user("outsider_pdf", role="client")
    c = auth_client(outsider["id"])
    resp = c.get(f"/reservations/{funded_booking}/devis.pdf")
    assert resp.status_code == 403


def test_booking_detail_allowed_for_client_party(
    auth_client, funded_booking, client_user
):
    """Sanity : le client du booking, lui, accede bien a sa fiche (pas 403)."""
    c = auth_client(client_user["id"])
    resp = c.get(f"/reservations/{funded_booking}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Messagerie pre-booking : can_message
# ---------------------------------------------------------------------------

def test_can_message_true_between_client_and_bidding_pilot(
    app_ctx, open_mission, pending_bid, client_user, pilot_user
):
    """Le client de la mission et le pilote ayant depose un devis peuvent
    s'ecrire (peu importe le sens)."""
    assert services.can_message(open_mission, client_user["id"], pilot_user["id"]) is True
    # symetrique : sens inverse aussi autorise
    assert services.can_message(open_mission, pilot_user["id"], client_user["id"]) is True


def test_can_message_false_for_outsider_without_bid(
    app_ctx, open_mission, pending_bid, client_user, make_user
):
    """Un tiers sans devis sur la mission ne peut pas ouvrir de fil avec le
    client."""
    outsider = make_user("nobid_pilot", role="both")
    assert services.can_message(open_mission, client_user["id"], outsider["id"]) is False


# ---------------------------------------------------------------------------
# Filtre anti-desintermediation : message_passes_filter(body, booking_funded)
# ---------------------------------------------------------------------------

def test_message_filter_blocks_phone_before_funding():
    """Un numero de telephone est bloque tant que la mission n'est pas financee."""
    ok, reason = services.message_passes_filter(
        "appelle moi au 0612345678", False
    )
    assert ok is False
    assert reason  # raison non vide expliquant le blocage


def test_message_filter_allows_plain_text_before_funding():
    """Un message neutre passe meme avant financement."""
    ok, reason = services.message_passes_filter("bonjour", False)
    assert ok is True
    assert reason is None


def test_message_filter_allows_everything_once_funded():
    """Une fois la mission financee, le filtre n'entrave plus les coordonnees."""
    ok, reason = services.message_passes_filter(
        "appelle moi au 0612345678", True
    )
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# Admin : routes /admin/* interdites a un user non admin (auth.admin_required)
# ---------------------------------------------------------------------------

def test_admin_certifications_forbidden_for_normal_user(auth_client, make_user):
    """Un utilisateur authentifie mais non admin -> 403 sur /admin/certifications."""
    user = make_user("not_admin", role="both")
    assert not user.get("is_admin")
    c = auth_client(user["id"])
    resp = c.get("/admin/certifications")
    assert resp.status_code == 403


def test_admin_certifications_forbidden_for_anonymous(client):
    """Anonyme sur une route admin : pas un 200 (admin_required renvoie 403)."""
    resp = client.get("/admin/certifications")
    assert resp.status_code == 403
