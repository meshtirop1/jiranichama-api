"""The plain-language rule book, generated from the settings (6.8 "Rendering").

Because the text is produced from the same JSON the engine enforces, the rule members
read and the rule the app applies cannot drift apart. If the two languages ever differ,
the English version applies (said in the book itself).

The Kiswahili wording is a first draft for review by a native speaker before the pilot.
"""

from dataclasses import dataclass

from apps.core import money

ROLE_NAMES = {
    "en": {"chair": "chairperson", "treasurer": "treasurer", "secretary": "secretary"},
    "sw": {"chair": "mwenyekiti", "treasurer": "mweka hazina", "secretary": "katibu"},
}
ORDER_NAMES = {
    "en": {"arrears": "unpaid past months", "fines": "fines", "current": "the current month"},
    "sw": {"arrears": "miezi iliyopita ambayo haijalipwa", "fines": "faini",
           "current": "mwezi huu"},
}


@dataclass
class Clause:
    ref: str
    text: str

    def as_dict(self) -> dict:
        return {"ref": self.ref, "text": self.text}


def kes(cents: int) -> str:
    """An amount in the currency being rendered (see ``render``)."""
    return money.fmt(abs(cents))


def _ordinal_en(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _join(items: list[str], lang: str) -> str:
    if len(items) == 1:
        return items[0]
    word = "and" if lang == "en" else "na"
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def render(rules: dict, lang: str = "en", currency: str = "KES") -> list[Clause]:
    """The rule book in ``lang``, with amounts in the chama's ``currency``."""
    with money.using(currency):
        return _render_sw(rules) if lang == "sw" else _render_en(rules)


def _render_en(r: dict) -> list[Clause]:
    c, f, a = r["contributions"], r["fines"]["late_contribution"], r["approvals"]
    roles = [ROLE_NAMES["en"][x] for x in r["officials"]["roles"]]
    order = [ORDER_NAMES["en"][x] for x in c["allocation_order"]]
    fee = r["membership"]["entry_fee_cents"]
    out = [
        Clause("0.1", "Our money stays in the chama's own bank account. JiraniChama records "
                      "it and never receives, holds or sends it. If the English and Kiswahili "
                      "versions of these rules differ, the English version applies."),
        Clause("1.1", "There is no entry fee." if fee == 0
               else f"A new member pays an entry fee of {kes(fee)}."),
        Clause("1.2", "New members join by a vote of the members."
               if r["membership"]["new_members_need_vote"]
               else "New members join when invited by an official."),
        Clause("2.1", f"The officials are the {_join(roles, 'en')}."),
        Clause("3.1", f"Each member pays {kes(c['amount_cents'])} every month, by the "
                      f"{_ordinal_en(c['due_day'])}. A payment is on time up to "
                      f"{_plural(c['grace_days'], 'day')} after that."),
    ]
    if f["enabled"]:
        cap = f["monthly_cap_cents"]
        limit = f", and fines are at most {kes(cap)} a month." if cap is not None else "."
        out.append(Clause("3.2", f"A contribution paid after that is late. The fine is "
                                 f"{kes(f['amount_cents'])}{limit}"))
    else:
        out.append(Clause("3.2", "There is no fine for paying late."))
    out.append(Clause("3.3", f"A payment is used first for {_join(order, 'en')}, then for "
                             "months ahead."))
    cash_days = c.get("cash_banking_days", 3)
    out.append(Clause("3.4", "Cash paid at a meeting counts from the day it was handed over if "
                             f"it reaches the bank within {_plural(cash_days, 'day')}; "
                             "otherwise from the day it was banked."))
    w = {"officials": 2, "member_vote": False, **(r.get("waivers") or {})}
    out.append(Clause("3.5", f"A member may ask for a fine to be waived, with a reason. It "
                             f"needs {_plural(w['officials'], 'official')}"
                             + (" and a vote of the members" if w["member_vote"] else "")
                             + ". Nobody decides a waiver of their own fine, and every "
                               "member sees each waiver and its reason."))
    stop = r["fines"].get("stop_after_missed_months", 3)
    out.append(Clause("3.6", f"After a member has missed {_plural(stop, 'monthly contribution')},"
                             " no more fines of any kind are added for them. The officials "
                             "agree a payment plan, a waiver or the exit process with them."))
    for i, tier in enumerate(a["tiers"], start=1):
        amount = (f"up to {kes(tier['up_to_cents'])}" if tier["up_to_cents"] is not None
                  else "above that" if i > 1 else "of any amount")
        vote = " and a vote of the members" if tier["member_vote"] else ""
        out.append(Clause(f"4.{i}", f"Money going out {amount} needs the approval of "
                                    f"{_plural(tier['officials'], 'official')}{vote}."))
    out.append(Clause(f"4.{len(a['tiers']) + 1}",
                      f"Approvals must be complete within {a['deadline_hours']} hours, or "
                      "nothing is paid."))
    out += [
        Clause("5.1", f"The chama's bank statement is checked at least every "
                      f"{r['statements']['every_days']} days."),
        Clause("6.1", "The chama pays for members' ID checks." if r["kyc"]["payer"] == "chama"
               else "Each member pays for their own ID check."),
        Clause("7.1", f"These rules start when all officials and at least "
                      f"{r['adoption']['members_share_pct']}% of the founding members have "
                      "confirmed them."),
        Clause("P.1", "Nobody approves money paid to themselves."),
        Clause("P.2", "Bank statements come from the bank's own email, or are uploaded by an "
                      "official and confirmed by a different official."),
        Clause("P.3", "Money can go out only after every official has passed the ID check."),
        Clause("P.4", "Records are never changed. A mistake is fixed by a new, visible "
                      "correction."),
    ]
    out[-4:-4] = (_rotation_en(r.get("rotation")) + _votes_en(r.get("votes"))
                  + _changes_en(r.get("rule_changes")) + _meetings_en(r.get("meetings"))
                  + _loans_en(r.get("loans")) + _disputes_en(r.get("disputes"))
                  + _elections_en(r.get("elections")) + _exit_en(r.get("exit")))
    return out


_CHANGE_DEFAULTS = {"notice_days": 14, "vote_days": 7, "quorum_pct": 50, "pass_pct": 66,
                    "cooling_days": 30}


_MEETING_DEFAULTS = {"absence_fine_cents": 0, "late_fine_cents": 0, "late_after_minutes": 15,
                     "apology_excuses": True, "objection_days": 7}


def _meetings_en(m: dict | None) -> list[Clause]:
    m = {**_MEETING_DEFAULTS, **(m or {})}
    absent = (f"A member who misses a meeting is fined {kes(m['absence_fine_cents'])}"
              if m["absence_fine_cents"] else "There is no fine for missing a meeting")
    absent += (", unless they sent an apology before it started." if m["apology_excuses"]
               and m["absence_fine_cents"] else ".")
    late = (f"A member who arrives more than {_plural(m['late_after_minutes'], 'minute')} "
            f"after the start is fined {kes(m['late_fine_cents'])}." if m["late_fine_cents"]
            else f"Arriving more than {_plural(m['late_after_minutes'], 'minute')} after the "
                 "start is recorded as late, without a fine.")
    return [
        Clause("11.1", "The secretary schedules meetings and members are reminded 2 days and "
                       "2 hours before. " + absent),
        Clause("11.2", late),
        Clause("11.3", f"The secretary shares the minutes with every member, who may object "
                       f"within {_plural(m['objection_days'], 'day')}. Minutes without "
                       "objection are then sealed and cannot change."),
    ]


def _meetings_sw(m: dict | None) -> list[Clause]:
    m = {**_MEETING_DEFAULTS, **(m or {})}
    absent = (f"Mwanachama asiyehudhuria mkutano anatozwa faini ya "
              f"{kes(m['absence_fine_cents'])}" if m["absence_fine_cents"]
              else "Hakuna faini ya kutohudhuria mkutano")
    absent += (", isipokuwa alituma udhuru kabla ya mkutano kuanza." if m["apology_excuses"]
               and m["absence_fine_cents"] else ".")
    late = (f"Mwanachama anayefika zaidi ya dakika {m['late_after_minutes']} baada ya kuanza "
            f"anatozwa faini ya {kes(m['late_fine_cents'])}." if m["late_fine_cents"]
            else f"Kufika zaidi ya dakika {m['late_after_minutes']} baada ya kuanza "
                 "kunaandikwa kama kuchelewa, bila faini.")
    return [
        Clause("11.1", "Katibu anapanga mikutano na wanachama wanakumbushwa siku 2 na saa 2 "
                       "kabla. " + absent),
        Clause("11.2", late),
        Clause("11.3", f"Katibu anawashirikisha wanachama wote kumbukumbu za mkutano, ambao "
                       f"wanaweza kupinga ndani ya siku {m['objection_days']}. Kumbukumbu "
                       "zisizopingwa zinafungwa na haziwezi kubadilika."),
    ]


def _exit_en(x: dict | None) -> list[Clause]:
    x = {"notice_days": 30, "deduction_pct": 0, "death_benefit_cents": 0, **(x or {})}
    ded = (f", less {x['deduction_pct']}% for leaving" if x["deduction_pct"] else "")
    benefit = (f" The chama also pays a welfare benefit of {kes(x['death_benefit_cents'])}."
               if x["death_benefit_cents"] else "")
    return [Clause("16.1", f"A member may leave after {_plural(x['notice_days'], 'day')}' "
                           f"notice and gets back their savings, less unpaid fines and any "
                           f"loan{ded}. When a member dies, their savings, less unpaid fines "
                           f"and any loan, go to their next of kin.{benefit} If a loan is "
                           "larger than the savings, the guarantors are called for the rest.")]


def _exit_sw(x: dict | None) -> list[Clause]:
    x = {"notice_days": 30, "deduction_pct": 0, "death_benefit_cents": 0, **(x or {})}
    ded = (f", ukiondoa asilimia {x['deduction_pct']} kwa kuondoka"
           if x["deduction_pct"] else "")
    benefit = (f" Chama pia kinalipa msaada wa {kes(x['death_benefit_cents'])}."
               if x["death_benefit_cents"] else "")
    return [Clause("16.1", f"Mwanachama anaweza kuondoka baada ya notisi ya siku "
                           f"{x['notice_days']} na kurudishiwa akiba yake, ukiondoa faini "
                           f"ambazo hazijalipwa na mkopo wowote{ded}. Mwanachama akifariki, "
                           "akiba yake, ukiondoa faini na mkopo, inakwenda kwa ndugu wa "
                           f"karibu.{benefit} Mkopo ukizidi akiba, wadhamini wanaitwa kwa "
                           "kiasi kilichobaki.")]


def _elections_en(e: dict | None) -> list[Clause]:
    e = {"term_months": 24, "vote_days": 7, **(e or {})}
    return [Clause("15.1", f"Officials are elected by a vote of the members, open for "
                           f"{_plural(e['vote_days'], 'day')}, for {e['term_months']} months. A "
                           "new official starts once their phone is set up to sign; until "
                           "then the outgoing official stays. If an official leaves suddenly, "
                           "the others may name an acting official, whom the members confirm "
                           "within 30 days.")]


def _elections_sw(e: dict | None) -> list[Clause]:
    e = {"term_months": 24, "vote_days": 7, **(e or {})}
    return [Clause("15.1", f"Viongozi wanachaguliwa kwa kura ya wanachama, iliyo wazi kwa siku "
                           f"{e['vote_days']}, kwa miezi {e['term_months']}. Kiongozi mpya "
                           "anaanza simu yake ikishawekwa kutia sahihi; hadi hapo kiongozi "
                           "anayeondoka anabaki. Kiongozi akiondoka ghafla, wengine wanaweza "
                           "kumteua kaimu, ambaye wanachama wanamthibitisha ndani ya siku 30.")]


_DISPUTE_DEFAULTS = {"decided_by": "officials", "officials": 2, "response_hours": 72,
                     "allow_officials_only": True}


def _disputes_en(d: dict | None) -> list[Clause]:
    d = {**_DISPUTE_DEFAULTS, **(d or {})}
    who = ("a vote of the members" if d["decided_by"] == "vote"
           else f"{_plural(d['officials'], 'official')} not involved in it")
    private = (" A personal matter may be shown to officials only."
               if d["allow_officials_only"] else "")
    return [Clause("14.1", f"Any member may raise a problem with a record. Every member sees "
                           f"it.{private} Those involved have {d['response_hours']} hours to "
                           f"respond; then it is settled by {who}, and the outcome is "
                           "written into the records. Money out still waiting for approval "
                           "pauses until it is settled.")]


def _disputes_sw(d: dict | None) -> list[Clause]:
    d = {**_DISPUTE_DEFAULTS, **(d or {})}
    who = ("kura ya wanachama" if d["decided_by"] == "vote"
           else f"viongozi {d['officials']} wasiohusika")
    private = (" Jambo la binafsi linaweza kuonyeshwa kwa viongozi tu."
               if d["allow_officials_only"] else "")
    return [Clause("14.1", f"Mwanachama yeyote anaweza kuleta tatizo kuhusu rekodi. Kila "
                           f"mwanachama analiona.{private} Wanaohusika wana saa "
                           f"{d['response_hours']} kujibu; kisha linamalizwa na {who}, na "
                           "uamuzi unaandikwa kwenye rekodi. Pesa za kutoka zinazosubiri "
                           "kuidhinishwa zinasimama hadi limalizwe.")]


def _rate(bp: int) -> str:
    whole, part = divmod(bp, 100)
    return f"{whole}" + (f".{part:02d}".rstrip("0") if part else "")


def _loans_en(n: dict | None) -> list[Clause]:
    if not n:
        return []
    how = ("on the amount borrowed (flat)" if n["interest"] == "flat"
           else "on the amount still owed (reducing)")
    g = n["guarantors"]
    order = ("after the month's contribution" if n["contribution_first"]
             else "before the month's contribution")
    pen = (f"A missed instalment, not paid within {_plural(n['grace_days'], 'day')}, is fined "
           f"{kes(n['late_penalty_cents'])}." if n["late_penalty_cents"]
           else f"An instalment not paid within {_plural(n['grace_days'], 'day')} is missed.")
    return [
        Clause("12.1", f"A member may borrow after {_plural(n['min_months'], 'month')} of "
                       f"membership, with no unpaid contributions and no other loan, up to "
                       f"{n['savings_multiple_pct']}% of their savings, less what they "
                       f"guarantee for others, for up to {_plural(n['max_term_months'], 'month')}. "
                       + (f"The loan needs {_plural(g, 'guarantor')}, who share the amount "
                          "equally." if g else "No guarantors are needed.")),
        Clause("12.2", f"Interest is {_rate(n['monthly_rate_bp'])}% a month {how}, charged "
                       f"on each instalment's due date. Repayments come {order}."),
        Clause("12.3", pen),
        Clause("12.4", f"After {_plural(n['default_after_missed'], 'missed instalment')} the "
                       "loan is in default: the officials propose recovery from the "
                       "borrower's savings, then from the guarantors in proportion to their "
                       "shares, and every member sees each deduction."),
    ]


def _loans_sw(n: dict | None) -> list[Clause]:
    if not n:
        return []
    how = ("kwa kiasi kilichokopwa" if n["interest"] == "flat"
           else "kwa kiasi kinachodaiwa bado")
    g = n["guarantors"]
    order = ("baada ya mchango wa mwezi" if n["contribution_first"]
             else "kabla ya mchango wa mwezi")
    pen = (f"Awamu isiyolipwa ndani ya siku {n['grace_days']} inatozwa faini ya "
           f"{kes(n['late_penalty_cents'])}." if n["late_penalty_cents"]
           else f"Awamu isiyolipwa ndani ya siku {n['grace_days']} imekosekana.")
    return [
        Clause("12.1", f"Mwanachama anaweza kukopa baada ya miezi {n['min_months']} ya "
                       f"uanachama, bila michango ambayo haijalipwa na bila mkopo mwingine, "
                       f"hadi asilimia {n['savings_multiple_pct']} ya akiba yake, ukiondoa "
                       f"anachodhamini kwa wengine, kwa hadi miezi {n['max_term_months']}. "
                       + (f"Mkopo unahitaji wadhamini {g}, wanaogawana kiasi sawa." if g
                          else "Hakuna wadhamini wanaohitajika.")),
        Clause("12.2", f"Riba ni asilimia {_rate(n['monthly_rate_bp'])} kwa mwezi {how}, "
                       f"inayotozwa tarehe ya kila awamu. Marejesho yanakuja {order}."),
        Clause("12.3", pen),
        Clause("12.4", f"Baada ya awamu {n['default_after_missed']} kukosekana mkopo "
                       "umeshindwa: viongozi wanapendekeza kurejesha kutoka akiba ya mkopaji, "
                       "kisha kwa wadhamini kulingana na sehemu zao, na kila mwanachama anaona "
                       "kila makato."),
    ]


def _changes_en(c: dict | None) -> list[Clause]:
    c = {**_CHANGE_DEFAULTS, **(c or {})}
    return [Clause("10.1", f"An official may propose a change to these rules with a reason. "
                           f"Members have {_plural(c['notice_days'], 'day')} to read it, then "
                           f"{_plural(c['vote_days'], 'day')} to vote. It passes if at least "
                           f"{c['quorum_pct']}% of members vote and more than "
                           f"{c['pass_pct']}% of the votes are yes, and starts on the 1st of "
                           f"the next month. A change turned down may be raised again after "
                           f"{_plural(c['cooling_days'], 'day')}. This rule itself can only "
                           "be changed under the stricter of its current and new thresholds.")]


def _changes_sw(c: dict | None) -> list[Clause]:
    c = {**_CHANGE_DEFAULTS, **(c or {})}
    return [Clause("10.1", f"Kiongozi anaweza kupendekeza mabadiliko ya kanuni hizi kwa sababu. "
                           f"Wanachama wana siku {c['notice_days']} kuyasoma, kisha siku "
                           f"{c['vote_days']} kupiga kura. Yanapita angalau asilimia "
                           f"{c['quorum_pct']} ya wanachama wakipiga kura na zaidi ya asilimia "
                           f"{c['pass_pct']} wakisema ndiyo, na yanaanza tarehe 1 ya mwezi "
                           f"unaofuata. Mabadiliko yaliyokataliwa yanaweza kuletwa tena baada "
                           f"ya siku {c['cooling_days']}. Kanuni hii yenyewe inabadilishwa tu "
                           "kwa kiwango kikali zaidi kati ya cha sasa na kipya.")]


def _votes_en(v: dict | None) -> list[Clause]:
    v = {"quorum_pct": 50, "pass_pct": 50, "secret": False, **(v or {})}
    how = "in secret: who voted is shown, not how" if v["secret"] else "openly"
    return [Clause("9.1", f"A members' vote counts if at least {v['quorum_pct']}% of the "
                          f"members who may vote take part, and passes with more than "
                          f"{v['pass_pct']}% of the votes cast. Members vote {how}. The "
                          "person a vote is about does not vote on it.")]


def _votes_sw(v: dict | None) -> list[Clause]:
    v = {"quorum_pct": 50, "pass_pct": 50, "secret": False, **(v or {})}
    how = ("kwa siri: wanaopiga kura wanaonyeshwa, si jinsi walivyopiga" if v["secret"]
           else "wazi")
    return [Clause("9.1", f"Kura ya wanachama inahesabiwa angalau asilimia {v['quorum_pct']} "
                          f"ya wanaoruhusiwa kupiga kura wakishiriki, na inapita kwa zaidi ya "
                          f"asilimia {v['pass_pct']} ya kura zilizopigwa. Wanachama wanapiga "
                          f"kura {how}. Mhusika wa kura hapigi kura hiyo.")]


def _rotation_en(rot: dict | None) -> list[Clause]:
    if not rot:
        return []
    order = ("in the order the officials set" if rot["order"] == "fixed"
             else "in an order drawn from the records, which anyone can re-check")
    arrears = {
        "skip": "A member behind on contributions when their turn comes is moved to the end "
                "of the order, and the next member is paid.",
        "deduct": "A member behind on contributions when their turn comes is paid the pot less "
                  "what they owe, and what they owe is recorded as paid.",
        "block": "A member behind on contributions is not paid until they catch up; the "
                 "rotation waits.",
    }[rot["arrears"]]
    return [Clause("8.1", f"Each month one member receives the pot (every member's "
                          f"contribution), {order}, until everyone has had a turn."),
            Clause("8.2", arrears)]


def _rotation_sw(rot: dict | None) -> list[Clause]:
    if not rot:
        return []
    order = ("kwa mpangilio uliowekwa na viongozi" if rot["order"] == "fixed"
             else "kwa mpangilio uliochaguliwa kutoka kwenye kumbukumbu, ambao yeyote anaweza "
                  "kuuhakiki")
    arrears = {
        "skip": "Mwanachama aliye na madeni ya michango zamu yake ikifika anahamishiwa mwisho "
                "wa mpangilio, na anayefuata analipwa.",
        "deduct": "Mwanachama aliye na madeni ya michango zamu yake ikifika analipwa mchango "
                  "wote ukiondoa anachodaiwa, na deni hilo linarekodiwa kuwa limelipwa.",
        "block": "Mwanachama aliye na madeni ya michango halipwi mpaka amalize deni; zamu "
                 "inasubiri.",
    }[rot["arrears"]]
    return [Clause("8.1", f"Kila mwezi mwanachama mmoja anapokea mchango wote wa wanachama, "
                          f"{order}, mpaka kila mmoja apate zamu."),
            Clause("8.2", arrears)]


def _render_sw(r: dict) -> list[Clause]:
    c, f, a = r["contributions"], r["fines"]["late_contribution"], r["approvals"]
    roles = [ROLE_NAMES["sw"][x] for x in r["officials"]["roles"]]
    order = [ORDER_NAMES["sw"][x] for x in c["allocation_order"]]
    out = [
        Clause("0.1", "Pesa yetu inabaki kwenye akaunti ya benki ya chama. JiraniChama "
                      "inaiandika tu, haipokei, haishiki wala haitumi pesa. Kanuni hizi za "
                      "Kiingereza na Kiswahili zikitofautiana, za Kiingereza ndizo zitatumika."),
        Clause("1.1", "Hakuna ada ya kujiunga." if r["membership"]["entry_fee_cents"] == 0
               else f"Mwanachama mpya analipa ada ya kujiunga ya "
                    f"{kes(r['membership']['entry_fee_cents'])}."),
        Clause("1.2", "Wanachama wapya wanajiunga kwa kura ya wanachama."
               if r["membership"]["new_members_need_vote"]
               else "Wanachama wapya wanajiunga wakialikwa na kiongozi."),
        Clause("2.1", f"Viongozi ni {_join(roles, 'sw')}."),
        Clause("3.1", f"Kila mwanachama analipa {kes(c['amount_cents'])} kila mwezi, kabla ya "
                      f"tarehe {c['due_day']}. Malipo ni kwa wakati hadi siku "
                      f"{c['grace_days']} baada ya hapo."),
    ]
    if f["enabled"]:
        cap = f["monthly_cap_cents"]
        out.append(Clause("3.2", f"Mchango unaolipwa baada ya hapo umechelewa. Faini ni "
                                 f"{kes(f['amount_cents'])}"
                                 + (f", na faini zisizidi {kes(cap)} kwa mwezi." if cap is not None
                                    else ".")))
    else:
        out.append(Clause("3.2", "Hakuna faini kwa kuchelewa kulipa."))
    out.append(Clause("3.3", f"Malipo yanatumika kwanza kwa {_join(order, 'sw')}, kisha kwa "
                             "miezi ijayo."))
    cash_days = c.get("cash_banking_days", 3)
    out.append(Clause("3.4", "Pesa taslimu iliyolipwa mkutanoni inahesabiwa tangu siku "
                             "ilipokabidhiwa ikifika benki ndani ya siku "
                             f"{cash_days}; la sivyo tangu siku ilipowekwa benki."))
    w = {"officials": 2, "member_vote": False, **(r.get("waivers") or {})}
    out.append(Clause("3.5", "Mwanachama anaweza kuomba faini isamehewe, kwa sababu. "
                             f"Inahitaji viongozi {w['officials']}"
                             + (" na kura ya wanachama" if w["member_vote"] else "")
                             + ". Hakuna anayeamua msamaha wa faini yake mwenyewe, na kila "
                               "mwanachama anaona kila msamaha na sababu yake."))
    stop = r["fines"].get("stop_after_missed_months", 3)
    out.append(Clause("3.6", f"Mwanachama akishakosa michango {stop} ya kila mwezi, hakuna "
                             "faini nyingine yoyote itakayoongezwa kwake. Viongozi "
                             "wanakubaliana naye mpango wa kulipa, msamaha au utaratibu wa "
                             "kuondoka."))
    for i, tier in enumerate(a["tiers"], start=1):
        amount = (f"hadi {kes(tier['up_to_cents'])}" if tier["up_to_cents"] is not None
                  else "zaidi ya hapo" if i > 1 else "ya kiasi chochote")
        vote = " na kura ya wanachama" if tier["member_vote"] else ""
        out.append(Clause(f"4.{i}", f"Pesa inayotoka {amount} inahitaji idhini ya viongozi "
                                    f"{tier['officials']}{vote}."))
    out.append(Clause(f"4.{len(a['tiers']) + 1}",
                      f"Idhini zikamilike ndani ya saa {a['deadline_hours']}, la sivyo hakuna "
                      "kinacholipwa."))
    out += [
        Clause("5.1", f"Taarifa ya benki ya chama inakaguliwa angalau kila siku "
                      f"{r['statements']['every_days']}."),
        Clause("6.1", "Chama kinalipia ukaguzi wa vitambulisho vya wanachama."
               if r["kyc"]["payer"] == "chama"
               else "Kila mwanachama analipia ukaguzi wa kitambulisho chake."),
        Clause("7.1", f"Kanuni hizi zinaanza viongozi wote na angalau asilimia "
                      f"{r['adoption']['members_share_pct']} ya wanachama waanzilishi "
                      "wakizithibitisha."),
        Clause("P.1", "Hakuna anayeidhinisha pesa anayolipwa yeye mwenyewe."),
        Clause("P.2", "Taarifa za benki zinatoka kwenye barua pepe ya benki yenyewe, au "
                      "zinapakiwa na kiongozi mmoja na kuthibitishwa na kiongozi mwingine."),
        Clause("P.3", "Pesa inaweza kutoka tu baada ya kila kiongozi kupita ukaguzi wa "
                      "kitambulisho."),
        Clause("P.4", "Kumbukumbu hazibadilishwi kamwe. Kosa linarekebishwa kwa marekebisho "
                      "mapya yanayoonekana."),
    ]
    out[-4:-4] = (_rotation_sw(r.get("rotation")) + _votes_sw(r.get("votes"))
                  + _changes_sw(r.get("rule_changes")) + _meetings_sw(r.get("meetings"))
                  + _loans_sw(r.get("loans")) + _disputes_sw(r.get("disputes"))
                  + _elections_sw(r.get("elections")) + _exit_sw(r.get("exit")))
    return out
