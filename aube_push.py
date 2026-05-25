"""Push de livrables AubePilot vers AubeDrive / AubePhotos.

Server-to-server : on POST le fichier multipart vers l'endpoint
`/api/internal/upload` de chaque service Aube avec le username du
proprietaire (le client) en clair et, si configuree, la cle interne
partagee `AUBE_INTERNAL_API_KEY` en header `X-Aube-Internal-Key`.

Si l'endpoint n'existe pas encore (scaffold initial AubePhotos /
quota AubeDrive), on log un warning et on retourne un dict
`{"ok": False, "reason": "..."}`. Le bouton reste actif dans l'UI
pour permettre la reessai apres deploiement de l'API.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from config import (
    AUBEDRIVE_URL,
    AUBEPHOTOS_URL,
    AUBE_INTERNAL_API_KEY,
    UPLOAD_DIR,
)

log = logging.getLogger("aubepilot.push")

_TIMEOUT = (10, 120)  # connect, read (livrables peuvent etre lourds)


def _post_internal_upload(*, base_url: str, file_path: str,
                          original_filename: str, mime_type: Optional[str],
                          username: str, booking_id: int,
                          extra: Optional[dict] = None) -> dict:
    """POST multipart sur <base_url>/api/internal/upload.

    Retourne {ok: bool, url: str|None, reason: str|None}.
    """
    if not os.path.exists(file_path):
        return {"ok": False, "url": None, "reason": "fichier local introuvable"}

    headers = {}
    if AUBE_INTERNAL_API_KEY:
        headers["X-Aube-Internal-Key"] = AUBE_INTERNAL_API_KEY

    data = {
        "username": username,             # proprietaire cible cote AubeDrive/Photos
        "source": "aubepilot",
        "source_booking_id": str(booking_id),
    }
    if extra:
        data.update(extra)

    try:
        with open(file_path, "rb") as fh:
            files = {
                "file": (original_filename, fh,
                         mime_type or "application/octet-stream"),
            }
            r = requests.post(
                f"{base_url.rstrip('/')}/api/internal/upload",
                data=data, files=files, headers=headers, timeout=_TIMEOUT,
            )
    except requests.RequestException as exc:
        log.warning("push %s failed: %s", base_url, exc)
        return {"ok": False, "url": None, "reason": f"service injoignable : {exc}"}

    if r.status_code == 404:
        return {"ok": False, "url": None,
                "reason": "API non disponible (endpoint manquant)"}
    if r.status_code >= 400:
        return {"ok": False, "url": None,
                "reason": f"refus du service ({r.status_code})"}

    try:
        body = r.json()
    except ValueError:
        body = {}
    return {
        "ok": True,
        "url": body.get("url") or body.get("public_url"),
        "reason": None,
    }


def push_to_aubedrive(deliverable: dict, client_username: str,
                      booking_id: int) -> dict:
    """Envoie un livrable vers AubeDrive (drive.aubeetoilee.com).
    Tous les types de fichiers acceptes."""
    return _post_internal_upload(
        base_url=AUBEDRIVE_URL,
        file_path=os.path.join(UPLOAD_DIR, deliverable["stored_filename"]),
        original_filename=deliverable["original_filename"],
        mime_type=deliverable.get("mime_type"),
        username=client_username,
        booking_id=booking_id,
        extra={"folder": "AubePilot"},
    )


def push_to_aubephotos(deliverable: dict, client_username: str,
                       booking_id: int) -> dict:
    """Envoie un livrable vers AubePhotos (photos.aubeetoilee.com).
    Reservation aux images (image/* ou kind=='image')."""
    if deliverable.get("kind") != "image":
        return {"ok": False, "url": None,
                "reason": "AubePhotos n'accepte que les images"}
    return _post_internal_upload(
        base_url=AUBEPHOTOS_URL,
        file_path=os.path.join(UPLOAD_DIR, deliverable["stored_filename"]),
        original_filename=deliverable["original_filename"],
        mime_type=deliverable.get("mime_type"),
        username=client_username,
        booking_id=booking_id,
        extra={"album": "AubePilot"},
    )
