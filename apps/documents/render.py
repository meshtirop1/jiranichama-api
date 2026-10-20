"""Statement figures to PDF bytes (ReportLab, pure Python: no system libraries to
install on the server or in CI).

Every page carries the statement's fingerprint and where to check it; the first page
carries a QR code to the check page.
"""

import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core import money

from .labels import LABELS

BRAND = colors.HexColor("#1B6B4A")
MUTED = colors.HexColor("#5F6B66")
RULE = colors.HexColor("#D5DDD9")


def kes(cents: int) -> str:
    """A figure in the currency being rendered: "1,000" or "1,000.50"."""
    return money.number(cents)


def grouped(fingerprint: str) -> str:
    return " ".join(fingerprint[i:i + 8] for i in range(0, len(fingerprint), 8))


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], alignment=0, textColor=BRAND,
                                fontSize=18, leading=22, spaceAfter=2),
        "h": ParagraphStyle("h", parent=base["Heading3"], fontName="Helvetica-Bold",
                            textColor=BRAND, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=9.5, leading=12),
        "muted": ParagraphStyle("m", parent=base["BodyText"], fontSize=8.5, leading=11,
                                textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontSize=8.5, leading=10.5),
        "num": ParagraphStyle("n", parent=base["BodyText"], fontSize=8.5, leading=10.5,
                              alignment=TA_RIGHT),
        "th": ParagraphStyle("th", parent=base["BodyText"], fontName="Helvetica-Bold",
                             fontSize=8.5, leading=10.5, textColor=BRAND),
        "thr": ParagraphStyle("thr", parent=base["BodyText"], fontName="Helvetica-Bold",
                              fontSize=8.5, leading=10.5, textColor=BRAND, alignment=TA_RIGHT),
    }


def _qr(url: str, size=28 * mm) -> Drawing:
    widget = QrCodeWidget(url)
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(widget)
    return d


def _grid(rows, widths, *, header=True, right_cols=()):
    """A ruled table. Header cells wrap, so long Kiswahili headings never overlap."""
    if header:
        s = _styles()
        rows = [[Paragraph(escape(str(h)), s["thr"] if i in right_cols else s["th"])
                 for i, h in enumerate(rows[0])]] + rows[1:]
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style.append(("LINEBELOW", (0, 0), (-1, 0), 0.8, BRAND))
    for c in right_cols:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _header(L, s, data, title, verify_url, issued_on: date, subtitle_lines):
    left = [Paragraph(escape(title), s["title"]),
            Paragraph(f"<b>{escape(data['chama']['name'])}</b>", s["body"])]
    left += [Paragraph(line, s["body"]) for line in subtitle_lines]
    left += [Paragraph(escape(f"{L['period']}: {data['from']} – {data['to']}"), s["body"]),
             Paragraph(escape(f"{L['made_on']} {issued_on.isoformat()} · "
                              + L["records_to"].format(seq=data["records_to_seq"])),
                       s["muted"])]
    t = Table([[left, _qr(verify_url)]], colWidths=[140 * mm, 32 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    return t


def _footer(L, fingerprint, verify_url, code):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.line(18 * mm, 20 * mm, A4[0] - 18 * mm, 20 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 16 * mm, L["check"].format(url=verify_url)
                          + f"  ({code[:5]}-{code[5:]})")
        canvas.drawString(18 * mm, 12.5 * mm, f"{L['fingerprint']}: {grouped(fingerprint)}")
        canvas.drawString(18 * mm, 9 * mm, L["never_holds"])
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, L["page"].format(n=doc.page))
        canvas.restoreState()
    return draw


def _member_story(L, s, data):
    t = L["types"]
    story = [Paragraph(escape(L["summary"]), s["h"])]
    totals = data["totals"]
    rows = [[L[k], kes(totals[k])] for k in ("opening_cents", "contributed_cents",
                                             "fines_raised_cents", "fines_paid_cents",
                                             "fines_waived_cents") if totals[k]]
    rows = rows or [[L["contributed_cents"], kes(0)]]
    story.append(_grid(rows, [120 * mm, 54 * mm], header=False, right_cols=(1,)))

    p = data["position"]
    story.append(Paragraph(escape(L["position"].format(on=p["on"])), s["h"]))
    rows = [[L["savings"], kes(p["contributed_cents"])],
            [L["arrears_cents"], kes(p["arrears_cents"])],
            [L["fines_cents"], kes(p["fines_cents"])]]
    if p["credit_cents"]:
        rows.append([L["credit_cents"], kes(p["credit_cents"])])
    if p["pending_cash_cents"]:
        rows.append([L["pending_cash_cents"], kes(p["pending_cash_cents"])])
    rows.append([L["next_due"], f"{kes(p['next_due_cents'])} ({p['next_due_period']})"
                 if p["next_due_period"] else L["nothing_due"]])
    story.append(_grid(rows, [120 * mm, 54 * mm], header=False, right_cols=(1,)))

    story.append(Paragraph(escape(L["details"]), s["h"]))
    if not data["lines"]:
        story.append(Paragraph(escape(L["no_lines"]), s["muted"]))
        return story
    rows = [[L["date"], L["receipt"], L["details"], L["amount"]]]
    for line in data["lines"]:
        if line["type"] == "reversal" and line["reverses_receipt"]:
            what = L["reversal_of"].format(receipt=line["reverses_receipt"])
        else:
            what = t.get(line["type"], line["type"])
        if line["period"]:
            what += f" · {line['period']}"
        text = escape(what)
        amount = escape(kes(line["amount_cents"]))
        if line["reversed"]:
            text = f"<strike>{text}</strike> ({escape(L['reversed'])})"
            amount = f"<strike>{amount}</strike>"
        rows.append([line["date"], line["receipt"], Paragraph(text, s["cell"]),
                     Paragraph(amount, s["num"])])
    story.append(_grid(rows, [24 * mm, 24 * mm, 96 * mm, 30 * mm], right_cols=(3,)))
    return story


def _chama_story(L, s, data):
    story = [Paragraph(escape(L["members"]), s["h"])]
    rows = [[L["code"], L["name"], L["in_period"], L["savings"], L["arrears_cents"],
             L["fines_cents"]]]
    for m in data["members"]:
        rows.append([m["code"], Paragraph(escape(m["name"] or "—"), s["cell"]),
                     kes(m["contributed_cents"]), kes(m["savings_cents"]),
                     kes(m["arrears_cents"]), kes(m["fines_cents"])])
    tot = data["totals"]
    rows.append(["", Paragraph(f"<b>{escape(L['total'])}</b>", s["cell"]),
                 kes(tot["contributed_cents"]), kes(tot["savings_cents"]),
                 kes(tot["arrears_cents"]), kes(tot["fines_cents"])])
    story.append(_grid(rows, [24 * mm, 50 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm],
                       right_cols=(2, 3, 4, 5)))

    b = data["bank"]
    story.append(Paragraph(escape(L["bank"]), s["h"]))
    if b["reconciled_to"]:
        rows = [[L["reconciled_to"], b["reconciled_to"]],
                [L["closing"], f"{money.currency_of()} {kes(b['closing_cents'])}"]]
    else:
        rows = [[L["none_yet"], ""]]
    rows.append([L["unmatched"], L["unmatched_value"].format(
        n=b["unmatched_count"], amount=kes(b["unmatched_cents"]))])
    rows.append([L["cash_waiting"], f"{money.currency_of()} {kes(b['cash_waiting_cents'])}"])
    story.append(_grid(rows, [120 * mm, 54 * mm], header=False, right_cols=(1,)))
    return story


def render(data: dict, *, language: str, fingerprint: str, verify_url: str, code: str,
           issued_on: date, currency: str = "KES") -> bytes:
    """The PDF, with amounts shown in the chama's ``currency``."""
    with money.using(currency):
        return _render(data, language=language, fingerprint=fingerprint,
                       verify_url=verify_url, code=code, issued_on=issued_on)


def _render(data: dict, *, language: str, fingerprint: str, verify_url: str, code: str,
            issued_on: date) -> bytes:
    # Amounts are figures only; the currency is named in the labels.
    L = {k: v.replace("KES", money.currency_of()) if isinstance(v, str) else v
         for k, v in LABELS[language].items()}
    s = _styles()
    buf = io.BytesIO()
    title = L[data["kind"]]
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=26 * mm, title=title,
                            author="JiraniChama", subject=data["chama"]["name"],
                            invariant=1)
    if data["kind"] == "member_statement":
        m = data["member"]
        role = L["roles"].get(m["role"], m["role"])
        sub = [escape(f"{L['member']}: {m['name'] or '—'} ({m['code']}) · {role}")]
        story = _member_story(L, s, data)
    else:
        sub = []
        story = _chama_story(L, s, data)
    story = [_header(L, s, data, title, verify_url, issued_on, sub), Spacer(1, 4 * mm)] + story
    footer = _footer(L, fingerprint, verify_url, code)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
