# Job Application Autofiller

Paste a job link and it fills out the application for you. It signs in to the company's
job site, or creates an account if you don't have one yet. Then it uploads your resume, fills
in every page, answers the self-identification (gender / race / veteran / disability) questions
the way you told it to, and submits. Optionally, Claude drafts answers to open-ended questions
like "Why do you want to work here?" from your resume and the job posting.

**Supported sites:**

| Site | Links look like | Account needed? |
|---|---|---|
| Workday | `*.myworkdayjobs.com/...`, `*.myworkday.com/...` | Yes (signs in or creates one) |
| Greenhouse | `boards.greenhouse.io/...`, `job-boards.greenhouse.io/...`, company pages with `?gh_jid=` | No |
| Lever | `jobs.lever.co/<company>/<id>` (with or without `/apply`) | No |

## Setup (one time, on your own computer)

You need Python 3.10 or newer.

```bash
git clone -b claude/job-application-autofiller-42rd3w https://github.com/ealakkatt1658/hello-world-python.git
cd hello-world-python
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

cp profile.example.yaml profile.yaml   # Windows: copy profile.example.yaml profile.yaml
```

Edit `profile.yaml` with your details and put your resume next to it (e.g. `resume.pdf`).
`profile.yaml` is git-ignored, so your personal info won't be committed. Values are used exactly
as you type them, so you don't need quotes, even for things like `No` or a ZIP code like `02134`.
Keep the indentation (two spaces) the same as in the example.

To keep your password out of the file, set it as an environment variable:

```bash
export WORKDAY_PASSWORD='your-password'        # Windows PowerShell: $env:WORKDAY_PASSWORD='...'
```

(`export` only lasts until you close that terminal window, so set it again in a new one.)

Then check everything is set up:

```bash
python -m job_autofill --check
```

It confirms your profile loads, your resume is found, your AI key works (if you set one), and
the browser starts.

## Use it

```bash
python -m job_autofill "https://company.wd5.myworkdayjobs.com/en-US/External/job/..../Software-Intern_R12345"
```

**Always put the link in quotes.** Links often contain `&`, which the terminal treats as a
command separator if the link isn't quoted.

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

For Greenhouse and Lever there's no sign-in: it opens the form on the job page, uploads
your resume, fills in everything, and submits the same way.

### Trying it out for the first time

1. Run `python -m job_autofill --check` and fix anything it reports.
2. Start with a **Greenhouse or Lever** job. They don't need an account, so they're the simplest.
3. **Leave off `--auto-submit`.** It will fill everything in and then stop and ask. Look over
   the form in the browser, then type anything other than `submit` to skip, or `submit` if
   it all looks right.
4. Then try a Workday job the same way.
5. If something goes wrong, send me the log file (its path is printed at the end of every
   run, under `logs/`) and the snapshot files in `screenshots/`. Whenever it stops or hits an
   error it saves the page's screenshot and HTML there, which shows exactly what it saw.
   These files contain your own details from the form, so only share them with people you
   trust.

### AI answers for open-ended questions (optional)

Some questions can't come from a fixed rule, like "Why do you want to work at Acme?",
"Tell us about a project you're proud of", or a company-specific dropdown. For those, the
tool can ask Claude to draft an answer. Claude sees your profile, your resume (PDF) and the
job posting. To turn it on, create an API key at https://console.anthropic.com and set it:

```bash
export ANTHROPIC_API_KEY='sk-ant-...'           # Windows PowerShell: $env:ANTHROPIC_API_KEY='sk-ant-...'
```

When the key is set, AI answers are on by default. Use `--no-ai` to turn them off for a run,
or `--ai` to force them on.

* **It doesn't make things up.** It answers only from your profile and resume. If a factual
  question isn't covered (e.g. "Do you have a security clearance?"), it replies UNKNOWN and
  the tool asks you instead.
* **It never answers sensitive questions.** Gender, race, veteran and disability status,
  consent boxes, criminal history and similar questions come only from your profile, or from you.
* **You review its answers.** Anything the AI wrote is listed in the terminal and the tool
  waits for you to type `submit`, even with `--auto-submit`. To turn this off, set
  `ai: review_before_submit: false` in `profile.yaml`.

### When it needs you

If it can't do something by itself, it stops and prints **ACTION NEEDED** in the terminal. You
fix it in the browser and press Enter. This happens when:

* the application asks a required question that neither your profile nor the AI can answer
  (it lists which ones),
* a CAPTCHA shows up,
* the site rejects a page (it shows you the error messages),
* Greenhouse emails you a security code to enter before it accepts the application.

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
| `--check` | Check your setup and exit |
| `--auto-submit` | Submit without asking you first (it still asks if the AI wrote any answers) |
| `--ai` / `--no-ai` | Force AI-drafted answers on or off (default: on if `ANTHROPIC_API_KEY` is set) |
| `--channel chrome` | Use your installed Google Chrome instead of Playwright's Chromium |
| `--browser-dir DIR` | Where cookies are saved, so you stay signed in between runs (default `.browser-profile`) |
| `--slow-mo MS` | Slow each browser action down (default 50 ms) |
| `-v` | Verbose logging |

Screenshots of each Review page and each confirmation page are saved in `screenshots/`.
Each run's full log goes to `logs/`.

## How it works

The code doesn't depend on any one company's form layout. On every page it lists each field
along with its question text and works out what kind of field it is: text box, dropdown,
searchable picker, radio buttons, checkboxes or a date. It looks the question up in the rules
built from your profile (`job_autofill/profile.py`), asks the AI if nothing matches, and fills
the field in. Anything already filled is left alone. It then repeats the pass, so questions
that only appear after an earlier answer get filled too.

| File | What it does |
|---|---|
| `job_autofill/filling.py` | The fill loop shared by every site, plus the list of questions the AI may not answer |
| `job_autofill/workday/` | Workday: sign-in and account creation, multi-step navigation, Workday's widgets |
| `job_autofill/forms.py` | Standard HTML forms: native selects, React-Select comboboxes, radio/checkbox groups |
| `job_autofill/greenhouse.py`, `lever.py` | The Greenhouse and Lever flows |
| `job_autofill/ai.py` | Drafting answers with Claude |

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests run each whole flow against local copies of Workday's, Greenhouse's and Lever's
forms in `tests/fixtures/`. The AI is replaced with a fake, so the tests don't need an API key
and never submit a real application.
