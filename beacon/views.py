"""Pages AubeBeacon de l'espace pilote.

    GET  /espace/pilote/aubebeacon                    balises, création, carte en direct, vols récents
    POST /espace/pilote/aubebeacon/creer
    POST /espace/pilote/aubebeacon/<id>/associer      drone de la flotte
    POST /espace/pilote/aubebeacon/<id>/dissocier
    POST /espace/pilote/aubebeacon/<id>/desactiver | /activer
    POST /espace/pilote/aubebeacon/<id>/jeton         régénère le jeton (l'ancien meurt aussitôt)
    POST /espace/pilote/aubebeacon/<id>/supprimer     dissocie aussi la balise de son drone AubeLink
    POST /espace/pilote/aubebeacon/<id>/aubelink      associer à un drone AubeLink (champ aubelink_drone_id)
    POST /espace/pilote/aubebeacon/<id>/aubelink/retirer  dissocier du drone AubeLink
    GET  /espace/pilote/aubebeacon/vols/<id>          trace et statistiques d'un vol

Les deux associations sont indépendantes : `associer` lie la balise à un
drone du catalogue AubePilot (`pilot_drones`), `aubelink` à un drone du
réseau AubeLink, qui tient seul cette association (cf. beacon/aubelink.py).
AubeLink est appelé au nom du propriétaire de la balise, même dans la vue
administrateur, et jamais au nom d'un compte supprimé. Les actions qui
appellent AubeLink sont limitées en débit (`security.rate_limit`) : elles
ignorent le cache et la pause du client, et le budget de la clé
d'intégration est partagé par tous les pilotes.

TODO : après une association ou une dissociation faite ailleurs (autre
onglet, interface AubeLink), le panneau suit au sondage de 15 s, mais les
boutons de la balise restent ceux du rendu serveur ; la note du panneau
invite alors à recharger la page (beacon-live.js, `aubelink.reload`).

Le jeton complet n'apparaît qu'une fois, sur la page qui suit la création ou
la rotation : il transite par la session signée (`beacon_reveal`), puis est
retiré au premier affichage. Un administrateur voit toutes les balises
(`?all=1`) ; un pilote ne voit et ne modifie que les siennes. Accès réservé
(cf. beacon/access.py) : 404 pour les comptes non autorisés.
"""
import logging

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

import auth
import i18n
import security
import services

from . import access as _access
from . import api as _api
from . import aubelink as _aubelink
from . import devices as _devices
from . import flights as _flights
from . import realtime as _realtime
from . import status as _status

log = logging.getLogger("aubepilot.beacon.views")

bp = Blueprint("beacon_views", __name__, url_prefix="/espace/pilote/aubebeacon")

REVEAL_KEY = "beacon_reveal"
# Actions qui appellent AubeLink sans cache (lecture fraîche, PUT, DELETE) :
# par IP et par route, inactif en test (cf. security.rate_limit).
AUBELINK_ACTION_LIMIT = {"per_minute": 10, "per_hour": 120}
DELETE_LIMIT = {"per_minute": 20, "per_hour": 200}
# Attente maximale d'AubeLink au rendu de la page (s) ; le reste arrive par
# le sondage de la route JSON. Un appel ne part que si ce qui reste du budget
# couvre son délai entier (AUBELINK_TIMEOUT_MS, 2,5 s par défaut), cf.
# aubelink.panels.
AUBELINK_PAGE_BUDGET_S = 3.0


def _pilot_or_403() -> dict:
    user = g.user
    if not _access.allowed(user):
        abort(404)              # la fonction n'existe pas pour ce compte
    if user["role"] not in ("pilot", "both") and not user.get("is_admin"):
        abort(403)
    return user


def _owner_scope(user: dict):
    return None if (user.get("is_admin") and request.args.get("all") == "1") else user["id"]


def _ip() -> str:
    return security.client_ip()


def _reveal(device: dict, token: str) -> None:
    session[REVEAL_KEY] = {"id": device["id"], "uid": device["device_uid"], "token": token}


def _back():
    return redirect(url_for("beacon_views.devices_page", all=request.args.get("all")))


def _t(key: str, **kwargs) -> str:
    return i18n.t(key, getattr(g, "lang", i18n.DEFAULT), **kwargs)


def _flash_aubelink_error(exc: "_aubelink.AubeLinkError") -> None:
    key, kwargs = _aubelink.flash_key(exc)
    flash(_t(key, **kwargs), "error")


def _acting(device: dict):
    """Délégué AubeLink : le propriétaire de la balise, jamais l'administrateur ;
    None si son compte est supprimé (aucun appel en son nom)."""
    return _aubelink.device_acting(device)


@bp.route("")
@auth.login_required
def devices_page():
    user = _pilot_or_403()
    scope = _owner_scope(user)
    rows = _devices.list_devices(scope)
    devices = []
    for d in rows:
        view = _api.device_view(d)
        view["events"] = _devices.list_events(d["id"], limit=8)
        devices.append(view)
    reveal = session.pop(REVEAL_KEY, None)
    aubelink = _aubelink.panels(rows, budget_s=AUBELINK_PAGE_BUDGET_S) if _aubelink.enabled() else None
    return render_template(
        "beacon_devices.html",
        devices=devices,
        aubelink=aubelink,
        aubelink_public_url=_aubelink.public_url(),
        drones=services.list_drones(user["id"]),
        flights=[_api.flight_view(f) | {"device_uid": f["device_uid"], "device_label": f.get("device_label")}
                 for f in _flights.list_flights(scope, limit=20)],
        reveal=reveal,
        realtime=_api.realtime_info(),
        thresholds=_status.thresholds(),
        show_all=scope is None,
    )


@bp.route("/creer", methods=["POST"])
@auth.login_required
def device_create():
    user = _pilot_or_403()
    try:
        device, token = _devices.create_device(
            user["id"], label=request.form.get("label", ""),
            drone_id=_to_int(request.form.get("drone_id")),
            device_uid=(request.form.get("device_uid") or "").strip() or None,
            user_id=user["id"], ip=_ip(),
        )
    except _devices.DeviceError as exc:
        flash(str(exc), "error")
        return _back()
    _reveal(device, token)
    security.audit(user["id"], "beacon_create", device["device_uid"])
    return _back()


def _to_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _device_or_404(device_id: int, user: dict) -> dict:
    scope = None if user.get("is_admin") else user["id"]
    device = _devices.get_device(device_id, scope)
    if not device:
        abort(404)
    return device


@bp.route("/<int:device_id>/associer", methods=["POST"])
@auth.login_required
def device_attach(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    try:
        _devices.attach_drone(device_id, device["owner_user_id"], _to_int(request.form.get("drone_id")) or 0,
                              user_id=user["id"], ip=_ip())
    except _devices.DeviceError as exc:
        flash(str(exc), "error")
        return _back()
    _realtime.publish_device(device_id, {"drone_id": _to_int(request.form.get("drone_id"))})
    flash("Balise associée au drone.", "success")
    return _back()


@bp.route("/<int:device_id>/dissocier", methods=["POST"])
@auth.login_required
def device_detach(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    _devices.detach_drone(device_id, device["owner_user_id"], user_id=user["id"], ip=_ip())
    _realtime.publish_device(device_id, {"drone_id": None})
    flash("Balise dissociée.", "info")
    return _back()


@bp.route("/<int:device_id>/desactiver", methods=["POST"])
@auth.login_required
def device_disable(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    _devices.set_enabled(device_id, device["owner_user_id"], False, user_id=user["id"], ip=_ip())
    security.audit(user["id"], "beacon_disable", device["device_uid"])
    _realtime.publish_device(device_id, {"enabled": False})
    flash("Balise désactivée : sa télémétrie est refusée.", "info")
    return _back()


@bp.route("/<int:device_id>/activer", methods=["POST"])
@auth.login_required
def device_enable(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    _devices.set_enabled(device_id, device["owner_user_id"], True, user_id=user["id"], ip=_ip())
    _realtime.publish_device(device_id, {"enabled": True})
    flash("Balise réactivée.", "success")
    return _back()


@bp.route("/<int:device_id>/jeton", methods=["POST"])
@auth.login_required
def device_rotate(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    token = _devices.rotate_token(device_id, device["owner_user_id"], user_id=user["id"], ip=_ip())
    security.audit(user["id"], "beacon_rotate_token", device["device_uid"])
    _reveal(device, token)
    return _back()


@bp.route("/<int:device_id>/renommer", methods=["POST"])
@auth.login_required
def device_rename(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    _devices.rename(device_id, device["owner_user_id"], request.form.get("label", ""))
    return _back()


@bp.route("/<int:device_id>/supprimer", methods=["POST"])
@auth.login_required
@security.rate_limit(**DELETE_LIMIT)
def device_delete(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    # AubeLink d'abord : son association disparaît avec la balise. Une panne,
    # ou un propriétaire au compte supprimé (aucun appel en son nom),
    # n'empêche pas la suppression ; le numéro, retiré, ne sera pas réattribué.
    orphan = False
    if _aubelink.enabled():
        acting = _acting(device)
        if acting is None:
            orphan = True
            log.warning("aubelink.unlink_skipped device=%s reason=owner_deleted", device["device_uid"])
        else:
            try:
                _aubelink.unlink_beacon(acting, device["device_uid"])
            except _aubelink.AubeLinkError as exc:
                orphan = True
                log.warning("aubelink.unlink_failed device=%s code=%s status=%s",
                            device["device_uid"], exc.code, exc.status)
    _devices.delete_device(device_id, device["owner_user_id"])
    security.audit(user["id"], "beacon_delete", device["device_uid"])
    if orphan:
        flash(_t("aubelink.delete_orphan"), "error")
    else:
        flash("Balise supprimée, avec ses vols et ses points.", "info")
    return _back()


@bp.route("/<int:device_id>/aubelink", methods=["POST"])
@auth.login_required
@security.rate_limit(**AUBELINK_ACTION_LIMIT)
def device_aubelink_link(device_id):
    """Associe la balise à un drone AubeLink libre du propriétaire (lecture
    fraîche, puis PUT chez AubeLink)."""
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    if not _aubelink.enabled():
        abort(404)
    acting = _acting(device)
    if acting is None:
        flash(_t("aubelink.owner_deleted"), "error")
        return _back()
    drone_id = (request.form.get("aubelink_drone_id") or "").strip().upper()
    if not _aubelink.DRONE_ID_RE.match(drone_id):
        flash(_t("aubelink.err.invalid_drone"), "error")
        return _back()
    uid = device["device_uid"]
    try:
        # Le PUT d'AubeLink délogerait en silence la balise que porte déjà le
        # drone : refus d'après une lecture fraîche, jamais d'après le sélecteur.
        carried = _aubelink.beacon_on_drone(acting, drone_id)
        if carried and carried != uid:
            flash(_t("aubelink.err.drone_busy", drone=drone_id, uid=carried), "error")
            return _back()
        _aubelink.link(acting, drone_id, uid)
    except _aubelink.AubeLinkError as exc:
        _flash_aubelink_error(exc)
        return _back()
    security.audit(user["id"], "beacon_aubelink_link", f"{uid}->{drone_id}")
    flash(_t("aubelink.paired", drone=drone_id), "success")
    return _back()


@bp.route("/<int:device_id>/aubelink/retirer", methods=["POST"])
@auth.login_required
@security.rate_limit(**AUBELINK_ACTION_LIMIT)
def device_aubelink_unlink(device_id):
    """Dissocie la balise de son drone AubeLink, retrouvé par une lecture fraîche."""
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    if not _aubelink.enabled():
        abort(404)
    acting = _acting(device)
    if acting is None:
        flash(_t("aubelink.owner_deleted"), "error")
        return _back()
    uid = device["device_uid"]
    try:
        drone_id = _aubelink.unlink_beacon(acting, uid)
    except _aubelink.AubeLinkError as exc:
        _flash_aubelink_error(exc)
        return _back()
    if not drone_id:
        flash(_t("aubelink.err.not_linked"), "info")
        return _back()
    security.audit(user["id"], "beacon_aubelink_unlink", f"{uid}->{drone_id}")
    flash(_t("aubelink.unpaired", drone=drone_id), "info")
    return _back()


@bp.route("/vols/<int:flight_id>")
@auth.login_required
def flight_page(flight_id):
    user = _pilot_or_403()
    flight = _flights.get_flight(flight_id, None if user.get("is_admin") else user["id"])
    if not flight:
        abort(404)
    track = _flights.track(flight_id)
    return render_template(
        "beacon_flight.html",
        flight=_api.flight_view(flight) | {
            "device_uid": flight["device_uid"], "device_label": flight.get("device_label"),
            "drone_brand": flight.get("drone_brand"), "drone_model": flight.get("drone_model"),
            "owner_name": flight.get("owner_name"),
        },
        track=track,
    )
