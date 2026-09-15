"""Page admin « État de Stripe » : lecture seule, jamais de clé affichée."""


def test_diagnostic_en_mode_simule():
    import payments
    d = payments.diagnostics()
    assert d["mode"] in ("FAKE", "TEST", "LIVE", "DISABLED")
    assert d["webhook_url"].endswith("/stripe/webhook")
    assert isinstance(d["errors"], list) and isinstance(d["webhooks"], list)


def test_page_reservee_aux_admins_sans_secret(client, auth_client, make_user, app_ctx):
    import db
    from config import STRIPE_SECRET_KEY
    u = make_user("admin_stripe", role="both")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
    html = auth_client(u["id"]).get("/admin/stripe").data.decode()
    assert "Où en est Stripe." in html and "Activer Connect" in html
    assert 'name="robots" content="noindex' in html
    assert "sk_" not in html and "whsec_" not in html
    if STRIPE_SECRET_KEY:
        assert STRIPE_SECRET_KEY not in html
    v = make_user("pas_admin_stripe", role="client")
    assert auth_client(v["id"]).get("/admin/stripe").status_code == 403
    assert client.application.test_client().get("/admin/stripe").status_code in (302, 403)
