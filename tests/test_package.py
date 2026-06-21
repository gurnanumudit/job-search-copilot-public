from datetime import datetime, timezone

from job_agent.cli import handle_package
from job_agent.models import ApplicationReadiness, ApplicationRequirement, JobPosting, JobScore
from job_agent.package_builder import build_package_report
from job_agent.paths import ProjectPaths
from job_agent.storage import save_job, save_readiness, save_score


def _job() -> JobPosting:
    return JobPosting(
        job_id="replit-data-scientist",
        company="Replit",
        title="Data Scientist",
        location="Foster City, CA",
        source="test",
        job_url="https://example.com/jobs/replit-ds",
        apply_url=None,
        description="Developer productivity, experimentation, and applied AI.",
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
        why_fit=["Strong experimentation overlap.", "Developer productivity alignment."],
        risks=["Hybrid expectation."],
        tailoring_notes=["Highlight AI tooling work early.", "Keep experimentation wins visible."],
        referral_recommended=True,
        sponsorship_compatibility="unknown",
        disqualifying_reasons=[],
    )


def _readiness() -> ApplicationReadiness:
    return ApplicationReadiness(
        job_id="replit-data-scientist",
        readiness_status="needs_review",
        application_difficulty="easy",
        required_assets=["Resume"],
        missing_assets=["LinkedIn"],
        estimated_time_minutes=12,
        requirements=[
            ApplicationRequirement(
                job_id="replit-data-scientist",
                field_label="LinkedIn",
                field_type="url",
                required=True,
                draft_answer="Missing",
                answer_confidence="missing",
                missing_input=True,
                notes="Add profile URL.",
            )
        ],
    )


def test_build_package_report_creates_application_packet(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    for relative_path, content in [
        (
            "profile/preferences.yaml",
            (
                'candidate:\n'
                '  name: "Example Candidate"\n'
                '  linkedin: "https://www.linkedin.com/in/example-candidate"\n'
                '  citizenship_countries: ["Canada"]\n'
                '  us_legal_permanent_resident: false\n'
                '  approved_i140: true\n'
            ),
        ),
        ("profile/answer_bank.md", "# Answer Bank\n\n## Salary Expectations\nAnswer: Flexible depending on scope.\n"),
        ("profile/ai_tooling_resume.md", "# AI Tooling Resume\n\n- Built internal tooling.\n"),
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    generated = build_package_report(paths, _job(), _score(), _profile(paths), _readiness())
    application_dir = generated.application_dir

    assert (application_dir / "report.md").exists()
    assert (application_dir / "Example_Candidate_Resume.md").exists()
    assert (application_dir / "application_answers.md").exists()
    assert (application_dir / "additional_information.md").exists()
    assert (application_dir / "cover_letter.md").exists()
    assert (application_dir / "Example_Candidate_Cover_Letter.pdf").exists()
    assert (application_dir / "application_plan.json").exists()
    assert (application_dir / "referral_message.md").exists()
    assert (application_dir / "tailoring_notes.md").exists()
    assert (application_dir / "resume_provenance.md").exists()
    assert generated.final_resume_pdf_path == tmp_path / "resumes" / "Replit" / "Example_Candidate_Resume.pdf"
    assert generated.additional_information_path == application_dir / "additional_information.md"
    assert generated.cover_letter_path == application_dir / "cover_letter.md"
    assert generated.cover_letter_pdf_path == application_dir / "Example_Candidate_Cover_Letter.pdf"
    assert generated.application_plan_path == application_dir / "application_plan.json"
    assert generated.final_resume_pdf_path.exists()
    assert (tmp_path / "resumes" / "Replit" / "Example_Candidate_Resume_Source.md").exists()
    report_text = (application_dir / "report.md").read_text(encoding="utf-8")
    assert "Base Resume Used: AI Tooling" in report_text
    assert "Resume Provenance Path:" in report_text
    assert "Final Resume PDF Path:" in report_text
    assert "Cover Letter PDF Path:" in report_text
    assert "Application Plan Path:" in report_text
    assert "## Suggested Next Action" in report_text
    assert "Tailored Resume Path:" in report_text
    assert "| Field | Required | Draft Answer | Confidence | Missing | Notes |" in report_text
    assert "## Missing Prep" in report_text
    provenance_text = (application_dir / "resume_provenance.md").read_text(encoding="utf-8")
    assert "Base Resume Version: ai_tooling_resume.md" in provenance_text
    assert "Generated Resume Path:" in provenance_text
    assert "Final Resume PDF Path:" in provenance_text
    assert "## LinkedIn" in (application_dir / "application_answers.md").read_text(encoding="utf-8")
    assert "developer productivity" in (application_dir / "additional_information.md").read_text(encoding="utf-8").lower()
    plan_text = (application_dir / "application_plan.json").read_text(encoding="utf-8")
    assert '"submit_requires_explicit_user_approval": true' in plan_text
    assert '"field": "LinkedIn"' in plan_text
    assert '"value": "https://www.linkedin.com/in/example-candidate"' in plan_text
    assert '"answer_bank"' in plan_text
    assert '"salary_expectations": "Flexible depending on scope."' in plan_text
    assert '"application_source": "LinkedIn"' in plan_text
    assert '"export_control": "Citizen of Canada. I am not a U.S. legal permanent resident, so no permanent resident status date applies. I do have an approved I-140."' in plan_text
    assert '"cover_letter_pdf"' in plan_text


def _profile(paths: ProjectPaths):
    from job_agent.profile import load_profile

    return load_profile(paths)


def test_handle_package_accepts_job_file_input(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    job_file = tmp_path / "incoming_job.md"
    job_file.write_text(
        "\n".join(
            [
                "# Replit - Data Scientist",
                "",
                "Company: Replit",
                "Title: Data Scientist",
                "Location: Foster City, CA",
                "Job URL: https://example.com/jobs/replit-ds",
                "",
                "## Description",
                "Developer productivity, experimentation, and applied AI.",
            ]
        ),
        encoding="utf-8",
    )
    for relative_path, content in [
        (
            "profile/preferences.yaml",
            "\n".join(
                [
                    "candidate:",
                    '  name: "Example Candidate"',
                    '  work_authorization: "authorized_us_requires_sponsorship"',
                    "",
                    "location_preferences:",
                    "  preferred: ['Remote']",
                    "",
                    "role_lanes: []",
                    "job_priorities: {}",
                ]
            ),
        ),
        ("profile/profile_bank.md", "# Candidate\n"),
        ("profile/answer_bank.md", "# Answer Bank\n"),
        ("profile/main_resume.md", "# Main Resume\n"),
        ("profile/experimentation_resume.md", "# Experimentation Resume\n"),
        ("profile/ai_tooling_resume.md", "# AI Tooling Resume\n"),
        (
            "sources/role_keywords.yaml",
            "\n".join(
                [
                    "include_keywords:",
                    "  experimentation: ['experimentation']",
                    "  ai_tooling: ['developer productivity', 'ai']",
                    "  platform: []",
                    "exclude_keywords: []",
                ]
            ),
        ),
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    message = handle_package(paths, file_path=str(job_file))

    assert "Generated application packet" in message
    assert (tmp_path / "outputs" / "applications" / "replit_data_scientist" / "report.md").exists()


def test_build_package_report_does_not_modify_base_resume(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    base_resume_path = tmp_path / "profile" / "ai_tooling_resume.md"
    base_resume_path.parent.mkdir(parents=True, exist_ok=True)
    original_resume = "# AI Tooling Resume\n\n- Built internal tooling.\n"
    base_resume_path.write_text(original_resume, encoding="utf-8")
    for relative_path, content in [
        ("profile/preferences.yaml", 'candidate:\n  name: "Example Candidate"\n'),
        ("profile/profile_bank.md", "# Candidate\n"),
        ("profile/answer_bank.md", "# Answers\n"),
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path != base_resume_path:
            path.write_text(content, encoding="utf-8")

    build_package_report(paths, _job(), _score(), _profile(paths), _readiness())

    assert base_resume_path.read_text(encoding="utf-8") == original_resume


def test_build_package_report_preserves_reviewed_narrative_drafts(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    application_dir = tmp_path / "outputs" / "applications" / "replit_data_scientist"
    application_dir.mkdir(parents=True, exist_ok=True)
    reviewed_text = "# Additional Information Draft\n\nReviewed user text.\n"
    (application_dir / "additional_information.md").write_text(reviewed_text, encoding="utf-8")
    for relative_path, content in [
        ("profile/preferences.yaml", 'candidate:\n  name: "Example Candidate"\n'),
        ("profile/profile_bank.md", "# Candidate\n"),
        ("profile/answer_bank.md", "# Answers\n"),
        ("profile/ai_tooling_resume.md", "# AI Tooling Resume\n"),
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    build_package_report(paths, _job(), _score(), _profile(paths), _readiness())

    assert (application_dir / "additional_information.md").read_text(encoding="utf-8") == reviewed_text
