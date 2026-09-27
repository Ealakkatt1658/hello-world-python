"""Greenhouse and Lever flows against local copies of their forms, with a fake AI."""

import pytest

from job_autofill.ai import AIAnswerer
from job_autofill.base import NeedsAttention
from job_autofill.filling import SENSITIVE
from job_autofill.greenhouse import GreenhouseApplier
from job_autofill.lever import LeverApplier
from job_autofill.sites import applier_for
from tests.conftest import FIXTURES
from tests.fakes import FakeClaude

GREENHOUSE_URL = "https://job-boards.greenhouse.io/acmerockets/jobs/4001"
LEVER_URL = "https://jobs.lever.co/mockco/123"


@pytest.fixture
def profile(tmp_path):
    resume = tmp_path / "Jane_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    return {
        "account": {"email": "jane@example.com"},
        "resume": str(resume),
        "personal": {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "phone": "5555551234",
            "address": {"city": "Springfield", "state": "Illinois", "country": "United States of America"},
        },
        "links": {"linkedin": "https://linkedin.com/in/jane", "github": "https://github.com/jane"},
        "work_experience": [{"title": "TA", "company": "UIUC", "start": "01/2025", "end": "present"}],
        "application": {"work_authorized": "Yes", "require_sponsorship": "No"},
        "self_identification": {
            "gender": ["Female", "Decline"],
            "hispanic_or_latino": "No",
            "ethnicity": "Asian",
            "veteran_status": ["not a protected veteran", "not a veteran"],
            "disability_status": ["No, I don't have a disability", "No, I do not have a disability"],
            "accept_terms": True,
        },
        "answers": [
            {"question": "authorized to work .* without sponsorship", "answer": "Yes"},
            {"question": "languages do you know", "answer": ["Python", "SQL"]},
        ],
    }


@pytest.fixture
def serve(page):
    def _serve(routes: dict[str, str]):
        def handler(body):
            return lambda route: route.fulfill(body=body, content_type="text/html")

        for url, fixture in routes.items():
            page.route(url, handler((FIXTURES / fixture).read_text()))

    return _serve


def test_dispatch():
    assert applier_for(GREENHOUSE_URL) is GreenhouseApplier
    assert applier_for("https://boards.greenhouse.io/embed/job_app?for=acme&token=1") is GreenhouseApplier
    assert applier_for("https://careers.acme.com/jobs?gh_jid=4001") is GreenhouseApplier
    assert applier_for(LEVER_URL + "/apply") is LeverApplier
    assert applier_for("https://example.com/jobs/1") is None


def test_greenhouse_full_application(page, serve, profile, tmp_path):
    serve({GREENHOUSE_URL: "mock_greenhouse.html"})
    claude = FakeClaude({"why do you want to work": "I want to build flight software that ships on real launches."})
    answerer = AIAnswerer(profile, client=claude)
    confirmations = []
    applier = GreenhouseApplier(
        page, profile, auto_submit=True, interactive=False, screenshot_dir=tmp_path,
        confirm=lambda msg: confirmations.append(msg) or "submit", answerer=answerer,
    )
    assert applier.run(GREENHOUSE_URL) is True

    got = page.evaluate("window.__submitted")
    assert got == {
        "First Name": "Jane",
        "Last Name": "Doe",
        "Email": "jane@example.com",
        "Phone": "5555551234",
        "Country": "United States",
        "Location (City)": "Springfield, Illinois, United States",
        "Resume/CV": "Jane_Doe_Resume.pdf",
        "LinkedIn Profile": "https://linkedin.com/in/jane",
        "Website": "",
        "Why do you want to work at Acme Rockets?": "I want to build flight software that ships on real launches.",
        "How many years of Rust experience do you have?": "",
        "Are you legally authorized to work in the United States?": "Yes",
        "Will you now or in the future require sponsorship for employment visa status?": "No",
        "Gender": "Female",
        "Are you Hispanic/Latino?": "No",
        "Please identify your race": "Asian",
        "Veteran Status": "I am not a protected veteran",
        "Disability Status": "No, I don't have a disability",
        "I consent to the Acme Rockets candidate privacy notice": True,
        "__honeypot": "",  # hidden anti-bot field left alone
    }
    # The AI saw the job posting, only non-sensitive questions went to it,
    # and because it drafted an answer we were asked to confirm despite --auto-submit.
    assert "reusable launch vehicles" in claude.calls[0]["messages"][0]["content"][-2]["text"]
    assert claude.questions() and not any(SENSITIVE.search(q) for q in claude.questions())
    assert len(confirmations) == 1
    assert answerer.drafted == [("Why do you want to work at Acme Rockets?", "I want to build flight software that ships on real launches.")]


def test_greenhouse_without_ai_asks_for_essay_question(page, serve, profile):
    serve({GREENHOUSE_URL: "mock_greenhouse.html"})
    applier = GreenhouseApplier(page, profile, auto_submit=True, interactive=False, screenshot_dir=None)
    with pytest.raises(NeedsAttention, match="Why do you want to work"):
        applier.run(GREENHOUSE_URL)


def test_lever_full_application(page, serve, profile, tmp_path):
    serve({LEVER_URL: "mock_lever_posting.html", LEVER_URL + "/apply": "mock_lever_apply.html"})
    claude = FakeClaude({"additional information": "Happy to share project demos on request."})
    answerer = AIAnswerer(profile, client=claude)
    applier = LeverApplier(
        page, profile, auto_submit=True, interactive=False, screenshot_dir=tmp_path,
        confirm=lambda _: "submit", answerer=answerer,
    )
    assert applier.run(LEVER_URL + "/apply") is True

    got = page.evaluate("window.__submitted")
    assert got == {
        "resume": "Jane_Doe_Resume.pdf",
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "5555551234",
        "location": "Springfield, Illinois",
        "org": "UIUC",
        "urls[LinkedIn]": "https://linkedin.com/in/jane",
        "urls[GitHub]": "https://github.com/jane",
        "cards[a][field0]": "Yes",
        "cards[b][field0]": "Python; SQL",
        "comments": "Happy to share project demos on request.",
        "eeo[gender]": "Female",
        "eeo[race]": "Asian (Not Hispanic or Latino)",
        "eeo[veteran]": "I am not a veteran",
        "eeo[disability]": "No, I do not have a disability",
        "consent[store]": True,
    }
    assert "data engineering intern" in claude.calls[0]["messages"][0]["content"][-2]["text"].lower()


def test_greenhouse_blocked_submit_is_not_reported_as_success(page, serve, profile):
    """The job description already says "Thank you for your interest"; that must not count."""
    serve({GREENHOUSE_URL: "mock_greenhouse.html"})
    del profile["application"]["work_authorized"]  # leaves a required question empty
    pauses = []
    applier = GreenhouseApplier(
        page, profile, auto_submit=True, interactive=True, screenshot_dir=None,
        confirm=lambda msg: pauses.append(msg) or "",
    )
    applier.confirm_timeout_ms = 2000
    assert applier.run(GREENHOUSE_URL) is False
    assert page.evaluate("window.__submitted") is None
    assert len(pauses) == 2  # the unanswered question, then the blocked submission
