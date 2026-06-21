from __future__ import annotations

import csv
import re

from .models import ApplicationReadiness, JobPosting, JobScore, SourceProbeResult
from .paths import ProjectPaths


def _score_status(score: JobScore | None) -> str:
    if not score:
        return ""
    if score.sponsorship_compatibility == "incompatible":
        return "not_relevant_no_sponsorship"
    if score.sponsorship_compatibility == "unknown":
        return "needs_visa_review"
    return ""


def _readiness_status(readiness: ApplicationReadiness | None) -> str:
    if not readiness:
        return "not_inspected"
    return readiness.readiness_status


def _missing_items(readiness: ApplicationReadiness | None) -> str:
    if not readiness or not readiness.missing_assets:
        return ""
    return "; ".join(readiness.missing_assets)


def _recommended_action(score: JobScore | None, readiness: ApplicationReadiness | None) -> str:
    if not score:
        return "Review manually"
    if score.sponsorship_compatibility == "incompatible":
        return "Skip"
    if readiness:
        if readiness.readiness_status == "needs_prep":
            return "Prep before apply"
        if readiness.readiness_status == "needs_review":
            if score.referral_recommended:
                return "Review and apply with referral"
            return "Review before apply"
        if readiness.readiness_status == "ready":
            if score.referral_recommended:
                return "Apply with referral"
            return "Apply"
        if readiness.readiness_status == "blocked":
            return "Blocked"
    if score.priority in {"must_apply", "apply"}:
        if score.referral_recommended:
            return "Inspect application and apply with referral"
        return "Inspect application"
    if score.priority == "maybe":
        return "Review if interested"
    return "Skip"


def _underscore_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "job"


def _application_packet_path(paths: ProjectPaths, job: JobPosting) -> str:
    packet_dir = paths.outputs_applications_dir / f"{_underscore_slug(job.company)}_{_underscore_slug(job.title)}"
    return str(packet_dir) if packet_dir.exists() else ""


def _tailored_resume_path(paths: ProjectPaths, job: JobPosting) -> str:
    packet_dir = paths.outputs_applications_dir / f"{_underscore_slug(job.company)}_{_underscore_slug(job.title)}"
    if not packet_dir.exists():
        return ""
    matches = sorted(packet_dir.glob("*_Resume.md"))
    return str(matches[0]) if matches else ""


def export_ranked_jobs(
    paths: ProjectPaths,
    jobs: list[JobPosting],
    scores: dict[str, JobScore],
    readiness_by_job: dict[str, ApplicationReadiness] | None = None,
) -> None:
    readiness_by_job = readiness_by_job or {}
    with paths.ranked_jobs_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date_found",
                "company",
                "role",
                "fit_score",
                "priority",
                "lane",
                "resume_version",
                "location",
                "apply_url",
                "referral_recommended",
                "sponsorship_compatibility",
                "readiness_status",
                "missing_assets",
                "tailored_resume_path",
                "application_packet_path",
                "status",
                "action",
                "job_url",
                "remote_policy",
                "salary_range",
                "ats",
                "notes",
            ],
        )
        writer.writeheader()
        ranked_jobs = sorted(
            jobs,
            key=lambda job: (
                -(scores.get(job.job_id).fit_score if scores.get(job.job_id) else -1),
                job.company.lower(),
                job.title.lower(),
            ),
        )
        for job in ranked_jobs:
            score = scores.get(job.job_id)
            readiness = readiness_by_job.get(job.job_id)
            writer.writerow(
                {
                    "date_found": job.date_found.isoformat(),
                    "company": job.company,
                    "role": job.title,
                    "fit_score": score.fit_score if score else "",
                    "priority": score.priority if score else "",
                    "lane": score.lane if score else "",
                    "resume_version": score.resume_version if score else "",
                    "location": job.location or "",
                    "apply_url": job.apply_url or "",
                    "referral_recommended": score.referral_recommended if score else "",
                    "sponsorship_compatibility": score.sponsorship_compatibility if score else "",
                    "readiness_status": _readiness_status(readiness),
                    "missing_assets": _missing_items(readiness),
                    "tailored_resume_path": _tailored_resume_path(paths, job),
                    "application_packet_path": _application_packet_path(paths, job),
                    "status": _score_status(score),
                    "action": _recommended_action(score, readiness),
                    "job_url": job.job_url,
                    "remote_policy": job.remote_policy or "",
                    "salary_range": job.salary_range or "",
                    "ats": job.ats or "",
                    "notes": score.reason_summary if score else "",
                }
            )


def export_shortlist(
    paths: ProjectPaths,
    jobs: list[JobPosting],
    scores: dict[str, JobScore],
    readiness_by_job: dict[str, ApplicationReadiness] | None = None,
    source_health_by_company: dict[str, str] | None = None,
    limit: int = 20,
    min_score: int = 50,
) -> None:
    readiness_by_job = readiness_by_job or {}
    source_health_by_company = source_health_by_company or {}
    ranked_jobs = sorted(
        jobs,
        key=lambda job: (
            -(scores.get(job.job_id).fit_score if scores.get(job.job_id) else -1),
            job.company.lower(),
            job.title.lower(),
        ),
    )
    candidates = [
        job
        for job in ranked_jobs
        if scores.get(job.job_id)
        and scores[job.job_id].fit_score >= min_score
        and scores[job.job_id].priority != "skip"
        and scores[job.job_id].sponsorship_compatibility != "incompatible"
    ][:limit]

    lines = [
        "# Job Search Shortlist",
        "",
        f"Generated from ranked jobs with minimum score {min_score}.",
        "",
        "## Summary",
        "",
        f"- Ranked jobs considered: {len(jobs)}",
        f"- Shortlisted roles: {len(candidates)}",
        f"- Minimum score: {min_score}",
        "",
    ]
    if not candidates:
        lines.extend(
            [
                "No roles currently meet the shortlist threshold.",
                "",
                "Recommended next action: run discovery against more reachable sources or lower the threshold for review-only exploration.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Roles",
                "",
                "| Rank | Company | Role | Score | Lane | Sponsorship | Source | Action | Link |",
                "|---:|---|---|---:|---|---|---|---|---|",
            ]
        )
        for index, job in enumerate(candidates, start=1):
            score = scores[job.job_id]
            readiness = readiness_by_job.get(job.job_id)
            source_status = source_health_by_company.get(job.company, "unknown")
            source_label = _source_label(source_status, job.ats)
            link = job.apply_url or job.job_url
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        _escape_markdown_table(job.company),
                        _escape_markdown_table(job.title),
                        str(score.fit_score),
                        _escape_markdown_table(score.lane),
                        _escape_markdown_table(score.sponsorship_compatibility),
                        _escape_markdown_table(source_label),
                        _escape_markdown_table(_recommended_action(score, readiness)),
                        f"[open]({link})",
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.extend(["## Why These", ""])
        for job in candidates:
            score = scores[job.job_id]
            lines.append(
                f"- **{_escape_markdown_table(job.company)} - {_escape_markdown_table(job.title)}**: "
                f"{_escape_markdown_table(score.reason_summary)}"
            )
        lines.append("")

    paths.shortlist_md.write_text("\n".join(lines), encoding="utf-8")


def export_shortlist_from_ranked_rows(
    paths: ProjectPaths,
    rows: list[dict[str, str]],
    limit: int = 20,
    min_score: int = 50,
) -> None:
    def row_score(row: dict[str, str]) -> int:
        try:
            return int(row.get("fit_score") or 0)
        except ValueError:
            return 0

    ranked_rows = sorted(
        rows,
        key=lambda row: (
            -row_score(row),
            (row.get("company") or "").lower(),
            (row.get("role") or "").lower(),
        ),
    )
    candidates = [
        row
        for row in ranked_rows
        if row_score(row) >= min_score
        and row.get("priority") != "skip"
        and row.get("sponsorship_compatibility") != "incompatible"
    ][:limit]

    lines = [
        "# Job Search Shortlist",
        "",
        f"Generated from {paths.ranked_jobs_csv.name} with minimum score {min_score}.",
        "",
        "## Summary",
        "",
        f"- Ranked jobs considered: {len(rows)}",
        f"- Shortlisted roles: {len(candidates)}",
        f"- Minimum score: {min_score}",
        "",
    ]
    if candidates:
        lines.extend(
            [
                "## Roles",
                "",
                "| Rank | Company | Role | Score | Lane | Sponsorship | Source | Action | Link |",
                "|---:|---|---|---:|---|---|---|---|---|",
            ]
        )
        for index, row in enumerate(candidates, start=1):
            link = row.get("apply_url") or row.get("job_url") or ""
            source_label = _source_label(
                row.get("source_health", "") or row.get("source", "") or "current_csv",
                row.get("ats", ""),
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        _escape_markdown_table(row.get("company", "")),
                        _escape_markdown_table(row.get("role", "")),
                        str(row_score(row)),
                        _escape_markdown_table(row.get("lane", "")),
                        _escape_markdown_table(row.get("sponsorship_compatibility", "")),
                        _escape_markdown_table(source_label),
                        _escape_markdown_table(row.get("action", "")),
                        f"[open]({link})" if link else "",
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.extend(["## Why These", ""])
        for row in candidates:
            lines.append(
                f"- **{_escape_markdown_table(row.get('company', ''))} - {_escape_markdown_table(row.get('role', ''))}**: "
                f"{_escape_markdown_table(row.get('notes', ''))}"
            )
        lines.append("")
    else:
        lines.extend(
            [
                "No roles currently meet the shortlist threshold.",
                "",
                "Recommended next action: run discovery against more reachable sources or lower the threshold for review-only exploration.",
                "",
            ]
        )

    paths.shortlist_md.write_text("\n".join(lines), encoding="utf-8")


def _source_label(source_health: str, adapter: str | None) -> str:
    if adapter:
        return f"{source_health} / {adapter}"
    return source_health


def export_application_requirements(
    paths: ProjectPaths,
    jobs: dict[str, JobPosting],
    readiness_items: list[ApplicationReadiness],
) -> None:
    with paths.application_requirements_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "company",
                "role",
                "field_label",
                "required",
                "draft_answer",
                "confidence",
                "missing_input",
                "notes",
            ],
        )
        writer.writeheader()
        for readiness in readiness_items:
            job = jobs.get(readiness.job_id)
            if not job:
                continue
            for requirement in readiness.requirements:
                writer.writerow(
                    {
                        "company": job.company,
                        "role": job.title,
                        "field_label": requirement.field_label,
                        "required": requirement.required,
                        "draft_answer": requirement.draft_answer or "",
                        "confidence": requirement.answer_confidence,
                        "missing_input": requirement.missing_input,
                        "notes": requirement.notes or "",
                    }
                )


def export_source_health(paths: ProjectPaths, probes: list[SourceProbeResult]) -> None:
    with paths.source_health_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "company",
                "status",
                "configured_source",
                "detected_adapter",
                "source_url",
                "job_count",
                "sample_titles",
                "failure_kind",
                "failure_hint",
                "notes",
            ],
        )
        writer.writeheader()
        for probe in probes:
            writer.writerow(
                {
                    "company": probe.company,
                    "status": probe.status,
                    "configured_source": probe.configured_source or "",
                    "detected_adapter": probe.detected_adapter or "",
                    "source_url": probe.source_url or "",
                    "job_count": probe.job_count,
                    "sample_titles": "; ".join(probe.sample_titles),
                    "failure_kind": probe.failure_kind or "",
                    "failure_hint": probe.failure_hint or "",
                    "notes": probe.notes or "",
                }
            )

    lines = [
        "# Source Health",
        "",
        "| Company | Status | Adapter | Jobs | Notes |",
        "|---|---|---|---:|---|",
    ]
    for probe in probes:
        notes = probe.notes or probe.failure_hint or ""
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_markdown_table(probe.company),
                    _escape_markdown_table(probe.status),
                    _escape_markdown_table(probe.detected_adapter or probe.configured_source or ""),
                    str(probe.job_count),
                    _escape_markdown_table(notes),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("## Samples")
    lines.append("")
    for probe in probes:
        samples = "; ".join(probe.sample_titles) if probe.sample_titles else "None"
        lines.append(f"- **{probe.company}**: {samples}")
    paths.source_health_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _escape_markdown_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
