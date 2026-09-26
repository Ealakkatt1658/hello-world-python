"""Runs the whole Workday flow against a local imitation of Workday's UI."""

import datetime as dt

import pytest

from job_autofill.workday import NeedsAttention, WorkdayApplier


@pytest.fixture
def profile(tmp_path):
    resume = tmp_path / "Jane_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    return {
        "account": {"email": "jane@example.com", "password": "S3cret!pass"},
        "resume": str(resume),
        "personal": {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "phone": "5555551234",
            "phone_country_code": "United States of America (+1)",
            "address": {
                "line1": "123 Main St",
                "city": "Springfield",
                "state": "Illinois",
                "postal_code": "62701",
                "country": "United States of America",
            },
        },
        "links": {"linkedin": "https://linkedin.com/in/jane"},
        "work_experience": [
            {"title": "SWE Intern", "company": "Example Corp", "location": "Chicago, IL",
             "start": "05/2025", "end": "08/2025", "description": "Built tools."},
            {"title": "TA", "company": "UIUC", "start": "01/2025", "end": "present"},
        ],
        "education": [
            {"school": "University of Illinois Urbana-Champaign", "degree": "Bachelor's", "field_of_study": "Computer Science",
             "gpa": "3.8", "start": "2023", "end": "2027"},
        ],
        "skills": ["Python", "SQL"],
        "application": {
            "how_did_you_hear": "Job Board > LinkedIn",
            "work_authorized": "Yes",
            "require_sponsorship": "No",
        },
        "self_identification": {
            "gender": "Female",
            "hispanic_or_latino": "No",
            "ethnicity": "Asian",
            "veteran_status": "not a protected veteran",
            "disability_status": "No, I do not have a disability",
            "accept_terms": True,
        },
        "answers": [{"question": "non-?compete", "answer": "No"}],
    }


def test_full_application(page, mock_workday_url, profile, tmp_path):
    applier = WorkdayApplier(page, profile, auto_submit=True, interactive=False, screenshot_dir=tmp_path / "shots")
    assert applier.run(mock_workday_url) is True

    got = page.evaluate("window.__submitted")
    today = dt.date.today()
    expected = {
        "How Did You Hear About Us?": "LinkedIn",
        "Have you previously worked for Mock Corp?": "No",
        "Country": "United States of America",
        "Given Name(s)": "Jane",
        "Family Name": "Doe",
        "I have a preferred name": False,
        "Address Line 1": "123 Main St",
        "City": "Springfield",
        "State": "Illinois",
        "Postal Code": "62701",
        "Phone Device Type": "Mobile",
        "Country Phone Code": "United States of America (+1)",
        "Phone Number": "5555551234",
        "Phone Extension": "",
        "Work Experience 1 / Job Title": "SWE Intern",
        "Work Experience 1 / Company": "Example Corp",
        "Work Experience 1 / Location": "Chicago, IL",
        "Work Experience 1 / I currently work here": False,
        "Work Experience 1 / From": "05/2025",
        "Work Experience 1 / To": "08/2025",
        "Work Experience 1 / Role Description": "Built tools.",
        "Work Experience 2 / Job Title": "TA",
        "Work Experience 2 / I currently work here": True,
        "Work Experience 2 / To": "/",
        "Education 1 / School or University": "University of Illinois Urbana-Champaign",
        "Education 1 / Degree": "Bachelor's Degree",
        "Education 1 / Field of Study": "Computer Science",
        "Education 1 / Overall Result (GPA)": "3.8",
        "Education 1 / From": "2023",
        "Education 1 / To (Actual or Expected)": "2027",
        "Type to Add Skills": "Python; SQL",
        "Resume/CV": "Jane_Doe_Resume.pdf",
        "LinkedIn Profile": "https://linkedin.com/in/jane",
        "Are you legally authorized to work in the United States?": "Yes",
        "Will you now or in the future require sponsorship for employment visa status?": "No",
        "Are you at least 18 years of age?": "Yes",
        "Do you have a non-compete agreement?": "No",
        "Gender": "Female",
        "Are you Hispanic or Latino?": "No",
        "Race/Ethnicity": "Asian (United States of America)",
        "Veteran Status": "I am not a protected veteran",
        "Yes, I have read and consent to the terms and conditions": True,
        "Name": "Jane Doe",
        "Date": f"{today.month:02d}/{today.day:02d}/{today.year}",
        "Language": "English",
        "Please check one of the boxes below:": "No, I do not have a disability and have not had one in the past",
    }
    for key, value in expected.items():
        assert got.get(key) == value, key
    assert page.get_by_text("Application Submitted").is_visible()


def test_signs_in_to_existing_account(page, mock_workday_url, profile, tmp_path):
    page.goto(mock_workday_url)
    page.evaluate("accounts['jane@example.com'] = 'S3cret!pass'")
    applier = WorkdayApplier(page, profile, interactive=False, screenshot_dir=None)
    applier.open_application = lambda url: page.click('[data-automation-id="adventureButton"]') or page.click(
        '[data-automation-id="applyManually"]'
    )
    applier.open_application(mock_workday_url)
    applier.authenticate()
    assert applier.in_application(2000)
    assert page.locator('[data-automation-id="verifyPassword"]').count() == 0  # never went to account creation


def test_stops_at_review_without_auto_submit(page, mock_workday_url, profile, tmp_path):
    answers = iter(["no"])
    applier = WorkdayApplier(page, profile, interactive=False, screenshot_dir=None, confirm=lambda _: next(answers))
    assert applier.run(mock_workday_url) is False
    assert applier.on_review_page()


def test_unanswered_required_question_needs_attention(page, mock_workday_url, profile):
    del profile["application"]["work_authorized"]
    applier = WorkdayApplier(page, profile, auto_submit=True, interactive=False, screenshot_dir=None)
    with pytest.raises(NeedsAttention, match="legally authorized"):
        applier.run(mock_workday_url)
