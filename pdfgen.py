"""Generation de documents PDF (devis) avec reportlab — pur Python, aucune
dependance systeme.

Le devis reprend les termes du devis accepte (prestation, livrables,
conditions) et un echeancier de paiement (acompte + solde + total)."""

import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

try:
    from config import UPLOAD_DIR
except Exception:  # pragma: no cover - pdfgen utilisable hors app
    UPLOAD_DIR = None


def _logo_path(booking: dict):
    """Resout le chemin disque du logo (avatar du pilote), ou None."""
    rel = (booking.get("pilot_avatar") or "").strip()
    if not rel or not UPLOAD_DIR or not rel.startswith("uploads/"):
        return None
    path = os.path.join(UPLOAD_DIR, rel[len("uploads/"):])
    return path if os.path.exists(path) else None


def _logo_flowable(booking: dict, size_mm: float = 18):
    """Image du logo pilote dimensionnee, ou None si indisponible/illisible."""
    path = _logo_path(booking)
    if not path:
        return None
    try:
        img = Image(path, width=size_mm * mm, height=size_mm * mm)
        img.hAlign = "LEFT"
        return img
    except Exception:
        return None

# Charte (indigo / encre) cohérente avec le site
INK = colors.HexColor("#14161f")
INK_SOFT = colors.HexColor("#3a3f52")
INK_DIM = colors.HexColor("#686d80")
ACCENT = colors.HexColor("#4257b2")
RULE = colors.HexColor("#dde0ec")
PAPER3 = colors.HexColor("#eef0f8")

# Part d'acompte demandee a la reservation (le solde est du a la livraison)
ACOMPTE_PCT = 0.30


def _money(amount, currency):
    return f"{amount:,.2f} {currency}".replace(",", " ")


def _esc(text) -> str:
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _styles():
    ss = getSampleStyleSheet()
    base = ss["Normal"]
    base.fontName = "Helvetica"
    base.fontSize = 9.5
    base.leading = 14
    base.textColor = INK_SOFT
    return {
        "normal": base,
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold",
                             fontSize=22, leading=26, textColor=INK),
        "eyebrow": ParagraphStyle("eyebrow", parent=base, fontName="Helvetica-Bold",
                                  fontSize=8, leading=12, textColor=ACCENT,
                                  spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold",
                             fontSize=11, leading=15, textColor=INK,
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base),
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=11,
                                textColor=INK_DIM),
        "right": ParagraphStyle("right", parent=base, alignment=TA_RIGHT),
        "brand": ParagraphStyle("brand", parent=base, fontName="Helvetica-Bold",
                                fontSize=13, textColor=INK),
    }


def devis_pdf(booking: dict, mission_type_label: str = "") -> bytes:
    """Retourne les octets d'un PDF de devis pour une reservation."""
    S = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title=f"Devis - reservation {booking.get('id')}",
    )
    cur = booking.get("currency") or "EUR"
    total = float(booking.get("agreed_price") or 0)
    acompte = round(total * ACOMPTE_PCT, 2)
    solde = round(total - acompte, 2)
    ref = booking.get("id")
    date = (booking.get("created_at") or "")[:10]

    el = []

    # --- En-tete : marque du pilote (logo + nom commercial + accroche) ---
    brand_name = (booking.get("pilot_business_name")
                  or booking.get("pilot_name") or "Aube Pilot")
    tagline = (booking.get("pilot_headline") or "").strip()

    brand_para = Paragraph(_esc(brand_name), S["brand"])
    brand_stack = [brand_para]
    if tagline:
        brand_stack.append(Paragraph(_esc(tagline), S["small"]))

    logo = _logo_flowable(booking)
    if logo:
        left_cell = Table([[logo, brand_stack]], colWidths=[22 * mm, 84 * mm])
        left_cell.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (1, 0), (1, 0), 8),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
    else:
        left_cell = brand_stack

    header = Table([[
        left_cell,
        Paragraph(f"<b>DEVIS</b><br/>N° AP-{ref}<br/>{date}", S["right"]),
    ]], colWidths=[108 * mm, 52 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (1, 0), (1, 0), INK),
        ("FONTSIZE", (1, 0), (1, 0), 9.5),
    ]))
    el.append(header)
    el.append(Spacer(1, 8))
    el.append(Table([[""]], colWidths=[160 * mm],
                    style=[("LINEBELOW", (0, 0), (-1, -1), 2, ACCENT)]))
    el.append(Spacer(1, 12))

    # --- Parties ---
    pilot_block = [Paragraph(f"<b>{_esc(brand_name)}</b>", S["body"])]
    real_name = (booking.get("pilot_name") or "").strip()
    if (booking.get("pilot_business_name") and real_name
            and real_name != brand_name):
        pilot_block.append(Paragraph(real_name, S["small"]))
    contact = " · ".join(x for x in [booking.get("pilot_email"),
                                     booking.get("pilot_phone")] if x)
    if contact:
        pilot_block.append(Paragraph(_esc(contact), S["small"]))
    if booking.get("pilot_portfolio_url"):
        pilot_block.append(Paragraph(_esc(booking["pilot_portfolio_url"]), S["small"]))

    client_block = [Paragraph(_esc(booking.get("client_name") or "—"), S["body"])]
    client_loc = " · ".join(x for x in [booking.get("client_city"),
                                        booking.get("client_country")] if x)
    if client_loc:
        client_block.append(Paragraph(_esc(client_loc), S["small"]))

    parties = Table([[
        Paragraph("PRESTATAIRE (PILOTE)", S["eyebrow"]),
        Paragraph("CLIENT", S["eyebrow"]),
    ], [
        pilot_block,
        client_block,
    ]], colWidths=[80 * mm, 80 * mm])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("BOTTOMPADDING", (0, 0), (-1, 0), 2)]))
    el.append(parties)
    el.append(Spacer(1, 12))

    # --- Objet ---
    el.append(Paragraph("OBJET DE LA MISSION", S["eyebrow"]))
    objet = booking.get("mission_title") or "Mission drone"
    lieu = booking.get("country") or ""
    if booking.get("city"):
        lieu += f" · {booking['city']}"
    meta = " · ".join([x for x in [mission_type_label, lieu] if x])
    el.append(Paragraph(f"<b>{objet}</b>", S["body"]))
    if meta:
        el.append(Paragraph(meta, S["small"]))
    if booking.get("bid_eta_hours"):
        el.append(Paragraph(f"Durée estimée : {booking['bid_eta_hours']} h", S["small"]))

    # --- Prestation / livrables / conditions ---
    def section(title, text):
        if text:
            el.append(Paragraph(title, S["h2"]))
            for para in str(text).split("\n"):
                if para.strip():
                    el.append(Paragraph(para.replace("&", "&amp;"), S["body"]))

    section("Prestation", booking.get("bid_description"))
    section("Livrables", booking.get("bid_deliverables"))
    section("Conditions / détails techniques", booking.get("bid_terms"))

    el.append(Spacer(1, 14))

    # --- Echeancier de paiement ---
    el.append(Paragraph("MONTANTS &amp; ÉCHÉANCIER", S["eyebrow"]))
    rows = [
        ["Désignation", "Montant"],
        [f"Acompte ({int(ACOMPTE_PCT * 100)} % — à la réservation)", _money(acompte, cur)],
        [f"Solde ({int((1 - ACOMPTE_PCT) * 100)} % — à la livraison)", _money(solde, cur)],
        ["TOTAL À RÉGLER", _money(total, cur)],
    ]
    t = Table(rows, colWidths=[110 * mm, 50 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("BACKGROUND", (0, -1), (-1, -1), PAPER3),
        ("TEXTCOLOR", (0, -1), (-1, -1), INK),
        ("TEXTCOLOR", (0, 1), (-1, -2), INK_SOFT),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, RULE),
    ]))
    el.append(t)
    el.append(Spacer(1, 12))

    el.append(Paragraph(
        "Paiement sécurisé via AubePilot : les fonds sont conservés en séquestre "
        "(escrow) et versés au pilote après validation de la livraison par le "
        "client. Devis valable 30 jours.", S["small"]))

    el.append(Spacer(1, 18))
    el.append(Table([[""]], colWidths=[160 * mm],
                    style=[("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE)]))
    el.append(Spacer(1, 4))
    el.append(Paragraph(
        f"Devis émis par {_esc(brand_name)} via AubePilot — une marque de "
        "L'Aube Étoilée · pilot.aubeetoilee.com", S["small"]))

    doc.build(el)
    return buf.getvalue()
