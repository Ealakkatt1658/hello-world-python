"""Builds profile.yaml by asking simple questions, so nobody has to edit YAML by hand.

Run with ``python -m job_autofill --setup`` (the SETUP launchers do this for you).
"""

from __future__ import annotations

import getpass
import shutil
from pathlib import Path
from typing import Callable, Optional

import yaml

DECLINE = [
    "I do not wish to self-identify",
    "Decline to self-identify",
    "I do not wish to answer",
    "I don't wish to answer",
    "I do not want to answer",
    "Prefer not to say",
]

GENDER = {
    "Male": ["Male", "Man"],
    "Female": ["Female", "Woman"],
    "Non-binary": ["Non-binary", "Nonbinary"],
    "Prefer not to say": DECLINE,
}
YES_NO_DECLINE = {"Yes": ["Yes"], "No": ["No"], "Prefer not to say": DECLINE}
RACE = {
    "Asian": ["Asian"],
    "Black or African American": ["Black or African American"],
    "White": ["White"],
    "American Indian or Alaska Native": ["American Indian or Alaska Native"],
    "Native Hawaiian or Other Pacific Islander": ["Native Hawaiian or Other Pacific Islander"],
    "Two or more races": ["Two or More Races", "Two or more races"],
    "Prefer not to say": DECLINE,
}
VETERAN = {
    "I am not a veteran": ["I am not a protected veteran", "I am not a veteran", "not a protected veteran", "not a veteran"],
    "I am a protected veteran": [
        "I identify as one or more of the classifications of protected veteran",
        "I am a protected veteran",
        "I am a veteran",
    ],
    "Prefer not to say": DECLINE,
}
DISABILITY = {
    "No, I don't have a disability": [
        "No, I do not have a disability",
        "No, I don't have a disability",
        "I do not have a disability",
    ],
    "Yes, I have a disability (or had one in the past)": ["Yes, I have a disability", "Yes"],
    "Prefer not to say": ["I do not want to answer", "I don't wish to answer", *DECLINE],
}
DEGREES = ["Bachelor's Degree", "Master's Degree", "Associate's Degree", "High School Diploma", "Doctorate"]


class Asker:
    def __init__(self, ask: Callable[[str], str] = input, secret: Callable[[str], str] = getpass.getpass):
        self.ask = ask
        self.secret = secret

    def text(self, question: str, default: str = "", required: bool = False) -> str:
        hint = f" [{default}]" if default else (" (optional, press Enter to skip)" if not required else "")
        while True:
            answer = self.ask(f"{question}{hint}: ").strip() or default
            if answer or not required:
                return answer
            print("  This one is needed.")

    def choice(self, question: str, options: list[str], default: int = 1) -> str:
        print(question)
        for n, option in enumerate(options, 1):
            print(f"  {n}. {option}")
        while True:
            answer = self.ask(f"Type a number [{default}]: ").strip() or str(default)
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            print(f"  Please type a number from 1 to {len(options)}.")

    def yes(self, question: str, default: bool = False) -> bool:
        answer = self.ask(f"{question} (y/n) [{'y' if default else 'n'}]: ").strip().lower()
        return default if not answer else answer.startswith("y")

    def month_year(self, question: str, allow_present: bool = False, required: bool = False) -> str:
        extra = ", or 'present'" if allow_present else ""
        while True:
            answer = self.text(f"{question} (MM/YYYY{extra})", required=required)
            if not answer or (allow_present and answer.lower() == "present"):
                return answer.lower()
            parts = answer.replace("-", "/").split("/")
            if len(parts) == 2 and all(p.isdigit() for p in parts) and 1 <= int(parts[0]) <= 12 and len(parts[1]) == 4:
                return f"{int(parts[0]):02d}/{parts[1]}"
            print("  Please type it like 05/2027.")


def clean_dropped_path(raw: str) -> str:
    """Dragging a file into a terminal adds quotes (Windows) or backslash-escapes (Mac)."""
    path = raw.strip().strip("'\"")
    if "\\ " in path:
        path = path.replace("\\ ", " ")
    return path


def ask_resume(a: Asker, folder: Path) -> str:
    while True:
        raw = a.text(
            "Your resume: drag the PDF file into this window (or type its full path), then press Enter",
            required=False,
        )
        if not raw:
            print("  Skipping the resume. You can run setup again later to add it.")
            return ""
        source = Path(clean_dropped_path(raw)).expanduser()
        if source.is_file():
            target = folder / f"resume{source.suffix.lower() or '.pdf'}"
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            print(f"  Saved a copy as {target.name}")
            return f"./{target.name}"
        print(f"  Couldn't find a file at {source}. Try dragging it in again.")


def run_setup(path: Path = Path("profile.yaml"), ask: Callable[[str], str] = input,
              secret: Callable[[str], str] = getpass.getpass) -> Optional[Path]:
    a = Asker(ask, secret)
    path = path.resolve()
    print("\nLet's set up your profile. Your answers are saved only on this computer, in", path.name)
    print("Press Enter to accept the suggestion in [brackets].\n")
    if path.exists():
        if not a.yes(f"{path.name} already exists. Replace it with new answers?", default=False):
            print("Keeping your existing profile.")
            return None
        backup = path.with_name(path.name + ".bak")
        shutil.copyfile(path, backup)
        print(f"  (Old one saved as {backup.name})")

    print("\n--- About you ---")
    first = a.text("First name", required=True)
    last = a.text("Last name", required=True)
    email = a.text("Email address", required=True)
    phone = a.text("Phone number (digits only)", required=True)
    print("\n--- Address ---")
    address = {
        "line1": a.text("Street address"),
        "city": a.text("City"),
        "state": a.text("State (full name, e.g. Illinois)"),
        "postal_code": a.text("ZIP code"),
        "country": a.text("Country", default="United States of America"),
    }

    print("\n--- Links ---")
    links = {
        "linkedin": a.text("LinkedIn profile URL"),
        "github": a.text("GitHub URL"),
        "website": a.text("Personal website / portfolio URL"),
    }

    print("\n--- Resume ---")
    resume = ask_resume(a, path.parent)

    print("\n--- School ---")
    education = []
    school = a.text("School / university name")
    if school:
        education.append({
            "school": school,
            "degree": a.choice("Degree you're working on (or finished):", DEGREES),
            "field_of_study": a.text("Major / field of study"),
            "gpa": a.text("GPA"),
            "start": a.month_year("Start date"),
            "end": a.month_year("Graduation date (expected is fine)"),
        })

    print("\n--- Jobs & internships (most recent first) ---")
    work = []
    while a.yes("Add a job or internship?" if not work else "Add another one?", default=not work):
        work.append({
            "title": a.text("  Job title", required=True),
            "company": a.text("  Company", required=True),
            "location": a.text("  Location (City, State)"),
            "start": a.month_year("  Start date", required=True),
            "end": a.month_year("  End date", allow_present=True) or "present",
            "description": a.text("  One or two sentences about what you did"),
        })

    skills_raw = a.text("\nSkills, separated by commas (e.g. Python, Excel, SQL)")
    skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

    print("\n--- Common application questions ---")
    application = {
        "how_did_you_hear": ["LinkedIn", "Job Board > LinkedIn", "Other"],
        "previously_worked_here": "No",
        "over_18": "Yes" if a.yes("Are you 18 or older?", default=True) else "No",
        "work_authorized": "Yes" if a.yes("Are you legally allowed to work in the US?", default=True) else "No",
        "require_sponsorship": "Yes" if a.yes("Will you need visa sponsorship now or in the future?", default=False) else "No",
        "willing_to_relocate": "Yes" if a.yes("Are you willing to relocate?", default=True) else "No",
        "relatives_at_company": "No",
        "graduation_date": education[0]["end"] if education else "",
    }

    print("\n--- Voluntary self-identification ---")
    print("Companies ask these for equal-opportunity reporting. Answering is optional and never")
    print("affects hiring decisions. 'Prefer not to say' is always available.\n")
    gender = a.choice("Gender:", list(GENDER), default=len(GENDER))
    hispanic = a.choice("Are you Hispanic or Latino?", list(YES_NO_DECLINE), default=3)
    race = a.choice("Race:", list(RACE), default=len(RACE))
    veteran = a.choice("Veteran status:", list(VETERAN), default=1)
    disability = a.choice("Disability status:", list(DISABILITY), default=len(DISABILITY))

    print("\n--- Workday password ---")
    print("Workday sites need an account. This tool signs in, or creates the account, with your")
    print("email and the password below. Use 8+ characters with an uppercase letter, a number and a symbol.")
    password = a.secret("Password to use for Workday accounts (typing is hidden; Enter to skip and be asked later): ")

    print("\n--- AI answers (optional) ---")
    print("For questions like 'Why do you want to work here?', the tool can have Claude write a draft")
    print("from your resume. That needs an Anthropic API key (console.anthropic.com, pay-per-use).")
    api_key = a.secret("Paste your API key (hidden; Enter to skip): ").strip()

    profile = {
        "account": {"email": email, "password": password},
        "resume": resume,
        "personal": {
            "first_name": first,
            "last_name": last,
            "email": email,
            "phone": phone,
            "phone_device_type": "Mobile",
            "phone_country_code": "United States of America (+1)",
            "address": address,
        },
        "links": links,
        "work_experience": work,
        "education": education,
        "skills": skills,
        "application": application,
        "self_identification": {
            "gender": GENDER[gender],
            "hispanic_or_latino": YES_NO_DECLINE[hispanic],
            "ethnicity": RACE[race],
            "veteran_status": VETERAN[veteran],
            "disability_status": DISABILITY[disability],
            "accept_terms": "true",
        },
        "answers": [
            {"question": "non-?compete|restrictive covenant", "answer": "No"},
        ],
        "ai": {"api_key": api_key, "review_before_submit": "true"},
    }
    header = (
        "# Made by `python -m job_autofill --setup`. Run that again to start over, or edit this file\n"
        "# (keep the two-space indentation). See profile.example.yaml for every option, including\n"
        "# `answers:` for your own answers to company-specific questions.\n"
        "# This file holds your password and API key: keep it private.\n\n"
    )
    path.write_text(header + yaml.safe_dump(profile, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    print(f"\nSaved your profile to {path}")
    return path
