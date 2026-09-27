import datetime as dt

from job_autofill.matching import SKIP, Rule, best_option, find_answer, parse_date
from job_autofill.profile import build_rules

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
