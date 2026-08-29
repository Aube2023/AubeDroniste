"""Vérification des profils document par document (back-office admin).

- verifier -> brevet verified + badge profil users.is_verified
- refuser (motif) -> rejected, motif visible cote pilote
- revoquer / supprimer / expiration -> badge recalcule
- apercu du justificatif dans une iframe same-origin (en-tetes)
Conventions conftest : imports dans les tests.
"""
import os
from datetime import date, timedelta

import pytest


@pytest.fixture()
def admin_user(make_user, app_ctx):
    import db
    u = make_user("cert_admin", role="client")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
    return u


def _add_cert(pilot_id, *, doc=True, expires_at="", authority="Transport Canada",
              title="Operations avancees (RPAS)"):
    import services
    from config import UPLOAD_DIR
    path = ""
    if doc:
        name = f"u{pilot_id}_cert_test_{authority[:2].lower()}_{title[:3].lower()}.pdf"
        with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
            f.write(b"%PDF-1.4\n% justificatif de test\n")
        path = f"uploads/{name}"
    return services.add_certification(pilot_id, authority=authority, title=title,
                                      reference="TC-42", expires_at=expires_at,
                                      document_path=path)


def _user_verified(uid):
    import db
    return int(db.fetchone("SELECT is_verified FROM users WHERE id=?", (uid,))["is_verified"])


def test_verify_sets_cert_and_profile_badge(app_ctx, make_user, admin_user):
    import services
    pilot = make_user("cert_p1", role="pilot")
    cid = _add_cert(pilot["id"])
    assert _user_verified(pilot["id"]) == 0
    assert any(c["id"] == cid for c in services.list_certifications_for_review("pending"))
    res = services.review_certification(cid, admin_user["id"], "verified", "vu registre TC")
    assert res and res["profile_verified"] is True
    c = services.get_certification(cid)
    assert c["is_verified"] == 1 and c["review_status"] == "verified"
    assert c["reviewed_by"] == admin_user["id"] and c["review_note"] == "vu registre TC"
    assert _user_verified(pilot["id"]) == 1
    assert not any(x["id"] == cid for x in services.list_certifications_for_review("pending"))
    assert any(x["id"] == cid for x in services.list_certifications_for_review("verified"))
    # visible dans l'annuaire avec le badge + filtre "brevet verifie"
    hit = [p for p in services.search_pilots(only_verified=True, limit=500) if p["id"] == pilot["id"]]
    assert hit and hit[0]["is_verified"] == 1 and hit[0]["verified_authorities"] == ["Transport Canada"]


def test_reject_with_note_and_pilot_sees_it(client, auth_client, make_user, admin_user):
    import services
    with client.application.app_context():
        pilot = make_user("cert_p2", role="pilot")
        cid = _add_cert(pilot["id"])
    c = auth_client(admin_user["id"])
    r = c.post(f"/admin/certifications/{cid}/refuser", data={"note": "Document illisible"})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        cert = services.get_certification(cid)
        assert cert["review_status"] == "rejected" and cert["is_verified"] == 0
        assert _user_verified(pilot["id"]) == 0
        assert services.count_certifications_pending() >= 0
    html = auth_client(pilot["id"]).get("/espace/pilote").data.decode()
    assert "justificatif refusé" in html and "Document illisible" in html


def test_reject_requires_note(client, auth_client, make_user, admin_user):
    import services
    with client.application.app_context():
        pilot = make_user("cert_p3", role="pilot")
        cid = _add_cert(pilot["id"])
    c = auth_client(admin_user["id"])
    c.post(f"/admin/certifications/{cid}/refuser", data={"note": ""})
    with client.application.app_context():
        assert services.get_certification(cid)["review_status"] == "pending"


def test_revoke_and_delete_recompute_badge(app_ctx, make_user, admin_user):
    import services
    pilot = make_user("cert_p4", role="pilot")
    c1 = _add_cert(pilot["id"], title="A")
    c2 = _add_cert(pilot["id"], title="B", authority="DGAC")
    services.review_certification(c1, admin_user["id"], "verified")
    services.review_certification(c2, admin_user["id"], "verified")
    assert _user_verified(pilot["id"]) == 1
    services.review_certification(c1, admin_user["id"], "pending", "doute")
    assert _user_verified(pilot["id"]) == 1          # il reste B
    assert services.get_certification(c1)["review_status"] == "pending"
    assert services.delete_certification(c2, pilot["id"]) is True
    assert _user_verified(pilot["id"]) == 0          # plus rien de verifie


def test_expired_cert_never_activates_badge(app_ctx, make_user, admin_user):
    import services
    pilot = make_user("cert_p5", role="pilot")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    cid = _add_cert(pilot["id"], expires_at=yesterday)
    res = services.review_certification(cid, admin_user["id"], "verified")
    assert res["profile_verified"] is False
    assert services.get_certification(cid)["is_expired"] is True
    assert _user_verified(pilot["id"]) == 0
    # brevet valide + cron : recalcul coherent
    cid2 = _add_cert(pilot["id"], title="C", expires_at=(date.today() + timedelta(days=30)).isoformat())
    services.review_certification(cid2, admin_user["id"], "verified")
    assert _user_verified(pilot["id"]) == 1
    assert services.refresh_all_user_verified() == 0   # deja a jour


def test_set_certification_verified_compat(app_ctx, make_user):
    import services
    pilot = make_user("cert_p6", role="pilot")
    cid = _add_cert(pilot["id"])
    assert services.set_certification_verified(cid, True) is True
    assert _user_verified(pilot["id"]) == 1
    assert services.set_certification_verified(cid, False) is True
    assert _user_verified(pilot["id"]) == 0
    assert services.set_certification_verified(999999, True) is False


def test_admin_page_lists_pending_with_preview_and_counts(client, auth_client, make_user, admin_user):
    with client.application.app_context():
        pilot = make_user("cert_p7", role="pilot", country="Canada", city="Québec")
        cid = _add_cert(pilot["id"])
        uid = pilot["id"]
    c = auth_client(admin_user["id"])
    html = c.get("/admin/certifications").data.decode()
    assert f'id="cert-{cid}"' in html and f"@{pilot['username']}" in html
    assert f'<iframe src="/pilotes/{uid}/brevets/{cid}/document' in html      # apercu PDF integre
    assert "À vérifier ·" in html and "Vérifier ce brevet" in html and "Refuser" in html
    assert "Le nom sur le document est" in html and "Contrôles obligatoires" in html
    # sans les 3 controles cochés : refus (le brevet reste en attente)
    r = c.post(f"/admin/certifications/{cid}/verifier", data={"note": "ok", "back": "pending"})
    assert r.status_code in (302, 303)
    assert f'id="cert-{cid}"' in c.get("/admin/certifications").data.decode()
    r = c.post(f"/admin/certifications/{cid}/verifier",
               data={"note": "ok", "back": "pending", "check_name": "1",
                     "check_number": "1", "check_valid": "1"})
    assert r.status_code in (302, 303)
    html = c.get("/admin/certifications?status=verified").data.decode()
    assert f'id="cert-{cid}"' in html and "Révoquer la vérification" in html
    assert "nom ✓ numéro ✓ validité ✓" in html
    dash = c.get("/espace").data.decode()
    assert "Brevets à vérifier" in dash


def test_document_preview_headers_allow_same_origin_framing(client, auth_client, make_user, admin_user):
    with client.application.app_context():
        pilot = make_user("cert_p8", role="pilot")
        cid = _add_cert(pilot["id"])
        uid = pilot["id"]
    r = auth_client(admin_user["id"]).get(f"/pilotes/{uid}/brevets/{cid}/document")
    assert r.status_code == 200
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("Content-Disposition") == "inline"
    # la page admin autorise les iframes same-origin
    csp = auth_client(admin_user["id"]).get("/admin/certifications").headers.get("Content-Security-Policy", "")
    assert "frame-src 'self'" in csp


def test_review_routes_forbidden_for_non_admin(client, auth_client, make_user):
    with client.application.app_context():
        pilot = make_user("cert_p9", role="pilot")
        cid = _add_cert(pilot["id"])
    c = auth_client(pilot["id"])
    assert c.post(f"/admin/certifications/{cid}/verifier",
                  data={"check_name": "1", "check_number": "1", "check_valid": "1"}).status_code == 403
    assert c.post(f"/admin/certifications/{cid}/refuser", data={"note": "x"}).status_code == 403
    assert c.get("/admin/certifications").status_code == 403
