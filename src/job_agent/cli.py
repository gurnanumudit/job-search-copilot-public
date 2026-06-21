from __future__ import annotations

import argparse
import csv
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .apply_qa import export_apply_gap_backlog, export_apply_qa, parse_status_filter, run_apply_batch
from .apply_preflight import export_apply_preflight
from .apply_runner import capture_browser_session_sync, run_apply
from .discovery import discover_company_jobs, enrich_job_posting, probe_company_source, render_job_markdown
from .exporters import (
    export_application_requirements,
    export_ranked_jobs,
    export_shortlist,
    export_shortlist_from_ranked_rows,
    export_source_health,
)
from .inspector import inspect_application_form_sync
from .markdown import parse_job_markdown, parse_job_url
from .package_builder import build_package_report
from .paths import ProjectPaths, discover_project_root
from .profile import load_companies, load_exclusions, load_profile, load_role_keywords
from .scoring import score_job
from .storage import (
    ensure_directories,
    list_jobs,
    list_readiness,
    list_scores,
    load_job,
    load_readiness,
    load_score,
    save_job,
    save_readiness,
    save_score,
    save_text,
)
from .templates import template_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m job_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create project folders and example files.")

    discover_parser = subparsers.add_parser("discover", help="Discover jobs from target company sources.")
    discover_parser.add_argument("--companies", required=True)
    discover_parser.add_argument("--mode", choices=["fast", "deep", "two-phase"], default="deep")
    discover_parser.add_argument("--enrich-top-n", type=int, default=25)
    discover_parser.add_argument(
        "--source-status",
        help="Comma-separated source health statuses to include from outputs/source_health.csv, e.g. workflow_ready,partial.",
    )

    probe_parser = subparsers.add_parser("probe-sources", help="Check company source health without saving jobs.")
    probe_parser.add_argument("--companies", required=True)
    probe_parser.add_argument("--workers", type=int, default=1)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest a pasted job description file.")
    ingest_parser.add_argument("--file", required=True)

    score_parser = subparsers.add_parser("score", help="Score an ingested job.")
    score_parser.add_argument("--job-id", required=True)

    enrich_parser = subparsers.add_parser("enrich", help="Enrich already discovered jobs with full descriptions.")
    enrich_parser.add_argument("--top-n", type=int, default=25)
    enrich_parser.add_argument("--min-score", type=int, default=60)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect an application page without submitting.")
    inspect_parser.add_argument("--job-id", required=True)

    package_parser = subparsers.add_parser("package", help="Generate a package report for a job.")
    package_group = package_parser.add_mutually_exclusive_group(required=True)
    package_group.add_argument("--job-id")
    package_group.add_argument("--file")
    package_group.add_argument("--job-url")
    package_parser.add_argument("--apply-url")

    apply_parser = subparsers.add_parser("apply", help="Fill an application from its generated application_plan.json.")
    apply_parser.add_argument("--job-id", required=True)
    apply_parser.add_argument(
        "--ats",
        choices=["auto", "ashby", "greenhouse", "lever", "workday", "generic"],
        default="auto",
    )
    apply_parser.add_argument("--submit", action="store_true", help="Submit after filling. Omit for review-only mode.")
    apply_parser.add_argument("--headed", action="store_true", help="Show the browser while filling.")
    apply_parser.add_argument(
        "--fill-legal-acknowledgements",
        action="store_true",
        help="Check arbitration/certification acknowledgements after explicit user review.",
    )
    apply_session_group = apply_parser.add_mutually_exclusive_group()
    apply_session_group.add_argument(
        "--storage-state",
        help="Path to a Playwright storage_state JSON file with an already-authenticated browser session.",
    )
    apply_session_group.add_argument(
        "--user-data-dir",
        help="Path to a persistent Chromium profile directory to reuse for authenticated application flows.",
    )

    apply_batch_parser = subparsers.add_parser("apply-batch", help="Run review-mode apply over multiple packets.")
    apply_batch_parser.add_argument(
        "--status",
        default="not_run",
        help="Comma-separated QA statuses to run, e.g. not_run,invalid_review.",
    )
    apply_batch_parser.add_argument("--limit", type=int, help="Maximum packets to run.")
    apply_batch_parser.add_argument(
        "--ats",
        choices=["auto", "ashby", "greenhouse", "lever", "workday", "generic"],
        default="auto",
    )
    apply_batch_parser.add_argument("--headed", action="store_true", help="Show browser windows while filling.")
    apply_batch_parser.add_argument("--dry-run", action="store_true", help="List selected packets without opening browsers.")
    apply_batch_session_group = apply_batch_parser.add_mutually_exclusive_group()
    apply_batch_session_group.add_argument(
        "--storage-state",
        help="Path to a Playwright storage_state JSON file with an already-authenticated browser session.",
    )
    apply_batch_session_group.add_argument(
        "--user-data-dir",
        help="Path to a persistent Chromium profile directory to reuse for authenticated application flows.",
    )

    subparsers.add_parser("apply-qa", help="Summarize apply_review.json coverage across application packets.")
    subparsers.add_parser("apply-gaps", help="Aggregate reusable answer and platform-runner gaps from apply reviews.")
    subparsers.add_parser("apply-preflight", help="Check profile answers needed for self-serve application filling.")

    capture_session_parser = subparsers.add_parser(
        "capture-session",
        help="Open a login page and save a Playwright storage-state JSON for authenticated apply runs.",
    )
    capture_session_parser.add_argument("--url", required=True, help="Login or candidate portal URL to open.")
    capture_session_parser.add_argument("--output", required=True, help="Path where storage-state JSON should be saved.")
    capture_session_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="How long to keep the browser open for login before saving session state.",
    )
    capture_session_parser.add_argument("--headless", action="store_true", help="Run without showing the browser.")

    export_parser = subparsers.add_parser("export", help="Export tracker files.")
    export_parser.add_argument("--format", choices=["csv"], required=True)
    export_parser.add_argument("--scope", choices=["latest", "all"], default="latest")

    shortlist_parser = subparsers.add_parser("shortlist", help="Export a Markdown shortlist from scored jobs.")
    shortlist_parser.add_argument("--limit", type=int, default=20)
    shortlist_parser.add_argument("--min-score", type=int, default=50)
    return parser


def _project_paths() -> ProjectPaths:
    return ProjectPaths(discover_project_root())


def handle_init(paths: ProjectPaths) -> str:
    ensure_directories(paths)
    for file_path, content in template_files(paths.root).items():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")
    return f"Initialized project at {paths.root}"


def handle_ingest(paths: ProjectPaths, file_path: str) -> str:
    ensure_directories(paths)
    source_path = Path(file_path)
    target_raw_path = paths.jobs_raw_dir / source_path.name
    if source_path.resolve() != target_raw_path.resolve():
        shutil.copy2(source_path, target_raw_path)
    job = parse_job_markdown(target_raw_path)
    save_job(paths, job)
    return f"Ingested {job.job_id}"


def _ingest_job_file(paths: ProjectPaths, file_path: str, apply_url: str | None = None):
    source_path = Path(file_path)
    target_raw_path = paths.jobs_raw_dir / source_path.name
    if source_path.resolve() != target_raw_path.resolve():
        shutil.copy2(source_path, target_raw_path)
    job = parse_job_markdown(target_raw_path)
    if apply_url:
        job = job.model_copy(update={"apply_url": apply_url})
    save_job(paths, job)
    return job


def _ingest_job_url(paths: ProjectPaths, job_url: str, apply_url: str | None = None):
    job = parse_job_url(job_url)
    if apply_url:
        job = job.model_copy(update={"apply_url": apply_url})
    save_job(paths, job)
    raw_path = paths.jobs_raw_dir / f"{job.job_id}.md"
    raw_path.write_text(render_job_markdown(job), encoding="utf-8")
    return job


def handle_score(paths: ProjectPaths, job_id: str) -> str:
    ensure_directories(paths)
    profile = load_profile(paths)
    keywords = load_role_keywords(paths)
    job = load_job(paths, job_id)
    score = score_job(job, profile, keywords)
    save_score(paths, score)
    return f"Scored {job.job_id}: {score.fit_score}/100 using {score.resume_version}"


def _save_and_score_job(paths: ProjectPaths, job, profile, keywords):
    save_job(paths, job)
    save_text_path = paths.jobs_raw_dir / f"{job.job_id}.md"
    save_text_path.write_text(render_job_markdown(job), encoding="utf-8")
    score = score_job(job, profile, keywords)
    save_score(paths, score)
    return score


def _rank_jobs_by_score(jobs, scores):
    return sorted(
        jobs,
        key=lambda job: (
            -(scores.get(job.job_id).fit_score if scores.get(job.job_id) else -1),
            job.company.lower(),
            job.title.lower(),
        ),
    )


def _source_health_by_company(paths: ProjectPaths) -> dict[str, str]:
    if not paths.source_health_csv.exists():
        return {}
    with paths.source_health_csv.open(newline="", encoding="utf-8") as handle:
        return {
            row["company"]: row["status"]
            for row in csv.DictReader(handle)
            if row.get("company") and row.get("status")
        }


def _readiness_by_job_for_export(paths: ProjectPaths, jobs) -> dict[str, object]:
    readiness_by_job = {}
    for job in jobs:
        readiness_path = paths.readiness_dir / f"{job.job_id}.json"
        if job.apply_url and readiness_path.exists():
            readiness_by_job[job.job_id] = load_readiness(paths, job.job_id)
    return readiness_by_job


def _save_latest_discovery_job_ids(paths: ProjectPaths, jobs) -> None:
    job_ids = sorted({job.job_id for job in jobs})
    save_text(paths.latest_discovery_job_ids, "\n".join(job_ids) + ("\n" if job_ids else ""))


def _latest_discovery_job_ids(paths: ProjectPaths) -> set[str]:
    if not paths.latest_discovery_job_ids.exists():
        return set()
    return {
        line.strip()
        for line in paths.latest_discovery_job_ids.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def handle_discover(
    paths: ProjectPaths,
    companies_file: str,
    mode: str = "deep",
    enrich_top_n: int = 25,
    source_status: str | None = None,
) -> str:
    ensure_directories(paths)
    profile = load_profile(paths)
    keywords = load_role_keywords(paths)
    exclusions = load_exclusions(paths)
    companies = load_companies(paths, config_path=Path(companies_file))
    skipped_by_health = 0
    if source_status:
        allowed_statuses = {status.strip() for status in source_status.split(",") if status.strip()}
        health_by_company = _source_health_by_company(paths)
        original_count = len(companies.companies)
        companies = companies.model_copy(
            update={
                "companies": [
                    company
                    for company in companies.companies
                    if health_by_company.get(company.name) in allowed_statuses
                ]
            }
        )
        skipped_by_health = original_count - len(companies.companies)

    discovered_jobs = []
    scores = {}
    failures: list[tuple[str, str]] = []
    for company in companies.companies:
        try:
            company_jobs = discover_company_jobs(company, exclusions, enrich_details=mode == "deep")
        except Exception as exc:
            failures.append((company.name, str(exc)))
            continue
        for job in company_jobs:
            scores[job.job_id] = _save_and_score_job(paths, job, profile, keywords)
            discovered_jobs.append(job)

    enriched_count = 0
    if mode == "two-phase" and enrich_top_n > 0:
        jobs_by_id = {job.job_id: job for job in discovered_jobs}
        for job in _rank_jobs_by_score(discovered_jobs, scores)[:enrich_top_n]:
            enriched_job = enrich_job_posting(job)
            if enriched_job.description != job.description or enriched_job.apply_url != job.apply_url:
                jobs_by_id[job.job_id] = enriched_job
                scores[enriched_job.job_id] = _save_and_score_job(paths, enriched_job, profile, keywords)
                enriched_count += 1
        discovered_jobs = list(jobs_by_id.values())

    _save_latest_discovery_job_ids(paths, discovered_jobs)
    readiness_by_job = _readiness_by_job_for_export(paths, discovered_jobs)
    export_ranked_jobs(paths, discovered_jobs, scores, readiness_by_job)
    export_shortlist(
        paths,
        discovered_jobs,
        scores,
        readiness_by_job,
        source_health_by_company=_source_health_by_company(paths),
    )
    if failures:
        failure_lines = ["# Discovery Failures", ""]
        for company_name, error in failures:
            failure_lines.append(f"- {company_name}: {error}")
        save_text(paths.outputs_dir / "discovery_failures.md", "\n".join(failure_lines) + "\n")
    else:
        save_text(
            paths.outputs_dir / "discovery_failures.md",
            "# Discovery Failures\n\nNo discovery failures in the latest run.\n",
        )
    summary = f"Discovered {len(discovered_jobs)} jobs"
    if source_status:
        summary += f" from {len(companies.companies)} source(s); skipped {skipped_by_health} by source health"
    if mode == "two-phase":
        summary += f"; enriched {enriched_count} top job(s)"
    summary += f" and exported {paths.ranked_jobs_csv} plus {paths.shortlist_md}"
    if failures:
        summary += f"; {len(failures)} source(s) failed. See {paths.outputs_dir / 'discovery_failures.md'}"
    return summary


def handle_enrich(paths: ProjectPaths, top_n: int = 25, min_score: int = 60) -> str:
    ensure_directories(paths)
    profile = load_profile(paths)
    keywords = load_role_keywords(paths)
    jobs = list_jobs(paths)
    scores = {score.job_id: score for score in list_scores(paths)}
    candidates = [
        job
        for job in jobs
        if scores.get(job.job_id) and scores[job.job_id].fit_score >= min_score
    ]
    enriched_count = 0
    for job in _rank_jobs_by_score(candidates, scores)[:top_n]:
        enriched_job = enrich_job_posting(job)
        if enriched_job.description != job.description or enriched_job.apply_url != job.apply_url:
            scores[enriched_job.job_id] = _save_and_score_job(paths, enriched_job, profile, keywords)
            enriched_count += 1
    jobs = list_jobs(paths)
    readiness_by_job = _readiness_by_job_for_export(paths, jobs)
    export_ranked_jobs(paths, jobs, scores, readiness_by_job)
    export_shortlist(
        paths,
        jobs,
        scores,
        readiness_by_job,
        source_health_by_company=_source_health_by_company(paths),
    )
    return f"Enriched {enriched_count} job(s) and exported {paths.ranked_jobs_csv} plus {paths.shortlist_md}"


def handle_probe_sources(paths: ProjectPaths, companies_file: str, workers: int = 1) -> str:
    ensure_directories(paths)
    companies = load_companies(paths, config_path=Path(companies_file))
    if workers <= 1:
        probes = [probe_company_source(company) for company in companies.companies]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            probes = list(executor.map(probe_company_source, companies.companies))
    export_source_health(paths, probes)
    ready_count = sum(1 for probe in probes if probe.status == "workflow_ready")
    partial_count = sum(1 for probe in probes if probe.status == "partial")
    blocked_count = sum(1 for probe in probes if probe.status == "blocked")
    needs_adapter_count = sum(1 for probe in probes if probe.status == "needs_adapter")
    return (
        f"Probed {len(probes)} sources: {ready_count} ready, {partial_count} partial, "
        f"{needs_adapter_count} need adapter, {blocked_count} blocked. "
        f"See {paths.source_health_csv} and {paths.source_health_md}"
    )


def handle_inspect(paths: ProjectPaths, job_id: str) -> str:
    ensure_directories(paths)
    profile = load_profile(paths)
    job = load_job(paths, job_id)
    score = None
    score_path = paths.scores_dir / f"{job_id}.json"
    if score_path.exists():
        score = load_score(paths, job_id)
    result = inspect_application_form_sync(paths, job, profile, score)
    save_readiness(paths, result.readiness)
    blocked = f" blocked={result.blocked_reason}" if result.blocked_reason else ""
    return f"Inspected {job.job_id}: {result.readiness.readiness_status}.{blocked}"


def handle_package(
    paths: ProjectPaths,
    job_id: str | None = None,
    file_path: str | None = None,
    job_url: str | None = None,
    apply_url: str | None = None,
) -> str:
    ensure_directories(paths)
    if file_path:
        job = _ingest_job_file(paths, file_path, apply_url=apply_url)
        job_id = job.job_id
    elif job_url:
        job = _ingest_job_url(paths, job_url, apply_url=apply_url)
        job_id = job.job_id
    elif job_id:
        job = load_job(paths, job_id)
        if apply_url and apply_url != job.apply_url:
            job = job.model_copy(update={"apply_url": apply_url})
            save_job(paths, job)
    else:
        raise ValueError("Either job_id or file_path is required.")

    score_path = paths.scores_dir / f"{job.job_id}.json"
    if score_path.exists():
        score = load_score(paths, job.job_id)
    else:
        profile = load_profile(paths)
        keywords = load_role_keywords(paths)
        score = score_job(job, profile, keywords)
        save_score(paths, score)

    readiness = None
    readiness_path = paths.readiness_dir / f"{job.job_id}.json"
    if job.apply_url and readiness_path.exists():
        readiness = load_readiness(paths, job.job_id)
    elif job.apply_url:
        profile = load_profile(paths)
        result = inspect_application_form_sync(paths, job, profile, score)
        readiness = result.readiness
        save_readiness(paths, readiness)

    generated = build_package_report(paths, job, score, load_profile(paths), readiness)
    return f"Generated application packet at {generated.application_dir}"


def handle_apply(
    paths: ProjectPaths,
    job_id: str,
    ats: str = "auto",
    submit: bool = False,
    headed: bool = False,
    fill_legal_acknowledgements: bool = False,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
) -> str:
    ensure_directories(paths)
    review = run_apply(
        paths,
        job_id=job_id,
        ats=ats,
        submit=submit,
        headless=not headed,
        fill_legal_acknowledgements=fill_legal_acknowledgements,
        storage_state_path=storage_state,
        user_data_dir=user_data_dir,
    )
    if review["status"] == "submitted":
        return f"Submitted {job_id}. See apply_review.md for confirmation."
    if review["status"] == "blocked_submit_review_required":
        return f"Submit blocked for {job_id}; resolve apply_review.md blockers before rerunning submit."
    return f"Prepared {job_id} for review without submitting. See apply_review.md and apply_review/review.png."


def handle_apply_qa(paths: ProjectPaths) -> str:
    ensure_directories(paths)
    csv_path, md_path = export_apply_qa(paths)
    return f"Exported {csv_path} and {md_path}"


def handle_apply_gaps(paths: ProjectPaths) -> str:
    ensure_directories(paths)
    json_path, md_path = export_apply_gap_backlog(paths)
    return f"Exported {json_path} and {md_path}"


def handle_apply_preflight(paths: ProjectPaths) -> str:
    ensure_directories(paths)
    json_path, md_path = export_apply_preflight(paths)
    return f"Exported {json_path} and {md_path}"


def handle_apply_batch(
    paths: ProjectPaths,
    status_filter: str = "not_run",
    limit: int | None = None,
    ats: str = "auto",
    headed: bool = False,
    dry_run: bool = False,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
) -> str:
    ensure_directories(paths)
    result = run_apply_batch(
        paths,
        statuses=parse_status_filter(status_filter),
        limit=limit,
        ats=ats,
        headless=not headed,
        dry_run=dry_run,
        storage_state_path=storage_state,
        user_data_dir=user_data_dir,
    )
    return (
        f"Apply batch selected {result['candidates']} packet(s), "
        f"completed {result['completed']}, failed {result['failed']}. "
        f"See {result['batch_markdown']}."
    )


def handle_capture_session(url: str, output: str, wait_seconds: int = 120, headless: bool = False) -> str:
    saved_path = capture_browser_session_sync(
        url=url,
        output_path=output,
        wait_seconds=wait_seconds,
        headless=headless,
    )
    return f"Saved authenticated browser session to {saved_path}"


def handle_export(paths: ProjectPaths, scope: str = "latest") -> str:
    ensure_directories(paths)
    jobs = list_jobs(paths)
    if scope == "latest":
        latest_job_ids = _latest_discovery_job_ids(paths)
        if latest_job_ids:
            jobs = [job for job in jobs if job.job_id in latest_job_ids]
    scores = {score.job_id: score for score in list_scores(paths)}
    readiness_items = list_readiness(paths)
    jobs_by_id = {job.job_id: job for job in jobs}
    readiness_by_job = {
        readiness.job_id: readiness
        for readiness in readiness_items
        if jobs_by_id.get(readiness.job_id) and jobs_by_id[readiness.job_id].apply_url
    }
    export_ranked_jobs(paths, jobs, scores, readiness_by_job)
    export_application_requirements(paths, jobs_by_id, list(readiness_by_job.values()))
    export_shortlist(
        paths,
        jobs,
        scores,
        readiness_by_job,
        source_health_by_company=_source_health_by_company(paths),
    )
    return f"Exported {paths.ranked_jobs_csv}, {paths.application_requirements_csv}, and {paths.shortlist_md} ({scope} scope)"


def handle_shortlist(paths: ProjectPaths, limit: int = 20, min_score: int = 50) -> str:
    ensure_directories(paths)
    if not paths.ranked_jobs_csv.exists():
        raise FileNotFoundError(f"{paths.ranked_jobs_csv} does not exist. Run discover or export first.")
    source_health = _source_health_by_company(paths)
    with paths.ranked_jobs_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["source_health"] = source_health.get(row.get("company", ""), "unknown")
    export_shortlist_from_ranked_rows(paths, rows, limit=limit, min_score=min_score)
    return f"Exported {paths.shortlist_md}"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = _project_paths()

    if args.command == "init":
        print(handle_init(paths))
    elif args.command == "discover":
        print(
            handle_discover(
                paths,
                args.companies,
                mode=args.mode,
                enrich_top_n=args.enrich_top_n,
                source_status=args.source_status,
            )
        )
    elif args.command == "probe-sources":
        print(handle_probe_sources(paths, args.companies, workers=args.workers))
    elif args.command == "ingest":
        print(handle_ingest(paths, args.file))
    elif args.command == "score":
        print(handle_score(paths, args.job_id))
    elif args.command == "enrich":
        print(handle_enrich(paths, top_n=args.top_n, min_score=args.min_score))
    elif args.command == "inspect":
        print(handle_inspect(paths, args.job_id))
    elif args.command == "package":
        print(handle_package(paths, job_id=args.job_id, file_path=args.file, job_url=args.job_url, apply_url=args.apply_url))
    elif args.command == "apply":
        print(
            handle_apply(
                paths,
                args.job_id,
                ats=args.ats,
                submit=args.submit,
                headed=args.headed,
                fill_legal_acknowledgements=args.fill_legal_acknowledgements,
                storage_state=args.storage_state,
                user_data_dir=args.user_data_dir,
            )
        )
    elif args.command == "apply-batch":
        print(
            handle_apply_batch(
                paths,
                status_filter=args.status,
                limit=args.limit,
                ats=args.ats,
                headed=args.headed,
                dry_run=args.dry_run,
                storage_state=args.storage_state,
                user_data_dir=args.user_data_dir,
            )
        )
    elif args.command == "apply-qa":
        print(handle_apply_qa(paths))
    elif args.command == "apply-gaps":
        print(handle_apply_gaps(paths))
    elif args.command == "apply-preflight":
        print(handle_apply_preflight(paths))
    elif args.command == "capture-session":
        print(handle_capture_session(args.url, args.output, wait_seconds=args.wait_seconds, headless=args.headless))
    elif args.command == "export":
        print(handle_export(paths, scope=args.scope))
    elif args.command == "shortlist":
        print(handle_shortlist(paths, limit=args.limit, min_score=args.min_score))
    else:
        parser.error(f"Unsupported command: {args.command}")
    return 0
