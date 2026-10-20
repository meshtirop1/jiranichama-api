"""Changing the rules (5.10). See docs/flows/rule-changes.md.

An official proposes new settings with a reason. They are checked like any rules. The
change is shown clause by clause against the current rule book, then every member has the
notice period to read it. A members' vote follows, with the rule-change thresholds. If it
passes, a new rule version is scheduled for the 1st of next month; it never applies
backwards. If it fails or lapses, the same settings cannot be proposed again until the
cooling-off period has passed.
"""

from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core.problems import Problem
from apps.ledger import services as ledger

from . import book, validation
from .models import RuleProposal, RuleVersion, rules_hash, version_at

DEFAULTS = {"notice_days": 14, "vote_days": 7, "quorum_pct": 50, "pass_pct": 66,
            "cooling_days": 30}


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("rule_changes") or {})}


def changed_paths(old: dict, new: dict, prefix: str = "") -> list[str]:
    """Every setting that differs, as "section/key" paths."""
    out = []
    for key in sorted(set(old) | set(new)):
        path = f"{prefix}{key}"
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            out += changed_paths(a, b, path + "/")
        elif a != b:
            out.append(path)
    return out


def changed_clauses(old: dict, new: dict, lang: str) -> list[dict]:
    before = {c.ref: c.text for c in book.render(old, lang)}
    after = {c.ref: c.text for c in book.render(new, lang)}
    return [{"ref": ref, "before": before.get(ref), "after": after.get(ref)}
            for ref in sorted(set(before) | set(after), key=_ref_key)
            if before.get(ref) != after.get(ref)]


def _ref_key(ref: str):
    head, _, tail = ref.partition(".")
    return (head.isalpha(), head, int(tail) if tail.isdigit() else 0)


def _sections(paths: list[str]) -> set[str]:
    return {p.split("/", 1)[0] for p in paths}


# A rule change can never be pushed through by a few: at least half of all members take
# part and two-thirds of ALL eligible members agree (protection plan 2026-10-20).
RULE_CHANGE_MIN_QUORUM = 50
RULE_CHANGE_MIN_PASS = 66


def thresholds(current: dict, new: dict, paths: list[str]) -> dict:
    """The rule-change rule may itself be changed, but always under the stricter of the
    current and the proposed thresholds, never a smaller one (5.10)."""
    cur = settings_from(current)
    if "rule_changes" not in _sections(paths):
        return cur
    nxt = settings_from(new)
    return {**cur, "quorum_pct": max(cur["quorum_pct"], nxt["quorum_pct"]),
            "pass_pct": max(cur["pass_pct"], nxt["pass_pct"])}


def propose(chama: Chama, by: Membership, *, rules: dict, reason: str) -> RuleProposal:
    if chama.state != Chama.State.ACTIVE:
        raise Problem("chama_not_active", "Rules change once the chama is active.",
                      status.HTTP_409_CONFLICT)
    if by.role not in OFFICIAL_ROLES:
        raise Problem("not_allowed", "An official proposes rule changes.",
                      status.HTTP_403_FORBIDDEN)
    reason = (reason or "").strip()[:500]
    if not reason:
        raise Problem("reason_required", "Say why the rules should change.")
    today = timezone.localdate()
    current = version_at(chama, today)
    try:
        validation.validate(rules)
    except validation.RulesInvalid as exc:
        raise Problem("rules_invalid", "Some settings contradict each other or are out of "
                                       "range.", errors=[e.as_dict() for e in exc.errors]
                      ) from exc
    paths = changed_paths(current.rules, rules)
    if not paths:
        raise Problem("no_change", "These are the rules already in force.")
    sections = _sections(paths)
    with transaction.atomic():
        Chama.objects.select_for_update().get(pk=chama.pk)
        for open_ in RuleProposal.objects.filter(chama=chama, state__in=RuleProposal.OPEN):
            if sections & _sections(open_.changed):
                raise Problem("proposal_open", "Another proposal is changing the same rules; "
                                               "this one waits until it is decided.",
                              status.HTTP_409_CONFLICT)
        cool = settings_from(current.rules)["cooling_days"]
        since = timezone.now() - timedelta(days=cool)
        for past in RuleProposal.objects.filter(chama=chama, created_at__gte=since,
                                                state__in=[RuleProposal.State.REJECTED,
                                                           RuleProposal.State.LAPSED]):
            if set(past.changed) == set(paths):
                raise Problem("cooling_off", f"The same change was turned down recently; it "
                                             f"can be raised again {cool} days after.",
                              status.HTTP_409_CONFLICT)
        s = thresholds(current.rules, rules, paths)
        p = RuleProposal.objects.create(
            chama=chama, proposed_by=by, base_version=current, rules=rules, reason=reason,
            changed=paths, quorum_pct=s["quorum_pct"], pass_pct=s["pass_pct"],
            notice_until=timezone.now() + timedelta(days=s["notice_days"]),
            vote_days=s["vote_days"])
    if s["notice_days"] == 0:
        advance(chama)
    return p


def withdraw(p: RuleProposal, by: Membership) -> RuleProposal:
    if p.proposed_by_id != by.id:
        raise Problem("not_allowed", "Only the official who proposed it withdraws it.",
                      status.HTTP_403_FORBIDDEN)
    if p.state != RuleProposal.State.NOTICE:
        raise Problem("cannot_withdraw", "It can only be withdrawn during the notice period.",
                      status.HTTP_409_CONFLICT)
    p.state = RuleProposal.State.WITHDRAWN
    p.save(update_fields=["state"])
    return p


def advance(chama: Chama | None = None) -> int:
    """Hourly: notice periods that have ended open their vote; scheduled versions whose
    day has come are put in force."""
    from apps.votes.services import open_vote

    n = 0
    qs = RuleProposal.objects.filter(state=RuleProposal.State.NOTICE,
                                     notice_until__lte=timezone.now())
    if chama is not None:
        qs = qs.filter(chama=chama)
    for p in qs.select_related("chama"):
        with transaction.atomic():
            p = RuleProposal.objects.select_for_update().get(pk=p.pk)
            if p.state != RuleProposal.State.NOTICE:
                continue
            v = open_vote(p.chama, subject=f"rules.proposal:{p.id}",
                          question=f"Change the rules: {p.reason[:200]}?",
                          rules=p.base_version.rules,
                          closes_at=timezone.now() + timedelta(days=p.vote_days),
                          quorum_pct=max(p.quorum_pct, RULE_CHANGE_MIN_QUORUM),
                          pass_pct=max(p.pass_pct, RULE_CHANGE_MIN_PASS), over_all=True)
            p.state, p.vote_id = RuleProposal.State.VOTING, v.id
            p.save(update_fields=["state", "vote_id"])
            n += 1
    put_in_force()
    return n


def _first_of_next_month(d: date) -> date:
    return (d.replace(day=1) + timedelta(days=32)).replace(day=1)


def on_vote_closed(vote) -> None:
    """The members have decided (votes.services._close)."""
    p = RuleProposal.objects.select_for_update().get(pk=vote.subject.split(":", 1)[1])
    if p.state != RuleProposal.State.VOTING:
        return
    if vote.state != "passed":
        p.state = (RuleProposal.State.REJECTED if vote.state == "failed"
                   else RuleProposal.State.LAPSED)
        p.save(update_fields=["state"])
        return
    starts = _first_of_next_month(timezone.localdate())
    last = RuleVersion.objects.filter(chama=p.chama).order_by("-version_no").first()
    version = RuleVersion.objects.create(
        chama=p.chama, version_no=last.version_no + 1, rules=p.rules,
        schema_version=p.base_version.schema_version, template=p.base_version.template,
        effective_from=starts, hash=rules_hash(p.rules), state=RuleVersion.State.SCHEDULED)
    ledger.append(chama=p.chama, type="rule_version_adopted", source="rule_proposal",
                  source_id=p.id, rule_version_id=version.id,
                  decision={"version_no": version.version_no, "rules_hash": version.hash,
                            "effective_from": starts.isoformat(), "changed": p.changed,
                            "vote": str(vote.id), "yes": vote.yes, "no": vote.no})
    p.state, p.new_version = RuleProposal.State.ADOPTED, version
    p.save(update_fields=["state", "new_version"])


def put_in_force(today: date | None = None) -> int:
    """Scheduled versions whose start date has come replace the version in force."""
    today = today or timezone.localdate()
    n = 0
    for v in RuleVersion.objects.filter(state=RuleVersion.State.SCHEDULED,
                                        effective_from__lte=today).order_by("effective_from"):
        with transaction.atomic():
            RuleVersion.objects.filter(chama=v.chama, state=RuleVersion.State.IN_FORCE) \
                .update(state=RuleVersion.State.SUPERSEDED)
            RuleVersion.objects.filter(pk=v.pk).update(state=RuleVersion.State.IN_FORCE)
            RuleProposal.objects.filter(new_version=v).update(state=RuleProposal.State.IN_FORCE)
            n += 1
    return n
