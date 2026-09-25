"""AubeBeacon dans AubePilot : télémétrie en direct des drones équipés d'une balise.

    beacon.register(app)   enregistre les deux blueprints, l'exemption CSRF de
                           l'ingestion et le contexte de gabarit.

Modules : schema (DDL), devices (identité, jeton, association), telemetry
(validation + ingestion), flights (sessions et statistiques), status
(ONLINE / DEGRADED / OFFLINE), realtime (hub WebSocket, tickets), aubelink
(client AubeLink : drone AubeLink associé à chaque balise), api (routes
JSON), views (pages de l'espace pilote). Documentation d'ensemble :
AubeBeacon/README.md et docs/api/ ; AubeLink/docs/api.md pour AubeLink.
"""
VERSION = "0.1.0"


def register(app) -> None:
    # Imports ici : `beacon.schema` doit rester importable sans Flask (db.py au
    # demarrage, scripts, tests du depot AubeBeacon).
    from flask import g

    import security

    from . import access, api, aubelink, devices, realtime, views

    app.register_blueprint(api.bp)
    app.register_blueprint(views.bp)
    # L'ingestion s'authentifie par jeton porteur, sans session : pas de CSRF.
    security.CSRF_EXEMPT_ROUTES.add("beacon_api.telemetry_ingest")

    @app.context_processor
    def _beacon_context():
        # `beacon_enabled` conditionne les liens (tableau de bord, Mes drones) :
        # un compte non autorisé ne voit jamais le nom de la fonction.
        user = getattr(g, "user", None)
        enabled = access.allowed(user)
        has_beacons = False
        if enabled and user.get("role") in ("pilot", "both"):
            try:
                has_beacons = devices.count_for_owner(user["id"]) > 0
            except Exception:      # table absente pendant une migration : rien à afficher
                has_beacons = False
        # `aubelink_enabled` : panneau, sélecteur et liens AubeLink (fonction
        # configurée ET compte autorisé à voir AubeBeacon).
        return {"beacon_enabled": enabled, "beacon_has_devices": has_beacons,
                "beacon_realtime_enabled": realtime.enabled(), "beacon_version": VERSION,
                "aubelink_enabled": enabled and aubelink.enabled()}
