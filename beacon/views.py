"""Pages AubeBeacon de l'espace pilote.

    GET  /espace/pilote/aubebeacon                    balises, création, carte en direct, vols récents
    POST /espace/pilote/aubebeacon/creer
    POST /espace/pilote/aubebeacon/<id>/associer      drone de la flotte
    POST /espace/pilote/aubebeacon/<id>/dissocier
    POST /espace/pilote/aubebeacon/<id>/desactiver | /activer
    POST /espace/pilote/aubebeacon/<id>/jeton         régénère le jeton (l'ancien meurt aussitôt)
    POST /espace/pilote/aubebeacon/<id>/supprimer
    GET  /espace/pilote/aubebeacon/vols/<id>          trace et statistiques d'un vol

Le jeton complet n'apparaît qu'une fois, sur la page qui suit la création ou
la rotation : il transite par la session signée (`beacon_reveal`), puis est
retiré au premier affichage. Un administrateur voit toutes les balises
(`?all=1`) ; un pilote ne voit et ne modifie que les siennes. Accès réservé
(cf. beacon/access.py) : 404 pour les comptes non autorisés.
"""
import logging

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

import auth
import security
import services

from . import access as _access
from . import api as _api
from . import devices as _devices
from . import flights as _flights
from . import realtime as _realtime
from . import status as _status

log = logging.getLogger("aubepilot.beacon.views")

bp = Blueprint("beacon_views", __name__, url_prefix="/espace/pilote/aubebeacon")

REVEAL_KEY = "beacon_reveal"


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
    return render_template(
        "beacon_devices.html",
        devices=devices,
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
def device_delete(device_id):
    user = _pilot_or_403()
    device = _device_or_404(device_id, user)
    _devices.delete_device(device_id, device["owner_user_id"])
    security.audit(user["id"], "beacon_delete", device["device_uid"])
    flash("Balise supprimée, avec ses vols et ses points.", "info")
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
