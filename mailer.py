"""Mailer AubePilot — SMTP en prod, dump local en dev.

Configuration via variables d'environnement :
  SMTP_HOST       (vide = mode dev, dump dans data/mail/*.eml)
  SMTP_PORT       587
  SMTP_USER       (optionnel selon serveur)
  SMTP_PASSWORD   (optionnel)
  SMTP_TLS        "1" (defaut) | "0"
  SMTP_FROM       no-reply@aubeetoilee.com
  SMTP_FROM_NAME  "AubePilot"

Les emails transactionnels sont **bilingues** (FR puis EN dans le meme
message) afin que le destinataire voie sa langue quel que soit son
parametrage cote serveur.

Ne casse jamais le flux metier en cas d'echec : on log et on retourne
False, l'inscription / la mission / l'enchere se poursuit normalement.
"""
import logging
import os
import re
import smtplib
import threading
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from flask import render_template
from jinja2 import TemplateNotFound

import i18n
from config import MAIL_DUMP_DIR, SITE_URL

log = logging.getLogger("aubepilot.mailer")


def _smtp_config() -> dict:
    return {
        "host":      os.environ.get("SMTP_HOST", "").strip(),
        "port":      int(os.environ.get("SMTP_PORT", "587")),
        "user":      os.environ.get("SMTP_USER", "").strip(),
        "password":  os.environ.get("SMTP_PASSWORD", ""),
        "from_email":os.environ.get("SMTP_FROM", "no-reply@aubeetoilee.com"),
        "from_name": os.environ.get("SMTP_FROM_NAME", "AubePilot"),
        "tls":       os.environ.get("SMTP_TLS", "1") == "1",
    }


def _strip_html(html: str) -> str:
    """Fallback texte si .txt est absent : strip naif des tags."""
    text = re.sub(r"<style.*?</style>", "", html, flags=re.S | re.I)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_message(*, to: str, subject: str, template: str,
                   context: dict, cfg: dict) -> EmailMessage:
    ctx = dict(context)
    ctx.setdefault("site_url", SITE_URL)
    ctx.setdefault("year", datetime.now().year)
    # double rendu : on rend explicitement chaque langue pour le contenu bilingue
    ctx.setdefault("lang_blocks", [
        {"lang": "fr", "T": lambda k, **kw: i18n.t(k, lang="fr", **kw)},
        {"lang": "en", "T": lambda k, **kw: i18n.t(k, lang="en", **kw)},
    ])

    html_body = render_template(f"emails/{template}.html", **ctx)
    try:
        text_body = render_template(f"emails/{template}.txt", **ctx)
    except TemplateNotFound:
        text_body = _strip_html(html_body)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="aubeetoilee.com")
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _dump_local(msg: EmailMessage, to: str) -> bool:
    try:
        os.makedirs(MAIL_DUMP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe_to = re.sub(r"[^a-zA-Z0-9._-]", "_", to)[:60]
        path = os.path.join(MAIL_DUMP_DIR, f"{ts}-{safe_to}.eml")
        with open(path, "wb") as f:
            f.write(bytes(msg))
        log.info("[dev] email dumped: %s", path)
        return True
    except OSError as exc:
        log.error("[dev] dump failed: %s", exc)
        return False


def _send_via_smtp(msg: EmailMessage, cfg: dict) -> bool:
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.ehlo()
            if cfg["tls"]:
                s.starttls(); s.ehlo()
            if cfg["user"]:
                s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.error("SMTP failed (%s:%s) -> %s", cfg["host"], cfg["port"], exc)
        return False


def send(*, to: str, subject: str, template: str, context: dict,
         async_: bool = True) -> bool:
    """Envoie un email (HTML + texte). Retourne True si l'envoi (ou le dump)
    a reussi, False sinon. Ne leve jamais.

    En mode async (defaut), l'envoi part dans un thread daemon : la requete
    HTTP n'attend pas le SMTP.
    """
    if not to or "@" not in to:
        log.warning("send: destinataire invalide '%s'", to)
        return False

    cfg = _smtp_config()
    try:
        msg = _build_message(
            to=to, subject=subject, template=template,
            context=context, cfg=cfg,
        )
    except Exception as exc:  # rendu template defectueux
        log.error("template '%s' invalide: %s", template, exc)
        return False

    def _do():
        if not cfg["host"]:
            _dump_local(msg, to)
        else:
            _send_via_smtp(msg, cfg)

    if async_:
        # On capture le contexte d'app avant de quitter le request handler
        # (render_template a deja eu lieu, plus besoin du contexte ici).
        threading.Thread(target=_do, daemon=True).start()
        return True
    _do()
    return True


# ---------------------------------------------------------------------------
# Helpers metier — appeles depuis auth.py et services.py
# ---------------------------------------------------------------------------

def send_welcome(user: dict) -> bool:
    return send(
        to=user["email"],
        subject=i18n.t("email.welcome.subject"),
        template="welcome",
        context={"user": user},
    )


def send_new_bid(client: dict, mission: dict, bid: dict, pilot: dict) -> bool:
    return send(
        to=client["email"],
        subject=i18n.t("email.new_bid.subject", title=mission["title"]),
        template="new_bid",
        context={"client": client, "mission": mission, "bid": bid, "pilot": pilot},
    )


def send_bid_accepted(pilot: dict, mission: dict, booking: dict, client: dict) -> bool:
    return send(
        to=pilot["email"],
        subject=i18n.t("email.bid_accepted.subject", title=mission["title"]),
        template="bid_accepted",
        context={"pilot": pilot, "mission": mission, "booking": booking, "client": client},
    )


def send_new_message(recipient: dict, sender: dict, mission: dict, body: str) -> bool:
    return send(
        to=recipient["email"],
        subject=i18n.t("email.new_message.subject", title=mission["title"]),
        template="new_message",
        context={"recipient": recipient, "sender": sender,
                 "mission": mission, "body": body},
    )


def send_bid_rejected(pilot: dict, mission: dict, bid: dict,
                      client: dict, reason: str = "") -> bool:
    """Notifie le pilote que son devis a ete refuse par le client.
    Le pilote peut alors reviser et resoumettre une nouvelle version."""
    return send(
        to=pilot["email"],
        subject=f"Devis refuse / Bid declined, {mission['title']}",
        template="bid_rejected",
        context={"pilot": pilot, "mission": mission, "bid": bid,
                 "client": client, "reason": reason or ""},
    )


def send_bid_revised(client: dict, mission: dict, bid: dict, pilot: dict) -> bool:
    """Notifie le client que le pilote a soumis une version revisee
    de son devis (revision_no > 1)."""
    return send(
        to=client["email"],
        subject=f"Devis revise / Revised bid, {mission['title']}",
        template="bid_revised",
        context={"client": client, "mission": mission, "bid": bid,
                 "pilot": pilot},
    )


def send_pilot_stripe_required(pilot: dict, mission: dict, booking: dict,
                               client: dict) -> bool:
    """Relance le pilote dont le compte Stripe n'est pas finalise apres
    qu'un client a tente de payer la mission."""
    return send(
        to=pilot["email"],
        subject=f"Action requise / Action required, {mission['title']}",
        template="pilot_stripe_required",
        context={"pilot": pilot, "mission": mission, "booking": booking,
                 "client": client},
    )
