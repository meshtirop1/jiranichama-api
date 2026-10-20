from datetime import date

import pytest

from apps.registration import guides

from .phase1 import active_chama

pytestmark = pytest.mark.django_db
TODAY = date.today()


def test_every_country_has_a_guide_and_every_paper_it_names_exists():
    for country in ("KE", "UG", "TZ", "RW", "BI", "CD", "SS", "ET", "SO"):
        for lang in ("en", "sw"):
            g = guides.guide(country, lang)
            assert g["routes"] and g["disclaimer"]
            for r in g["routes"]:
                assert r["office"] and r["law"] and r["steps"]
                assert set(r["papers"]) <= set(g["papers"])
    assert guides.guide("KE", "en")["detailed"] and not guides.guide("ET", "en")["detailed"]


def test_a_member_reads_the_guide_and_downloads_papers_filled_from_the_chama(api):
    c, (chair, treasurer, secretary, *_) = active_chama(since=TODAY, members=5)
    api.force_authenticate(secretary.user)
    r = api.get(f"/api/v1/chamas/{c.id}/registration/?lang=sw")
    assert r.status_code == 200
    body = r.json()
    assert body["guide"]["country"] == "KE" and body["remind"] and not body["registered"]
    assert "constitution" in body["missing"] and body["can_edit"]
    for paper in guides.MADE_BY_APP:
        pdf = api.get(f"/api/v1/chamas/{c.id}/registration/papers/{paper}/")
        assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF")
    assert api.get(f"/api/v1/chamas/{c.id}/registration/papers/nonsense/").status_code == 404


def test_officials_tick_off_papers_and_the_reminders_stop_with_the_certificate(api):
    c, (chair, treasurer, secretary, member, _) = active_chama(since=TODAY, members=5)
    api.force_authenticate(member.user)
    url = f"/api/v1/chamas/{c.id}/registration/"
    assert api.patch(url, {"tick": "constitution"}, format="json").status_code == 403
    api.force_authenticate(chair.user)
    r = api.patch(url, {"tick": "constitution"}, format="json").json()
    assert "constitution" in r["ticked"] and "constitution" not in r["missing"]
    r = api.patch(url, {"untick": "constitution"}, format="json").json()
    assert "constitution" not in r["ticked"]
    assert api.patch(url, {"tick": "nonsense"}, format="json").status_code == 400
    r = api.patch(url, {"certificate_no": "SDO/123/2026", "registered_on": TODAY.isoformat()},
                  format="json").json()
    assert r["registered"] and not r["remind"] and "certificate" in r["ticked"]
    assert api.get(f"/api/v1/chamas/{c.id}/").json()["registered"] is True


def test_the_guide_is_there_before_any_chama_exists(api):
    from .phase1 import make_user

    u = make_user("+256712345678", "Okello")
    api.force_authenticate(u)
    g = api.get("/api/v1/registration/guide/?country=UG").json()
    assert g["country"] == "UG" and "NGO Act, 2016" in g["routes"][0]["law"]


def test_the_profile_says_the_id_check_has_not_started(api):
    from .phase1 import make_user

    u = make_user("+254712345670", "Wanjiru")
    api.force_authenticate(u)
    me = api.get("/api/v1/auth/me/").json()
    assert me["kyc_status"] == "not_started" and me["date_joined"]
