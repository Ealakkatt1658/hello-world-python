from job_autofill.ai import AIAnswerer, make_answerer
from job_autofill.filling import Field, ai_may_answer
from tests.fakes import FakeClaude

PROFILE = {
    "account": {"email": "a@b.com", "password": "hunter2"},
    "personal": {"first_name": "Jane", "last_name": "Doe", "email": "a@b.com"},
    "answers": [{"question": "x", "answer": "y"}],
}


def field(label, kind="textarea", required=True):
    return Field(id="1", kind=kind, label=label, required=required, filled=False)


def test_answer_and_cache():
    claude = FakeClaude({"why": "Because rockets."})
    ai = AIAnswerer(PROFILE, client=claude)
    ai.set_job("https://x", "Job text " * 50)
    assert ai(field("Why us?"), []) == "Because rockets."
    assert ai(field("Why us?"), []) == "Because rockets."
    assert len(claude.calls) == 1
    assert ai.drafted == [("Why us?", "Because rockets.")]


def test_unknown_and_refusal_return_none():
    assert AIAnswerer(PROFILE, client=FakeClaude({}))(field("Clearance level?"), []) is None
    refusing = FakeClaude({"why": "..."}, stop_reason="refusal")
    assert AIAnswerer(PROFILE, client=refusing)(field("Why us?"), []) is None


def test_options_are_mapped_to_real_option_text():
    ai = AIAnswerer(PROFILE, client=FakeClaude({"hear about": "linkedin"}))
    assert ai(field("Where did you hear about us?", "select"), ["Indeed", "LinkedIn", "Other"]) == "LinkedIn"
    ai = AIAnswerer(PROFILE, client=FakeClaude({"hear about": "a friend told me"}))
    assert ai(field("Where did you hear about us?", "select"), ["Indeed", "LinkedIn"]) is None


def test_password_and_private_sections_not_sent():
    claude = FakeClaude({"why": "ok"})
    ai = AIAnswerer(PROFILE, client=claude)
    ai(field("Why us?"), [])
    system = claude.calls[0]["system"][0]["text"]
    assert "hunter2" not in system and "answers" not in system
    assert claude.calls[0]["model"] == "claude-opus-5"
    assert claude.calls[0]["fallbacks"] == "default"


def test_sensitive_and_optional_short_fields_not_sent_to_ai():
    assert not ai_may_answer(field("Veteran Status", "select"))
    assert not ai_may_answer(field("Please identify your race", "combobox"))
    assert not ai_may_answer(field("Have you ever been convicted of a felony?", "radio"))
    assert not ai_may_answer(field("I agree to the terms", "checkbox"))
    assert not ai_may_answer(field("Twitter handle", "text", required=False))
    assert ai_may_answer(field("Anything else we should know?", "textarea", required=False))
    assert ai_may_answer(field("Years of Python experience", "text"))


def test_make_answerer_modes(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert make_answerer(PROFILE, None) is None
    assert make_answerer(PROFILE, False) is None
    assert make_answerer({**PROFILE, "ai": {"enabled": False}}, None) is None


def test_sensitive_filter_is_word_based():
    assert ai_may_answer(field("Do you have a non-compete agreement?", "select"))
    assert ai_may_answer(field("Describe a project you embraced", "textarea"))
