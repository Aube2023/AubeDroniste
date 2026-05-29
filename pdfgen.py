"""Generation de documents PDF (devis) avec reportlab — pur Python, aucune
dependance systeme.

Le devis reprend les termes du devis accepte (prestation, livrables,
conditions) et un echeancier de paiement (acompte + solde + total)."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

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

    # --- En-tete : marque + bloc devis ---
    header = Table([[
        Paragraph("Aube <font color='#4257b2'>Pilot</font>", S["brand"]),
        Paragraph(f"<b>DEVIS</b><br/>N° AP-{ref}<br/>{date}", S["right"]),
    ]], colWidths=[100 * mm, 60 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (1, 0), (1, 0), INK),
        ("FONTSIZE", (1, 0), (1, 0), 9.5),
    ]))
    el.append(header)
    el.append(Spacer(1, 6))
    el.append(Table([[""]], colWidths=[160 * mm],
                    style=[("LINEBELOW", (0, 0), (-1, -1), 2, ACCENT)]))
    el.append(Spacer(1, 12))

    # --- Parties ---
    parties = Table([[
        Paragraph("PRESTATAIRE (PILOTE)", S["eyebrow"]),
        Paragraph("CLIENT", S["eyebrow"]),
    ], [
        Paragraph(booking.get("pilot_name") or "—", S["body"]),
        Paragraph(booking.get("client_name") or "—", S["body"]),
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
        "AubePilot · une marque de L'Aube Étoilée · pilot.aubeetoilee.com · "
        "auth partagée @aubemail.com", S["small"]))

    doc.build(el)
    return buf.getvalue()
