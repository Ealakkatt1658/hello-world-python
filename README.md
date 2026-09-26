# Job Application Autofiller

Paste a job link and it fills out the application for you. It signs in to the company's
job site, or creates an account if you don't have one yet. Then it uploads your resume, fills
in every page, answers the self-identification (gender / race / veteran / disability) questions
the way you told it to, and submits.

**Supported so far:** Workday (`*.myworkdayjobs.com` / `*.myworkday.com`).

## Setup (one time, on your own computer)

You need Python 3.9 or newer.

```bash
git clone <this repo> && cd hello-world-python
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

cp profile.example.yaml profile.yaml
```

Edit `profile.yaml` with your details and put your resume next to it (e.g. `resume.pdf`).
`profile.yaml` is git-ignored, so your personal info won't be committed.

To keep your password out of the file, set it as an environment variable:

```bash
export WORKDAY_PASSWORD='your-password'        # Windows PowerShell: $env:WORKDAY_PASSWORD='...'
```

## Use it

```bash
python -m job_autofill "https://company.wd5.myworkdayjobs.com/en-US/External/job/..../Software-Intern_R12345"
```

A browser window opens and you can watch it work:

1. It opens the posting and clicks **Apply → Apply Manually**.
2. It signs in with your email and password. If that account doesn't exist, it creates one.
   If Workday asks you to verify your email, it pauses until you click the link in the email.
3. It fills in every step: My Information, My Experience (resume, jobs, education, skills,
   links), Application Questions, Voluntary Disclosures and Self Identify.
4. On the **Review** page it saves a screenshot and asks you to type `submit`.
   Add `--auto-submit` to skip that question and submit right away.

You can pass several links at once. Each company's application runs one after another:

```bash
python -m job_autofill URL1 URL2 URL3 --auto-submit
```

### When it needs you

If it can't do something by itself, it stops and prints **ACTION NEEDED** in the terminal. You
fix it in the browser and press Enter. This happens when:

* the application asks a required question your profile doesn't answer (it lists which ones),
* a CAPTCHA shows up,
* Workday rejects a page (it shows you the error messages).

To stop it asking the same question again, add the answer under `answers:` in `profile.yaml`.
`question` is matched against the question's text and is case-insensitive:

```yaml
answers:
  - question: "security clearance"
    answer: "No"
  - question: "how many years of .* experience"
    answer: "1"
```

For dropdowns and radio buttons, a short piece of the option's text is enough
(`"not a protected veteran"` will pick "I am not a protected veteran"). You can also give a list
of fallbacks: `["LinkedIn", "Job Board > LinkedIn", "Other"]`. The `>` goes through Workday's
nested menus one level at a time.

### Options

| Flag | What it does |
|---|---|
| `--profile PATH` | Use a different profile file (default `profile.yaml`) |
| `--auto-submit` | Submit without asking you first |
| `--channel chrome` | Use your installed Google Chrome instead of Playwright's Chromium |
| `--browser-dir DIR` | Where cookies are saved, so you stay signed in between runs (default `.browser-profile`) |
| `--slow-mo MS` | Slow each browser action down (default 50 ms) |
| `-v` | Verbose logging |

Screenshots of each Review page and each confirmation page are saved in `screenshots/`.

## How it works

`job_autofill/workday/fields.py` does **not** depend on each company's form layout. It lists
every field on the page along with its label and works out what kind of field it is: text box,
"Select One" dropdown, searchable picker, radio buttons, checkboxes or a date. Then it looks the
label up in the rules built from your profile (`job_autofill/profile.py`) and fills it in.
Anything you've already filled is left alone.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests run the whole flow against `tests/fixtures/mock_workday.html`, a local copy of
Workday's markup and widgets. No real applications are submitted.
