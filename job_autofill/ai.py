"""Drafting answers to open-ended application questions with Claude.

Only used for questions your profile has no rule for. It is given your profile,
your resume and the job posting, and told to answer only from those: if the
answer isn't in them (e.g. "Do you hold a security clearance?" with nothing in
your resume about it) it replies UNKNOWN and the tool asks you instead of
guessing. Self-identification, legal and consent questions never go to the AI
(see ``filling.SENSITIVE``).
"""

from __future__ import annotations

import base64
import importlib.util
import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from job_autofill.filling import Field
from job_autofill.matching import best_option, truthy

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
MAX_JOB_CHARS = 30_000

SYSTEM_PROMPT = """\
You fill in job and internship applications on behalf of the applicant described below. \
You are given one question from an application form at a time and reply with the text \
to put in that field.

Rules:
- Use only facts from the applicant's profile and resume. Never invent experience, \
skills, employers, dates, numbers, certifications, clearances or availability.
- If the answer to a factual question isn't in the profile or resume, reply with \
exactly UNKNOWN. The applicant will answer it themselves.
- Open-ended questions ("Why do you want to work here?", "Tell us about a project") \
should get a genuine, specific answer in the applicant's voice (first person), drawing \
on the resume and on the job posting. Keep it under 150 words unless the question asks \
for more. Plain text only: no markdown, no greeting, no sign-off, no placeholders.
- Short factual fields get a short answer (e.g. a year, a number, a name, a URL).
- When a list of options is given, reply with exactly one option copied verbatim.
- Reply with the answer only, nothing else.

Applicant profile (YAML):
{profile}
"""

# Fields that are private or irrelevant for drafting answers.
_PROFILE_OMIT = {"account", "answers", "ai"}


def ai_available() -> bool:
    return importlib.util.find_spec("anthropic") is not None


class AIAnswerer:
    def __init__(self, profile: dict, model: Optional[str] = None, effort: str = "medium", client=None) -> None:
        settings = profile.get("ai") or {}
        self.model = model or settings.get("model") or DEFAULT_MODEL
        self.effort = (settings.get("effort") or effort).strip().lower()
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        public = {k: v for k, v in profile.items() if k not in _PROFILE_OMIT}
        self.system = SYSTEM_PROMPT.format(profile=yaml.safe_dump(public, sort_keys=True, allow_unicode=True))
        self.resume_block = _resume_block(profile.get("resume"))
        self.job_url = ""
        self.job_text = ""
        self.cache: dict[tuple, Optional[str]] = {}
        self.drafted: list[tuple[str, str]] = []  # (question, answer) for the current job
        self.disabled = False

    def set_job(self, url: str, description: str) -> None:
        self.job_url = url
        self.job_text = description[:MAX_JOB_CHARS]
        self.drafted = []

    def __call__(self, field: Field, options: list[str]) -> Optional[str]:
        key = (self.job_url, field.label, field.kind, tuple(options))
        if key in self.cache:
            return self.cache[key]
        if self.disabled:
            return None
        answer = self._ask(field, options)
        if answer is not None and options:
            idx = best_option(options, answer)
            answer = options[idx] if idx is not None else None
        self.cache[key] = answer
        if answer is not None:
            self.drafted.append((field.label, answer))
        return answer

    def _ask(self, field: Field, options: list[str]) -> Optional[str]:
        import anthropic

        question = f"Question: {field.label}\nField type: {field.kind}"
        if field.kind == "textarea":
            question += " (free text)"
        if options:
            question += "\nOptions (reply with one, verbatim):\n" + "\n".join(f"- {o}" for o in options)

        content: list[dict] = []
        if self.resume_block:
            content.append(self.resume_block)
        content.append(
            {
                "type": "text",
                "text": f"Job posting ({self.job_url or 'unknown URL'}):\n\n{self.job_text or '(not available)'}",
                # Cache profile + resume + posting; only the question changes between calls.
                "cache_control": {"type": "ephemeral"},
            }
        )
        content.append({"type": "text", "text": question})

        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": self.system}],
                messages=[{"role": "user", "content": content}],
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            log.error("AI turned off for this run: your Anthropic API key was rejected (%s). "
                      "Check ANTHROPIC_API_KEY, or run with --no-ai.", exc.message)
            self.disabled = True
            return None
        except anthropic.NotFoundError:
            log.error("AI turned off for this run: model %r not found. Check 'ai: model:' in profile.yaml.", self.model)
            self.disabled = True
            return None
        except anthropic.BadRequestError as exc:
            if "credit" in str(exc.message).lower():
                log.error("AI turned off for this run: your Anthropic account is out of credits.")
                self.disabled = True
            else:
                log.warning("  AI request rejected: %s", exc.message)
            return None
        except anthropic.APIConnectionError as exc:
            log.warning("  AI unavailable (network): %s", exc)
            return None
        except anthropic.RateLimitError:
            log.warning("  AI rate-limited; skipping %r", field.label)
            return None
        except anthropic.APIStatusError as exc:
            log.warning("  AI request failed (%s): %s", exc.status_code, exc.message)
            return None

        if response.stop_reason == "refusal":
            log.info("  AI declined to answer %r", field.label)
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text or text.upper().startswith("UNKNOWN"):
            return None
        return text


def _resume_block(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            "title": "Applicant resume",
            "cache_control": {"type": "ephemeral"},  # same resume across every job
        }
    if p.suffix.lower() in (".txt", ".md"):
        return {
            "type": "text",
            "text": "Applicant resume:\n\n" + p.read_text(encoding="utf-8", errors="replace"),
            "cache_control": {"type": "ephemeral"},
        }
    log.warning("The AI can only read PDF or .txt resumes, so it won't see %s (it will still be uploaded).", p.name)
    return None


def make_answerer(profile: dict, mode: Optional[bool]) -> Optional[AIAnswerer]:
    """``mode``: True = --ai, False = --no-ai, None = decide from profile/environment."""
    settings = profile.get("ai") or {}
    if mode is None:
        mode = settings.get("enabled")
        if mode not in (None, ""):
            mode = truthy(mode)
        else:
            mode = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if not mode:
        return None
    if not ai_available():
        log.warning("AI answers need the 'anthropic' package: pip install anthropic")
        return None
    try:
        answerer = AIAnswerer(profile)
    except Exception as exc:  # e.g. no API key configured
        log.warning("AI answers disabled: %s", exc)
        return None
    log.info("AI answers enabled (%s) for questions your profile doesn't cover.", answerer.model)
    return answerer
