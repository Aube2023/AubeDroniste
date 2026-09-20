"""Ce que la politique de confidentialite promet doit etre fait par le code.

- Suppression de compte : les fichiers televerses (brevet, justificatif
  d'identite, attestation RC, photo de drone, portfolio) partent du disque ;
  les livrables d'une reservation, eux, restent pour l'autre partie.
- Donnees techniques (IP, navigateur) bornees dans le temps : sessions
  expirees purgees, IP du formulaire de contact effacee apres 90 jours.
"""
import os

import db
import services
from config import UPLOAD_DIR


def _touch(rel):
    full = os.path.join(UPLOAD_DIR, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(b"x")
    return full


def test_suppression_compte_efface_les_fichiers_televerses(make_user, app_ctx):
    p = make_user("hyg_files", role="pilot", country="Canada")
    uid = p["id"]
    mine = [_touch(f"u{uid}_cert_1.pdf"), _touch(f"u{uid}_namechange_1.jpg"),
            _touch(f"u{uid}_rc_1.pdf"), _touch(f"u{uid}_drone_1.jpg"),
            _touch(f"avatar_u{uid}_1.png"), _touch(f"portfolio_u{uid}/1_photo.jpg")]
    # Voisins a ne PAS toucher : un autre pilote (prefixe proche) et un livrable.
    other = _touch(f"u{uid}1_cert_1.pdf")
    deliverable = _touch("booking_999999/1_rendu.mp4")
    db.execute("UPDATE pilot_profiles SET insurance_document_path=? WHERE user_id=?",
               (f"uploads/u{uid}_rc_1.pdf", uid))

    assert services.delete_account(uid)["ok"]

    assert all(not os.path.exists(f) for f in mine)
    assert not os.path.isdir(os.path.join(UPLOAD_DIR, f"portfolio_u{uid}"))
    assert os.path.exists(other) and os.path.exists(deliverable)
    row = db.fetchone("SELECT insurance_document_path FROM pilot_profiles WHERE user_id=?", (uid,))
    assert row["insurance_document_path"] is None
    os.remove(other)
    os.remove(deliverable)


def test_purge_sessions_expirees_et_ip_de_contact(make_user, app_ctx):
    u = make_user("hyg_sess", role="client", country="Canada")
    db.execute("INSERT INTO sessions (sid, user_id, expires_at, user_agent, ip) VALUES "
               "('hyg_old', ?, datetime('now', '-3 days'), 'UA', '203.0.113.9')", (u["id"],))
    db.execute("INSERT INTO sessions (sid, user_id, expires_at, user_agent, ip) VALUES "
               "('hyg_new', ?, datetime('now', '+20 days'), 'UA', '203.0.113.9')", (u["id"],))
    db.execute("INSERT INTO contact_messages (name, email, topic, body, ip, created_at) VALUES "
               "('A', 'a@x.test', 'q', 'vieux', '198.51.100.1', datetime('now', '-100 days'))")
    db.execute("INSERT INTO contact_messages (name, email, topic, body, ip, created_at) VALUES "
               "('B', 'b@x.test', 'q', 'recent', '198.51.100.2', datetime('now', '-10 days'))")

    done = services.purge_technical_data()

    assert done["sessions"] >= 1 and done["contact_ips"] >= 1
    assert db.fetchone("SELECT 1 FROM sessions WHERE sid='hyg_old'") is None
    assert db.fetchone("SELECT 1 FROM sessions WHERE sid='hyg_new'") is not None
    assert db.fetchone("SELECT ip FROM contact_messages WHERE body='vieux'")["ip"] is None
    assert db.fetchone("SELECT ip FROM contact_messages WHERE body='recent'")["ip"] == "198.51.100.2"


def test_page_publique_de_suppression_de_compte(client):
    # Google Play exige un lien, accessible sans connexion, qui explique
    # comment supprimer son compte ; bilingue FR/EN, relie a la politique.
    r = client.get("/supprimer-mon-compte")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Supprimer mon compte AubePilot" in html and "Delete my AubePilot account" in html
    assert "support@aubemail.com" in html and "/espace/parametres" in html
    assert 'id="suppression"' in client.get("/confidentialite").data.decode()
