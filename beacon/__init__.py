"""AubeBeacon dans AubePilot : télémétrie en direct des drones équipés d'une balise.

    beacon.register(app)   enregistre les deux blueprints, l'exemption CSRF de
                           l'ingestion et le contexte de gabarit.

Modules : schema (DDL), devices (identité, jeton, association), telemetry
(validation + ingestion), flights (sessions et statistiques), status
(ONLINE / DEGRADED / OFFLINE), realtime (hub WebSocket, tickets), api
(routes JSON), views (pages de l'espace pilote). Documentation d'ensemble :
AubeBeacon/README.md et docs/api/.
"""
VERSION = "0.1.0"


def register(app) -> None:
    # Imports ici : `beacon.schema` doit rester importable sans Flask (db.py au
    # demarrage, scripts, tests du depot AubeBeacon).
    from flask import g

    import security

    from . import api, devices, realtime, views

    app.register_blueprint(api.bp)
    app.register_blueprint(views.bp)
    # L'ingestion s'authentifie par jeton porteur, sans session : pas de CSRF.
    security.CSRF_EXEMPT_ROUTES.add("beacon_api.telemetry_ingest")

    @app.context_processor
    def _beacon_context():
        user = getattr(g, "user", None)
        has_beacons = False
        if user and user.get("role") in ("pilot", "both"):
            try:
                has_beacons = devices.count_for_owner(user["id"]) > 0
            except Exception:      # table absente pendant une migration : rien à afficher
                has_beacons = False
        return {"beacon_has_devices": has_beacons, "beacon_realtime_enabled": realtime.enabled(),
                "beacon_version": VERSION}
