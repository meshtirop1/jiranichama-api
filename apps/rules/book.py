"""The plain-language rule book, generated from the settings (6.8 "Rendering").

Because the text is produced from the same JSON the engine enforces, the rule members
read and the rule the app applies cannot drift apart. If the two languages ever differ,
the English version applies (said in the book itself).

The Kiswahili wording is a first draft for review by a native speaker before the pilot.
"""

from dataclasses import dataclass

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
    whole, part = divmod(abs(cents), 100)
    return f"KES {whole:,}" + (f".{part:02d}" if part else "")


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


def render(rules: dict, lang: str = "en") -> list[Clause]:
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
    return out


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
    return out
