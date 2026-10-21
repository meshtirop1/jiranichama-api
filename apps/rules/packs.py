"""Country rule packs (owner request 2026-10-20: "each country with its full law
enforcement"; JiraniChama_Countries_Plan section 1).

A pack sits under every chama's own rule book. Officials may be stricter, never weaker:
- **blocks**: rule settings the law forbids are refused when the rules are set or changed;
- **size**: the smallest and largest group the law allows (joining past the top is
  refused; the chama goes active only with enough founding members);
- **duties**: what the law asks the group to do (register, report, required officials),
  shown to every member with whether it looks done.

The findings come from public law and regulators' guidelines; where a point is
unconfirmed the pack warns instead of blocking. It is guidance, not legal advice.
"""

from dataclasses import dataclass, field

# Our own floor everywhere (protection plan 2026-10-20).
PLATFORM_MIN_MEMBERS = 5


@dataclass(frozen=True)
class Pack:
    country: str
    min_members: int = PLATFORM_MIN_MEMBERS
    max_members: int | None = None
    simple_interest_only: bool = False  # loans.interest must be "flat"
    no_penalty_on_default: bool = False  # loans.late_penalty_cents must be 0
    no_interest: bool = False  # loans.monthly_rate_bp must be 0
    max_term_months: int | None = None
    warn_annual_rate_bp: int | None = None  # warn when the yearly rate is above this
    duties: tuple = field(default_factory=tuple)  # keys into DUTIES


PACKS = {
    "KE": Pack("KE", duties=("register_voluntary", "records_7_years", "data_odpc")),
    "UG": Pack("UG", min_members=10, max_members=30, simple_interest_only=True,
               no_penalty_on_default=True,
               duties=("register_required", "money_counters", "women_60", "share_out_yearly",
                       "re_register_half", "constitution_language")),
    "TZ": Pack("TZ", min_members=10, max_members=50,
               duties=("register_required", "name_cmg", "quarterly_report",
                       "internal_control", "all_sign_constitution", "credit_consent")),
    "RW": Pack("RW", min_members=5, duties=("register_sector", "separation_of_duties")),
    "BI": Pack("BI", min_members=10, max_members=29, max_term_months=3,
               duties=("brb_sponsor",)),
    "ET": Pack("ET", warn_annual_rate_bp=1200),
    "SS": Pack("SS", warn_annual_rate_bp=1500),
    "SO": Pack("SO", no_interest=True, duties=("fines_charity",)),
    "CD": Pack("CD"),
}

# Plain-language duties, en and sw, and how the app can tell whether one looks done.
DUTIES = {
    "register_voluntary": {
        "en": "Registering is voluntary, but a registered group keeps a member register "
              "(updated within 14 days), keeps records 7 years and gives a yearly account.",
        "sw": "Kusajili si lazima, lakini kikundi kilichosajiliwa huweka daftari la "
              "wanachama (husasishwa ndani ya siku 14), kumbukumbu miaka 7 na hesabu ya "
              "kila mwaka."},
    "records_7_years": {
        "en": "Records are kept at least 7 years. JiraniChama keeps them sealed for you.",
        "sw": "Kumbukumbu huhifadhiwa angalau miaka 7. JiraniChama inazihifadhi zimefungwa."},
    "data_odpc": {
        "en": "JiraniChama is registered with the Data Commissioner for your members' data.",
        "sw": "JiraniChama imesajiliwa na Kamishna wa Data kwa data ya wanachama wenu."},
    "register_required": {
        "en": "The law requires the group to register. See the Register tab.",
        "sw": "Sheria inataka kikundi kisajiliwe. Angalia kichupo cha Sajili."},
    "money_counters": {
        "en": "The committee has a chairperson, treasurer, secretary and two money counters.",
        "sw": "Kamati ina mwenyekiti, mweka hazina, katibu na wahesabu pesa wawili."},
    "women_60": {
        "en": "In a mixed group, at least 60% of the committee are women.",
        "sw": "Katika kikundi mchanganyiko, angalau 60% ya kamati ni wanawake."},
    "share_out_yearly": {
        "en": "Savings are shared out at least once a year.",
        "sw": "Akiba hugawanywa angalau mara moja kwa mwaka."},
    "re_register_half": {
        "en": "If more than half the members change, the group registers again.",
        "sw": "Zaidi ya nusu ya wanachama wakibadilika, kikundi husajiliwa upya."},
    "constitution_language": {
        "en": "The constitution is in English and a language every member understands "
              "(JiraniChama gives it in English and Kiswahili).",
        "sw": "Katiba iko kwa Kiingereza na lugha kila mwanachama anaielewa (JiraniChama "
              "inaitoa kwa Kiingereza na Kiswahili)."},
    "name_cmg": {
        "en": "The group's registered name includes \"Community Microfinance Group\".",
        "sw": "Jina la usajili la kikundi lina \"Kikundi cha Huduma Ndogo za Fedha cha "
              "Jamii\"."},
    "quarterly_report": {
        "en": "A report goes to the council every quarter. Use the chama statement for the "
              "last 3 months.",
        "sw": "Ripoti inaenda halmashauri kila robo mwaka. Tumieni taarifa ya chama ya miezi "
              "3 iliyopita."},
    "internal_control": {
        "en": "One member who is not an official checks the records (internal control).",
        "sw": "Mwanachama mmoja asiye kiongozi hukagua kumbukumbu (udhibiti wa ndani)."},
    "all_sign_constitution": {
        "en": "Every member signs the constitution and the minutes.",
        "sw": "Kila mwanachama anatia sahihi katiba na kumbukumbu za mkutano."},
    "credit_consent": {
        "en": "Members agree in writing before their credit data is shared.",
        "sw": "Wanachama wanakubali kwa maandishi kabla data ya mikopo haijashirikiwa."},
    "register_sector": {
        "en": "The group registers (free) with the Sector.",
        "sw": "Kikundi kinasajiliwa (bure) na Sekta."},
    "separation_of_duties": {
        "en": "Different people start, check and approve each payment. JiraniChama's "
              "approvals already work this way.",
        "sw": "Watu tofauti huanzisha, hukagua na huidhinisha kila malipo. Idhini za "
              "JiraniChama tayari ziko hivyo."},
    "brb_sponsor": {
        "en": "Groups may use digital tools only through a sponsor registered with the "
              "central bank. JiraniChama is not open in Burundi until it has one.",
        "sw": "Vikundi vinaweza kutumia zana za kidijitali kupitia mdhamini aliyesajiliwa na "
              "benki kuu tu."},
    "fines_charity": {
        "en": "Late fines go to charity, never to the group's profit.",
        "sw": "Faini za kuchelewa huenda kwa msaada, kamwe si faida ya kikundi."},
}


def pack_for(country: str | None) -> Pack:
    return PACKS.get(country or "KE", PACKS["KE"])


def rule_errors(rules: dict, country: str | None):
    """Settings in a rule book the country's law forbids. Returns RuleError objects."""
    from .validation import RuleError

    p = pack_for(country)
    loans = rules.get("loans")
    errors = []
    if loans:
        if p.simple_interest_only and loans.get("interest") != "flat":
            errors.append(RuleError("loans/interest", "The law here allows simple interest "
                                    "only on members' loans.", "country_simple_interest"))
        if p.no_penalty_on_default and loans.get("late_penalty_cents", 0):
            errors.append(RuleError("loans/late_penalty_cents", "The law here does not allow "
                                    "the loan to grow when a member falls behind.",
                                    "country_no_default_penalty"))
        if p.no_interest and loans.get("monthly_rate_bp", 0):
            errors.append(RuleError("loans/monthly_rate_bp", "Loans here are interest-free; "
                                    "use a fixed service fee instead.", "country_no_interest"))
        if p.max_term_months and loans.get("max_term_months", 0) > p.max_term_months:
            errors.append(RuleError("loans/max_term_months",
                                    f"Loans here are repaid within {p.max_term_months} "
                                    "months.", "country_max_term"))
    return errors


def warnings(rules: dict, country: str | None) -> list[dict]:
    p = pack_for(country)
    loans = rules.get("loans") or {}
    out = []
    if p.warn_annual_rate_bp and loans.get("monthly_rate_bp", 0) * 12 > p.warn_annual_rate_bp:
        out.append({"code": "country_rate_high",
                    "max_annual_pct": p.warn_annual_rate_bp / 100})
    return out


def compliance(chama, lang: str = "en") -> dict:
    """What the country's law asks of this chama, and whether it looks done."""
    from apps.chamas.models import Membership
    from apps.rules.models import version_at

    lang = lang if lang in ("en", "sw") else "en"
    p = pack_for(chama.country)
    members = chama.memberships.filter(
        state__in=[Membership.State.ACTIVE, Membership.State.EXITING]).count()
    version = version_at(chama, __import__("datetime").date.today())
    rules = version.rules if version else (chama.draft_rules or {})
    registered = bool(getattr(getattr(chama, "registration_progress", None),
                              "certificate_no", ""))
    items = [{
        "key": "size", "ok": members >= p.min_members and (
            p.max_members is None or members <= p.max_members),
        "level": "block",
        "members": members, "min": p.min_members, "max": p.max_members,
    }]
    for e in rule_errors(rules, chama.country):
        items.append({"key": e.code, "ok": False, "level": "block", "text": e.message})
    for w in warnings(rules, chama.country):
        items.append({"key": w["code"], "ok": False, "level": "warn", **w})
    for d in p.duties:
        ok = None  # the app cannot tell; shown as something to do
        if d in ("register_required", "register_sector"):
            ok = registered
        elif d in ("records_7_years", "separation_of_duties", "data_odpc"):
            ok = True
        items.append({"key": d, "ok": ok, "level": "duty", "text": DUTIES[d][lang]})
    return {"country": p.country, "min_members": p.min_members,
            "max_members": p.max_members, "items": items}
