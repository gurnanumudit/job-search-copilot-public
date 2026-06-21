from datetime import datetime, timezone

from job_agent.exporters import export_application_requirements, export_ranked_jobs, export_shortlist
from job_agent.package_builder import build_package_report
from job_agent.models import ApplicationReadiness, ApplicationRequirement, JobPosting, JobScore
from job_agent.paths import ProjectPaths
from job_agent.profile import load_profile
from job_agent.storage import save_readiness


def test_export_writes_csvs(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "preferences.yaml").write_text('candidate:\n  name: "Example Candidate"\n', encoding="utf-8")
    (tmp_path / "profile" / "main_resume.md").write_text("# Main Resume\n", encoding="utf-8")
    (tmp_path / "profile" / "profile_bank.md").write_text("# Candidate\n", encoding="utf-8")
    (tmp_path / "profile" / "answer_bank.md").write_text("# Answers\n", encoding="utf-8")
    job = JobPosting(
        job_id="job-1",
        company="Example",
        title="Staff Data Scientist",
        location="Remote",
        source="test",
        job_url="https://example.com",
        description="Desc",
        date_found=datetime.now(timezone.utc),
    )
    score = JobScore(
        job_id="job-1",
        fit_score=80,
        priority="apply",
        lane="Main",
        resume_version="main_resume.md",
        reason_summary="Good fit.",
        why_fit=["Fit"],
        risks=["Risk"],
        tailoring_notes=["Note"],
        referral_recommended=True,
        sponsorship_compatibility="incompatible",
        disqualifying_reasons=["Posting states sponsorship is not supported."],
    )
    readiness = ApplicationReadiness(
        job_id="job-1",
        readiness_status="needs_review",
        application_difficulty="easy",
        required_assets=["Resume"],
        missing_assets=[],
        estimated_time_minutes=10,
        requirements=[
            ApplicationRequirement(
                job_id="job-1",
                field_label="Resume",
                field_type="file",
                required=True,
                draft_answer="Use main_resume.md",
                answer_confidence="high",
                missing_input=False,
            )
        ],
    )
    build_package_report(paths, job, score, load_profile(paths), readiness)
    export_ranked_jobs(paths, [job], {score.job_id: score}, {readiness.job_id: readiness})
    export_application_requirements(paths, {job.job_id: job}, [readiness])
    ranked_jobs_csv = paths.ranked_jobs_csv.read_text(encoding="utf-8")
    assert "fit_score" in ranked_jobs_csv
    assert "sponsorship_compatibility" in ranked_jobs_csv
    assert "incompatible" in ranked_jobs_csv
    assert "not_relevant_no_sponsorship" in ranked_jobs_csv
    assert "readiness_status" in ranked_jobs_csv
    assert "needs_review" in ranked_jobs_csv
    assert "tailored_resume_path" in ranked_jobs_csv
    assert "application_packet_path" in ranked_jobs_csv
    assert "Skip" in ranked_jobs_csv
    application_requirements_csv = paths.application_requirements_csv.read_text(encoding="utf-8")
    assert "Resume" in application_requirements_csv
    assert "confidence" in application_requirements_csv


def test_export_ignores_stale_readiness_for_jobs_without_apply_url(tmp_path) -> None:
    from job_agent.cli import handle_export
    from job_agent.storage import save_job, save_score

    paths = ProjectPaths(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    job = JobPosting(
        job_id="job-2",
        company="Example",
        title="Staff Data Scientist",
        location="Remote",
        source="test",
        job_url="https://example.com",
        apply_url=None,
        description="Desc",
        date_found=datetime.now(timezone.utc),
    )
    score = JobScore(
        job_id="job-2",
        fit_score=80,
        priority="apply",
        lane="Main",
        resume_version="main_resume.md",
        reason_summary="Good fit.",
        why_fit=["Fit"],
        risks=["Risk"],
        tailoring_notes=["Note"],
        referral_recommended=True,
        sponsorship_compatibility="unknown",
        disqualifying_reasons=[],
    )
    readiness = ApplicationReadiness(
        job_id="job-2",
        readiness_status="ready",
        application_difficulty="easy",
        required_assets=[],
        missing_assets=[],
        estimated_time_minutes=10,
        requirements=[],
    )
    save_job(paths, job)
    save_score(paths, score)
    save_readiness(paths, readiness)

    handle_export(paths)

    ranked_jobs_csv = paths.ranked_jobs_csv.read_text(encoding="utf-8")
    assert "not_inspected" in ranked_jobs_csv


def test_export_shortlist_writes_actionable_markdown(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    job = JobPosting(
        job_id="job-3",
        company="Example",
        title="Applied AI Engineer",
        location="Remote",
        source="test",
        job_url="https://example.com/jobs/applied-ai",
        apply_url="https://example.com/jobs/applied-ai/apply",
        description="Build AI tooling and customer-facing workflows.",
        date_found=datetime.now(timezone.utc),
    )
    score = JobScore(
        job_id="job-3",
        fit_score=82,
        priority="apply",
        lane="AI Tooling",
        resume_version="ai_tooling_resume.md",
        reason_summary="Strong AI tooling alignment.",
        why_fit=["AI tooling"],
        risks=[],
        tailoring_notes=[],
        referral_recommended=False,
        sponsorship_compatibility="unknown",
        disqualifying_reasons=[],
    )

    export_shortlist(
        paths,
        [job],
        {score.job_id: score},
        source_health_by_company={"Example": "workflow_ready"},
    )

    shortlist = paths.shortlist_md.read_text(encoding="utf-8")
    assert "Applied AI Engineer" in shortlist
    assert "workflow_ready" in shortlist
    assert "Inspect application" in shortlist
    assert "Strong AI tooling alignment." in shortlist


def test_handle_shortlist_uses_current_ranked_csv_not_stored_history(tmp_path) -> None:
    from job_agent.cli import handle_shortlist
    from job_agent.storage import save_job, save_score

    paths = ProjectPaths(tmp_path)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    stale_job = JobPosting(
        job_id="stale-job",
        company="StaleCo",
        title="Staff Data Scientist",
        location="Remote",
        source="test",
        job_url="https://stale.example.com",
        description="A stale historical job.",
        date_found=datetime.now(timezone.utc),
    )
    stale_score = JobScore(
        job_id="stale-job",
        fit_score=90,
        priority="must_apply",
        lane="Main",
        resume_version="main_resume.md",
        reason_summary="This should not appear.",
        why_fit=[],
        risks=[],
        tailoring_notes=[],
        referral_recommended=False,
        sponsorship_compatibility="unknown",
        disqualifying_reasons=[],
    )
    save_job(paths, stale_job)
    save_score(paths, stale_score)
    paths.ranked_jobs_csv.write_text(
        "\n".join(
            [
                "company,role,fit_score,priority,lane,sponsorship_compatibility,action,apply_url,job_url,notes",
                "CurrentCo,Applied AI Engineer,70,apply,AI Tooling,unknown,Inspect application,https://current.example.com/apply,https://current.example.com,Current role.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    handle_shortlist(paths)

    shortlist = paths.shortlist_md.read_text(encoding="utf-8")
    assert "CurrentCo" in shortlist
    assert "StaleCo" not in shortlist


def test_handle_export_latest_scope_uses_latest_discovery_manifest(tmp_path) -> None:
    from job_agent.cli import handle_export
    from job_agent.storage import save_job, save_score

    paths = ProjectPaths(tmp_path)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        JobPosting(
            job_id="current-job",
            company="CurrentCo",
            title="Applied AI Engineer",
            location="Remote",
            source="test",
            job_url="https://current.example.com",
            description="Current discovery job.",
            date_found=datetime.now(timezone.utc),
        ),
        JobPosting(
            job_id="stale-job",
            company="StaleCo",
            title="Staff Data Scientist",
            location="Remote",
            source="test",
            job_url="https://stale.example.com",
            description="Stale historical job.",
            date_found=datetime.now(timezone.utc),
        ),
    ]
    for job in jobs:
        save_job(paths, job)
        save_score(
            paths,
            JobScore(
                job_id=job.job_id,
                fit_score=70,
                priority="apply",
                lane="AI Tooling",
                resume_version="ai_tooling_resume.md",
                reason_summary=f"{job.company} current summary.",
                why_fit=[],
                risks=[],
                tailoring_notes=[],
                referral_recommended=False,
                sponsorship_compatibility="unknown",
                disqualifying_reasons=[],
            ),
        )
    paths.latest_discovery_job_ids.write_text("current-job\n", encoding="utf-8")

    handle_export(paths)

    ranked_jobs = paths.ranked_jobs_csv.read_text(encoding="utf-8")
    assert "CurrentCo" in ranked_jobs
    assert "StaleCo" not in ranked_jobs
