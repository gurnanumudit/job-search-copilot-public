import json

from job_agent import cli
from job_agent.apply_qa import (
    collect_apply_gap_items,
    collect_apply_qa_rows,
    export_apply_gap_backlog,
    export_apply_qa,
    parse_status_filter,
    run_apply_batch,
)
from job_agent.paths import ProjectPaths


def _write_packet(tmp_path, slug: str, *, review: dict | None = None) -> None:
    application_dir = tmp_path / "outputs" / "applications" / slug
    application_dir.mkdir(parents=True)
    plan = {
        "job": {
            "company": "ExampleCo",
            "title": slug.replace("_", " ").title(),
            "job_id": slug,
            "apply_url": f"https://jobs.ashbyhq.com/example/{slug}/application",
            "ats": "ashby",
        },
        "artifacts": {},
        "fill_fields": [],
        "detected_requirements": [],
    }
    (application_dir / "application_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    if review is not None:
        (application_dir / "apply_review.json").write_text(json.dumps(review), encoding="utf-8")


def test_collect_apply_qa_rows_classifies_packets(tmp_path) -> None:
    _write_packet(tmp_path, "not_run")
    _write_packet(
        tmp_path,
        "ready_role",
        review={
            "resolved_ats": "ashby",
            "status": "ready_for_review",
            "completed_actions": ["Filled email"],
            "metrics": {
                "detected_fields_total": 2,
                "detected_fields_filled": 1,
                "detected_fields_skipped": 1,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 0,
                "optional_no_value_fields": 1,
            },
            "field_fill_report": [
                {"field": "Why us?", "status": "filled", "reason": "filled detected text field"},
                {
                    "field": "Certification",
                    "status": "skipped",
                    "reason": "legal acknowledgement left for explicit review",
                },
            ],
            "unfilled_fields": [],
            "optional_no_value_fields": [{"label": "Portfolio", "answer_key": "portfolio"}],
        },
    )
    _write_packet(
        tmp_path,
        "needs_runner",
        review={
            "resolved_ats": "ashby",
            "status": "ready_for_review",
            "completed_actions": ["Filled email"],
            "metrics": {
                "detected_fields_total": 1,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 1,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 0,
            },
            "field_fill_report": [
                {"field": "Why us?", "status": "skipped", "reason": "matching field was not found on page"}
            ],
            "unfilled_fields": [],
        },
    )
    _write_packet(
        tmp_path,
        "missing_resume",
        review={
            "resolved_ats": "ashby",
            "status": "ready_for_review",
            "completed_actions": ["Filled email"],
            "file_upload_report": {"status": "missing_input"},
            "metrics": {
                "detected_fields_total": 0,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 0,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 0,
                "resume_upload_verified": 0,
                "resume_upload_missing": 1,
            },
            "field_fill_report": [],
            "unfilled_fields": [],
        },
    )
    _write_packet(
        tmp_path,
        "optional_empty",
        review={
            "resolved_ats": "greenhouse",
            "status": "ready_for_review",
            "completed_actions": ["Filled email", "Uploaded resume"],
            "file_upload_report": {"status": "uploaded"},
            "metrics": {
                "detected_fields_total": 0,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 0,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 1,
                "unfilled_optional_or_unknown_visible_fields": 1,
                "resume_upload_verified": 1,
                "resume_upload_missing": 0,
            },
            "field_fill_report": [],
            "unfilled_fields": [{"label": "Portfolio", "type": "url", "required": False}],
            "answer_gap_recommendations": [
                {
                    "source": "unfilled_field",
                    "field": "Portfolio",
                    "suggested_answer_key": "portfolio",
                    "reason": "mapped answer key has no configured answer",
                    "recommendation": "Add a reusable `portfolio` answer to profile preferences or profile/answer_bank.md so future forms can fill it automatically.",
                }
            ],
        },
    )
    _write_packet(
        tmp_path,
        "blocked_workday",
        review={
            "resolved_ats": "workday",
            "status": "blocked_account_required",
            "completed_actions": ["Clicked Workday start button: Apply"],
            "metrics": {
                "detected_fields_total": 0,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 0,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 0,
            },
            "field_fill_report": [],
            "unfilled_fields": [],
            "remaining_review_items": ["Workday sign-in step detected."],
        },
    )
    _write_packet(
        tmp_path,
        "blocked_captcha",
        review={
            "resolved_ats": "greenhouse",
            "status": "blocked_human_verification_required",
            "completed_actions": [],
            "metrics": {
                "detected_fields_total": 0,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 0,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 0,
            },
            "field_fill_report": [],
            "unfilled_fields": [],
            "remaining_review_items": ["Human verification or CAPTCHA step detected."],
        },
    )
    _write_packet(
        tmp_path,
        "blocked_submit",
        review={
            "resolved_ats": "ashby",
            "status": "blocked_submit_review_required",
            "completed_actions": ["Filled email"],
            "metrics": {
                "detected_fields_total": 0,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 0,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 1,
            },
            "field_fill_report": [],
            "unfilled_fields": [{"label": "Portfolio", "type": "url", "required": False}],
            "remaining_review_items": ["Submit blocked: Unfilled optional/unknown visible fields remain: Portfolio."],
        },
    )

    rows = collect_apply_qa_rows(ProjectPaths(tmp_path))
    by_job = {row["job_id"]: row for row in rows}

    assert by_job["not_run"]["status"] == "not_run"
    assert by_job["ready_role"]["status"] == "ready_for_final_review"
    assert by_job["ready_role"]["detected_fields_filled"] == "1"
    assert by_job["ready_role"]["optional_no_value_fields"] == "1"
    assert by_job["needs_runner"]["status"] == "ready_for_final_review"
    assert by_job["needs_runner"]["actionable_skips"] == "0"
    assert by_job["missing_resume"]["status"] == "needs_manual_field_review"
    assert by_job["missing_resume"]["resume_upload_status"] == "missing_input"
    assert by_job["missing_resume"]["next_action"] == "Review unfilled required fields or resume upload"
    assert by_job["optional_empty"]["status"] == "needs_optional_field_review"
    assert by_job["optional_empty"]["unfilled_visible_fields"] == "1"
    assert by_job["optional_empty"]["next_action"] == "Review optional empty fields or add answer-bank defaults"
    assert by_job["optional_empty"]["answer_gap_count"] == "1"
    assert by_job["optional_empty"]["answer_gap_sources"] == "unfilled_field"
    assert "Portfolio: Add a reusable" in by_job["optional_empty"]["top_answer_gap"]
    assert by_job["blocked_workday"]["status"] == "blocked_account_required"
    assert by_job["blocked_workday"]["next_action"] == "Run with an authenticated browser session or add account-handling support"
    assert by_job["blocked_captcha"]["status"] == "blocked_human_verification_required"
    assert by_job["blocked_captcha"]["next_action"] == "Complete CAPTCHA/2FA in a browser session, then rerun apply review"
    assert by_job["blocked_submit"]["status"] == "blocked_submit_review_required"
    assert by_job["blocked_submit"]["next_action"] == "Resolve review blockers, then rerun submit with explicit approval"


def test_export_apply_qa_writes_csv_and_markdown(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    _write_packet(tmp_path, "not_run")
    _write_packet(
        tmp_path,
        "gap_role",
        review={
            "resolved_ats": "lever",
            "status": "ready_for_review",
            "completed_actions": ["Filled email"],
            "metrics": {
                "detected_fields_total": 0,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 0,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 1,
            },
            "field_fill_report": [],
            "unfilled_fields": [{"label": "Portfolio", "type": "url", "required": False}],
            "answer_gap_recommendations": [
                {
                    "source": "unfilled_field",
                    "field": "Portfolio",
                    "suggested_answer_key": "portfolio",
                    "reason": "mapped answer key has no configured answer",
                    "recommendation": "Add a reusable `portfolio` answer to profile preferences.",
                }
            ],
        },
    )

    csv_path, md_path = export_apply_qa(paths)

    csv_text = csv_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")
    assert "job_id,ats,status" in csv_text
    assert "answer_gap_count" in csv_text
    assert "optional_no_value_fields" in csv_text
    assert "not_run" in csv_text
    assert "# Apply Runner QA" in md_text
    assert "Application packets: 2" in md_text
    assert "Policy Blank" in md_text
    assert "## Answer Gap Recommendations" in md_text
    assert "Portfolio: Add a reusable `portfolio` answer to profile preferences." in md_text
    assert (tmp_path / "outputs" / "apply_gap_backlog.json").exists()
    assert (tmp_path / "outputs" / "apply_gap_backlog.md").exists()


def test_export_apply_gap_backlog_groups_reusable_answer_fixes(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    for slug, field in [("portfolio_one", "Portfolio"), ("portfolio_two", "Personal Website")]:
        _write_packet(
            tmp_path,
            slug,
            review={
                "resolved_ats": "greenhouse",
                "status": "ready_for_review",
                "completed_actions": ["Filled email"],
                "metrics": {
                    "detected_fields_total": 0,
                    "detected_fields_filled": 0,
                    "detected_fields_skipped": 0,
                    "unfilled_required_visible_fields": 0,
                    "unfilled_visible_fields": 1,
                },
                "field_fill_report": [],
                "unfilled_fields": [{"label": field, "type": "url", "required": False}],
                "answer_gap_recommendations": [
                    {
                        "source": "unfilled_field",
                        "field": field,
                        "suggested_answer_key": "portfolio",
                        "reason": "mapped answer key has no configured answer",
                        "recommendation": "Add a reusable `portfolio` answer to profile preferences.",
                    }
                ],
            },
        )

    json_path, md_path = export_apply_gap_backlog(paths)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    md_text = md_path.read_text(encoding="utf-8")
    gap_items = collect_apply_gap_items(paths)

    assert len(gap_items) == 2
    assert payload["total_gap_items"] == 2
    assert payload["backlog"][0]["category"] == "reusable_answer"
    assert payload["backlog"][0]["suggested_answer_key"] == "portfolio"
    assert payload["backlog"][0]["count"] == 2
    assert "## Prioritized Fixes" in md_text
    assert "| reusable_answer | 2 | portfolio |" in md_text


def test_parse_status_filter_defaults_to_not_run() -> None:
    assert parse_status_filter(None) == {"not_run"}
    assert parse_status_filter("not_run,invalid_review") == {"not_run", "invalid_review"}


def test_run_apply_batch_dry_run_selects_matching_packets(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    _write_packet(tmp_path, "not_run")

    result = run_apply_batch(paths, dry_run=True)

    assert result["dry_run"] is True
    assert result["candidates"] == 1
    assert result["completed"] == 0
    assert result["results"][0]["status"] == "would_run"
    assert (tmp_path / "outputs" / "apply_batch_report.md").exists()
    assert (tmp_path / "outputs" / "apply_batch_report.json").exists()


def test_run_apply_batch_runs_review_mode_and_refreshes_qa(monkeypatch, tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    _write_packet(tmp_path, "not_run")
    calls = {}

    def fake_run_apply(
        paths,
        *,
        job_id,
        ats,
        submit,
        headless,
        fill_legal_acknowledgements,
        storage_state_path,
        user_data_dir,
    ):
        calls.update(
            {
                "job_id": job_id,
                "ats": ats,
                "submit": submit,
                "headless": headless,
                "fill_legal_acknowledgements": fill_legal_acknowledgements,
                "storage_state_path": storage_state_path,
                "user_data_dir": user_data_dir,
            }
        )
        review_path = paths.outputs_applications_dir / "not_run" / "apply_review.json"
        review_path.write_text(
            json.dumps(
                {
                    "resolved_ats": "ashby",
                    "status": "ready_for_review",
                    "completed_actions": ["Filled email"],
                    "metrics": {
                        "detected_fields_total": 0,
                        "detected_fields_filled": 0,
                        "detected_fields_skipped": 0,
                        "unfilled_required_visible_fields": 0,
                        "unfilled_visible_fields": 0,
                    },
                    "field_fill_report": [],
                    "unfilled_fields": [],
                }
            ),
            encoding="utf-8",
        )
        return {"status": "ready_for_review"}

    monkeypatch.setattr("job_agent.apply_qa.run_apply", fake_run_apply)

    result = run_apply_batch(paths, statuses={"not_run"}, ats="auto", headless=True, storage_state_path="/tmp/state.json")

    assert result["completed"] == 1
    assert calls == {
        "job_id": "not_run",
        "ats": "auto",
        "submit": False,
        "headless": True,
        "fill_legal_acknowledgements": False,
        "storage_state_path": "/tmp/state.json",
        "user_data_dir": None,
    }
    qa_text = (tmp_path / "outputs" / "apply_review_qa.md").read_text(encoding="utf-8")
    assert "ready_for_final_review" in qa_text


def test_handle_apply_batch_delegates_with_safe_defaults(monkeypatch, tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    calls = {}

    def fake_run_apply_batch(paths, *, statuses, limit, ats, headless, dry_run, storage_state_path, user_data_dir):
        calls.update(
            {
                "paths": paths,
                "statuses": statuses,
                "limit": limit,
                "ats": ats,
                "headless": headless,
                "dry_run": dry_run,
                "storage_state_path": storage_state_path,
                "user_data_dir": user_data_dir,
            }
        )
        return {
            "candidates": 2,
            "completed": 0,
            "failed": 0,
            "batch_markdown": str(tmp_path / "outputs" / "apply_batch_report.md"),
        }

    monkeypatch.setattr(cli, "run_apply_batch", fake_run_apply_batch)

    message = cli.handle_apply_batch(
        paths,
        status_filter="not_run,invalid_review",
        limit=2,
        dry_run=True,
        user_data_dir="/tmp/profile",
    )

    assert "selected 2 packet" in message
    assert calls == {
        "paths": paths,
        "statuses": {"not_run", "invalid_review"},
        "limit": 2,
        "ats": "auto",
        "headless": True,
        "dry_run": True,
        "storage_state_path": None,
        "user_data_dir": "/tmp/profile",
    }


def test_handle_apply_gaps_exports_backlog(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    _write_packet(
        tmp_path,
        "gap_role",
        review={
            "resolved_ats": "ashby",
            "status": "ready_for_review",
            "completed_actions": ["Filled email"],
            "metrics": {
                "detected_fields_total": 0,
                "detected_fields_filled": 0,
                "detected_fields_skipped": 0,
                "unfilled_required_visible_fields": 0,
                "unfilled_visible_fields": 1,
            },
            "field_fill_report": [],
            "unfilled_fields": [{"label": "Portfolio", "type": "url", "required": False}],
            "answer_gap_recommendations": [
                {
                    "source": "unfilled_field",
                    "field": "Portfolio",
                    "suggested_answer_key": "portfolio",
                    "reason": "mapped answer key has no configured answer",
                    "recommendation": "Add a reusable `portfolio` answer to profile preferences.",
                }
            ],
        },
    )

    message = cli.handle_apply_gaps(paths)

    assert "apply_gap_backlog.json" in message
    assert (tmp_path / "outputs" / "apply_gap_backlog.json").exists()
    assert (tmp_path / "outputs" / "apply_gap_backlog.md").exists()
