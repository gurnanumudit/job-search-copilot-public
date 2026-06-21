from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .markdown import slugify
from .models import CompanySourceConfig, ExclusionsConfig, JobPosting, SourceProbeResult


USER_AGENT = "job-search-copilot/0.1 (+local-first)"


class DiscoveryFetchError(RuntimeError):
    def __init__(self, url: str, kind: str, message: str, hint: str | None = None, attempts: list[str] | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.kind = kind
        self.hint = hint
        self.attempts = attempts or []

    def __str__(self) -> str:
        summary = f"[{self.kind}] {super().__str__()}"
        if self.hint:
            summary += f" Hint: {self.hint}"
        if self.attempts:
            summary += f" Attempts: {' | '.join(self.attempts)}"
        return summary


def discover_company_jobs(
    company: CompanySourceConfig,
    exclusions: ExclusionsConfig | None = None,
    enrich_details: bool = True,
) -> list[JobPosting]:
    if company.ats == "stripe_reader":
        jobs = _discover_stripe_reader(company, enrich_details=enrich_details)
    elif company.ashby_board_slug or company.ashby_jobs_url:
        jobs = _discover_ashby(company, enrich_details=enrich_details)
    elif company.greenhouse_board_token:
        jobs = _discover_greenhouse(company)
    elif company.lever_company:
        jobs = _discover_lever(company)
    elif company.jobs_json_url:
        jobs = _discover_generic_json(company)
    elif company.careers_url:
        jobs = _discover_generic_html(company, enrich_details=enrich_details)
    else:
        jobs = []
    filtered_jobs = [job for job in jobs if not _is_excluded(job, exclusions)]
    filtered_jobs = [job for job in filtered_jobs if _matches_company_include_keywords(job, company)]
    if company.max_jobs is not None:
        filtered_jobs = filtered_jobs[: company.max_jobs]
    return filtered_jobs


def enrich_job_posting(job: JobPosting) -> JobPosting:
    if job.ats != "ashby" and "jobs.ashbyhq.com" not in job.job_url:
        return job
    try:
        payload = _extract_ashby_app_data(_load_text(job.job_url), job.job_url)
    except Exception:
        return job
    posting = payload.get("posting")
    if not isinstance(posting, dict):
        return job
    posting_id = _first_text(posting, "id", "jobPostingId", "jobId") or job.job_url.rstrip("/").split("/")[-1]
    job_url = urljoin(job.job_url.rsplit("/", 1)[0] + "/", posting_id)
    return job.model_copy(
        update={
            "title": posting.get("title") or job.title,
            "location": _ashby_location(posting) or job.location,
            "remote_policy": _normalize_location(posting.get("workplaceType")) or job.remote_policy,
            "salary_range": _normalize_location(
                posting.get("compensationTierSummary") or posting.get("scrapeableCompensationSalarySummary")
            )
            or job.salary_range,
            "employment_type": _normalize_location(posting.get("employmentType")) or job.employment_type,
            "job_url": job_url,
            "apply_url": urljoin(job_url.rstrip("/") + "/", "application"),
            "ats": job.ats or "ashby",
            "description": _ashby_description(posting) or job.description,
        }
    )


def probe_company_source(company: CompanySourceConfig) -> SourceProbeResult:
    try:
        if company.ats == "stripe_reader":
            return _probe_stripe_reader(company)
        if company.ashby_board_slug or company.ashby_jobs_url:
            return _probe_ashby(company)
        if company.greenhouse_board_token:
            return _probe_greenhouse(company)
        if company.lever_company:
            return _probe_lever(company)
        if company.jobs_json_url:
            return _probe_generic_json(company)
        if company.careers_url:
            return _probe_careers_url(company)
        return SourceProbeResult(
            company=company.name,
            status="not_configured",
            notes="No source URL or ATS adapter is configured.",
        )
    except DiscoveryFetchError as exc:
        return SourceProbeResult(
            company=company.name,
            status="blocked",
            configured_source=_configured_source(company),
            detected_adapter=company.ats,
            source_url=exc.url,
            failure_kind=exc.kind,
            failure_hint=exc.hint,
            notes=str(exc),
        )
    except Exception as exc:
        return SourceProbeResult(
            company=company.name,
            status="blocked",
            configured_source=_configured_source(company),
            detected_adapter=company.ats,
            source_url=_source_url(company),
            failure_kind="probe_failed",
            failure_hint="Inspect this source manually or add a source-specific adapter.",
            notes=_short_error(exc),
        )


def render_job_markdown(job: JobPosting) -> str:
    lines = [
        f"# {job.company} - {job.title}",
        "",
        f"Company: {job.company}",
        f"Title: {job.title}",
    ]
    if job.location:
        lines.append(f"Location: {job.location}")
    if job.remote_policy:
        lines.append(f"Remote Policy: {job.remote_policy}")
    if job.salary_range:
        lines.append(f"Salary Range: {job.salary_range}")
    if job.employment_type:
        lines.append(f"Employment Type: {job.employment_type}")
    lines.extend(
        [
            f"Job URL: {job.job_url}",
            f"Apply URL: {job.apply_url or job.job_url}",
        ]
    )
    if job.ats:
        lines.append(f"ATS: {job.ats}")
    lines.extend(["", "## Description", job.description.strip(), ""])
    return "\n".join(lines)


def _probe_greenhouse(company: CompanySourceConfig) -> SourceProbeResult:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company.greenhouse_board_token}/jobs?content=true"
    payload = _load_json_for_probe(url)
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    titles = [_first_text(item, "title") for item in jobs if isinstance(item, dict)]
    return _probe_result(
        company=company,
        configured_source="greenhouse",
        detected_adapter="greenhouse",
        source_url=url,
        job_count=len(jobs),
        sample_titles=titles,
        notes="Greenhouse board API returned jobs.",
    )


def _probe_lever(company: CompanySourceConfig) -> SourceProbeResult:
    url = f"https://api.lever.co/v0/postings/{company.lever_company}?mode=json"
    payload = _load_json_for_probe(url)
    jobs = payload if isinstance(payload, list) else []
    titles = [_first_text(item, "text", "title") for item in jobs if isinstance(item, dict)]
    return _probe_result(
        company=company,
        configured_source="lever",
        detected_adapter="lever",
        source_url=url,
        job_count=len(jobs),
        sample_titles=titles,
        notes="Lever postings API returned jobs.",
    )


def _probe_ashby(company: CompanySourceConfig) -> SourceProbeResult:
    url = company.ashby_jobs_url or f"https://jobs.ashbyhq.com/{company.ashby_board_slug}/"
    html = _load_text_for_probe(url)
    payload = _extract_ashby_app_data(html, url)
    postings = _candidate_ashby_postings_for_company(_extract_ashby_postings(payload), company)
    titles = [_first_text(item, "title") for item in postings]
    return _probe_result(
        company=company,
        configured_source="ashby",
        detected_adapter="ashby",
        source_url=url,
        job_count=len(postings),
        sample_titles=titles,
        notes="Ashby app data returned listed postings.",
    )


def _probe_generic_json(company: CompanySourceConfig) -> SourceProbeResult:
    url = company.jobs_json_url or ""
    payload = _load_json_for_probe(url)
    jobs = _extract_jobs_collection(payload)
    titles = [_first_text(item, "title", "text", "name") for item in jobs]
    return _probe_result(
        company=company,
        configured_source="generic_json",
        detected_adapter=company.ats or "generic_json",
        source_url=url,
        job_count=len(jobs),
        sample_titles=titles,
        notes="Generic JSON source returned job-like records.",
    )


def _probe_stripe_reader(company: CompanySourceConfig) -> SourceProbeResult:
    url = company.careers_url or "https://stripe.com/jobs/search"
    markdown = _load_text_for_probe(_reader_url(url))
    links = _extract_stripe_job_links(markdown)
    candidate_links = _candidate_stripe_links_for_company(links, company)
    return _probe_result(
        company=company,
        configured_source="stripe_reader",
        detected_adapter="stripe_reader",
        source_url=url,
        job_count=len(candidate_links),
        sample_titles=[link["title"] for link in candidate_links],
        notes="Stripe public jobs page fetched through unpaid reader fallback because direct local Stripe fetch is unavailable.",
    )


def _probe_careers_url(company: CompanySourceConfig) -> SourceProbeResult:
    url = company.careers_url or ""
    html = _load_text_for_probe(url)
    links = _extract_job_links_from_html(url, html)
    ats_detection = _detect_ats_from_links(links)
    if ats_detection:
        ats_kind, token = ats_detection
        return SourceProbeResult(
            company=company.name,
            status="partial",
            configured_source="careers_url",
            detected_adapter=ats_kind,
            source_url=url,
            job_count=len(_candidate_links_for_company(links, company)),
            sample_titles=_sample_titles(link["title"] for link in links),
            notes=f"Careers page links to {ats_kind} token '{token}'. Configure this adapter directly if its API is reachable.",
        )

    structured_jobs = _extract_structured_job_postings(html)
    if structured_jobs:
        return _probe_result(
            company=company,
            configured_source="careers_url",
            detected_adapter="structured_json",
            source_url=url,
            job_count=len(structured_jobs),
            sample_titles=[_first_text(item, "title") for item in structured_jobs],
            notes="Careers page exposes schema.org JobPosting JSON-LD.",
        )

    candidate_links = _candidate_links_for_company(links, company)
    if candidate_links:
        return SourceProbeResult(
            company=company.name,
            status="partial",
            configured_source="careers_url",
            detected_adapter=company.ats or "generic_html",
            source_url=url,
            job_count=len(candidate_links),
            sample_titles=_sample_titles(link["title"] for link in candidate_links),
            notes="Generic HTML parser found job-like links, but a stronger adapter may be needed for clean descriptions.",
        )

    return SourceProbeResult(
        company=company.name,
        status="needs_adapter",
        configured_source="careers_url",
        detected_adapter="browser_needed",
        source_url=url,
        job_count=0,
        failure_kind="no_job_links_found",
        failure_hint=_failure_hint("no_job_links_found"),
        notes="Page fetched successfully, but no job links or JobPosting JSON-LD were detected.",
    )


def _probe_result(
    company: CompanySourceConfig,
    configured_source: str,
    detected_adapter: str,
    source_url: str,
    job_count: int,
    sample_titles: list[str | None],
    notes: str,
) -> SourceProbeResult:
    return SourceProbeResult(
        company=company.name,
        status="workflow_ready" if job_count else "needs_adapter",
        configured_source=configured_source,
        detected_adapter=detected_adapter,
        source_url=source_url,
        job_count=job_count,
        sample_titles=_sample_titles(sample_titles),
        failure_kind=None if job_count else "no_jobs_found",
        failure_hint=None if job_count else "Source fetched, but no jobs were returned.",
        notes=notes if job_count else f"{notes} No jobs were returned.",
    )


def _configured_source(company: CompanySourceConfig) -> str | None:
    if company.ashby_board_slug or company.ashby_jobs_url:
        return "ashby"
    if company.greenhouse_board_token:
        return "greenhouse"
    if company.lever_company:
        return "lever"
    if company.jobs_json_url:
        return "generic_json"
    if company.careers_url:
        return "careers_url"
    return None


def _source_url(company: CompanySourceConfig) -> str | None:
    if company.ashby_jobs_url:
        return company.ashby_jobs_url
    if company.ashby_board_slug:
        return f"https://jobs.ashbyhq.com/{company.ashby_board_slug}/"
    if company.greenhouse_board_token:
        return f"https://boards-api.greenhouse.io/v1/boards/{company.greenhouse_board_token}/jobs?content=true"
    if company.lever_company:
        return f"https://api.lever.co/v0/postings/{company.lever_company}?mode=json"
    return company.jobs_json_url or company.careers_url


def _sample_titles(values: Any) -> list[str]:
    titles: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            titles.append(re.sub(r"\s+", " ", value).strip())
        if len(titles) == 5:
            break
    return titles


def _discover_greenhouse(company: CompanySourceConfig) -> list[JobPosting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company.greenhouse_board_token}/jobs?content=true"
    payload = _load_json(url)
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    normalized: list[JobPosting] = []
    for item in jobs:
        normalized.append(
            _job_posting(
                company=company.name,
                title=item.get("title") or "Unknown Title",
                location=_nested_text(item.get("location")),
                description=_strip_html(item.get("content") or ""),
                job_url=item.get("absolute_url") or item.get("url") or "",
                apply_url=item.get("absolute_url") or item.get("url") or "",
                ats=company.ats or "greenhouse",
                employment_type=_metadata_value(item.get("metadata"), "Employment Type"),
                remote_policy=_metadata_value(item.get("metadata"), "Workplace Type"),
                source=url,
            )
        )
    return normalized


def _discover_lever(company: CompanySourceConfig) -> list[JobPosting]:
    url = f"https://api.lever.co/v0/postings/{company.lever_company}?mode=json"
    payload = _load_json(url)
    jobs = payload if isinstance(payload, list) else []
    normalized: list[JobPosting] = []
    for item in jobs:
        categories = item.get("categories") or {}
        normalized.append(
            _job_posting(
                company=company.name,
                title=item.get("text") or item.get("title") or "Unknown Title",
                location=categories.get("location"),
                description=_strip_html(item.get("descriptionPlain") or item.get("description") or ""),
                job_url=item.get("hostedUrl") or item.get("applyUrl") or "",
                apply_url=item.get("applyUrl") or item.get("hostedUrl") or "",
                ats=company.ats or "lever",
                employment_type=categories.get("commitment"),
                remote_policy=categories.get("workplaceType") or categories.get("team"),
                source=url,
            )
        )
    return normalized


def _discover_ashby(company: CompanySourceConfig, enrich_details: bool = True) -> list[JobPosting]:
    url = company.ashby_jobs_url or f"https://jobs.ashbyhq.com/{company.ashby_board_slug}/"
    html = _load_text(url)
    payload = _extract_ashby_app_data(html, url)
    postings = _candidate_ashby_postings_for_company(_extract_ashby_postings(payload), company)
    normalized: list[JobPosting] = []
    for item in postings:
        if enrich_details:
            item = _load_ashby_detail_posting(url, item)
        posting_id = _first_text(item, "id", "jobPostingId", "jobId")
        job_url = urljoin(url.rstrip("/") + "/", posting_id or "")
        apply_url = urljoin(job_url.rstrip("/") + "/", "application")
        description = _ashby_description(item)
        normalized.append(
            _job_posting(
                company=company.name,
                title=item.get("title") or "Unknown Title",
                location=_ashby_location(item),
                description=description,
                job_url=job_url,
                apply_url=apply_url,
                ats=company.ats or "ashby",
                employment_type=_normalize_location(item.get("employmentType")),
                remote_policy=_normalize_location(item.get("workplaceType")),
                salary_range=_normalize_location(
                    item.get("compensationTierSummary") or item.get("scrapeableCompensationSalarySummary")
                ),
                source=url,
            )
        )
    return normalized


def _discover_generic_json(company: CompanySourceConfig) -> list[JobPosting]:
    payload = _load_json(company.jobs_json_url or "")
    jobs = _extract_jobs_collection(payload)
    normalized: list[JobPosting] = []
    for item in jobs:
        normalized.append(
            _job_posting(
                company=item.get("company") or company.name,
                title=_first_text(item, "title", "text", "name") or "Unknown Title",
                location=_normalize_location(item.get("location")),
                description=_strip_html(_first_text(item, "description", "content", "body", "text") or ""),
                job_url=_first_text(item, "job_url", "absolute_url", "url", "hostedUrl", "apply_url") or "",
                apply_url=_first_text(item, "apply_url", "applyUrl", "absolute_url", "url", "hostedUrl"),
                ats=item.get("ats") or company.ats,
                employment_type=_normalize_location(item.get("employment_type") or item.get("type") or item.get("commitment")),
                remote_policy=_normalize_location(
                    item.get("remote_policy") or item.get("workplaceType") or item.get("workplace_type")
                ),
                source=company.jobs_json_url or company.name,
            )
        )
    return normalized


def _discover_stripe_reader(company: CompanySourceConfig, enrich_details: bool = True) -> list[JobPosting]:
    search_url = company.careers_url or "https://stripe.com/jobs/search"
    markdown = _load_text(_reader_url(search_url))
    links = _candidate_stripe_links_for_company(_extract_stripe_job_links(markdown), company)
    normalized: list[JobPosting] = []
    for link in links:
        detail_markdown = ""
        if enrich_details:
            try:
                detail_markdown = _load_text(_reader_url(link["url"]))
            except Exception:
                detail_markdown = ""
        detail = _parse_stripe_detail_markdown(detail_markdown)
        title = detail.get("title") or link["title"]
        description = detail.get("description") or link.get("context") or title
        normalized.append(
            _job_posting(
                company=company.name,
                title=title,
                location=detail.get("location") or _stripe_location_from_context(link.get("context", "")),
                description=description,
                job_url=link["url"],
                apply_url=link["url"],
                ats="stripe_reader",
                employment_type=detail.get("employment_type"),
                remote_policy=detail.get("remote_policy"),
                salary_range=detail.get("salary_range"),
                source=f"stripe_reader:{search_url}",
            )
        )
    return normalized


def _discover_generic_html(company: CompanySourceConfig, enrich_details: bool = True) -> list[JobPosting]:
    careers_url = company.careers_url or ""
    html = _load_text(careers_url)
    links = _extract_job_links_from_html(careers_url, html)
    ats_detection = _detect_ats_from_links(links)
    if ats_detection:
        ats_kind, token = ats_detection
        try:
            if ats_kind == "greenhouse":
                return _discover_greenhouse(company.model_copy(update={"greenhouse_board_token": token, "ats": "greenhouse"}))
            if ats_kind == "lever":
                return _discover_lever(company.model_copy(update={"lever_company": token, "ats": "lever"}))
            if ats_kind == "ashby":
                return _discover_ashby(
                    company.model_copy(
                        update={
                            "ashby_board_slug": token,
                            "ashby_jobs_url": f"https://jobs.ashbyhq.com/{token}/",
                            "ats": "ashby",
                        }
                    ),
                    enrich_details=enrich_details,
                )
        except DiscoveryFetchError:
            pass
    if not links:
        raise DiscoveryFetchError(
            careers_url,
            kind="no_job_links_found",
            message="No job links were found in the fetched careers page HTML.",
            hint="This site may render listings client-side or need an ATS-specific adapter.",
        )
    links = _candidate_links_for_company(links, company)
    normalized: list[JobPosting] = []
    for link in links:
        detail_html = ""
        if _is_known_ats_job_url(link["url"]):
            detail_html = link["title"]
        else:
            try:
                detail_html = _load_text(link["url"])
            except Exception:
                detail_html = link["title"]

        nested_links = _extract_job_links_from_html(link["url"], detail_html)
        if _looks_like_listing_hub_link(link["title"], link["url"]) and nested_links:
            for nested_link in _candidate_links_for_company(nested_links, company):
                normalized.append(
                    _job_posting(
                        company=company.name,
                        title=nested_link["title"],
                        location=None,
                        description=nested_link["title"],
                        job_url=nested_link["url"],
                        apply_url=nested_link["url"],
                        ats=company.ats,
                        employment_type=None,
                        remote_policy=None,
                        source=careers_url,
                    )
                )
            continue

        normalized.append(
            _job_posting(
                company=company.name,
                title=link["title"],
                location=None,
                description=_strip_html(detail_html),
                job_url=link["url"],
                apply_url=link["url"],
                ats=company.ats,
                employment_type=None,
                remote_policy=None,
                source=careers_url,
            )
        )
    return normalized


def _extract_job_links_from_html(base_url: str, html: str) -> list[dict[str, str]]:
    parser = CareersPageParser(base_url=base_url)
    parser.feed(html)
    return parser.job_links()


def _detect_ats_from_links(links: list[dict[str, str]]) -> tuple[str, str] | None:
    for link in links:
        href = link["url"]
        greenhouse_match = re.search(r"https?://job-boards\.greenhouse\.io/([^/]+)/jobs/", href)
        if greenhouse_match:
            return "greenhouse", greenhouse_match.group(1)
        lever_match = re.search(r"https?://jobs\.lever\.co/([^/]+)/", href)
        if lever_match:
            return "lever", lever_match.group(1)
        ashby_match = re.search(r"https?://jobs\.ashbyhq\.com/([^/?#]+)", href)
        if ashby_match:
            return "ashby", ashby_match.group(1)
    return None


def _candidate_links_for_company(links: list[dict[str, str]], company: CompanySourceConfig) -> list[dict[str, str]]:
    candidate_links = links
    if company.include_keywords:
        keyword_filtered_links = [
            link
            for link in links
            if any(_normalize(keyword) in _normalize(f"{link['title']} {link['url']}") for keyword in company.include_keywords)
        ]
        if keyword_filtered_links:
            candidate_links = keyword_filtered_links
    if company.max_jobs is not None:
        return candidate_links[: company.max_jobs]
    return candidate_links


def _reader_url(url: str) -> str:
    return f"https://r.jina.ai/http://r.jina.ai/http://{url}"


def _extract_stripe_job_links(markdown: str) -> list[dict[str, str]]:
    links: dict[str, dict[str, str]] = {}
    pattern = re.compile(r"\[([^\]]+)\]\((https://stripe\.com/jobs/listing/[^)\s]+)\)([^\n]*)")
    for match in pattern.finditer(markdown):
        title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
        url = match.group(2).strip()
        context = re.sub(r"\s+", " ", _strip_markdown_images(match.group(3))).strip(" *")
        if title and "/jobs/listing/" in url:
            links[url] = {"title": title, "url": url, "context": context}
    return list(links.values())


def _candidate_stripe_links_for_company(
    links: list[dict[str, str]],
    company: CompanySourceConfig,
) -> list[dict[str, str]]:
    candidate_links = links
    if company.include_keywords:
        keyword_filtered_links = [
            link
            for link in links
            if any(
                _normalize(keyword) in _normalize(f"{link['title']} {link.get('context', '')} {link['url']}")
                for keyword in company.include_keywords
            )
        ]
        if keyword_filtered_links:
            candidate_links = keyword_filtered_links
    if company.max_jobs is not None:
        return candidate_links[: company.max_jobs]
    return candidate_links


def _parse_stripe_detail_markdown(markdown: str) -> dict[str, str]:
    if not markdown.strip():
        return {}
    title_match = re.search(r"(?m)^Title:\s*(.+?)\s*$", markdown)
    title = title_match.group(1).strip() if title_match else ""
    salary_match = re.search(r"The annual US base salary range for this role is ([^.]+)\.", markdown)
    office_locations = _stripe_field_after_heading(markdown, "Office locations")
    remote_locations = _stripe_field_after_heading(markdown, "Remote locations")
    job_type = _stripe_field_after_heading(markdown, "Job type")
    location = ", ".join(value for value in [office_locations, remote_locations] if value)
    remote_policy = "Remote" if remote_locations else None
    if office_locations and remote_locations:
        remote_policy = "Hybrid/Remote"
    elif office_locations:
        remote_policy = "Hybrid"
    return {
        "title": title,
        "description": _stripe_description(markdown),
        "location": location,
        "remote_policy": remote_policy or "",
        "employment_type": job_type,
        "salary_range": salary_match.group(1).strip() if salary_match else "",
    }


def _stripe_description(markdown: str) -> str:
    start = markdown.find("Markdown Content:")
    if start != -1:
        markdown = markdown[start + len("Markdown Content:") :]
    markdown = re.sub(r"(?m)^\d+\.\s+\[[^\]]+\]\([^)]+\).*$", "", markdown)
    return re.sub(r"\s+", " ", markdown).strip()


def _stripe_field_after_heading(markdown: str, heading: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(heading)}\s*$\n+\s*(.+?)\s*(?:\n\n|$)")
    match = pattern.search(markdown)
    if not match:
        return ""
    value = match.group(1).strip()
    if value.startswith("#") or value.startswith("["):
        return ""
    return re.sub(r"\s+", " ", value)


def _stripe_location_from_context(context: str) -> str | None:
    cleaned = _strip_markdown_images(context)
    cleaned = re.sub(r"^[A-Za-z &]+", "", cleaned).strip(" *")
    return cleaned or None


def _strip_markdown_images(value: str) -> str:
    return re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", value)


def _is_known_ats_job_url(url: str) -> bool:
    normalized_url = _normalize(url)
    return any(host in normalized_url for host in ["job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"])


def _job_posting(
    company: str,
    title: str,
    location: str | None,
    description: str,
    job_url: str,
    apply_url: str | None,
    ats: str | None,
    employment_type: str | None,
    remote_policy: str | None,
    source: str,
    salary_range: str | None = None,
) -> JobPosting:
    clean_title = re.sub(r"\s+", " ", title).strip()
    clean_company = re.sub(r"\s+", " ", company).strip()
    clean_description = re.sub(r"\s+", " ", description).strip()
    fallback_url = apply_url or job_url or source
    return JobPosting(
        job_id=slugify(f"{clean_company}-{clean_title}"),
        company=clean_company,
        title=clean_title,
        location=location,
        remote_policy=remote_policy,
        salary_range=salary_range,
        employment_type=employment_type,
        source=source,
        job_url=job_url or fallback_url,
        apply_url=apply_url or job_url or fallback_url,
        ats=ats,
        description=clean_description or clean_title,
        date_found=datetime.now(timezone.utc),
    )


def _matches_company_include_keywords(job: JobPosting, company: CompanySourceConfig) -> bool:
    if not company.include_keywords:
        return True
    text = _normalize(
        " ".join(
            filter(
                None,
                [
                    job.title,
                    job.location,
                    job.remote_policy,
                    job.employment_type,
                    job.description,
                ],
            )
        )
    )
    return any(_normalize(keyword) in text for keyword in company.include_keywords)


def _is_excluded(job: JobPosting, exclusions: ExclusionsConfig | None) -> bool:
    if not exclusions:
        return False
    title = _normalize(job.title)
    location = _normalize(" ".join(filter(None, [job.location, job.remote_policy])))
    description = _normalize(job.description)
    if any(term in title for term in (_normalize(value) for value in exclusions.titles)):
        return True
    if any(term in description for term in (_normalize(value) for value in exclusions.phrases)):
        return True
    if any(term in location for term in (_normalize(value) for value in exclusions.locations)):
        return True
    return False


def _load_text(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme and Path(url).exists():
        return Path(url).read_text(encoding="utf-8")
    if parsed.scheme == "file":
        return _load_text_via_urllib(url)

    attempt_errors: list[str] = []
    for retry_index in range(2):
        try:
            return _load_text_via_urllib(url)
        except Exception as exc:
            attempt_errors.append(f"urllib[{retry_index + 1}]: {_short_error(exc)}")
            if retry_index == 0:
                time.sleep(0.2)

    try:
        return _load_text_via_curl(url)
    except Exception as exc:
        attempt_errors.append(f"curl: {_short_error(exc)}")

    try:
        return _load_text_via_playwright(url)
    except Exception as exc:
        attempt_errors.append(f"playwright: {_short_error(exc)}")

    kind = _classify_fetch_failure(attempt_errors)
    raise DiscoveryFetchError(
        url,
        kind=kind,
        message="Unable to fetch the careers source after urllib, curl, and browser fallbacks.",
        hint=_failure_hint(kind),
        attempts=attempt_errors,
    )


def _load_text_for_probe(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme and Path(url).exists():
        return Path(url).read_text(encoding="utf-8")
    if parsed.scheme == "file":
        return _load_text_via_urllib(url, timeout=8)

    attempt_errors: list[str] = []
    try:
        return _load_text_via_urllib(url, timeout=8)
    except Exception as exc:
        attempt_errors.append(f"urllib: {_short_error(exc)}")

    try:
        return _load_text_via_curl(url, timeout=12)
    except Exception as exc:
        attempt_errors.append(f"curl: {_short_error(exc)}")

    kind = _classify_fetch_failure(attempt_errors)
    raise DiscoveryFetchError(
        url,
        kind=kind,
        message="Unable to fetch the careers source during bounded source probing.",
        hint=_failure_hint(kind),
        attempts=attempt_errors,
    )


def _load_text_via_urllib(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _load_text_via_curl(url: str, timeout: int = 45) -> str:
    result = subprocess.run(
        ["curl", "-LfsS", "-A", USER_AGENT, url],
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout.decode("utf-8", errors="replace")


def _load_text_via_playwright(url: str) -> str:
    return asyncio.run(_playwright_fetch(url))


async def _playwright_fetch(url: str) -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed.") from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return await page.content()
        finally:
            await browser.close()


def _load_json(url: str) -> Any:
    return json.loads(_load_text(url))


def _load_json_for_probe(url: str) -> Any:
    return json.loads(_load_text_for_probe(url))


def _short_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()


def _classify_fetch_failure(attempt_errors: list[str]) -> str:
    lowered = " ".join(attempt_errors).lower()
    if "can't assign requested address" in lowered or "couldn't connect to server" in lowered:
        return "network_unreachable"
    if "certificate verify failed" in lowered or "ssl" in lowered:
        return "ssl_verification_failed"
    if "403" in lowered or "forbidden" in lowered:
        return "blocked_by_site"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "chromium executable doesn't exist" in lowered or "playwright" in lowered:
        return "browser_fallback_unavailable"
    return "fetch_failed"


def _failure_hint(kind: str) -> str:
    hints = {
        "network_unreachable": "Check outbound connectivity or VPN/proxy settings from this machine before debugging the parser.",
        "ssl_verification_failed": "Install the local CA bundle or use a fetch path that trusts the system certificates.",
        "blocked_by_site": "This source may block generic HTTP clients; try a browser-based adapter or an ATS-specific endpoint.",
        "timeout": "Retry later or add a source-specific fetcher with lighter requests.",
        "browser_fallback_unavailable": "Install Playwright browsers with `playwright install chromium` to enable browser fallback.",
        "fetch_failed": "Inspect the source manually and consider adding an ATS-specific adapter.",
        "no_job_links_found": "The page likely renders jobs client-side or uses a non-generic DOM shape.",
    }
    return hints.get(kind, "Inspect the source manually and consider a source-specific adapter.")


def _extract_jobs_collection(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("jobs", "positions", "openings", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_structured_job_postings(html: str) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    for match in re.finditer(
        r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
    ):
        raw_json = unescape(match.group(1)).strip()
        if not raw_json:
            continue
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        postings.extend(_collect_job_posting_nodes(payload))
    return postings


def _collect_job_posting_nodes(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        nodes: list[dict[str, Any]] = []
        for item in payload:
            nodes.extend(_collect_job_posting_nodes(item))
        return nodes
    if not isinstance(payload, dict):
        return []

    item_type = payload.get("@type")
    if item_type == "JobPosting" or (isinstance(item_type, list) and "JobPosting" in item_type):
        return [payload]

    nodes = []
    for key in ("@graph", "itemListElement", "mainEntity", "hasPart"):
        if key in payload:
            nodes.extend(_collect_job_posting_nodes(payload[key]))
    return nodes


def _extract_ashby_app_data(html: str, url: str) -> dict[str, Any]:
    marker_index = html.find("window.__appData")
    if marker_index == -1:
        raise DiscoveryFetchError(
            url,
            kind="ashby_app_data_missing",
            message="Ashby page did not contain window.__appData.",
            hint="Ashby may have changed its public page shape; inspect the board HTML.",
        )
    assignment_index = html.find("=", marker_index)
    object_index = html.find("{", assignment_index)
    if assignment_index == -1 or object_index == -1:
        raise DiscoveryFetchError(
            url,
            kind="ashby_app_data_missing",
            message="Ashby window.__appData assignment was present but no JSON object was found.",
            hint="Inspect the board HTML for a changed app-data bootstrap format.",
        )
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[object_index:])
    except json.JSONDecodeError as exc:
        raise DiscoveryFetchError(
            url,
            kind="ashby_app_data_invalid",
            message=f"Unable to parse Ashby window.__appData JSON: {exc}",
            hint="Inspect the app-data blob and update the Ashby adapter.",
        ) from exc
    if not isinstance(payload, dict):
        raise DiscoveryFetchError(
            url,
            kind="ashby_app_data_invalid",
            message="Ashby window.__appData was not a JSON object.",
            hint="Inspect the app-data blob and update the Ashby adapter.",
        )
    return payload


def _extract_ashby_postings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    board = payload.get("jobBoard")
    if isinstance(board, dict):
        postings = board.get("jobPostings")
        if isinstance(postings, list):
            return [item for item in postings if isinstance(item, dict) and item.get("isListed", True)]
    posting = payload.get("posting")
    if isinstance(posting, dict):
        return [posting] if posting.get("isListed", True) else []
    return []


def _candidate_ashby_postings_for_company(
    postings: list[dict[str, Any]],
    company: CompanySourceConfig,
) -> list[dict[str, Any]]:
    candidate_postings = postings
    if company.include_keywords:
        keyword_filtered_postings = [
            posting
            for posting in postings
            if any(_normalize(keyword) in _normalize(_ashby_description(posting)) for keyword in company.include_keywords)
        ]
        if keyword_filtered_postings:
            candidate_postings = keyword_filtered_postings
    if company.max_jobs is not None:
        return candidate_postings[: company.max_jobs]
    return candidate_postings


def _load_ashby_detail_posting(board_url: str, item: dict[str, Any]) -> dict[str, Any]:
    posting_id = _first_text(item, "id", "jobPostingId", "jobId")
    if not posting_id:
        return item
    detail_url = urljoin(board_url.rstrip("/") + "/", posting_id)
    try:
        payload = _extract_ashby_app_data(_load_text(detail_url), detail_url)
    except Exception:
        return item
    posting = payload.get("posting")
    if not isinstance(posting, dict):
        return item
    return {**item, **posting}


def _ashby_description(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for label, key in [
        ("Team", "teamName"),
        ("Department", "departmentName"),
        ("Location", "locationName"),
        ("Workplace", "workplaceType"),
        ("Employment Type", "employmentType"),
        ("Compensation", "compensationTierSummary"),
        ("Summary", "shortDescription"),
    ]:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{label}: {_strip_html(value)}")
    description = item.get("descriptionPlainText") or item.get("descriptionHtml")
    if isinstance(description, str) and description.strip():
        parts.append(f"Description: {_strip_html(description)}")
    secondary_locations = item.get("secondaryLocations")
    if isinstance(secondary_locations, list):
        locations = [
            _first_text(location, "locationName", "locationExternalName", "address")
            for location in secondary_locations
            if isinstance(location, dict)
        ]
        locations = [location for location in locations if location]
        if locations:
            parts.append(f"Secondary Locations: {', '.join(locations)}")
    return "\n".join(parts)


def _ashby_location(item: dict[str, Any]) -> str | None:
    locations = [_normalize_location(item.get("locationName"))]
    secondary_locations = item.get("secondaryLocations")
    if isinstance(secondary_locations, list):
        for location in secondary_locations:
            if isinstance(location, dict):
                locations.append(_first_text(location, "locationName", "locationExternalName", "address"))
    cleaned = [location for location in locations if location]
    return ", ".join(dict.fromkeys(cleaned)) if cleaned else None


def _nested_text(value: Any) -> str | None:
    if isinstance(value, dict):
        return _first_text(value, "name", "location", "value", "text")
    if isinstance(value, str):
        return value.strip() or None
    return None


def _metadata_value(metadata: Any, name: str) -> str | None:
    if not isinstance(metadata, list):
        return None
    normalized_target = _normalize(name)
    for item in metadata:
        if not isinstance(item, dict):
            continue
        label = _normalize(str(item.get("name") or item.get("label") or ""))
        if label == normalized_target:
            return _first_text(item, "value", "text", "name")
    return None


def _first_text(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _first_text(value, "name", "value", "text")
            if nested:
                return nested
    return None


def _normalize_location(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _first_text(value, "name", "value", "text")
    return None


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


class CareersPageParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self._links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        href = attributes.get("href")
        if href:
            self._current_href = urljoin(self.base_url, href)
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._current_href is None:
            return
        title = _clean_job_link_title(re.sub(r"\s+", " ", "".join(self._current_text)).strip(), self._current_href)
        if title and _looks_like_job_link(title, self._current_href):
            self._links.append({"title": title, "url": self._current_href})
        self._current_href = None
        self._current_text = []

    def job_links(self) -> list[dict[str, str]]:
        deduped: dict[str, dict[str, str]] = {}
        for item in self._links:
            deduped[item["url"]] = item
        return list(deduped.values())


def _looks_like_job_link(title: str, href: str) -> bool:
    haystack = _normalize(f"{title} {href}")
    if title.lower().startswith("skip to "):
        return False
    parsed = urlparse(href)
    if parsed.fragment and not parsed.path:
        return False
    if parsed.path.startswith("/product/") or "/product/" in parsed.path:
        return False
    if _looks_like_listing_hub_link(title, href):
        return True
    role_tokens = [
        "data scientist",
        "data science",
        "analytics engineer",
        "data engineer",
        "research engineer",
        "research scientist",
        "machine learning",
        "software engineer",
        "engineering manager",
        "product manager",
        "applied ai",
        "solutions architect",
    ]
    ats_tokens = [
        "job-boards.greenhouse.io",
        "jobs.lever.co",
        "jobs.ashbyhq.com",
        "metacareers.com/profile/job_details",
        "/jobs/",
        "/job/",
    ]
    return any(token in haystack for token in role_tokens) or any(token in haystack for token in ats_tokens)


def _looks_like_listing_hub_link(title: str, href: str) -> bool:
    haystack = _normalize(f"{title} {href}")
    return any(token in haystack for token in ["open roles", "careers/jobs", "jobsearch", "all jobs", "view roles"])


def _clean_job_link_title(title: str, href: str) -> str:
    clean_title = re.sub(r"\bApply$", "", title).strip()
    if "greenhouse.io" not in href:
        return clean_title
    location_match = re.search(
        r"(Remote-Friendly|San Francisco|New York City|New York|Seattle|London|Dublin|Tokyo|Singapore|Sydney|"
        r"Washington|Boston|Munich|Paris|Seoul|Toronto|Vancouver|Zurich|Zürich|Ontario|Los Angeles|Palo Alto|"
        r"Mountain View|Foster City|Remote|United States|US)",
        clean_title,
    )
    if location_match and location_match.start() > 12:
        return clean_title[: location_match.start()].strip(" ,-;|")
    return clean_title
