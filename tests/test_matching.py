import datetime as dt

import pytest

from job_autofill.matching import SKIP, Rule, best_option, checkbox_state, find_answer, parse_date
from job_autofill.profile import ProfileError, build_rules, load_profile

PROFILE = {
    "account": {"email": "a@b.com"},
    "personal": {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "a@b.com",
        "phone": "5555551234",
        "address": {"country": "United States of America", "state": "Illinois", "city": "Springfield"},
    },
    "application": {"work_authorized": "Yes", "require_sponsorship": "No"},
    "self_identification": {"veteran_status": "I am not a protected veteran"},
    "answers": [{"question": "sponsor", "answer": "Maybe"}],
}


def test_best_option_prefers_exact_then_prefix_then_contains():
    opts = ["Select One", "No", "Not applicable", "Yes"]
    assert best_option(opts, "No") == 1
    assert best_option(opts, "not") == 2
    assert best_option(["I am a protected veteran", "I am not a protected veteran"], "not a protected") == 1
    assert best_option(opts, ["Maybe", "Yes"]) == 3
    assert best_option(opts, "Maybe") is None


def test_builtin_rules():
    rules = build_rules(PROFILE)
    assert find_answer(rules, "Given Name(s)*", "text") == "Jane"
    assert find_answer(rules, "Family Name", "text") == "Doe"
    assert find_answer(rules, "Country", "dropdown") == "United States of America"
    assert find_answer(rules, "State", "dropdown") == "Illinois"
    assert find_answer(rules, "City", "text") == "Springfield"
    assert find_answer(rules, "Phone Extension", "text") is SKIP
    assert find_answer(rules, "Are you legally authorized to work in the US?", "radio") == "Yes"
    assert find_answer(rules, "Veteran Status", "dropdown") == "I am not a protected veteran"
    assert find_answer(rules, "I have a preferred name", "checkbox") is False
    assert find_answer(rules, "Favourite colour", "text") is None


def test_custom_answers_take_priority():
    rules = build_rules(PROFILE)
    assert find_answer(rules, "Will you require visa sponsorship?", "radio") == "Maybe"


def test_rule_kinds_and_context():
    rules = [Rule("terms", True, {"checkbox"})]
    assert find_answer(rules, "I agree", "checkbox", context="Please accept the terms. I agree") is True
    assert find_answer(rules, "terms", "text") is None


def test_parse_date():
    assert parse_date("05/2025") == {"month": "05", "year": "2025"}
    assert parse_date("5/7/2025") == {"month": "05", "day": "07", "year": "2025"}
    assert parse_date("2027") == {"year": "2027"}
    assert parse_date("2025-08") == {"month": "08", "year": "2025"}
    today = dt.date.today()
    assert parse_date("today")["year"] == str(today.year)


def test_best_option_words_and_negation():
    assert best_option(["United Kingdom +44", "United States +1"], "United States of America") == 1
    assert best_option(["I am a veteran", "I am not a veteran"], "not a protected veteran") == 1
    assert best_option(["I am a protected veteran"], "not a protected veteran") is None
    assert best_option(["I don’t wish to answer"], "I don't wish to answer") == 0
    assert best_option(["Yes", "No"], False) == 1


def test_checkbox_state():
    assert checkbox_state("anything", True) is True
    assert checkbox_state("anything", "yes") is True
    assert checkbox_state("anything", "No") is False
    assert checkbox_state("No, I do not have a disability and have not had one", "No, I do not have a disability")
    assert not checkbox_state("Yes, I have a disability", "No, I do not have a disability")
    assert not checkbox_state("I do not want to answer", ["No, I don't have a disability"])


def test_profile_yaml_keeps_values_as_written(tmp_path):
    (tmp_path / "resume.pdf").write_bytes(b"%PDF")
    path = tmp_path / "profile.yaml"
    path.write_text(
        "account: {email: a@b.com}\n"
        "resume: resume.pdf\n"
        "personal:\n  first_name: Sam\n  last_name: Lee\n  email: a@b.com\n"
        "  address: {postal_code: 02134}\n"
        "application: {previously_worked_here: No, over_18: yes}\n"
        "self_identification: {accept_terms: true}\n"
    )
    profile = load_profile(path)
    assert profile["personal"]["address"]["postal_code"] == "02134"
    assert profile["resume"] == str(tmp_path / "resume.pdf")
    rules = build_rules(profile)
    assert find_answer(rules, "Postal Code", "text") == "02134"
    assert find_answer(rules, "Have you previously worked for Acme?", "radio") == "No"
    assert find_answer(rules, "Are you at least 18 years old?", "radio") == "yes"
    assert checkbox_state("I agree", find_answer(rules, "I agree to the terms and conditions", "checkbox")) is True


def test_profile_errors_are_friendly(tmp_path):
    path = tmp_path / "profile.yaml"
    base = "account: {email: a@b.com}\npersonal: {first_name: A, last_name: B, email: a@b.com}\n"
    path.write_text(base + "answers:\n  - question: 'why (us'\n    answer: x\n")
    with pytest.raises(ProfileError, match="valid pattern"):
        load_profile(path)
    path.write_text(base + "answers:\n  - question: why\n")
    with pytest.raises(ProfileError, match="needs both"):
        load_profile(path)
    path.write_text(base + "resume: missing.pdf\n")
    with pytest.raises(ProfileError, match="not found"):
        load_profile(path)
    path.write_text("personal:\n  first_name: [unclosed\n")
    with pytest.raises(ProfileError, match="valid YAML"):
        load_profile(path)


def test_education_month_year_rules():
    profile = {**PROFILE, "education": [{"school": "UIUC", "start": "08/2023", "end": "05/2027"}]}
    rules = build_rules(profile)
    assert find_answer(rules, "Start date month", "combobox") == ["August", "08", "8"]
    assert find_answer(rules, "End date year", "text") == "2027"
    assert best_option(["01", "05", "08"], find_answer(rules, "End date month", "select")) == 1
