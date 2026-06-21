import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from job_agent import cli
from job_agent.apply_runner import (
    application_dir_for_job,
    plan_value,
    render_review_json,
    render_review_summary,
    resolve_ats,
    review_metrics,
    run_apply,
)
from job_agent.paths import ProjectPaths


def _write_application_plan(
    tmp_path,
    *,
    apply_url="https://jobs.ashbyhq.com/openai/example/application",
    ats=None,
    detected_requirements=None,
    answer_bank=None,
):
    application_dir = tmp_path / "outputs" / "applications" / "openai_data_scientist"
    application_dir.mkdir(parents=True)

    resume_pdf = tmp_path / "Resume" / "OpenAI" / "Example_Candidate_Resume.pdf"
    resume_pdf.parent.mkdir(parents=True)
    resume_pdf.write_text("fake pdf bytes", encoding="utf-8")

    additional_information = application_dir / "additional_information.md"
    additional_information.write_text(
        "# Additional Information Draft\n\nI am excited about OpenAI and the chance to work with strong teams.",
        encoding="utf-8",
    )
    cover_letter = application_dir / "cover_letter.md"
    cover_letter.write_text(
        "# Cover Letter Draft\n\nI would be excited to bring my experimentation and AI tooling background to this team.",
        encoding="utf-8",
    )
    cover_letter_pdf = application_dir / "Example_Candidate_Cover_Letter.pdf"
    cover_letter_pdf.write_text("fake cover letter pdf bytes", encoding="utf-8")

    plan = {
        "job": {
            "job_id": "openai-data-scientist",
            "company": "OpenAI",
            "title": "Data Scientist",
            "apply_url": apply_url,
            "ats": ats,
        },
        "artifacts": {
            "resume_pdf": str(resume_pdf),
            "additional_information": str(additional_information),
            "cover_letter": str(cover_letter),
            "cover_letter_pdf": str(cover_letter_pdf),
        },
        "automation_policy": {
            "submit_requires_explicit_user_approval": True,
            "legal_acknowledgements_require_review": True,
        },
        "fill_fields": [
            {"field": "Legal Name", "value": "Example Candidate"},
            {"field": "Email", "value": "candidate@example.com"},
            {"field": "Phone Number", "value": "555-555-5555"},
            {"field": "LinkedIn", "value": "https://www.linkedin.com/in/example-candidate"},
            {"field": "Current Company", "value": "Example Company"},
            {"field": "Current Location", "value": "San Francisco, CA"},
            {"field": "Application Source", "value": "LinkedIn"},
            {"field": "Additional Information", "value_file": str(additional_information)},
            {"field": "Cover Letter", "value_file": str(cover_letter)},
        ],
        "answer_bank": answer_bank or {},
        "detected_requirements": detected_requirements or [],
    }
    (application_dir / "application_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return application_dir, plan


def test_application_dir_for_job_finds_matching_plan(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    application_dir, _ = _write_application_plan(tmp_path)

    assert application_dir_for_job(paths, "openai-data-scientist") == application_dir


def test_plan_value_reads_plain_values_and_markdown_value_files(tmp_path) -> None:
    _, plan = _write_application_plan(tmp_path)

    assert plan_value(plan, "Email") == "candidate@example.com"
    assert plan_value(plan, "Additional Information").startswith("I am excited about OpenAI")


def test_render_review_summary_includes_remaining_review_items(tmp_path) -> None:
    _, plan = _write_application_plan(tmp_path)
    summary = render_review_summary(
        plan,
        {
            "mode": "review_only",
            "status": "ready_for_review",
            "completed_actions": ["Uploaded resume"],
            "file_upload_report": {
                "status": "uploaded",
                "expected_file_name": "Example_Candidate_Resume.pdf",
                "artifact_path": str(tmp_path / "Resume" / "OpenAI" / "Example_Candidate_Resume.pdf"),
            },
            "remaining_review_items": ["Legal acknowledgements/certifications were left for user review."],
            "optional_no_value_fields": [{"label": "Portfolio", "answer_key": "portfolio"}],
            "screenshot_path": str(tmp_path / "review.png"),
        },
    )

    assert "Company: OpenAI" in summary
    assert "- Uploaded resume" in summary
    assert "Resume Upload: uploaded" in summary
    assert "Expected File: Example_Candidate_Resume.pdf" in summary
    assert "Detected Field Coverage: 0/0 filled" in summary
    assert "Unfilled Optional/Unknown Visible Fields: 0" in summary
    assert "Optional No-Value Policy Fields: 1" in summary
    assert "Legal acknowledgements/certifications were left for user review." in summary


def test_render_review_json_includes_machine_readable_metrics(tmp_path) -> None:
    _, plan = _write_application_plan(tmp_path)
    review = {
        "mode": "review_only",
        "status": "ready_for_review",
        "completed_actions": ["Uploaded resume"],
        "file_upload_report": {"status": "uploaded", "expected_file_name": "Example_Candidate_Resume.pdf"},
        "field_fill_report": [
            {"field": "Why us?", "status": "filled", "reason": "filled detected text field"},
            {"field": "Certification", "status": "skipped", "reason": "legal acknowledgement left for explicit review"},
        ],
        "unfilled_fields": [{"label": "Portfolio", "type": "url", "required": False}],
        "optional_no_value_fields": [{"label": "GitHub", "answer_key": "github"}],
        "remaining_review_items": [],
    }

    assert review_metrics(review) == {
        "completed_actions": 1,
        "detected_fields_total": 2,
        "detected_fields_filled": 1,
        "detected_fields_skipped": 1,
        "unfilled_visible_fields": 1,
        "unfilled_required_visible_fields": 0,
        "unfilled_optional_or_unknown_visible_fields": 1,
        "optional_no_value_fields": 1,
        "resume_upload_verified": 1,
        "resume_upload_missing": 0,
    }
    payload = json.loads(render_review_json(plan, review, "greenhouse"))
    assert payload["resolved_ats"] == "greenhouse"
    assert payload["metrics"]["detected_fields_filled"] == 1
    assert payload["file_upload_report"]["status"] == "uploaded"


def test_resolve_ats_infers_supported_platforms_from_plan_urls(tmp_path) -> None:
    _, greenhouse_plan = _write_application_plan(
        tmp_path / "greenhouse",
        apply_url="https://job-boards.greenhouse.io/example/jobs/123",
    )
    _, lever_plan = _write_application_plan(
        tmp_path / "lever",
        apply_url="https://jobs.lever.co/example/abc-123/apply",
    )
    _, workday_plan = _write_application_plan(
        tmp_path / "workday",
        apply_url="https://example.wd5.myworkdayjobs.com/en-US/example/job/data-scientist",
    )

    assert resolve_ats(greenhouse_plan, "auto") == "greenhouse"
    assert resolve_ats(lever_plan, "auto") == "lever"
    assert resolve_ats(workday_plan, "auto") == "workday"
    assert resolve_ats(greenhouse_plan, "ashby") == "ashby"
    assert resolve_ats({"job": {"apply_url": "https://careers.example.com/apply/123"}}, "auto") == "generic"


def test_run_apply_dispatches_to_inferred_greenhouse_runner(monkeypatch, tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url="https://job-boards.greenhouse.io/example/jobs/123")
    calls = {}

    def fake_apply_greenhouse(
        paths,
        application_dir,
        *,
        submit,
        headless,
        fill_legal_acknowledgements,
        storage_state_path,
        user_data_dir,
    ):
        calls.update(
            {
                "application_dir": application_dir,
                "submit": submit,
                "headless": headless,
                "fill_legal_acknowledgements": fill_legal_acknowledgements,
                "storage_state_path": storage_state_path,
                "user_data_dir": user_data_dir,
            }
        )
        return {
            "mode": "review_only",
            "status": "ready_for_review",
            "completed_actions": ["Filled email"],
            "remaining_review_items": [],
        }

    monkeypatch.setattr("job_agent.apply_runner.apply_greenhouse_sync", fake_apply_greenhouse)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", submit=False, headless=True)

    assert review["status"] == "ready_for_review"
    assert calls["submit"] is False
    assert calls["headless"] is True
    assert calls["storage_state_path"] is None
    assert calls["user_data_dir"] is None
    assert (calls["application_dir"] / "apply_review.md").exists()


def test_run_apply_fills_optional_detected_custom_fields_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "greenhouse.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <input id="first_name" />
              <input id="last_name" />
              <input id="email" type="email" />
              <input id="resume" name="resume" type="file" />
              <label for="why">Why do you want to work here?</label>
              <textarea id="why"></textarea>
              <label for="portfolio">Portfolio</label>
              <input id="portfolio" type="url" />
              <label for="certify">I certify that this application is accurate.</label>
              <input id="certify" type="checkbox" />
              <button type="submit">Submit Application</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats="greenhouse",
        detected_requirements=[
            {
                "field_label": "Why do you want to work here?",
                "field_type": "textarea",
                "required": False,
                "draft_answer": "Because the role maps to my experimentation and product data background.",
                "answer_confidence": "medium",
                "missing_input": False,
            },
            {
                "field_label": "I certify that this application is accurate.",
                "field_type": "checkbox",
                "required": True,
                "draft_answer": "Yes",
                "answer_confidence": "high",
                "missing_input": False,
            },
        ],
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)

    assert review["status"] == "ready_for_review"
    assert {
        "field": "Why do you want to work here?",
        "status": "filled",
        "reason": "filled detected text field",
    } in review["field_fill_report"]
    assert {
        "field": "I certify that this application is accurate.",
        "status": "skipped",
        "reason": "legal acknowledgement left for explicit review",
    } in review["field_fill_report"]
    assert {"label": "Portfolio", "type": "url", "required": False, "answer_key": "portfolio", "reason": "No configured truthful value; optional field left blank for final review."} in review["optional_no_value_fields"]
    assert {"label": "Portfolio", "type": "url", "required": False} not in review["unfilled_fields"]
    review_text = (
        tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.md"
    ).read_text(encoding="utf-8")
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )
    assert "## Field Fill Report" in review_text
    assert "## Optional Fields Left Blank By Policy" in review_text
    assert "Why do you want to work here?" in review_text
    assert "Portfolio" in review_text
    assert review_json["metrics"]["detected_fields_total"] == 2
    assert review_json["metrics"]["detected_fields_filled"] == 1
    assert review_json["metrics"]["unfilled_visible_fields"] == 0
    assert review_json["metrics"]["optional_no_value_fields"] == 1
    assert review_json["optional_no_value_fields"][0]["answer_key"] == "portfolio"


def test_run_apply_generic_fills_unknown_platform_form_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "custom.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="phone">Phone</label>
              <input id="phone" />
              <label for="linkedin">LinkedIn</label>
              <input id="linkedin" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="why">Why do you want this job?</label>
              <textarea id="why"></textarea>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        detected_requirements=[
            {
                "field_label": "Why do you want this job?",
                "field_type": "textarea",
                "required": False,
                "draft_answer": "This role lines up with my product data and experimentation background.",
                "answer_confidence": "medium",
                "missing_input": False,
            }
        ],
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert "Filled full name" in review["completed_actions"]
    assert "Filled email" in review["completed_actions"]
    assert any(action.startswith("Uploaded resume:") for action in review["completed_actions"])
    assert review["file_upload_report"]["status"] == "uploaded"
    assert review_json["resolved_ats"] == "generic"
    assert review_json["metrics"]["detected_fields_filled"] == 1
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0
    assert review_json["metrics"]["resume_upload_verified"] == 1
    assert review_json["file_upload_report"]["expected_file_name"] == "Example_Candidate_Resume.pdf"


def test_run_apply_uploads_resume_when_multiple_file_inputs_use_visible_labels(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "multi_file_upload.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="upload1">Cover Letter</label>
              <input id="upload1" type="file" />
              <label for="upload2">Resume</label>
              <input id="upload2" type="file" />
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)

    assert review["status"] == "ready_for_review"
    assert review["file_upload_report"]["status"] == "uploaded"
    assert review["file_upload_report"]["matched_input"]["id"] == "upload2"
    assert review["file_upload_report"]["matched_input"]["label"] == "Resume"
    file_inputs = {item["id"]: item for item in review["file_upload_report"]["file_inputs"]}
    assert file_inputs["upload1"]["file_names"] == ["Example_Candidate_Cover_Letter.pdf"]
    assert file_inputs["upload2"]["file_names"] == ["Example_Candidate_Resume.pdf"]
    assert any(action.startswith("Uploaded cover letter:") for action in review["completed_actions"])
    assert "Resume input was not found; upload manually." not in review["remaining_review_items"]


def test_run_apply_generic_flags_missing_resume_input_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "missing_resume.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert review["file_upload_report"]["status"] == "missing_input"
    assert "Resume input was not found; upload manually." in review["remaining_review_items"]
    assert review_json["metrics"]["resume_upload_missing"] == 1
    assert review_json["file_upload_report"]["status"] == "missing_input"


def test_run_apply_submit_refuses_incomplete_review_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "submit_guard.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form onsubmit="document.body.innerHTML = 'SUBMITTED'; return false;">
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="portfolio">Portfolio</label>
              <input id="portfolio" type="url" />
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(
        paths,
        job_id="openai-data-scientist",
        ats="auto",
        submit=True,
        headless=True,
        fill_legal_acknowledgements=True,
    )

    assert review["status"] == "blocked_submit_review_required"
    assert "Submitted application" not in review["completed_actions"]
    assert review["file_upload_report"]["status"] == "missing_input"
    assert any("Resume upload is not complete" in item for item in review["remaining_review_items"])
    assert not any("Unfilled optional/unknown visible fields remain" in item for item in review["remaining_review_items"])
    assert review["optional_no_value_fields"][0]["answer_key"] == "portfolio"
    assert review["confirmation_text"] == "Submit blocked because the application review is incomplete."
    recommendations = review["answer_gap_recommendations"]
    assert any(item["source"] == "resume_upload" for item in recommendations)
    assert not any(item.get("suggested_answer_key") == "portfolio" for item in recommendations)


def test_run_apply_submit_allows_optional_no_value_policy_field_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "submit_optional_policy.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form onsubmit="document.body.innerHTML = 'SUBMITTED'; return false;">
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="portfolio">Portfolio</label>
              <input id="portfolio" type="url" />
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(
        paths,
        job_id="openai-data-scientist",
        ats="auto",
        submit=True,
        headless=True,
        fill_legal_acknowledgements=True,
    )

    assert review["status"] == "submitted"
    assert review["file_upload_report"]["status"] == "uploaded"
    assert review["unfilled_fields"] == []
    assert review["optional_no_value_fields"][0]["label"] == "Portfolio"
    assert review["optional_no_value_fields"][0]["answer_key"] == "portfolio"
    assert not review["answer_gap_recommendations"]


def test_run_apply_fills_optional_narrative_variants_from_packet_drafts(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "narrative_variants.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="mission">What excites you about our mission?</label>
              <textarea id="mission"></textarea>
              <label for="letter">Letter of Interest</label>
              <textarea id="letter"></textarea>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)

    assert review["status"] == "ready_for_review"
    assert "Filled answer-bank field: what excites you" in review["completed_actions"]
    assert "Filled cover letter: letter of interest" in review["completed_actions"]
    assert review["file_upload_report"]["status"] == "uploaded"


def test_run_apply_fills_live_optional_fields_from_answer_bank(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "optional.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="salary">Salary Expectations</label>
              <input id="salary" />
              <label for="motivation">Why are you looking?</label>
              <textarea id="motivation"></textarea>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={
            "salary_expectations": "Flexible depending on scope, level, location, and total compensation.",
            "why_looking": "I am looking for roles at the intersection of experimentation, AI tooling, and product measurement.",
        },
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "salary expectations",
        "status": "filled",
        "reason": "filled from answer_bank.salary_expectations",
    } in review["field_fill_report"]
    assert {
        "field": "why are you looking",
        "status": "filled",
        "reason": "filled from answer_bank.why_looking",
    } in review["field_fill_report"]
    assert review_json["metrics"]["detected_fields_filled"] == 2
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_fills_application_source_field_from_packet_default(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "application_source.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="source">How did you hear about us?</label>
              <select id="source">
                <option value="">Select one</option>
                <option value="linkedin">LinkedIn</option>
                <option value="company">Company Website</option>
                <option value="referral">Referral</option>
              </select>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "how did you hear",
        "status": "filled",
        "reason": "filled from answer_bank.application_source",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_visible_fields"] == 0


def test_run_apply_fills_distributed_teams_experience_from_answer_bank(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "distributed_teams.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="distributed">How many years of experience do you have working within remote and geo-dispersed global teams?</label>
              <input id="distributed" type="text" required />
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={"distributed_teams_experience": "9+"},
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "remote and geo-dispersed global teams",
        "status": "filled",
        "reason": "filled from answer_bank.distributed_teams_experience",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_fills_country_field_from_answer_bank_alias(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "country_alias.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="country">Country</label>
              <select id="country" required>
                <option value="">Select one</option>
                <option value="us">United States</option>
                <option value="ca">Canada</option>
              </select>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={"country": "United States"},
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "country",
        "status": "filled",
        "reason": "filled from answer_bank.country",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_recovers_audited_optional_fields_from_answer_bank(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "audited_optional.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="best_work">Link to your best work</label>
              <input id="best_work" name="best_work" type="url" />
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={"portfolio": "https://www.linkedin.com/in/example-candidate"},
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "Link to your best work",
        "status": "filled",
        "reason": "filled from final_audit.answer_bank.portfolio",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_visible_fields"] == 0


def test_run_apply_advances_safe_multistep_form_and_fills_later_fields(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "multistep.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <section id="step1">
                <label for="name">Full Name</label>
                <input id="name" />
                <label for="email">Email</label>
                <input id="email" type="email" />
                <button type="button" onclick="document.querySelector('#step1').hidden = true; document.querySelector('#step2').hidden = false">Continue</button>
              </section>
              <section id="step2" hidden>
                <label for="resume">Resume</label>
                <input id="resume" name="resume" type="file" />
                <label for="salary">Salary Expectations</label>
                <input id="salary" />
                <label for="motivation">Why are you looking?</label>
                <textarea id="motivation"></textarea>
                <button type="submit">Submit Application</button>
              </section>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={
            "salary_expectations": "Flexible depending on scope, level, location, and total compensation.",
            "why_looking": "I am looking for roles at the intersection of experimentation, AI tooling, and product measurement.",
        },
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert "Advanced application step: continue" in review["completed_actions"]
    assert "Resume input was not found; upload manually." not in review["remaining_review_items"]
    assert review["file_upload_report"]["status"] == "uploaded"
    assert {
        "field": "salary expectations",
        "status": "filled",
        "reason": "filled from answer_bank.salary_expectations",
    } in review["field_fill_report"]
    assert {
        "field": "why are you looking",
        "status": "filled",
        "reason": "filled from answer_bank.why_looking",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_visible_fields"] == 0


def test_run_apply_recovers_optional_grouped_controls_from_answer_bank(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "optional_grouped.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <fieldset>
                <legend>Would you move for this role?</legend>
                <label for="relocate_yes">Yes</label>
                <input id="relocate_yes" name="relocate" type="radio" value="yes" />
                <label for="relocate_no">No</label>
                <input id="relocate_no" name="relocate" type="radio" value="no" />
              </fieldset>
              <label for="marketing">Send me job alerts</label>
              <input id="marketing" type="checkbox" />
              <button type="submit">Submit Application</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={"relocation": "Yes"},
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "Would you move for this role?",
        "status": "filled",
        "reason": "filled from final_audit.answer_bank.relocation",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_visible_fields"] == 0


def test_run_apply_fills_grouped_optional_controls_from_answer_bank(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "grouped_controls.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <fieldset>
                <legend>Are you legally authorized to work in the United States?</legend>
                <label for="authorized_yes">Yes</label>
                <input id="authorized_yes" name="authorized" type="radio" value="yes" />
                <label for="authorized_no">No</label>
                <input id="authorized_no" name="authorized" type="radio" value="no" />
              </fieldset>
              <fieldset>
                <legend>Will you now or in the future require sponsorship?</legend>
                <label for="sponsor_yes">Yes</label>
                <input id="sponsor_yes" name="sponsor" type="radio" value="yes" />
                <label for="sponsor_no">No</label>
                <input id="sponsor_no" name="sponsor" type="radio" value="no" />
              </fieldset>
              <label for="salary">Desired Salary</label>
              <select id="salary">
                <option value="">Select one</option>
                <option value="flexible">Flexible depending on scope, level, location, and total compensation.</option>
              </select>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={
            "work_authorization": "Yes",
            "sponsorship": "Yes, immigration sponsorship required",
            "salary_expectations": "Flexible depending on scope, level, location, and total compensation.",
        },
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "authorized to work",
        "status": "filled",
        "reason": "filled from answer_bank.work_authorization",
    } in review["field_fill_report"]
    assert {
        "field": "require sponsorship",
        "status": "filled",
        "reason": "filled from answer_bank.sponsorship",
    } in review["field_fill_report"]
    assert {
        "field": "desired salary",
        "status": "filled",
        "reason": "filled from answer_bank.salary_expectations",
    } in review["field_fill_report"]
    assert review_json["metrics"]["detected_fields_filled"] >= 3
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_fills_custom_combobox_from_answer_bank(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "custom_combobox.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label id="location-label">Current Location</label>
              <button
                type="button"
                role="combobox"
                aria-labelledby="location-label"
                aria-haspopup="listbox"
                onclick="document.querySelector('#location-options').hidden = false"
              >Select location</button>
              <div id="location-options" role="listbox" hidden>
                <div role="option" onclick="document.querySelector('[role=combobox]').innerText = this.innerText">San Francisco, CA</div>
                <div role="option">San Francisco, CA</div>
              </div>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={"current_location": "San Francisco, CA"},
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "current location",
        "status": "filled",
        "reason": "filled from answer_bank.current_location",
    } in review["field_fill_report"]
    assert review_json["metrics"]["detected_fields_filled"] >= 1
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_recovers_custom_dropdown_from_audited_label_hint(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "audited_custom_dropdown.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label id="office-label">Preferred office</label>
              <button
                type="button"
                role="combobox"
                aria-labelledby="office-label"
                aria-haspopup="listbox"
                onclick="document.querySelector('#office-options').hidden = false"
              >Select one</button>
              <div id="office-options" role="listbox" hidden>
                <div role="option">New York, NY</div>
                <div role="option" onclick="document.querySelector('[role=combobox]').innerText = this.innerText">San Francisco, CA</div>
              </div>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={"current_location": "San Francisco, CA"},
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "Preferred office",
        "status": "filled",
        "reason": "filled from final_audit.answer_bank.current_location",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_visible_fields"] == 0


def test_run_apply_selects_react_combobox_with_semantic_yes_option(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "react_select_semantic_yes.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label id="sponsor-label" for="sponsor">Do you require visa sponsorship? *</label>
              <input
                id="sponsor"
                role="combobox"
                aria-labelledby="sponsor-label"
                aria-haspopup="listbox"
                aria-controls="sponsor-options"
                aria-required="true"
                onclick="document.querySelector('#sponsor-options').hidden = false"
              />
              <div id="sponsor-options" role="listbox" hidden>
                <div role="option" onclick="document.querySelector('#sponsor').value = this.innerText; document.querySelector('#sponsor-options').hidden = true">Yes</div>
                <div role="option" onclick="document.querySelector('#sponsor').value = this.innerText; document.querySelector('#sponsor-options').hidden = true">No</div>
              </div>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats=None,
        answer_bank={"sponsorship": "Yes, I would require immigration sponsorship."},
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert {
        "field": "visa sponsorship",
        "status": "filled",
        "reason": "filled from answer_bank.sponsorship",
    } in review["field_fill_report"]
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_does_not_count_typed_react_select_search_as_committed_selection(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "react_select_uncommitted.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <div class="select">
                <div class="select__container">
                  <label id="gender-label" for="gender">What is your gender identity?*</label>
                  <div class="select-shell">
                    <div class="select__control">
                      <div class="select__value-container">
                        <div class="select__placeholder">Select...</div>
                        <div class="select__input-container" data-value="">
                          <input
                            id="gender"
                            class="select__input"
                            type="text"
                            role="combobox"
                            aria-labelledby="gender-label"
                            aria-required="true"
                            aria-haspopup="true"
                            onclick="document.querySelector('#gender-options').hidden = false"
                          />
                        </div>
                      </div>
                    </div>
                    <div id="gender-options" role="listbox" hidden>
                      <div role="option" onclick="document.querySelector('#gender').value = this.innerText; document.querySelector('#gender-options').hidden = true">Male</div>
                      <div role="option" onclick="document.querySelector('#gender').value = this.innerText; document.querySelector('#gender-options').hidden = true">Female</div>
                    </div>
                    <input required tabindex="-1" aria-hidden="true" class="requiredInput" value="" />
                  </div>
                </div>
              </div>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 1
    assert any(item.get("label") == "What is your gender identity?*" for item in review_json["unfilled_fields"])


def test_run_apply_lever_waits_for_resume_processing_and_restores_identity(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "lever_resume_processing.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full name</label>
              <input id="name" name="name" oninput="document.querySelector('#name-value').innerText = this.value" />
              <output id="name-value"></output>

              <label for="email">Email</label>
              <input id="email" name="email" type="email" oninput="document.querySelector('#email-value').innerText = this.value" />
              <output id="email-value"></output>

              <label for="phone">Phone</label>
              <input id="phone" name="phone" type="tel" oninput="document.querySelector('#phone-value').innerText = this.value" />
              <output id="phone-value"></output>

              <label for="linkedin">LinkedIn</label>
              <input id="linkedin" name="urls[LinkedIn]" type="url" oninput="document.querySelector('#linkedin-value').innerText = this.value" />
              <output id="linkedin-value"></output>

              <label for="current-company">Current company</label>
              <input id="current-company" oninput="document.querySelector('#company-value').innerText = this.value" />
              <output id="company-value"></output>

              <label for="location">Current Location</label>
              <input id="location" oninput="document.querySelector('#location-value').innerText = this.value" />
              <output id="location-value"></output>

              <label for="resume">Resume</label>
              <input
                id="resume"
                name="resume"
                type="file"
                onchange="
                  document.querySelector('#status').hidden = false;
                  setTimeout(() => {
                    document.querySelector('#name').value = 'Asian';
                    document.querySelector('#name').dispatchEvent(new Event('input', { bubbles: true }));
                    document.querySelector('#email').value = 'parser@example.com';
                    document.querySelector('#email').dispatchEvent(new Event('input', { bubbles: true }));
                  }, 100);
                  setTimeout(() => { document.querySelector('#status').hidden = true; }, 500);
                "
              />
              <div id="status" hidden>Analyzing resume...</div>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats="lever")

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )
    review_html = (
        tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review" / "review.html"
    ).read_text(encoding="utf-8")

    assert review["status"] == "ready_for_review"
    assert "Waited for Lever resume processing" in review["completed_actions"]
    assert "Re-filled legal name after resume processing" in review["completed_actions"]
    assert "Re-filled legal name after Lever pass" in review["completed_actions"]
    assert '<output id="name-value">Example Candidate</output>' in review_html
    assert '<output id="email-value">candidate@example.com</output>' in review_html
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_lever_fills_location_and_source_group(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "lever_location_source.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full name</label>
              <input id="name" name="name" />

              <label for="email">Email</label>
              <input id="email" name="email" type="email" />

              <label for="phone">Phone</label>
              <input id="phone" name="phone" type="tel" />

              <label for="linkedin">LinkedIn URL</label>
              <input id="linkedin" name="urls[LinkedIn]" type="url" />

              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />

              <label for="office">Which location are you applying for?</label>
              <select id="office" required>
                <option value="">Select...</option>
                <option value="fc">Foster City, CA</option>
                <option value="bos">Boston, MA</option>
              </select>

              <div id="source-group">
                <p>How did you hear about us?</p>
                <label><input type="radio" name="source" required /> Zoox Ads / Social Media</label>
                <label><input type="radio" name="source" required /> LinkedIn</label>
                <label><input type="radio" name="source" required /> Employee Referral</label>
              </div>

              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats="lever")

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert "Selected Foster City, CA for which location are you applying for" in review["completed_actions"]
    assert any("linkedin" in action.lower() for action in review["completed_actions"])
    assert review_json["metrics"]["unfilled_required_visible_fields"] == 0


def test_run_apply_workday_fills_visible_form_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "workday.html"
    form_path.write_text(
        """
        <html>
          <body>
            <button type="button" onclick="document.querySelector('#application').hidden = false">Apply Manually</button>
            <form id="application" hidden>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <label for="why">Why are you interested?</label>
              <textarea id="why"></textarea>
              <button type="submit">Submit Application</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats="workday",
        detected_requirements=[
            {
                "field_label": "Why are you interested?",
                "field_type": "textarea",
                "required": False,
                "draft_answer": "The role maps to my experimentation and product data background.",
                "answer_confidence": "medium",
                "missing_input": False,
            }
        ],
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "ready_for_review"
    assert "Clicked Workday start button: Apply Manually" in review["completed_actions"]
    assert "Filled full name" in review["completed_actions"]
    assert any(action.startswith("Uploaded resume:") for action in review["completed_actions"])
    assert review_json["resolved_ats"] == "workday"
    assert review_json["metrics"]["detected_fields_filled"] == 1


def test_run_apply_workday_reports_account_gate_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "workday_signin.html"
    form_path.write_text(
        """
        <html>
          <body>
            <button type="button" onclick="document.querySelector('#gate').hidden = false">Apply</button>
            <section id="gate" hidden>
              <h1>Sign in to your candidate home account</h1>
              <a href="/create">Create account</a>
              <a href="/forgot">Forgot password?</a>
            </section>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats="workday")

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "blocked_account_required"
    assert review["confirmation_text"] == "Account sign-in or candidate portal step detected."
    assert "Account sign-in or candidate portal step detected." in review["remaining_review_items"]
    assert review_json["resolved_ats"] == "workday"
    assert review_json["status"] == "blocked_account_required"


def test_run_apply_generic_reports_captcha_blocker_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "captcha.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <div class="g-recaptcha">I'm not a robot</div>
              <iframe title="reCAPTCHA challenge"></iframe>
              <button type="submit">Apply</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "blocked_human_verification_required"
    assert review["confirmation_text"] == "Human verification or CAPTCHA step detected."
    assert "Human verification or CAPTCHA step detected." in review["remaining_review_items"]
    assert review_json["status"] == "blocked_human_verification_required"
    assert review_json["metrics"]["detected_fields_filled"] == 0


def test_run_apply_ignores_invisible_recaptcha_page_infrastructure(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "invisible_recaptcha.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Full Name</label>
              <input id="name" />
              <label for="email">Email</label>
              <input id="email" type="email" />
              <label for="resume">Resume</label>
              <input id="resume" name="resume" type="file" />
              <button type="submit">Apply</button>
            </form>
            <script id="recaptchaScript" src="https://www.recaptcha.net/recaptcha/api.js?render=site-key"></script>
            <div class="grecaptcha-badge" style="width: 256px; height: 60px; display: block;">
              <iframe title="reCAPTCHA" role="presentation" src="https://www.recaptcha.net/recaptcha/api2/anchor?size=invisible"></iframe>
              <textarea id="g-recaptcha-response" name="g-recaptcha-response" style="display: none;"></textarea>
            </div>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)

    assert review["status"] == "ready_for_review"
    assert "Filled full name" in review["completed_actions"]
    assert any(action.startswith("Uploaded resume:") for action in review["completed_actions"])
    assert not any("Human verification or CAPTCHA" in item for item in review["remaining_review_items"])


def test_run_apply_ashby_waits_for_resume_parsing_before_final_audit(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "ashby_resume_parsing.html"
    form_path.write_text(
        """
        <html>
          <body>
            <form>
              <label for="name">Legal Name</label>
              <input id="name" name="_systemfield_name" />
              <label for="preferred">Preferred Name (if applicable)</label>
              <input id="preferred" placeholder="Type here..." />
              <label for="email">Email</label>
              <input id="email" name="_systemfield_email" type="email" />
              <label for="resume">Resume</label>
              <input id="_systemfield_resume" type="file" onchange="
                document.querySelector('#parsing').hidden = false;
                document.querySelector('#preferred').value = '';
                document.querySelector('#phone').value = '';
                document.querySelector('#additional').value = '';
                setTimeout(() => document.querySelector('#parsing').hidden = true, 250);
              " />
              <div id="parsing" hidden>Parsing your resume. Autofilling key fields...</div>
              <label for="phone">Phone Number</label>
              <input id="phone" type="tel" />
              <label for="location">Where are you currently located?</label>
              <input id="location" role="combobox" placeholder="Start typing..." />
              <label for="start">When can you start a new role?</label>
              <input id="start" placeholder="Pick date..." />
              <p>Are you authorized to work in the country where the job is located?</p>
              <button type="button">Yes</button><button type="button">No</button>
              <p>Will you now or in the future require sponsorship for employment visa status in this country?</p>
              <button type="button">Yes</button><button type="button">No</button>
              <p>Are you able to work from our US office three days per week?</p>
              <button type="button">Yes</button><button type="button">No</button>
              <label for="additional">Additional Information</label>
              <textarea id="additional" placeholder="Type here..."></textarea>
              <button type="submit">Submit Application</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats="ashby",
        answer_bank={
            "preferred_name": "Example",
            "phone": "555-555-5555",
        },
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)

    assert review["status"] == "ready_for_review"
    assert "Waited for Ashby resume parsing" in review["completed_actions"]
    assert sum(1 for action in review["completed_actions"] if action.startswith("Uploaded resume:")) == 1
    assert review["file_upload_report"]["status"] in {"uploaded", "uploaded_unverified"}
    assert review["file_upload_report"]["upload_action_seen"] is True
    assert not any(item["label"] == "Phone Number" for item in review["unfilled_fields"])
    assert not any(item["label"] == "Preferred Name (if applicable)" for item in review["unfilled_fields"])
    assert not any(item["label"] == "Additional Information" for item in review["unfilled_fields"])


def test_run_apply_ashby_clicks_entry_cta_before_filling_form(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "ashby_entry_cta.html"
    form_path.write_text(
        """
        <html>
          <body>
            <div id="overview">
              <nav>
                <button type="button">Overview</button>
                <button type="button" onclick="
                  document.querySelector('#overview').hidden = true;
                  document.querySelector('#application').hidden = false;
                ">Application</button>
              </nav>
              <button type="button" onclick="
                document.querySelector('#overview').hidden = true;
                document.querySelector('#application').hidden = false;
              ">Apply for this Job</button>
            </div>
            <form id="application" hidden>
              <label for="name">Legal Name</label>
              <input id="name" name="_systemfield_name" />
              <label for="email">Email</label>
              <input id="email" name="_systemfield_email" type="email" />
              <label for="resume">Resume</label>
              <input id="_systemfield_resume" type="file" />
              <label for="linkedin">LinkedIn Profile</label>
              <input id="linkedin" />
              <button type="submit">Submit Application</button>
            </form>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats="ashby",
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)

    assert review["status"] == "ready_for_review"
    assert any(action.startswith("Clicked Ashby") for action in review["completed_actions"])
    assert any(action.startswith("Uploaded resume:") for action in review["completed_actions"])
    assert review["file_upload_report"]["status"] in {"uploaded", "uploaded_unverified"}


def test_run_apply_ashby_blocks_if_entry_screen_never_opens_form(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "ashby_overview_only.html"
    form_path.write_text(
        """
        <html>
          <body>
            <nav>
              <button type="button">Overview</button>
              <button type="button">Application</button>
            </nav>
            <button type="button">Apply for this Job</button>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(
        tmp_path,
        apply_url=form_path.resolve().as_uri(),
        ats="ashby",
    )

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)

    assert review["status"] == "blocked_application_entry_required"
    assert review["confirmation_text"] == "Ashby application form did not open automatically; review the entry step before submission."


def test_run_apply_generic_reports_login_blocker_with_local_browser(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")
    form_path = tmp_path / "login.html"
    form_path.write_text(
        """
        <html>
          <body>
            <h1>Sign in to apply</h1>
            <label for="email">Email</label>
            <input id="email" type="email" />
            <label for="password">Password</label>
            <input id="password" type="password" />
            <a href="/forgot">Forgot password?</a>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    paths = ProjectPaths(tmp_path)
    _write_application_plan(tmp_path, apply_url=form_path.resolve().as_uri(), ats=None)

    review = run_apply(paths, job_id="openai-data-scientist", ats="auto", headless=True)
    review_json = json.loads(
        (tmp_path / "outputs" / "applications" / "openai_data_scientist" / "apply_review.json").read_text(
            encoding="utf-8"
        )
    )

    assert review["status"] == "blocked_account_required"
    assert review["confirmation_text"] == "Account sign-in or candidate portal step detected."
    assert review_json["status"] == "blocked_account_required"


def test_run_apply_workday_uses_storage_state_for_authenticated_flow(tmp_path) -> None:
    pytest.importorskip("playwright.async_api")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler.
            authenticated = "job_auth=yes" in (self.headers.get("Cookie") or "")
            body = (
                """
                <html>
                  <body>
                    <button type="button" onclick="document.querySelector('#application').hidden = false">Apply Manually</button>
                    <form id="application" hidden>
                      <label for="name">Full Name</label>
                      <input id="name" />
                      <label for="email">Email</label>
                      <input id="email" type="email" />
                      <label for="resume">Resume</label>
                      <input id="resume" name="resume" type="file" />
                      <label for="why">Why are you interested?</label>
                      <textarea id="why"></textarea>
                      <button type="submit">Submit Application</button>
                    </form>
                  </body>
                </html>
                """
                if authenticated
                else "<html><body><button>Apply</button><h1>Sign in to your candidate home account</h1></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API name.
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        storage_state = tmp_path / "storage_state.json"
        storage_state.write_text(
            json.dumps(
                {
                    "cookies": [
                        {
                            "name": "job_auth",
                            "value": "yes",
                            "domain": "127.0.0.1",
                            "path": "/",
                            "expires": -1,
                            "httpOnly": False,
                            "secure": False,
                            "sameSite": "Lax",
                        }
                    ],
                    "origins": [],
                }
            ),
            encoding="utf-8",
        )
        paths = ProjectPaths(tmp_path)
        _write_application_plan(
            tmp_path,
            apply_url=f"http://127.0.0.1:{server.server_port}/workday",
            ats="workday",
            detected_requirements=[
                {
                    "field_label": "Why are you interested?",
                    "field_type": "textarea",
                    "required": False,
                    "draft_answer": "The role maps to my experimentation and product data background.",
                    "answer_confidence": "medium",
                    "missing_input": False,
                }
            ],
        )

        review = run_apply(
            paths,
            job_id="openai-data-scientist",
            ats="auto",
            headless=True,
            storage_state_path=storage_state,
        )

        assert review["status"] == "ready_for_review"
        assert f"Loaded browser storage state: {storage_state}" in review["completed_actions"]
        assert "Clicked Workday start button: Apply Manually" in review["completed_actions"]
        assert any(action.startswith("Uploaded resume:") for action in review["completed_actions"])
    finally:
        server.shutdown()
        server.server_close()


def test_handle_apply_delegates_to_runner_with_safety_flags(monkeypatch, tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
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
                "paths": paths,
                "job_id": job_id,
                "ats": ats,
                "submit": submit,
                "headless": headless,
                "fill_legal_acknowledgements": fill_legal_acknowledgements,
                "storage_state_path": storage_state_path,
                "user_data_dir": user_data_dir,
            }
        )
        return {"status": "ready_for_review"}

    monkeypatch.setattr(cli, "run_apply", fake_run_apply)

    message = cli.handle_apply(
        paths,
        "openai-data-scientist",
        ats="ashby",
        submit=False,
        headed=True,
        fill_legal_acknowledgements=False,
        storage_state="/tmp/state.json",
    )

    assert "Prepared openai-data-scientist for review" in message
    assert calls == {
        "paths": paths,
        "job_id": "openai-data-scientist",
        "ats": "ashby",
        "submit": False,
        "headless": False,
        "fill_legal_acknowledgements": False,
        "storage_state_path": "/tmp/state.json",
        "user_data_dir": None,
    }


def test_handle_capture_session_delegates_to_browser_session_capture(monkeypatch, tmp_path) -> None:
    calls = {}
    output_path = tmp_path / "session.json"

    def fake_capture_browser_session_sync(*, url, output_path, wait_seconds, headless):
        calls.update(
            {
                "url": url,
                "output_path": output_path,
                "wait_seconds": wait_seconds,
                "headless": headless,
            }
        )
        return output_path

    monkeypatch.setattr(cli, "capture_browser_session_sync", fake_capture_browser_session_sync)

    message = cli.handle_capture_session(
        "https://example.wd5.myworkdayjobs.com/login",
        str(output_path),
        wait_seconds=5,
        headless=True,
    )

    assert message == f"Saved authenticated browser session to {output_path}"
    assert calls == {
        "url": "https://example.wd5.myworkdayjobs.com/login",
        "output_path": str(output_path),
        "wait_seconds": 5,
        "headless": True,
    }
