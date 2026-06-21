from datetime import datetime, timezone

import pytest

from job_agent.inspector import inspect_application_form_sync, normalize_fields
from job_agent.models import CandidatePreferences, CandidateProfile, JobPosting, JobScore
from job_agent.paths import ProjectPaths


def test_normalize_fields_deduplicates_and_keeps_required() -> None:
    fields = normalize_fields(
        [
            {"label": "Email Address", "field_type": "email", "required": True, "options": []},
            {"label": "Email Address", "field_type": "email", "required": True, "options": []},
            {"label": "Why Replit?", "field_type": "textarea", "required": False, "options": []},
        ]
    )
    assert len(fields) == 2
    assert fields[0].required is True


def _profile() -> CandidateProfile:
    return CandidateProfile(
        profile_bank_text="# Profile\n",
        answer_bank_text="# Answer Bank\n\n## Location\nAnswer: San Francisco, CA\n",
        preferences=CandidatePreferences.model_validate(
            {
                "candidate": {
                    "name": "Example Candidate",
                    "email": "candidate@example.com",
                    "phone": "+1-555-555-5555",
                    "current_location": "San Francisco, CA",
                    "willing_sf_hybrid_3_days_per_week": True,
                },
                "location_preferences": {"preferred": ["San Francisco, CA"]},
                "role_lanes": [],
                "job_priorities": {},
            }
        ),
    )


def _score() -> JobScore:
    return JobScore(
        job_id="example-data-scientist",
        fit_score=88,
        priority="apply",
        lane="Experimentation",
        resume_version="experimentation_resume.md",
        reason_summary="Strong fit.",
        why_fit=["Experimentation overlap."],
        risks=[],
        tailoring_notes=[],
        referral_recommended=False,
    )


def test_inspect_application_form_detects_grouped_and_custom_optional_fields(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "application.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="email">Email</label>
              <input id="email" type="email" required />
              <fieldset>
                <legend>Are you willing to work from the San Francisco office three days per week?</legend>
                <label><input type="radio" name="sf-office" value="Yes" /> Yes</label>
                <label><input type="radio" name="sf-office" value="No" /> No</label>
              </fieldset>
              <fieldset>
                <legend>Preferred work samples</legend>
                <label><input type="checkbox" name="samples" value="Portfolio" /> Portfolio</label>
                <label><input type="checkbox" name="samples" value="GitHub" /> GitHub</label>
              </fieldset>
              <label id="office-label">Preferred office</label>
              <button type="button" role="combobox" aria-labelledby="office-label" aria-controls="office-options">Select</button>
              <ul id="office-options">
                <li role="option">San Francisco, CA</li>
                <li role="option">Seattle, WA</li>
              </ul>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    job = JobPosting(
        job_id="example-data-scientist",
        company="Example",
        title="Data Scientist",
        source="test",
        job_url=form_path.resolve().as_uri(),
        apply_url=form_path.resolve().as_uri(),
        description="Experimentation role.",
        date_found=datetime.now(timezone.utc),
    )

    result = inspect_application_form_sync(ProjectPaths(tmp_path), job, _profile(), _score())
    fields = {requirement.field_label: requirement for requirement in result.readiness.requirements}

    assert "Are you willing to work from the San Francisco office three days per week?" in fields
    assert fields["Are you willing to work from the San Francisco office three days per week?"].field_type == "radio"
    assert fields["Are you willing to work from the San Francisco office three days per week?"].detected_options == ["Yes", "No"]
    assert "Preferred work samples" in fields
    assert fields["Preferred work samples"].field_type == "checkbox"
    assert fields["Preferred work samples"].detected_options == ["Portfolio", "GitHub"]
    assert "Preferred office" in fields
    assert fields["Preferred office"].field_type == "custom_select"
    assert fields["Preferred office"].detected_options == ["San Francisco, CA", "Seattle, WA"]
