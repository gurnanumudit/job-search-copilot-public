from datetime import datetime, timezone

from job_agent.models import CandidatePreferences, CandidateProfile, JobPosting, RoleKeywordConfig
from job_agent.scoring import choose_lane, score_job


def _profile() -> CandidateProfile:
    return CandidateProfile(
        profile_bank_text="# Candidate\n",
        answer_bank_text="# Answers\n",
        preferences=CandidatePreferences.model_validate(
            {
                "candidate": {
                    "name": "Test User",
                    "email": "test@example.com",
                    "phone": "123",
                    "work_authorization": "authorized_us_requires_sponsorship",
                },
                "location_preferences": {
                    "preferred": ["San Francisco, CA", "Remote"],
                    "acceptable_hybrid": ["Foster City, CA"],
                    "avoid": ["Onsite outside California"],
                },
                "role_lanes": [],
                "job_priorities": {},
            }
        ),
    )


def _keywords() -> RoleKeywordConfig:
    return RoleKeywordConfig.model_validate(
        {
            "include_keywords": {
                "experimentation": ["experimentation", "causal inference"],
                "ai_tooling": ["agent", "developer productivity", "llm"],
                "platform": ["platform", "semantic metrics", "snowflake"],
            },
            "exclude_keywords": ["intern", "junior"],
        }
    )


def test_choose_lane_prefers_experimentation() -> None:
    job = JobPosting(
        job_id="job-1",
        company="Example",
        title="Senior Data Scientist, Experimentation",
        location="Remote",
        source="test",
        job_url="https://example.com",
        description="Lead experimentation, causal inference, and measurement science.",
        date_found=datetime.now(timezone.utc),
    )
    assert choose_lane(job, _keywords()) == "Experimentation"


def test_score_job_returns_apply_or_better_for_strong_fit() -> None:
    job = JobPosting(
        job_id="job-2",
        company="Example",
        title="Staff Data Scientist, Product Measurement",
        location="San Francisco, CA",
        remote_policy="Hybrid",
        source="test",
        job_url="https://example.com",
        description=(
            "Lead experimentation, semantic metrics, self-serve analytics, platform measurement, "
            "and AI-enabled workflow tooling."
        ),
        date_found=datetime.now(timezone.utc),
    )
    score = score_job(job, _profile(), _keywords())
    assert score.fit_score >= 75
    assert score.priority in {"apply", "must_apply"}


def test_score_job_marks_no_sponsorship_roles_as_not_relevant_for_h1b_profile() -> None:
    job = JobPosting(
        job_id="job-3",
        company="Example",
        title="Staff Data Scientist, Product Measurement",
        location="Remote",
        source="test",
        job_url="https://example.com",
        description=(
            "Lead experimentation and platform analytics. Candidates must be authorized to work in the United States "
            "without visa sponsorship. We will not sponsor employment visas."
        ),
        date_found=datetime.now(timezone.utc),
    )
    score = score_job(job, _profile(), _keywords())
    assert score.fit_score == 0
    assert score.priority == "skip"
    assert score.sponsorship_compatibility == "incompatible"
    assert score.referral_recommended is False
    assert "not relevant" in score.reason_summary.lower()


def test_score_job_does_not_match_intern_exclusion_inside_internal() -> None:
    job = JobPosting(
        job_id="job-4",
        company="Example",
        title="Senior Software Engineer, Internal Tools",
        location="Remote",
        source="test",
        job_url="https://example.com",
        description="Build internal tooling, developer productivity workflows, and AI platform automation.",
        date_found=datetime.now(timezone.utc),
    )

    score = score_job(job, _profile(), _keywords())

    assert "Exclusion keywords detected" not in " ".join(score.risks)
    assert score.fit_score >= 50


def test_data_scientist_title_is_prioritized_over_software_engineer_title() -> None:
    description = "Build experimentation, product measurement, analytics, platform metrics, and applied AI workflows."
    data_scientist = JobPosting(
        job_id="job-5",
        company="Example",
        title="Data Scientist, Safety",
        location="San Francisco, CA",
        remote_policy="Hybrid",
        source="test",
        job_url="https://example.com/ds",
        description=description,
        date_found=datetime.now(timezone.utc),
    )
    software_engineer = data_scientist.model_copy(
        update={
            "job_id": "job-6",
            "title": "Senior Software Engineer, Infrastructure",
            "job_url": "https://example.com/swe",
        }
    )

    data_score = score_job(data_scientist, _profile(), _keywords())
    software_score = score_job(software_engineer, _profile(), _keywords())

    assert data_score.fit_score > software_score.fit_score
    assert data_score.priority in {"maybe", "apply", "must_apply"}
    assert any("Data Scientist" in reason for reason in data_score.why_fit)
    assert any("software-engineering oriented" in risk for risk in software_score.risks)


def test_data_scientist_without_senior_title_is_not_skipped_when_role_matches() -> None:
    job = JobPosting(
        job_id="job-7",
        company="OpenAI",
        title="Data Scientist, Core Experimentation",
        location="San Francisco, CA",
        remote_policy="Hybrid",
        source="test",
        job_url="https://example.com/openai-ds",
        description="Own experimentation, causal inference, measurement, product analytics, and platform metrics.",
        date_found=datetime.now(timezone.utc),
    )

    score = score_job(job, _profile(), _keywords())

    assert score.fit_score >= 60
    assert score.priority in {"maybe", "apply", "must_apply"}
