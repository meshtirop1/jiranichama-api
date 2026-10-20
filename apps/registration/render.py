"""The registration papers as PDFs, filled from what JiraniChama already knows about the
chama: its name, rules, members and officials. Blanks are left only for what the app must
not hold (ID numbers, signatures) or does not know (where the group meets).
"""

import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.chamas.models import Chama, Membership
from apps.core import countries
from apps.rules import book
from apps.rules.models import version_at

BRAND = colors.HexColor("#1B6B4A")
MUTED = colors.HexColor("#5F6B66")
RULE = colors.HexColor("#D5DDD9")
LINE = "_" * 26

L = {
    "en": {
        "constitution": "Constitution", "founding_minutes": "Minutes of the founding meeting",
        "members_register": "Register of members", "officials_list": "List of officials",
        "application_letter": "Application for registration",
        "bank_resolution": "Resolution to open a bank account",
        "no": "No.", "name": "Full name", "code": "Member code", "role": "Role",
        "id": "ID number", "phone": "Phone", "sign": "Signature", "joined": "Joined",
        "date": "Date", "place": "Place", "present": "Members present",
        "chair_sign": "Chairperson", "sec_sign": "Secretary", "tre_sign": "Treasurer",
        "prepared": "Prepared with JiraniChama on {d}. JiraniChama keeps records only and "
                    "never holds the group's money.",
        "type": "Type of group", "country": "Country", "formed": "Formed",
        "roles": {"chair": "Chairperson", "treasurer": "Treasurer",
                  "secretary": "Secretary", "auditor": "Auditor",
                  "signatory": "Signatory", "member": "Member"},
        "types": {"merry_go_round": "Merry-go-round", "table_banking": "Table banking",
                  "welfare": "Welfare", "investment_club": "Investment club"},
        "c_name": "1. Name", "c_name_t": "The name of the group is {n}.",
        "c_obj": "2. Objectives",
        "c_obj_t": "To bring members together to save, support one another and grow "
                   "their money, and to keep true records every member can see.",
        "c_rules": "3. Rules of the group",
        "c_rules_t": "The members adopted these rules. They are kept in JiraniChama, where "
                     "every change is voted on and recorded.",
        "c_money": "4. Money and the bank account",
        "c_money_t": "All money is kept in the group's bank account. Every withdrawal "
                     "needs the approval of at least two signatories. JiraniChama holds no "
                     "money.",
        "c_adopt": "Adopted by the members at the founding meeting.",
        "m_open": "The meeting was opened at ______ by the chairperson.",
        "m_agenda": "Agenda",
        "m_items": [
            "Forming the group and its name",
            "Adopting the constitution",
            "Electing the officials",
            "Registering the group",
            "Opening a bank account",
        ],
        "m_res": "Resolutions",
        "m_r1": "The members agreed to form the group named {n}.",
        "m_r2": "The members adopted the constitution attached.",
        "m_r3": "The members elected the officials listed below.",
        "m_r4": "The members agreed to register the group with {o}.",
        "m_r5": "The members agreed to open a bank account in the group's name, with the "
                "signatories below, any two of whom must sign every withdrawal.",
        "m_close": "There being no other business, the meeting closed at ______.",
        "a_to": "To: {o}",
        "a_re": "RE: Application to register {n}",
        "a_body": "We, the officials of {n}, a {t} group of {k} members, apply to register "
                  "the group. The members formed the group and adopted the attached "
                  "constitution at a meeting whose minutes are attached, together with the "
                  "register of members and the list of officials.",
        "a_ask": "We kindly ask you to register the group and issue a certificate.",
        "a_yours": "Yours faithfully,",
        "b_body": "At a meeting of {n} held on ____________ the members resolved to open an "
                  "account in the name of {n} with {b}.",
        "b_sign": "The signatories are listed below. Any two (2) of them must sign every "
                  "withdrawal or transfer. No one person may withdraw alone.",
        "b_change": "Any change of signatories needs a new resolution of the members.",
        "bank_any": "a bank of the members' choice",
        "office_default": "the registering office",
    },
    "sw": {
        "constitution": "Katiba", "founding_minutes": "Kumbukumbu za mkutano wa kuanzisha",
        "members_register": "Daftari la wanachama", "officials_list": "Orodha ya viongozi",
        "application_letter": "Ombi la usajili",
        "bank_resolution": "Azimio la kufungua akaunti ya benki",
        "no": "Na.", "name": "Jina kamili", "code": "Nambari ya mwanachama",
        "role": "Wadhifa", "id": "Nambari ya kitambulisho", "phone": "Simu",
        "sign": "Sahihi", "joined": "Alijiunga", "date": "Tarehe", "place": "Mahali",
        "present": "Wanachama waliohudhuria",
        "chair_sign": "Mwenyekiti", "sec_sign": "Katibu", "tre_sign": "Mweka hazina",
        "prepared": "Imeandaliwa na JiraniChama tarehe {d}. JiraniChama huweka kumbukumbu "
                    "tu na haishiki pesa za kikundi.",
        "type": "Aina ya kikundi", "country": "Nchi", "formed": "Kilianzishwa",
        "roles": {"chair": "Mwenyekiti", "treasurer": "Mweka hazina", "secretary": "Katibu",
                  "auditor": "Mkaguzi", "signatory": "Mtia sahihi", "member": "Mwanachama"},
        "types": {"merry_go_round": "Mzunguko", "table_banking": "Table banking",
                  "welfare": "Ustawi", "investment_club": "Klabu ya uwekezaji"},
        "c_name": "1. Jina", "c_name_t": "Jina la kikundi ni {n}.",
        "c_obj": "2. Malengo",
        "c_obj_t": "Kuwaleta wanachama pamoja kuweka akiba, kusaidiana na kukuza pesa zao, "
                   "na kuweka kumbukumbu za kweli ambazo kila mwanachama anaziona.",
        "c_rules": "3. Sheria za kikundi",
        "c_rules_t": "Wanachama walikubali sheria hizi. Zimehifadhiwa kwenye JiraniChama, "
                     "ambapo kila mabadiliko hupigiwa kura na kurekodiwa.",
        "c_money": "4. Pesa na akaunti ya benki",
        "c_money_t": "Pesa zote huwekwa kwenye akaunti ya benki ya kikundi. Kila utoaji "
                     "unahitaji idhini ya angalau watia sahihi wawili. JiraniChama haishiki "
                     "pesa.",
        "c_adopt": "Imekubaliwa na wanachama kwenye mkutano wa kuanzisha.",
        "m_open": "Mkutano ulifunguliwa saa ______ na mwenyekiti.",
        "m_agenda": "Ajenda",
        "m_items": [
            "Kuanzisha kikundi na jina lake",
            "Kukubali katiba",
            "Kuchagua viongozi",
            "Kusajili kikundi",
            "Kufungua akaunti ya benki",
        ],
        "m_res": "Maazimio",
        "m_r1": "Wanachama walikubali kuanzisha kikundi kiitwacho {n}.",
        "m_r2": "Wanachama walikubali katiba iliyoambatishwa.",
        "m_r3": "Wanachama waliwachagua viongozi walioorodheshwa hapa chini.",
        "m_r4": "Wanachama walikubali kusajili kikundi na {o}.",
        "m_r5": "Wanachama walikubali kufungua akaunti ya benki kwa jina la kikundi, na watia "
                "sahihi walio hapa chini, wawili kati yao lazima watie sahihi kila utoaji.",
        "m_close": "Bila shughuli nyingine, mkutano ulifungwa saa ______.",
        "a_to": "Kwa: {o}",
        "a_re": "YAH: Ombi la kusajili {n}",
        "a_body": "Sisi, viongozi wa {n}, kikundi cha {t} cha wanachama {k}, tunaomba "
                  "kusajili kikundi. Wanachama walianzisha kikundi na kukubali katiba "
                  "iliyoambatishwa kwenye mkutano ambao kumbukumbu zake zimeambatishwa, "
                  "pamoja na daftari la wanachama na orodha ya viongozi.",
        "a_ask": "Tunaomba kwa heshima usajili kikundi na utoe cheti.",
        "a_yours": "Wako mwaminifu,",
        "b_body": "Kwenye mkutano wa {n} uliofanyika tarehe ____________ wanachama "
                  "waliazimia kufungua akaunti kwa jina la {n} katika {b}.",
        "b_sign": "Watia sahihi wameorodheshwa hapa chini. Wawili (2) kati yao lazima watie "
                  "sahihi kila utoaji au uhamisho. Hakuna mtu mmoja anayeweza kutoa peke yake.",
        "b_change": "Mabadiliko yoyote ya watia sahihi yanahitaji azimio jipya la wanachama.",
        "bank_any": "benki watakayochagua wanachama",
        "office_default": "ofisi ya usajili",
    },
}


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], alignment=0, textColor=BRAND,
                                fontSize=18, leading=22, spaceAfter=4),
        "h": ParagraphStyle("h", parent=base["Heading3"], textColor=BRAND, spaceBefore=8,
                            spaceAfter=3),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=10, leading=13.5),
        "muted": ParagraphStyle("m", parent=base["BodyText"], fontSize=8.5, leading=11,
                                textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontSize=9, leading=11),
        "th": ParagraphStyle("th", parent=base["BodyText"], fontName="Helvetica-Bold",
                             fontSize=9, leading=11, textColor=BRAND),
    }


def _grid(rows, widths, row_height=None):
    s = _styles()
    rows = [[Paragraph(escape(str(h)), s["th"]) for h in rows[0]]] + [
        [Paragraph(escape(str(c)), s["cell"]) for c in r] for r in rows[1:]]
    t = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT",
              rowHeights=[None] + [row_height] * (len(rows) - 1) if row_height else None)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1, BRAND),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def facts(chama: Chama, lang: str) -> dict:
    """What the papers are filled from."""
    t = L[lang]
    members = list(Membership.objects.filter(chama=chama).exclude(state="exited")
                   .select_related("user").order_by("member_code"))
    order = {"chair": 0, "treasurer": 1, "secretary": 2, "auditor": 3, "signatory": 4}
    officials = sorted([m for m in members if m.role != "member"],
                       key=lambda m: order.get(m.role, 9))
    version = version_at(chama, date.today())
    rules = version.rules if version else (chama.draft_rules or {})
    account = chama.bank_accounts.filter(active_to__isnull=True).first()
    country = countries.COUNTRIES.get(chama.country)
    return {
        "name": chama.name,
        "type": t["types"].get(chama.type, chama.type),
        "country": country.name if country else chama.country,
        "formed": chama.created_at.date().isoformat(),
        "members": [{"name": m.user.display_name if m.user_id else "", "code": m.member_code,
                     "role": t["roles"].get(m.role, m.role), "role_id": m.role,
                     "joined": m.joined_on.isoformat() if m.joined_on else ""}
                    for m in members],
        "officials": [{"name": m.user.display_name if m.user_id else "",
                       "role": t["roles"].get(m.role, m.role), "code": m.member_code}
                      for m in officials],
        "rules": rules,
        "currency": chama.currency,
        "bank": account.get_bank_display() if account else "",
    }


def _signatures(t, s, roles=("chair_sign", "sec_sign", "tre_sign")):
    cells = [[Paragraph(f"{escape(t[r])}<br/><br/>{LINE}<br/>{escape(t['sign'])} / "
                        f"{escape(t['date'])}", s["cell"]) for r in roles]]
    tab = Table(cells, colWidths=[58 * mm] * len(roles), hAlign="LEFT")
    tab.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 10)]))
    return KeepTogether([Spacer(1, 8 * mm), tab])


def _head(t, s, f, paper):
    return [Paragraph(escape(t[paper]), s["title"]),
            Paragraph(f"<b>{escape(f['name'])}</b>", s["body"]),
            Paragraph(escape(f"{t['type']}: {f['type']} · {t['country']}: {f['country']} · "
                             f"{t['formed']}: {f['formed']}"), s["muted"]),
            Spacer(1, 5 * mm)]


def _constitution(t, s, f, lang, office):
    story = [Paragraph(t["c_name"], s["h"]), Paragraph(escape(t["c_name_t"].format(
        n=f["name"])), s["body"]),
        Paragraph(t["c_obj"], s["h"]), Paragraph(escape(t["c_obj_t"]), s["body"]),
        Paragraph(t["c_rules"], s["h"]), Paragraph(escape(t["c_rules_t"]), s["body"])]
    try:
        clauses = book.render(f["rules"], lang, f["currency"]) if f["rules"] else []
    except Exception:  # an incomplete draft: the rules section stays short
        clauses = []
    for c in clauses:
        story.append(Paragraph(f"<b>{escape(c.ref)}</b>  {escape(c.text)}", s["body"]))
    story += [Paragraph(t["c_money"], s["h"]), Paragraph(escape(t["c_money_t"]), s["body"]),
              Spacer(1, 4 * mm), Paragraph(escape(t["c_adopt"]), s["body"]), _signatures(t, s)]
    return story


def _register(t, s, f, lang, office):
    rows = [[t["no"], t["name"], t["code"], t["role"], t["id"], t["sign"]]]
    for i, m in enumerate(f["members"], 1):
        rows.append([i, m["name"], m["code"], m["role"], "", ""])
    for i in range(len(f["members"]) + 1, len(f["members"]) + 4):  # room for new members
        rows.append([i, "", "", "", "", ""])
    return [_grid(rows, [10 * mm, 50 * mm, 30 * mm, 24 * mm, 32 * mm, 28 * mm], 9 * mm),
            _signatures(t, s, ("chair_sign", "sec_sign"))]


def _officials(t, s, f, lang, office):
    rows = [[t["no"], t["role"], t["name"], t["code"], t["id"], t["sign"]]]
    for i, m in enumerate(f["officials"], 1):
        rows.append([i, m["role"], m["name"], m["code"], "", ""])
    return [_grid(rows, [10 * mm, 28 * mm, 50 * mm, 30 * mm, 32 * mm, 24 * mm], 10 * mm)]


def _minutes(t, s, f, lang, office):
    story = [Paragraph(escape(f"{t['date']}: ____________   {t['place']}: "
                              "______________________"), s["body"]),
             Paragraph(escape(f"{t['present']}: {len(f['members'])}"), s["body"]),
             Spacer(1, 3 * mm), Paragraph(escape(t["m_open"]), s["body"]),
             Paragraph(t["m_agenda"], s["h"])]
    story += [Paragraph(escape(f"{i}. {item}"), s["body"])
              for i, item in enumerate(t["m_items"], 1)]
    story.append(Paragraph(t["m_res"], s["h"]))
    res = [t["m_r1"].format(n=f["name"]), t["m_r2"], t["m_r3"],
           t["m_r4"].format(o=office), t["m_r5"]]
    story += [Paragraph(escape(f"{i}. {r}"), s["body"]) for i, r in enumerate(res, 1)]
    rows = [[t["no"], t["role"], t["name"], t["code"]]] + [
        [i, m["role"], m["name"], m["code"]] for i, m in enumerate(f["officials"], 1)]
    story += [Spacer(1, 3 * mm), _grid(rows, [10 * mm, 34 * mm, 70 * mm, 40 * mm]),
              Spacer(1, 3 * mm), Paragraph(escape(t["m_close"]), s["body"]),
              _signatures(t, s, ("chair_sign", "sec_sign"))]
    return story


def _letter(t, s, f, lang, office):
    return [Paragraph(escape(t["a_to"].format(o=office)), s["body"]),
            Paragraph(escape(f"{t['date']}: ____________"), s["body"]), Spacer(1, 4 * mm),
            Paragraph(f"<b>{escape(t['a_re'].format(n=f['name']))}</b>", s["body"]),
            Spacer(1, 3 * mm),
            Paragraph(escape(t["a_body"].format(n=f["name"], t=f["type"],
                                                k=len(f["members"]))), s["body"]),
            Spacer(1, 2 * mm), Paragraph(escape(t["a_ask"]), s["body"]),
            Spacer(1, 4 * mm), Paragraph(escape(t["a_yours"]), s["body"]), _signatures(t, s)]


def _bank(t, s, f, lang, office):
    bank = f["bank"] or t["bank_any"]
    rows = [[t["no"], t["role"], t["name"], t["id"], t["sign"]]] + [
        [i, m["role"], m["name"], "", ""] for i, m in enumerate(f["officials"], 1)]
    return [Paragraph(escape(t["b_body"].format(n=f["name"], b=bank)), s["body"]),
            Spacer(1, 3 * mm), Paragraph(f"<b>{escape(t['b_sign'])}</b>", s["body"]),
            Spacer(1, 3 * mm),
            _grid(rows, [10 * mm, 30 * mm, 60 * mm, 36 * mm, 38 * mm], 10 * mm),
            Spacer(1, 3 * mm), Paragraph(escape(t["b_change"]), s["body"]),
            _signatures(t, s, ("chair_sign", "sec_sign"))]


STORIES = {
    "constitution": _constitution,
    "founding_minutes": _minutes,
    "members_register": _register,
    "officials_list": _officials,
    "application_letter": _letter,
    "bank_resolution": _bank,
}


def render(chama: Chama, paper: str, lang: str, office: str = "") -> bytes:
    lang = lang if lang in L else "en"
    t, s = L[lang], _styles()
    f = facts(chama, lang)
    office = office or t["office_default"]
    buf = io.BytesIO()
    footer = t["prepared"].format(d=date.today().isoformat())

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, footer)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"{t[paper]} - {f['name']}", author="JiraniChama")
    doc.build(_head(t, s, f, paper) + STORIES[paper](t, s, f, lang, office),
              onFirstPage=draw, onLaterPages=draw)
    return buf.getvalue()
