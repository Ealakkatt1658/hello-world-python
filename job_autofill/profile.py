"""Loading the applicant profile (profile.yaml) and turning it into answer rules."""

from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Any

import yaml

from job_autofill.matching import SKIP, Rule


class ProfileError(Exception):
    pass


def load_profile(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ProfileError(
            f"Profile file {path} not found. Copy profile.example.yaml to {path} and fill it in."
        )
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    for key in ("account", "personal"):
        if key not in data:
            raise ProfileError(f"Profile is missing the '{key}' section.")
    for key in ("first_name", "last_name", "email"):
        if not data["personal"].get(key):
            raise ProfileError(f"Profile is missing personal.{key}.")

    resume = data.get("resume")
    if resume:
        resume_path = Path(resume).expanduser()
        if not resume_path.is_absolute():
            resume_path = (path.parent / resume_path).resolve()
        if not resume_path.exists():
            raise ProfileError(f"Resume file {resume_path} not found.")
        data["resume"] = str(resume_path)
    return data


def account_email(profile: dict) -> str:
    return profile["account"].get("email") or profile["personal"]["email"]


def account_password(profile: dict) -> str:
    """Password from $WORKDAY_PASSWORD, then profile.yaml, then an interactive prompt."""
    password = os.environ.get("WORKDAY_PASSWORD") or profile["account"].get("password")
    if not password:
        password = getpass.getpass("Workday account password: ")
    return password


def _section(profile: dict, name: str) -> dict:
    return profile.get(name) or {}


def build_rules(profile: dict) -> list[Rule]:
    """Rules for single (non-repeating) fields, most specific first.

    The user's own ``answers`` list always wins over the built-in rules.
    """
    rules: list[Rule] = []
    for item in profile.get("answers") or []:
        rules.append(Rule(item["question"], item["answer"], set(item["kinds"]) if item.get("kinds") else None))

    per = _section(profile, "personal")
    addr = per.get("address") or {}
    links = _section(profile, "links")
    app = _section(profile, "application")
    si = _section(profile, "self_identification")

    def add(pattern: str, value: Any, kinds: set[str] | None = None) -> None:
        rules.append(Rule(pattern, value, kinds))

    TEXT = {"text", "textarea"}

    # --- My Information -------------------------------------------------
    add(r"how did you hear|source of (this )?application|referral source", app.get("how_did_you_hear"))
    add(
        r"previously (worked|been employed)|current or former|former (employee|worker)|"
        r"ever (worked|been employed)|employed by .* before",
        app.get("previously_worked_here", "No"),
    )
    add(r"preferred name", bool(per.get("preferred_name")), {"checkbox"})
    add(r"preferred (first )?name", per.get("preferred_name"), TEXT)
    add(r"country phone code|phone country|country code", per.get("phone_country_code"))
    add(r"^country( ?/ ?(region|territory))?$", addr.get("country"))
    add(r"given name|first name", per.get("first_name"), TEXT)
    add(r"middle name", per.get("middle_name"), TEXT)
    add(r"family name|last name|surname", per.get("last_name"), TEXT)
    add(r"address line 1|^address$|^street", addr.get("line1"), TEXT)
    add(r"address line 2", addr.get("line2"), TEXT)
    add(r"^(city|town)( ?/ ?(town|suburb))?$", addr.get("city"))
    add(r"^county", addr.get("county"))
    add(r"^(state|province|region)( ?/ ?(province|region|territory))?$", addr.get("state"))
    add(r"postal|zip", addr.get("postal_code"), TEXT)
    add(r"phone extension|^extension", SKIP)
    add(r"phone device type|phone type", per.get("phone_device_type", "Mobile"))
    add(r"phone number|^phone$|mobile number", per.get("phone"), TEXT)
    add(r"e-?mail", per.get("email"), TEXT)

    # --- Links ------------------------------------------------------------
    add(r"linkedin", links.get("linkedin"), TEXT)
    add(r"github", links.get("github"), TEXT)
    add(r"website|portfolio|personal url", links.get("website"), TEXT)

    # --- Common application questions --------------------------------------
    add(r"18 years|eighteen", app.get("over_18", "Yes"))
    add(r"sponsor", app.get("require_sponsorship"))
    add(
        r"(legally )?(authori[sz]ed|eligible|permitted|able) to (work|be employed)|work authori[sz]ation|"
        r"right to work",
        app.get("work_authorized"),
    )
    add(r"relocat", app.get("willing_to_relocate"))
    add(r"(relative|related|family member|friend).*(employ|work)", app.get("relatives_at_company", "No"))
    add(r"(expected |anticipated )?graduation (date|year)", app.get("graduation_date"))
    add(r"salary|compensation|pay expectation", app.get("desired_salary"))
    add(r"(earliest )?start date|available to start|availability", app.get("available_start"))

    # --- Voluntary disclosures / self-identification --------------------------
    add(r"^gender|^sex$|gender identity", si.get("gender"))
    add(r"hispanic|latin[oax]", si.get("hispanic_or_latino"))
    add(r"ethnicit|\brace\b", si.get("ethnicity"))
    add(r"veteran|military", si.get("veteran_status"))
    add(r"disabilit|check one of the boxes", si.get("disability_status"))
    add(r"sexual orientation", si.get("sexual_orientation"))
    add(r"pronoun", si.get("pronouns"))
    add(
        r"terms and conditions|privacy (policy|notice|statement)|i (have read|agree|acknowledge|consent|certify|understand)|"
        r"acknowledg|consent",
        si.get("accept_terms", True),
        {"checkbox"},
    )

    # --- Self Identify (disability form) page -----------------------------------
    full_name = " ".join(p for p in (per.get("first_name"), per.get("last_name")) if p)
    add(r"^name$|full name|your name|signature", full_name, TEXT)
    add(r"^date$|today'?s date|signature date|^date signed", "today", {"date"})
    add(r"^language$", profile.get("language", "English"))
    add(r"employee id", SKIP)

    return rules


def work_rules(job: dict) -> list[Rule]:
    current = str(job.get("end", "")).strip().lower() in {"present", "current", "now", ""}
    return [
        Rule(r"job title|position|^title", job.get("title"), {"text", "textarea"}),
        Rule(r"company|employer|organi[sz]ation", job.get("company"), {"text", "textarea"}),
        Rule(r"location", job.get("location"), {"text", "textarea"}),
        Rule(r"currently work", current, {"checkbox"}),
        Rule(r"^from|start", job.get("start"), {"date"}),
        Rule(r"^to$|^to\b|end", SKIP if current else job.get("end"), {"date"}),
        Rule(r"description|responsibilit|summary", job.get("description"), {"textarea", "text"}),
    ]


def education_rules(edu: dict) -> list[Rule]:
    return [
        Rule(r"school|university|college|institution", edu.get("school")),
        Rule(r"degree", edu.get("degree")),
        Rule(r"field of study|major|discipline", edu.get("field_of_study")),
        Rule(r"gpa|overall result|grade", edu.get("gpa"), {"text"}),
        Rule(r"^from|start", edu.get("start"), {"date"}),
        Rule(r"^to$|^to\b|end|actual or expected|graduation", edu.get("end"), {"date"}),
    ]


def website_rules(url: str) -> list[Rule]:
    return [Rule(r"url|website|link", url, {"text"})]
