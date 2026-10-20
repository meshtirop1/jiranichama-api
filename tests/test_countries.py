"""Countries (owner decision 2026-10-20): phones for nine countries, a country on each
person and chama, and amounts shown in the chama's own currency. Wave 1 is Kenya, Uganda
and Tanzania; the others are listed but cannot sign up yet."""

import pytest

from apps.accounts.phones import InvalidPhone, normalise_phone, phone_hint
from apps.chamas import services as chamas
from apps.core import money
from apps.core.problems import Problem
from apps.rules.book import render
from apps.rules.templates import template_for

from .phase1 import make_user


@pytest.mark.parametrize("raw,country,expected", [
    ("0712 345 678", None, "+254712345678"),       # Kenyan, as before countries
    ("+254712345678", "UG", "+254712345678"),      # a country code wins
    ("0772 123 456", "UG", "+256772123456"),
    ("256772123456", None, "+256772123456"),
    ("0754 123 456", "TZ", "+255754123456"),
    ("0788123456", "RW", "+250788123456"),
    ("79 123 456", "BI", "+25779123456"),
    ("0812345678", "CD", "+243812345678"),
    ("0912345678", "SS", "+211912345678"),
    ("0911234567", "ET", "+251911234567"),
    ("+252612345678", None, "+252612345678"),
])
def test_phones_are_read_in_their_country(raw, country, expected):
    assert normalise_phone(raw, country) == expected


def test_a_wrong_number_names_the_country():
    with pytest.raises(InvalidPhone, match="Uganda"):
        normalise_phone("0612", "UG")


def test_the_hint_keeps_the_country_code():
    assert phone_hint("+254712345678") == "+2547••• ••• 678"
    assert phone_hint("+256772123456") == "+2567••• ••• 456"


def test_amounts_show_in_the_chamas_currency():
    assert money.fmt(100_000, "KES") == "KES 1,000"
    assert money.fmt(100_050, "KES") == "KES 1,000.50"
    assert money.fmt(5_000_000, "UGX") == "UGX 50,000"
    assert money.fmt(5_000_050, "UGX") == "UGX 50,000"  # no cents in Uganda
    assert money.fmt(1_250, "USD") == "USD 12.50"
    assert money.fmt(-100_000, "TZS") == "TZS -1,000"


def test_the_rule_book_is_written_in_the_chamas_currency():
    rules = template_for("table_banking")
    clauses = {c.ref: c.text for c in render(rules, "en", "UGX")}
    assert "UGX 1,000" in clauses["3.1"]
    assert "KES" not in clauses["3.1"]


@pytest.mark.django_db
def test_a_chama_takes_its_founders_country_and_currency():
    founder = make_user("+256772000111", "Nakato")
    assert founder.country == "UG"
    chama = chamas.create_chama(founder=founder, name="Kampala Women", type="table_banking",
                                founder_role="chair")
    assert (chama.country, chama.currency) == ("UG", "UGX")


@pytest.mark.django_db
def test_a_chama_cannot_open_where_we_are_not_open_yet():
    founder = make_user("+254712000222", "Wanjiru")
    with pytest.raises(Problem) as exc:
        chamas.create_chama(founder=founder, name="Kigali", type="table_banking",
                            founder_role="chair", country="RW")
    assert exc.value.problem_code == "country_not_open"


@pytest.mark.django_db
def test_the_app_can_list_the_countries(api):
    body = api.get("/api/v1/countries/").json()
    open_ = [c["code"] for c in body["countries"] if c["open"]]
    assert open_ == ["KE", "UG", "TZ"]
    assert len(body["countries"]) == 9
    assert {"code": "am", "name": "አማርኛ", "translated": False} in body["languages"]


@pytest.mark.django_db
def test_signing_in_from_a_country_not_open_yet_is_refused(api):
    r = api.post("/api/v1/auth/otp/", {"phone": "+250788123456"}, format="json")
    assert r.status_code == 400
    assert "not open in Rwanda" in str(r.json())
