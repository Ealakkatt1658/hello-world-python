"""The general method against an imaginary careers site: sign-in wall, account creation,
multi-step form with custom widgets, and an application embedded in an iframe."""

import pytest

from job_autofill.generic import GenericApplier
from job_autofill.sites import applier_for, job_board
from tests.conftest import FIXTURES

CAREERS = "https://careers.example.com"


@pytest.fixture
def profile(tmp_path):
    resume = tmp_path / "Sam_Lee_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    return {
        "account": {"email": "sam@example.com", "password": "Str0ng!Pass"},
        "resume": str(resume),
        "personal": {
            "first_name": "Sam", "last_name": "Lee", "email": "sam@example.com", "phone": "5551234567",
            "address": {"city": "Boston", "state": "Massachusetts", "country": "United States of America"},
        },
        "links": {"linkedin": "https://linkedin.com/in/sam"},
        "application": {"work_authorized": "Yes", "require_sponsorship": "No"},
        "self_identification": {"accept_terms": "true"},
        "answers": [{"question": "why are you interested", "answer": "I love turning data into decisions."}],
    }


@pytest.fixture
def serve(page):
    def _serve(routes):
        for pattern, fixture in routes.items():
            body = (FIXTURES / fixture).read_text()
            page.route(pattern, lambda route, body=None, b=body: route.fulfill(body=b, content_type="text/html"))
    return _serve


def test_routing():
    assert applier_for(CAREERS + "/jobs/42") is GenericApplier
    assert applier_for("https://jobs.ashbyhq.com/acme/123") is GenericApplier
    assert job_board("https://www.linkedin.com/jobs/view/123") == "LinkedIn"
    assert job_board("https://boards.greenhouse.io/x") is None


def test_full_flow_on_unknown_site(page, serve, profile, tmp_path):
    serve({CAREERS + "/**": "mock_careers.html"})
    applier = GenericApplier(page, profile, auto_submit=True, interactive=False, screenshot_dir=tmp_path)
    assert applier.run(CAREERS + "/jobs/42") is True

    got = page.evaluate("JSON.parse(localStorage.getItem('application'))")
    assert got == {
        "first": "Sam", "last": "Lee", "email": "sam@example.com", "phone": "5551234567",
        "country": "United States", "linkedin": "https://linkedin.com/in/sam",
        "authorized": "Yes", "sponsorship": "No", "why": "I love turning data into decisions.",
        "resume": "Sam_Lee_Resume.pdf", "submitted": True,
    }
    accounts = page.evaluate("JSON.parse(localStorage.getItem('accounts'))")
    assert accounts == {"sam@example.com": {"password": "Str0ng!Pass", "first": "Sam", "last": "Lee"}}
    # The header's job search boxes were left alone.
    assert page.evaluate("[...document.querySelectorAll('header input')].map(i => i.value)") == ["", ""]


def test_application_in_iframe(page, serve, profile, tmp_path):
    serve({
        "https://www.acme-corp.com/careers/7": "mock_embedded_host.html",
        "https://acme.jobsite.example/apply/7": "mock_embedded_form.html",
    })
    applier = GenericApplier(page, profile, auto_submit=True, interactive=False, screenshot_dir=None)
    assert applier.run("https://www.acme-corp.com/careers/7") is True
    assert page.evaluate("window.__submitted") == {
        "full": "Sam Lee", "email": "sam@example.com", "phone": "5551234567", "resume": "Sam_Lee_Resume.pdf",
    }


def test_never_submits_without_confirmation(page, serve, profile):
    serve({CAREERS + "/**": "mock_careers.html"})
    answers = iter(["no"])
    applier = GenericApplier(page, profile, interactive=False, screenshot_dir=None, confirm=lambda _: next(answers))
    assert applier.run(CAREERS + "/jobs/42") is False
    assert "submitted" not in (page.evaluate("JSON.parse(localStorage.getItem('application'))") or {})
