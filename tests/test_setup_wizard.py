from job_autofill.matching import best_option
from job_autofill.profile import build_rules, load_profile
from job_autofill.matching import find_answer
from job_autofill.setup_wizard import clean_dropped_path, run_setup


def scripted(answers):
    it = iter(answers)
    return lambda prompt: next(it)


def test_setup_writes_a_working_profile(tmp_path):
    resume = tmp_path / "My Resume.pdf"
    resume.write_bytes(b"%PDF")
    answers = [
        "Sam", "Lee", "sam@example.com", "5551234567",                 # about you
        "1 Main St", "Boston", "Massachusetts", "02134", "",           # address (country default)
        "https://linkedin.com/in/sam", "", "",                          # links
        str(resume).replace(" ", "\\ "),                                # dragged-in resume (Mac style)
        "Boston University", "1", "Computer Science", "3.7", "9/2023", "05/2027",  # school
        "y", "Intern", "Acme", "Boston, MA", "06/2025", "08/2025", "Built things.",  # one job
        "n",
        "Python, SQL",
        "", "", "", "",                                                 # 18+, work auth, sponsorship, relocate
        "1", "2", "1", "1", "1",                                        # gender, hispanic, race, veteran, disability
    ]
    path = run_setup(tmp_path / "profile.yaml", ask=scripted(answers), secret=lambda _: "")
    profile = load_profile(path)

    assert (tmp_path / "resume.pdf").read_bytes() == b"%PDF"
    assert profile["personal"]["address"]["postal_code"] == "02134"
    assert profile["education"][0]["degree"] == "Bachelor's Degree"
    assert profile["education"][0]["start"] == "09/2023"
    assert profile["work_experience"][0]["end"] == "08/2025"
    assert profile["skills"] == ["Python", "SQL"]
    rules = build_rules(profile)
    assert find_answer(rules, "Are you legally authorized to work in the US?", "radio") == "Yes"
    assert find_answer(rules, "Will you require sponsorship?", "radio") == "No"
    vet = find_answer(rules, "Veteran Status", "dropdown")
    assert best_option(["I am a protected veteran", "I am not a protected veteran"], vet) == 1
    assert best_option(["I am a veteran", "I am not a veteran"], vet) == 1
    dis = find_answer(rules, "Disability Status", "dropdown")
    assert best_option(["Yes, I have a disability", "No, I don't have a disability", "I don't wish to answer"], dis) == 1


def test_veteran_yes_never_picks_not_a_veteran(tmp_path):
    from job_autofill.setup_wizard import VETERAN

    yes = VETERAN["I am a protected veteran"]
    assert best_option(["I am not a protected veteran", "I identify as one or more of the classifications of protected veteran"], yes) == 1
    assert best_option(["I am not a veteran", "I am a veteran"], yes) == 1


def test_clean_dropped_path():
    assert clean_dropped_path("'/Users/sam/My Resume.pdf' ") == "/Users/sam/My Resume.pdf"
    assert clean_dropped_path('"C:\\Users\\sam\\resume.pdf"') == "C:\\Users\\sam\\resume.pdf"
    assert clean_dropped_path("/Users/sam/My\\ Resume.pdf") == "/Users/sam/My Resume.pdf"
