from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .apply_runner import run_apply, review_metrics
from .paths import ProjectPaths


ACCEPTED_SKIP_REASONS = {
    "handled by ATS adapter",
    "file upload handled by ATS adapter",
    "legal acknowledgement left for explicit review",
    "handled by demographic group answer",
    "optional field intentionally left blank by no-value policy",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _job_value(plan: dict[str, Any], key: str) -> str:
    value = plan.get("job", {}).get(key)
    return str(value) if value is not None else ""


def _normalized_field_set(fields: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("label") or item.get("field") or "").lower().strip()
        for item in fields
        if str(item.get("label") or item.get("field") or "").strip()
    }


def _actionable_skips(
    field_fill_report: list[dict[str, Any]],
    unfilled_fields: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    filled_fields = {
        str(item.get("field") or "").lower().strip()
        for item in field_fill_report
        if item.get("status") == "filled"
    }
    unfilled_field_labels = _normalized_field_set(unfilled_fields or [])
    return [
        item
        for item in field_fill_report
        if item.get("status") == "skipped"
        and str(item.get("reason") or "") not in ACCEPTED_SKIP_REASONS
        and str(item.get("field") or "").lower().strip() not in filled_fields
        and (unfilled_fields is None or str(item.get("field") or "").lower().strip() in unfilled_field_labels)
    ]


def _answer_gap_recommendations(review_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in review_payload.get("answer_gap_recommendations", [])
        if isinstance(item, dict) and str(item.get("recommendation") or "").strip()
    ]


def _gap_sources(recommendations: list[dict[str, Any]]) -> str:
    sources = sorted({str(item.get("source") or "unknown") for item in recommendations})
    return "; ".join(sources)


def _top_gap_recommendation(recommendations: list[dict[str, Any]]) -> str:
    if not recommendations:
        return ""
    item = recommendations[0]
    field = str(item.get("field") or "Unknown field")
    recommendation = str(item.get("recommendation") or "").strip()
    answer_key = str(item.get("suggested_answer_key") or "").strip()
    suffix = f" [{answer_key}]" if answer_key else ""
    return f"{field}: {recommendation}{suffix}"


def _backlog_category(item: dict[str, str]) -> str:
    source = item.get("source", "")
    recommendation = item.get("recommendation", "").lower()
    reason = item.get("reason", "").lower()
    if source == "resume_upload":
        return "platform_upload_selector"
    if item.get("suggested_answer_key") and ("no configured answer" in reason or "add a reusable" in recommendation):
        return "reusable_answer"
    if item.get("suggested_answer_key"):
        return "field_alias_or_selector"
    if source == "skipped_detected_field":
        return "inspection_or_runner_mapping"
    return "manual_triage"


def _gap_group_key(item: dict[str, str]) -> tuple[str, str, str]:
    category = _backlog_category(item)
    answer_key = item.get("suggested_answer_key", "")
    field = item.get("field", "").lower().strip()
    if category == "reusable_answer" and answer_key:
        return category, answer_key, ""
    if category == "platform_upload_selector":
        return category, item.get("ats", ""), item.get("source", "")
    if answer_key:
        return category, answer_key, item.get("source", "")
    return category, field, item.get("source", "")


def collect_apply_gap_items(paths: ProjectPaths) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    application_dirs = paths.outputs_applications_dir.iterdir() if paths.outputs_applications_dir.exists() else []
    for application_dir in sorted(path for path in application_dirs if path.is_dir()):
        plan_path = application_dir / "application_plan.json"
        review_path = application_dir / "apply_review.json"
        if not plan_path.exists() or not review_path.exists():
            continue
        try:
            plan = _load_json(plan_path)
            review_payload = _load_json(review_path)
        except json.JSONDecodeError:
            continue

        status = _classify_review(review_payload)
        for recommendation in _answer_gap_recommendations(review_payload):
            items.append(
                {
                    "company": _job_value(plan, "company"),
                    "role": _job_value(plan, "title"),
                    "job_id": _job_value(plan, "job_id") or application_dir.name,
                    "ats": str(review_payload.get("resolved_ats") or _job_value(plan, "ats")),
                    "status": status,
                    "source": str(recommendation.get("source") or "unknown"),
                    "field": str(recommendation.get("field") or "Unknown field"),
                    "suggested_answer_key": str(recommendation.get("suggested_answer_key") or ""),
                    "reason": str(recommendation.get("reason") or ""),
                    "recommendation": str(recommendation.get("recommendation") or ""),
                    "application_dir": str(application_dir),
                    "review_json": str(review_path),
                }
            )
    return items


def build_apply_gap_backlog(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = _gap_group_key(item)
        group = grouped.setdefault(
            key,
            {
                "category": _backlog_category(item),
                "source": item["source"],
                "suggested_answer_key": item["suggested_answer_key"],
                "count": 0,
                "companies": [],
                "roles": [],
                "field_examples": [],
                "recommendation": item["recommendation"],
                "sample_reviews": [],
            },
        )
        group["count"] += 1
        for list_key, value in [
            ("companies", item["company"]),
            ("roles", item["role"]),
            ("field_examples", item["field"]),
            ("sample_reviews", item["review_json"]),
        ]:
            if value and value not in group[list_key]:
                group[list_key].append(value)

    category_priority = {
        "reusable_answer": 0,
        "field_alias_or_selector": 1,
        "platform_upload_selector": 2,
        "inspection_or_runner_mapping": 3,
        "manual_triage": 4,
    }
    return sorted(
        grouped.values(),
        key=lambda item: (category_priority.get(item["category"], 99), -int(item["count"]), item["source"], item["suggested_answer_key"]),
    )


def _classify_review(review_payload: dict[str, Any]) -> str:
    status = str(review_payload.get("status") or "")
    if status == "submitted":
        return "submitted"
    if status.startswith("blocked"):
        return status

    metrics = review_payload.get("metrics") or review_metrics(review_payload)
    upload_status = str((review_payload.get("file_upload_report") or {}).get("status") or "")
    if upload_status in {"missing_input", "not_uploaded"}:
        return "needs_manual_field_review"

    if int(metrics.get("unfilled_required_visible_fields") or 0) > 0:
        return "needs_manual_field_review"

    if _actionable_skips(review_payload.get("field_fill_report") or [], review_payload.get("unfilled_fields") or []):
        return "needs_runner_improvement"

    if int(metrics.get("unfilled_optional_or_unknown_visible_fields") or 0) > 0:
        return "needs_optional_field_review"

    return "ready_for_final_review"


def collect_apply_qa_rows(paths: ProjectPaths) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    application_dirs = paths.outputs_applications_dir.iterdir() if paths.outputs_applications_dir.exists() else []
    for application_dir in sorted(path for path in application_dirs if path.is_dir()):
        plan_path = application_dir / "application_plan.json"
        if not plan_path.exists():
            continue

        try:
            plan = _load_json(plan_path)
        except json.JSONDecodeError:
            rows.append(
                {
                    "company": "",
                    "role": "",
                    "job_id": application_dir.name,
                    "ats": "",
                    "status": "invalid_plan",
                    "detected_fields_filled": "0",
                    "detected_fields_total": "0",
                    "unfilled_required_visible_fields": "0",
                    "unfilled_visible_fields": "0",
                    "optional_no_value_fields": "0",
                    "resume_upload_status": "",
                    "actionable_skips": "0",
                    "answer_gap_count": "0",
                    "answer_gap_sources": "",
                    "top_answer_gap": "",
                    "application_dir": str(application_dir),
                    "apply_url": "",
                    "review_json": "",
                    "next_action": "Regenerate application packet",
                }
            )
            continue

        review_path = application_dir / "apply_review.json"
        if not review_path.exists():
            rows.append(
                {
                    "company": _job_value(plan, "company"),
                    "role": _job_value(plan, "title"),
                    "job_id": _job_value(plan, "job_id") or application_dir.name,
                    "ats": _job_value(plan, "ats"),
                    "status": "not_run",
                    "detected_fields_filled": "0",
                    "detected_fields_total": "0",
                    "unfilled_required_visible_fields": "0",
                    "unfilled_visible_fields": "0",
                    "optional_no_value_fields": "0",
                    "resume_upload_status": "",
                    "actionable_skips": "0",
                    "answer_gap_count": "0",
                    "answer_gap_sources": "",
                    "top_answer_gap": "",
                    "application_dir": str(application_dir),
                    "apply_url": _job_value(plan, "apply_url"),
                    "review_json": "",
                    "next_action": "Run apply in review mode",
                }
            )
            continue

        try:
            review_payload = _load_json(review_path)
        except json.JSONDecodeError:
            rows.append(
                {
                    "company": _job_value(plan, "company"),
                    "role": _job_value(plan, "title"),
                    "job_id": _job_value(plan, "job_id") or application_dir.name,
                    "ats": _job_value(plan, "ats"),
                    "status": "invalid_review",
                    "detected_fields_filled": "0",
                    "detected_fields_total": "0",
                    "unfilled_required_visible_fields": "0",
                    "unfilled_visible_fields": "0",
                    "optional_no_value_fields": "0",
                    "resume_upload_status": "",
                    "actionable_skips": "0",
                    "answer_gap_count": "0",
                    "answer_gap_sources": "",
                    "top_answer_gap": "",
                    "application_dir": str(application_dir),
                    "apply_url": _job_value(plan, "apply_url"),
                    "review_json": str(review_path),
                    "next_action": "Rerun apply review",
                }
            )
            continue

        metrics = review_payload.get("metrics") or review_metrics(review_payload)
        optional_no_value_count = len(review_payload.get("optional_no_value_fields") or [])
        actionable_skips = _actionable_skips(
            review_payload.get("field_fill_report") or [],
            review_payload.get("unfilled_fields") or [],
        )
        answer_gaps = _answer_gap_recommendations(review_payload)
        upload_status = str((review_payload.get("file_upload_report") or {}).get("status") or "")
        status = _classify_review(review_payload)
        rows.append(
            {
                "company": _job_value(plan, "company"),
                "role": _job_value(plan, "title"),
                "job_id": _job_value(plan, "job_id") or application_dir.name,
                "ats": str(review_payload.get("resolved_ats") or _job_value(plan, "ats")),
                "status": status,
                "detected_fields_filled": str(metrics.get("detected_fields_filled", 0)),
                "detected_fields_total": str(metrics.get("detected_fields_total", 0)),
                "unfilled_required_visible_fields": str(metrics.get("unfilled_required_visible_fields", 0)),
                "unfilled_visible_fields": str(metrics.get("unfilled_visible_fields", 0)),
                "optional_no_value_fields": str(metrics.get("optional_no_value_fields", optional_no_value_count)),
                "resume_upload_status": upload_status,
                "actionable_skips": str(len(actionable_skips)),
                "answer_gap_count": str(len(answer_gaps)),
                "answer_gap_sources": _gap_sources(answer_gaps),
                "top_answer_gap": _top_gap_recommendation(answer_gaps),
                "application_dir": str(application_dir),
                "apply_url": _job_value(plan, "apply_url"),
                "review_json": str(review_path),
                "next_action": _next_action(status),
            }
        )

    return sorted(rows, key=lambda row: (row["status"], row["company"].lower(), row["role"].lower()))


def _next_action(status: str) -> str:
    if status == "ready_for_final_review":
        return "Open review artifacts and approve final submit"
    if status == "submitted":
        return "No action"
    if status == "needs_manual_field_review":
        return "Review unfilled required fields or resume upload"
    if status == "needs_runner_improvement":
        return "Improve field mapping or answer bank"
    if status == "needs_optional_field_review":
        return "Review optional empty fields or add answer-bank defaults"
    if status == "not_run":
        return "Run apply in review mode"
    if status == "blocked_account_required":
        return "Run with an authenticated browser session or add account-handling support"
    if status == "blocked_human_verification_required":
        return "Complete CAPTCHA/2FA in a browser session, then rerun apply review"
    if status == "blocked_submit_review_required":
        return "Resolve review blockers, then rerun submit with explicit approval"
    if status == "invalid_review":
        return "Rerun apply review"
    if status == "invalid_plan":
        return "Regenerate application packet"
    return "Review manually"


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_apply_qa_markdown(rows: list[dict[str, str]]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    lines = [
        "# Apply Runner QA",
        "",
        f"Generated At: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Summary",
        "",
        f"- Application packets: {len(rows)}",
    ]
    for status in sorted(counts):
        lines.append(f"- {status}: {counts[status]}")

    lines.extend(
        [
            "",
            "## Applications",
            "",
            "| Company | Role | ATS | Status | Coverage | Required Empty | Visible Empty | Policy Blank | Resume | Skips | Gaps | Next Action |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for row in rows:
        coverage = f"{row['detected_fields_filled']}/{row['detected_fields_total']}"
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_markdown(row["company"]),
                    _escape_markdown(row["role"]),
                    _escape_markdown(row["ats"]),
                    _escape_markdown(row["status"]),
                    coverage,
                    row["unfilled_required_visible_fields"],
                    row["unfilled_visible_fields"],
                    row["optional_no_value_fields"],
                    _escape_markdown(row["resume_upload_status"]),
                    row["actionable_skips"],
                    row["answer_gap_count"],
                    _escape_markdown(row["next_action"]),
                ]
            )
            + " |"
        )
    rows_with_gaps = [row for row in rows if int(row.get("answer_gap_count") or 0) > 0]
    if rows_with_gaps:
        lines.extend(
            [
                "",
                "## Answer Gap Recommendations",
                "",
                "| Company | Role | Status | Sources | Top Recommendation |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in rows_with_gaps:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_markdown(row["company"]),
                        _escape_markdown(row["role"]),
                        _escape_markdown(row["status"]),
                        _escape_markdown(row["answer_gap_sources"]),
                        _escape_markdown(row["top_answer_gap"]),
                    ]
                )
                + " |"
            )
    lines.append("")
    return "\n".join(lines)


def export_apply_qa(paths: ProjectPaths) -> tuple[Path, Path]:
    rows = collect_apply_qa_rows(paths)
    csv_path = paths.outputs_dir / "apply_review_qa.csv"
    md_path = paths.outputs_dir / "apply_review_qa.md"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "company",
        "role",
        "job_id",
        "ats",
        "status",
        "detected_fields_filled",
        "detected_fields_total",
        "unfilled_required_visible_fields",
        "unfilled_visible_fields",
        "optional_no_value_fields",
        "resume_upload_status",
        "actionable_skips",
        "answer_gap_count",
        "answer_gap_sources",
        "top_answer_gap",
        "application_dir",
        "apply_url",
        "review_json",
        "next_action",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    md_path.write_text(render_apply_qa_markdown(rows), encoding="utf-8")
    export_apply_gap_backlog(paths)
    return csv_path, md_path


def render_apply_gap_backlog_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Apply Gap Backlog",
        "",
        f"Generated At: {payload['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Gap items: {payload['total_gap_items']}",
        f"- Backlog groups: {len(payload['backlog'])}",
    ]
    if not payload["backlog"]:
        lines.extend(["", "No answer gaps are currently recorded in apply reviews.", ""])
        return "\n".join(lines)

    category_counts: dict[str, int] = {}
    for item in payload["backlog"]:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + int(item["count"])
    for category in sorted(category_counts):
        lines.append(f"- {category}: {category_counts[category]}")

    lines.extend(
        [
            "",
            "## Prioritized Fixes",
            "",
            "| Category | Count | Key | Companies | Field Examples | Recommended Fix |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for item in payload["backlog"]:
        key = item.get("suggested_answer_key") or item.get("source") or "unknown"
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_markdown(str(item["category"])),
                    str(item["count"]),
                    _escape_markdown(str(key)),
                    _escape_markdown(", ".join(item.get("companies", [])[:5])),
                    _escape_markdown(", ".join(item.get("field_examples", [])[:5])),
                    _escape_markdown(str(item.get("recommendation") or "")),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def export_apply_gap_backlog(paths: ProjectPaths) -> tuple[Path, Path]:
    items = collect_apply_gap_items(paths)
    backlog = build_apply_gap_backlog(items)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_gap_items": len(items),
        "backlog": backlog,
    }
    json_path = paths.outputs_dir / "apply_gap_backlog.json"
    md_path = paths.outputs_dir / "apply_gap_backlog.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_apply_gap_backlog_markdown(payload), encoding="utf-8")
    return json_path, md_path


def parse_status_filter(value: str | None) -> set[str]:
    if not value:
        return {"not_run"}
    return {item.strip() for item in value.split(",") if item.strip()}


def run_apply_batch(
    paths: ProjectPaths,
    *,
    statuses: set[str] | None = None,
    limit: int | None = None,
    ats: str = "auto",
    headless: bool = True,
    dry_run: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    statuses = statuses or {"not_run"}
    rows = collect_apply_qa_rows(paths)
    candidates = [row for row in rows if row["status"] in statuses]
    if limit is not None:
        candidates = candidates[:limit]

    results: list[dict[str, str]] = []
    for row in candidates:
        if dry_run:
            results.append(
                {
                    "job_id": row["job_id"],
                    "company": row["company"],
                    "role": row["role"],
                    "status": "would_run",
                    "message": "Dry run only",
                }
            )
            continue

        try:
            review = run_apply(
                paths,
                job_id=row["job_id"],
                ats=ats,
                submit=False,
                headless=headless,
                fill_legal_acknowledgements=False,
                storage_state_path=storage_state_path,
                user_data_dir=user_data_dir,
            )
            results.append(
                {
                    "job_id": row["job_id"],
                    "company": row["company"],
                    "role": row["role"],
                    "status": str(review.get("status") or "unknown"),
                    "message": "Review-mode apply completed",
                }
            )
        except Exception as exc:  # noqa: BLE001 - batch report should capture per-job failures and continue.
            results.append(
                {
                    "job_id": row["job_id"],
                    "company": row["company"],
                    "role": row["role"],
                    "status": "failed",
                    "message": str(exc),
                }
            )

    csv_path, md_path = export_apply_qa(paths)
    batch_json_path = paths.outputs_dir / "apply_batch_report.json"
    batch_md_path = paths.outputs_dir / "apply_batch_report.md"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "statuses": sorted(statuses),
        "limit": limit,
        "candidates": len(candidates),
        "completed": sum(1 for item in results if item["status"] not in {"failed", "would_run"}),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "results": results,
        "qa_csv": str(csv_path),
        "qa_markdown": str(md_path),
    }
    batch_json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    batch_md_path.write_text(render_apply_batch_markdown(payload), encoding="utf-8")
    return payload | {"batch_json": str(batch_json_path), "batch_markdown": str(batch_md_path)}


def render_apply_batch_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Apply Batch Report",
        "",
        f"Generated At: {payload['generated_at']}",
        f"Dry Run: {payload['dry_run']}",
        f"Statuses: {', '.join(payload['statuses'])}",
        f"Candidates: {payload['candidates']}",
        f"Completed: {payload['completed']}",
        f"Failed: {payload['failed']}",
        "",
        "## Results",
        "",
        "| Company | Role | Job ID | Status | Message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in payload["results"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_markdown(item.get("company", "")),
                    _escape_markdown(item.get("role", "")),
                    _escape_markdown(item.get("job_id", "")),
                    _escape_markdown(item.get("status", "")),
                    _escape_markdown(item.get("message", "")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## QA Outputs",
            "",
            f"- CSV: {payload['qa_csv']}",
            f"- Markdown: {payload['qa_markdown']}",
            "",
        ]
    )
    return "\n".join(lines)
