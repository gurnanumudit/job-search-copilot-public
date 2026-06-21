from datetime import datetime, timezone

from job_agent.answers import build_readiness, generate_answer_for_field
from job_agent.models import CandidatePreferences, CandidateProfile, JobPosting, JobScore, RawFormField


def _profile() -> CandidateProfile:
    return CandidateProfile(
        profile_bank_text=(
            "# Candidate\n\n## Best Projects\n### Team Tools\nDescription:\nBuilt internal developer tooling.\n"
        ),
        answer_bank_text=(
            "# Answer Bank\n\n## Work Authorization\nAre you authorized to work in the United States?\n"
            "Answer: Yes.\n\nWill you now or in the future require sponsorship?\n"
            "Answer: Yes, I would require immigration sponsorship.\n\n"
            "Can you work from the San Francisco office three days per week?\n"
            "Answer: Yes.\n\n"
            "## Availability\nAnswer: Next month.\n\n"
            "## Location\nAnswer: San Francisco, CA. Open to moving.\n\n"
            "## Why Replit?\nDraft: Replit is interesting because of AI and developer productivity.\n"
        ),
        preferences=CandidatePreferences.model_validate(
            {
                "candidate": {
                    "name": "Example Candidate",
                    "preferred_name": "Example",
                    "email": "candidate@example.com",
                    "phone": "+1-555-555-5555",
                    "linkedin": "",
                    "github": "",
                    "portfolio": "",
                    "citizenship_countries": ["Canada"],
                    "us_legal_permanent_resident": False,
                    "approved_i140": True,
                    "current_location": "San Francisco, CA",
                    "start_date_or_availability": "Next month",
                    "willing_sf_hybrid_3_days_per_week": True,
                    "voluntary_self_identification": {
                        "gender": "Male",
                        "race": "Asian",
                        "veteran_status": "I am not a protected veteran",
                        "disability_status": "No, I don't have a disability",
                        "user_confirmed_for_future_applications": True,
                    },
                },
                "location_preferences": {"preferred": ["Remote"]},
                "role_lanes": [],
                "job_priorities": {},
            }
        ),
    )


def _placeholder_profile() -> CandidateProfile:
    profile = _profile()
    preferences = profile.preferences.model_copy(
        update={
            "candidate": {
                "name": "Example Candidate",
                "email": "candidate@example.com",
                "phone": "+1-555-555-5555",
                "linkedin": "",
                "github": "",
                "portfolio": "",
            }
        }
    )
    return profile.model_copy(update={"preferences": preferences})


def _job() -> JobPosting:
    return JobPosting(
        job_id="replit-data-scientist",
        company="Replit",
        title="Data Scientist",
        location="Foster City, CA",
        source="test",
        job_url="https://example.com",
        description="Developer productivity and applied AI role.",
        date_found=datetime.now(timezone.utc),
    )


def _score() -> JobScore:
    return JobScore(
        job_id="replit-data-scientist",
        fit_score=88,
        priority="apply",
        lane="AI Tooling",
        resume_version="ai_tooling_resume.md",
        reason_summary="Strong fit.",
        why_fit=["AI tooling overlap."],
        risks=["Missing Replit profile."],
        tailoring_notes=["Highlight AI tooling work."],
        referral_recommended=True,
    )


def test_generate_answer_for_known_company_question() -> None:
    requirement = generate_answer_for_field(
        RawFormField(label="Why Replit?", field_type="textarea", required=True),
        _profile(),
        _job(),
        _score(),
    )
    assert requirement.answer_confidence == "medium"
    assert "developer productivity" in (requirement.draft_answer or "").lower()


def test_build_readiness_marks_missing_assets() -> None:
    requirements = [
        generate_answer_for_field(RawFormField(label="LinkedIn", field_type="url", required=True), _profile(), _job(), _score()),
        generate_answer_for_field(
            RawFormField(label="Work Authorization", field_type="text", required=True), _profile(), _job(), _score()
        ),
    ]
    readiness = build_readiness(_job(), requirements)
    assert readiness.readiness_status == "needs_prep"
    assert "LinkedIn" in readiness.missing_assets


def test_build_readiness_treats_optional_missing_as_review() -> None:
    requirements = [
        generate_answer_for_field(RawFormField(label="LinkedIn", field_type="url", required=False), _profile(), _job(), _score()),
        generate_answer_for_field(
            RawFormField(label="Work Authorization", field_type="text", required=True), _profile(), _job(), _score()
        ),
    ]
    readiness = build_readiness(_job(), requirements)
    assert readiness.readiness_status == "needs_review"


def test_build_readiness_marks_required_unanswered_custom_questions() -> None:
    requirement = generate_answer_for_field(
        RawFormField(label="How many years of experience do you have working in Python?", field_type="number", required=True),
        _profile(),
        _job(),
        _score(),
    )

    readiness = build_readiness(_job(), [requirement])

    assert requirement.answer_confidence == "missing"
    assert readiness.readiness_status == "needs_prep"
    assert "How many years of experience do you have working in Python?" in readiness.missing_assets
    assert readiness.requirements[0].missing_input is True


def test_placeholder_contact_values_are_missing_required_prep() -> None:
    requirements = [
        generate_answer_for_field(RawFormField(label="Full Name", field_type="text", required=True), _placeholder_profile(), _job(), _score()),
        generate_answer_for_field(RawFormField(label="Email", field_type="email", required=True), _placeholder_profile(), _job(), _score()),
        generate_answer_for_field(RawFormField(label="Phone Number", field_type="tel", required=True), _placeholder_profile(), _job(), _score()),
    ]

    readiness = build_readiness(_job(), requirements)

    assert readiness.readiness_status == "needs_prep"
    assert readiness.missing_assets == ["Full Name", "Email", "Phone Number"]


def test_optional_preferred_name_does_not_create_missing_prep() -> None:
    requirement = generate_answer_for_field(
        RawFormField(label="Preferred Name (if applicable)", field_type="text", required=False),
        _placeholder_profile(),
        _job(),
        _score(),
    )

    readiness = build_readiness(_job(), [requirement])

    assert requirement.missing_input is False
    assert readiness.missing_assets == []
    assert readiness.readiness_status == "ready"


def test_voluntary_demographic_questions_do_not_block_readiness() -> None:
    requirement = generate_answer_for_field(
        RawFormField(label="Another Gender Identity", field_type="text", required=False),
        _profile(),
        _job(),
        _score(),
    )

    readiness = build_readiness(_job(), [requirement])

    assert requirement.missing_input is False
    assert readiness.missing_assets == []
    assert readiness.readiness_status == "ready"


def test_generate_answer_for_voluntary_demographics_uses_approved_defaults() -> None:
    gender = generate_answer_for_field(
        RawFormField(label="Gender", field_type="radio", required=False),
        _profile(),
        _job(),
        _score(),
    )
    race = generate_answer_for_field(
        RawFormField(label="Asian (Not Hispanic or Latino)", field_type="radio", required=False),
        _profile(),
        _job(),
        _score(),
    )

    assert gender.draft_answer == "Male"
    assert gender.answer_confidence == "high"
    assert race.draft_answer == "Asian"
    assert race.answer_confidence == "high"


def test_generate_answer_for_military_status_uses_approved_default() -> None:
    military = generate_answer_for_field(
        RawFormField(label="What is your military status?*", field_type="select", required=True),
        _profile(),
        _job(),
        _score(),
    )

    assert military.draft_answer == "I am not a protected veteran"
    assert military.answer_confidence == "high"


def test_generate_answer_for_optional_narrative_fields_by_default() -> None:
    additional_info = generate_answer_for_field(
        RawFormField(label="Additional Information", field_type="textarea", required=False),
        _profile(),
        _job(),
        _score(),
    )
    cover_letter = generate_answer_for_field(
        RawFormField(label="Cover Letter", field_type="textarea", required=False),
        _profile(),
        _job(),
        _score(),
    )

    assert additional_info.answer_confidence == "medium"
    assert "Replit" in (additional_info.draft_answer or "")
    assert cover_letter.answer_confidence == "medium"
    assert "Hi Replit team" in (cover_letter.draft_answer or "")


def test_generate_answer_for_sponsorship_question_flags_review() -> None:
    requirement = generate_answer_for_field(
        RawFormField(label="Will you now or in the future require sponsorship?", field_type="select", required=True),
        _profile(),
        _job(),
        _score(),
    )
    assert requirement.answer_confidence == "high"
    assert requirement.notes == "Legal answer. Review before submission."
    readiness = build_readiness(_job(), [requirement])
    assert readiness.readiness_status == "needs_review"


def test_generate_answer_for_openai_location_and_start_date_questions() -> None:
    location = generate_answer_for_field(
        RawFormField(label="Where are you currently located?", field_type="input", required=True),
        _profile(),
        _job(),
        _score(),
    )
    start_date = generate_answer_for_field(
        RawFormField(label="When can you start a new role?", field_type="text", required=True),
        _profile(),
        _job(),
        _score(),
    )
    date_placeholder = generate_answer_for_field(
        RawFormField(label="Pick date...", field_type="text", required=True),
        _profile(),
        _job(),
        _score(),
    )
    location_placeholder = generate_answer_for_field(
        RawFormField(label="Start typing...", field_type="input", required=False),
        _profile(),
        _job(),
        _score(),
    )

    assert location.draft_answer == "San Francisco, CA. Open to moving."
    assert location.answer_confidence == "high"
    assert start_date.draft_answer == "Next month."
    assert start_date.answer_confidence == "high"
    assert date_placeholder.draft_answer == "Next month."
    assert date_placeholder.answer_confidence == "high"
    assert location_placeholder.draft_answer == "San Francisco, CA. Open to moving."
    assert location_placeholder.answer_confidence == "high"


def test_generate_answer_for_three_day_office_question_flags_review() -> None:
    requirement = generate_answer_for_field(
        RawFormField(label="Are you able to work from our US office three days per week?", field_type="button", required=True),
        _profile(),
        _job(),
        _score(),
    )

    assert requirement.draft_answer == "Yes."
    assert requirement.answer_confidence == "high"
    assert requirement.notes == "Location commitment. Review before submission."
    readiness = build_readiness(_job(), [requirement])
    assert readiness.readiness_status == "needs_review"


def test_generate_answer_for_greenhouse_common_custom_questions() -> None:
    profile = _profile()
    job = _job().model_copy(update={"company": "Anthropic"})

    country = generate_answer_for_field(
        RawFormField(label="Country", field_type="custom_select", required=False),
        profile,
        job,
        _score(),
    )
    office = generate_answer_for_field(
        RawFormField(
            label="Are you open to working in-person in one of our offices 25% of the time?",
            field_type="custom_select",
            required=True,
        ),
        profile,
        job,
        _score(),
    )
    ai_policy = generate_answer_for_field(
        RawFormField(label="AI Policy for Application", field_type="custom_select", required=True),
        profile,
        job,
        _score(),
    )
    start = generate_answer_for_field(
        RawFormField(label="When is the earliest you would want to start working with us?", field_type="text", required=False),
        profile,
        job,
        _score(),
    )
    timeline = generate_answer_for_field(
        RawFormField(
            label="Do you have any deadlines or timeline considerations we should be aware of?",
            field_type="text",
            required=False,
        ),
        profile,
        job,
        _score(),
    )
    interviewed = generate_answer_for_field(
        RawFormField(label="Have you ever interviewed at Anthropic before?", field_type="custom_select", required=True),
        profile,
        job,
        _score(),
    )
    hispanic = generate_answer_for_field(
        RawFormField(label="Are you Hispanic/Latino?", field_type="custom_select", required=False),
        profile,
        job,
        _score(),
    )

    assert country.draft_answer == "United States"
    assert office.draft_answer == "Yes."
    assert ai_policy.draft_answer == "Yes."
    assert start.draft_answer == "Next month."
    assert timeline.draft_answer == "No additional deadlines beyond my target start timing."
    assert interviewed.draft_answer == "No."
    assert interviewed.answer_confidence == "low"
    assert hispanic.draft_answer == "No"


def test_generate_answer_for_export_control_question_uses_trusted_profile() -> None:
    requirement = generate_answer_for_field(
        RawFormField(
            label="Export Control and Compliance: please list any countries of which you are a citizen or legal permanent resident, and when you obtained such status.",
            field_type="textarea",
            required=True,
        ),
        _profile(),
        _job(),
        _score(),
    )

    assert requirement.draft_answer == (
        "Citizen of Canada. I am not a U.S. legal permanent resident, so no permanent resident status date applies. "
        "I do have an approved I-140."
    )
    assert requirement.answer_confidence == "high"
    assert requirement.notes == "Legal answer derived from trusted profile facts. Review before submission."
