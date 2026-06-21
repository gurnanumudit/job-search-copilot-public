import json
from pathlib import Path

import pytest

import job_agent.discovery as discovery
from job_agent.cli import handle_discover
from job_agent.discovery import DiscoveryFetchError, _classify_fetch_failure, discover_company_jobs
from job_agent.models import CompanySourceConfig, ExclusionsConfig
from job_agent.paths import ProjectPaths


def test_discover_company_jobs_from_generic_json_marks_titles_and_urls(tmp_path) -> None:
    jobs_payload = {
        "jobs": [
            {
                "title": "Staff Data Scientist",
                "location": "Remote",
                "description": "Lead experimentation and AI tooling work.",
                "absolute_url": "https://example.com/jobs/staff-ds",
            }
        ]
    }
    json_path = tmp_path / "jobs.json"
    json_path.write_text(json.dumps(jobs_payload), encoding="utf-8")
    company = CompanySourceConfig(
        name="ExampleCo",
        jobs_json_url=json_path.resolve().as_uri(),
        ats="custom",
    )

    jobs = discover_company_jobs(company)

    assert len(jobs) == 1
    assert jobs[0].company == "ExampleCo"
    assert jobs[0].title == "Staff Data Scientist"
    assert jobs[0].apply_url == "https://example.com/jobs/staff-ds"


def test_discover_company_jobs_from_relative_generic_json(monkeypatch, tmp_path) -> None:
    jobs_payload = {
        "jobs": [
            {
                "title": "Staff Data Scientist",
                "description": "Lead experimentation work.",
                "absolute_url": "https://example.com/jobs/staff-ds",
            }
        ]
    }
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    json_path = sources_dir / "jobs.json"
    json_path.write_text(json.dumps(jobs_payload), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    company = CompanySourceConfig(
        name="ExampleCo",
        jobs_json_url="sources/jobs.json",
        ats="manual_seed",
    )

    jobs = discover_company_jobs(company)

    assert len(jobs) == 1
    assert jobs[0].title == "Staff Data Scientist"
    assert jobs[0].ats == "manual_seed"


def test_discover_company_jobs_from_stripe_reader(monkeypatch) -> None:
    search_markdown = """
Title: Stripe Jobs

Markdown Content:
[Staff Data Scientist](https://stripe.com/jobs/listing/staff-data-scientist/7568328)*    Data & Data Science![Image](flag.svg)Remote in United States
[Account Executive](https://stripe.com/jobs/listing/account-executive/123)*    Sales![Image](flag.svg)London
"""
    detail_markdown = """
Title: Staff Data Scientist

URL Source: https://stripe.com/jobs/listing/staff-data-scientist/7568328

Markdown Content:
## Who we are
### About the team
Build experimentation, causal inference, and growth measurement systems.

Office locations

South San Francisco HQ, New York, or Seattle

Remote locations

Remote in United States

Job type

Full time
"""

    def fake_load_text(url: str) -> str:
        if "jobs/search" in url:
            return search_markdown
        if "staff-data-scientist" in url:
            return detail_markdown
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(discovery, "_load_text", fake_load_text)
    company = CompanySourceConfig(
        name="Stripe",
        ats="stripe_reader",
        careers_url="https://stripe.com/jobs/search",
        include_keywords=["data scientist", "experimentation"],
    )

    jobs = discover_company_jobs(company)

    assert len(jobs) == 1
    assert jobs[0].company == "Stripe"
    assert jobs[0].title == "Staff Data Scientist"
    assert jobs[0].ats == "stripe_reader"
    assert jobs[0].job_url == "https://stripe.com/jobs/listing/staff-data-scientist/7568328"
    assert jobs[0].apply_url == "https://stripe.com/jobs/listing/staff-data-scientist/7568328"
    assert jobs[0].location == "South San Francisco HQ, New York, or Seattle, Remote in United States"
    assert jobs[0].remote_policy == "Hybrid/Remote"
    assert jobs[0].employment_type == "Full time"
    assert "causal inference" in jobs[0].description


def test_discover_company_jobs_from_ashby_app_data(tmp_path) -> None:
    ashby_payload = {
        "jobBoard": {
            "jobPostings": [
                {
                    "id": "posting-1",
                    "jobId": "job-1",
                    "isListed": True,
                    "title": "Staff Data Scientist, Experimentation",
                    "teamName": "Product Data Science",
                    "departmentName": "Data",
                    "locationName": "San Francisco",
                    "workplaceType": "Hybrid",
                    "employmentType": "FullTime",
                    "compensationTierSummary": "$250K - $325K",
                    "secondaryLocations": [{"locationName": "Remote, US"}],
                },
                {
                    "id": "posting-2",
                    "isListed": False,
                    "title": "Unlisted Analyst",
                },
            ]
        }
    }
    html_path = tmp_path / "ashby.html"
    html_path.write_text(
        f"<html><script>window.__appData = {json.dumps(ashby_payload)};</script></html>",
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="ExampleAI", ashby_jobs_url=html_path.resolve().as_uri())

    jobs = discover_company_jobs(company)

    assert len(jobs) == 1
    assert jobs[0].ats == "ashby"
    assert jobs[0].title == "Staff Data Scientist, Experimentation"
    assert jobs[0].location == "San Francisco, Remote, US"
    assert jobs[0].remote_policy == "Hybrid"
    assert jobs[0].salary_range == "$250K - $325K"
    assert "Product Data Science" in jobs[0].description
    assert jobs[0].job_url.endswith("/posting-1")
    assert jobs[0].apply_url.endswith("/posting-1/application")


def test_discover_ashby_enriches_from_detail_page(monkeypatch) -> None:
    board_payload = {
        "jobBoard": {
            "jobPostings": [
                {
                    "id": "posting-1",
                    "isListed": True,
                    "title": "Staff Data Scientist, Experimentation",
                    "teamName": "Product Data Science",
                    "locationName": "San Francisco",
                }
            ]
        }
    }
    detail_payload = {
        "posting": {
            "id": "posting-1",
            "title": "Staff Data Scientist, Experimentation",
            "descriptionPlainText": "Build experimentation systems, causal inference workflows, and product measurement.",
            "locationName": "San Francisco",
        }
    }

    def fake_load_text(url: str) -> str:
        payload = detail_payload if url.endswith("/posting-1") else board_payload
        return f"<html><script>window.__appData = {json.dumps(payload)};</script></html>"

    monkeypatch.setattr(discovery, "_load_text", fake_load_text)
    company = CompanySourceConfig(name="ExampleAI", ashby_jobs_url="https://jobs.ashbyhq.com/exampleai/")

    jobs = discover_company_jobs(company)

    assert len(jobs) == 1
    assert "causal inference workflows" in jobs[0].description


def test_discover_ashby_can_skip_detail_enrichment(monkeypatch) -> None:
    board_payload = {
        "jobBoard": {
            "jobPostings": [
                {
                    "id": "posting-1",
                    "isListed": True,
                    "title": "Staff Data Scientist, Experimentation",
                    "teamName": "Product Data Science",
                    "locationName": "San Francisco",
                }
            ]
        }
    }
    detail_payload = {
        "posting": {
            "id": "posting-1",
            "title": "Staff Data Scientist, Experimentation",
            "descriptionPlainText": "Build causal inference workflows.",
            "locationName": "San Francisco",
        }
    }

    def fake_load_text(url: str) -> str:
        payload = detail_payload if url.endswith("/posting-1") else board_payload
        return f"<html><script>window.__appData = {json.dumps(payload)};</script></html>"

    monkeypatch.setattr(discovery, "_load_text", fake_load_text)
    company = CompanySourceConfig(name="ExampleAI", ashby_jobs_url="https://jobs.ashbyhq.com/exampleai/")

    jobs = discover_company_jobs(company, enrich_details=False)

    assert len(jobs) == 1
    assert "causal inference workflows" not in jobs[0].description


def test_discover_company_jobs_filters_by_company_keywords_and_max_jobs(tmp_path) -> None:
    jobs_payload = {
        "jobs": [
            {
                "title": "Staff Data Scientist",
                "description": "Lead experimentation platform work.",
                "absolute_url": "https://example.com/jobs/staff-ds",
            },
            {
                "title": "Senior Product Data Scientist",
                "description": "Own product analytics and metric design.",
                "absolute_url": "https://example.com/jobs/product-ds",
            },
            {
                "title": "Recruiter",
                "description": "Hiring operations.",
                "absolute_url": "https://example.com/jobs/recruiter",
            },
        ]
    }
    json_path = tmp_path / "jobs.json"
    json_path.write_text(json.dumps(jobs_payload), encoding="utf-8")
    company = CompanySourceConfig(
        name="ExampleCo",
        jobs_json_url=json_path.resolve().as_uri(),
        include_keywords=["experimentation", "product analytics"],
        max_jobs=1,
    )

    jobs = discover_company_jobs(company)

    assert len(jobs) == 1
    assert jobs[0].title == "Staff Data Scientist"


def test_discover_respects_exclusions_and_exports_ranked_tracker(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    jobs_payload = {
        "jobs": [
            {
                "title": "Staff Data Scientist",
                "location": "Remote",
                "description": "Lead experimentation, AI tooling, and product analytics.",
                "absolute_url": "https://example.com/jobs/staff-ds",
            },
            {
                "title": "Junior Data Scientist",
                "location": "Remote",
                "description": "Entry-level analytics role.",
                "absolute_url": "https://example.com/jobs/junior-ds",
            },
            {
                "title": "Principal Data Scientist",
                "location": "Remote",
                "description": "Lead measurement platform work. Must be authorized to work without sponsorship.",
                "absolute_url": "https://example.com/jobs/principal-ds",
            },
        ]
    }
    json_path = tmp_path / "jobs.json"
    json_path.write_text(json.dumps(jobs_payload), encoding="utf-8")

    for relative_path, content in [
        (
            "profile/preferences.yaml",
            "\n".join(
                [
                    "candidate:",
                    '  name: "Example Candidate"',
                    '  email: "mudit@example.com"',
                    '  phone: "+1-555-555-5555"',
                    '  work_authorization: "authorized_us_requires_sponsorship"',
                    "",
                    "location_preferences:",
                    "  preferred: ['Remote']",
                    "  acceptable_hybrid: []",
                    "  avoid: []",
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
                    "  ai_tooling: ['ai tooling']",
                    "  platform: ['platform']",
                    "exclude_keywords: ['junior']",
                ]
            ),
        ),
        (
            "sources/exclusions.yaml",
            "\n".join(
                [
                    "titles: ['junior']",
                    "phrases: []",
                    "locations: []",
                ]
            ),
        ),
        (
            "sources/companies.yaml",
            "\n".join(
                [
                    "companies:",
                    "  - name: 'ExampleCo'",
                    f"    jobs_json_url: '{json_path.resolve().as_uri()}'",
                    "    ats: 'custom'",
                ]
            ),
        ),
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    message = handle_discover(paths, str(tmp_path / "sources" / "companies.yaml"))
    ranked_csv = (tmp_path / "outputs" / "ranked_jobs.csv").read_text(encoding="utf-8")

    assert "Discovered 2 jobs" in message
    assert "Junior Data Scientist" not in ranked_csv
    assert "Principal Data Scientist" in ranked_csv
    assert "not_relevant_no_sponsorship" in ranked_csv
    assert "Staff Data Scientist" in ranked_csv
    assert "needs_visa_review" in ranked_csv
    assert "Inspect application" in ranked_csv or "Review if interested" in ranked_csv


def test_handle_discover_two_phase_enriches_top_ranked_jobs(monkeypatch, tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    jobs_payload = {
        "jobs": [
            {
                "title": "Staff Data Scientist",
                "location": "Remote",
                "description": "Lead experimentation platform work.",
                "absolute_url": "https://example.com/jobs/staff-ds",
            },
            {
                "title": "Data Analyst",
                "location": "Remote",
                "description": "Analytics reporting.",
                "absolute_url": "https://example.com/jobs/data-analyst",
            },
        ]
    }
    json_path = tmp_path / "jobs.json"
    json_path.write_text(json.dumps(jobs_payload), encoding="utf-8")

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
                    "  ai_tooling: []",
                    "  platform: ['platform']",
                    "exclude_keywords: []",
                ]
            ),
        ),
        ("sources/exclusions.yaml", "titles: []\nphrases: []\nlocations: []\n"),
        (
            "sources/companies.yaml",
            "\n".join(
                [
                    "companies:",
                    "  - name: ExampleCo",
                    f"    jobs_json_url: '{json_path.resolve().as_uri()}'",
                ]
            ),
        ),
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def fake_enrich(job):
        return job.model_copy(update={"description": job.description + " enriched full description"})

    monkeypatch.setattr(discovery, "enrich_job_posting", fake_enrich)
    monkeypatch.setattr("job_agent.cli.enrich_job_posting", fake_enrich)

    message = handle_discover(paths, str(tmp_path / "sources" / "companies.yaml"), mode="two-phase", enrich_top_n=1)
    raw_staff = (tmp_path / "jobs" / "raw" / "exampleco-staff-data-scientist.md").read_text(encoding="utf-8")
    raw_analyst = (tmp_path / "jobs" / "raw" / "exampleco-data-analyst.md").read_text(encoding="utf-8")

    assert "enriched 1 top job" in message
    assert "enriched full description" in raw_staff
    assert "enriched full description" not in raw_analyst


def test_discover_gracefully_records_company_source_failures(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    good_jobs_payload = {
        "jobs": [
            {
                "title": "Staff Data Scientist",
                "location": "Remote",
                "description": "Lead experimentation and AI tooling work.",
                "absolute_url": "https://example.com/jobs/staff-ds",
            }
        ]
    }
    good_json_path = tmp_path / "jobs.json"
    good_json_path.write_text(json.dumps(good_jobs_payload), encoding="utf-8")

    for relative_path, content in [
        (
            "profile/preferences.yaml",
            "\n".join(
                [
                    "candidate:",
                    '  name: "Example Candidate"',
                    '  email: "mudit@example.com"',
                    '  phone: "+1-555-555-5555"',
                    '  work_authorization: "authorized_us_requires_sponsorship"',
                    "",
                    "location_preferences:",
                    "  preferred: ['Remote']",
                    "  acceptable_hybrid: []",
                    "  avoid: []",
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
                    "  ai_tooling: ['ai tooling']",
                    "  platform: []",
                    "exclude_keywords: []",
                ]
            ),
        ),
        (
            "sources/exclusions.yaml",
            "\n".join(
                [
                    "titles: []",
                    "phrases: []",
                    "locations: []",
                ]
            ),
        ),
        (
            "sources/companies.yaml",
            "\n".join(
                [
                    "companies:",
                    "  - name: 'GoodCo'",
                    f"    jobs_json_url: '{good_json_path.resolve().as_uri()}'",
                    "    ats: 'custom'",
                    "  - name: 'BrokenCo'",
                    "    jobs_json_url: 'file:///does/not/exist.json'",
                    "    ats: 'custom'",
                ]
            ),
        ),
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    message = handle_discover(paths, str(tmp_path / "sources" / "companies.yaml"))
    failure_report = (tmp_path / "outputs" / "discovery_failures.md").read_text(encoding="utf-8")
    ranked_csv = (tmp_path / "outputs" / "ranked_jobs.csv").read_text(encoding="utf-8")

    assert "1 source(s) failed" in message
    assert "BrokenCo" in failure_report
    assert "GoodCo" in ranked_csv


def test_classify_fetch_failure_identifies_network_blocker() -> None:
    kind = _classify_fetch_failure(
        [
            'urllib[1]: <urlopen error [Errno 49] Can\'t assign requested address>',
            "curl: Command '['curl']' returned non-zero exit status 7.",
        ]
    )
    assert kind == "network_unreachable"


def test_discover_generic_html_raises_clear_error_when_no_job_links_found(tmp_path) -> None:
    html_path = tmp_path / "careers.html"
    html_path.write_text("<html><body><h1>Careers</h1><p>No links here.</p></body></html>", encoding="utf-8")
    company = CompanySourceConfig(name="EmptyCo", careers_url=html_path.resolve().as_uri())

    with pytest.raises(DiscoveryFetchError) as exc_info:
        discover_company_jobs(company)

    assert exc_info.value.kind == "no_job_links_found"


def test_discover_generic_html_does_not_treat_product_pages_as_jobs(tmp_path) -> None:
    html_path = tmp_path / "careers.html"
    html_path.write_text(
        '<html><body><a href="https://example.com/product/analytics">Analytics Platform</a></body></html>',
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="ProductCo", careers_url=html_path.resolve().as_uri())

    with pytest.raises(DiscoveryFetchError) as exc_info:
        discover_company_jobs(company)

    assert exc_info.value.kind == "no_job_links_found"


def test_discover_generic_html_can_follow_open_roles_hub(tmp_path) -> None:
    careers_path = tmp_path / "careers.html"
    jobs_path = tmp_path / "jobs.html"
    jobs_path.write_text(
        "\n".join(
            [
                "<html><body>",
                '<a href="role-1.html">Staff Data Scientist</a>',
                '<a href="role-2.html">Product Data Scientist</a>',
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )
    careers_path.write_text(
        "\n".join(
            [
                "<html><body>",
                '<a href="jobs.html">Explore open roles</a>',
                '<a href="#main-content">Skip to main content</a>',
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="ExampleCo", careers_url=careers_path.resolve().as_uri())

    jobs = discover_company_jobs(company)

    titles = {job.title for job in jobs}
    assert "Staff Data Scientist" in titles
    assert "Product Data Scientist" in titles
    assert "Skip to main content" not in titles


def test_discover_generic_html_auto_detects_greenhouse(monkeypatch, tmp_path) -> None:
    careers_path = tmp_path / "careers.html"
    careers_path.write_text(
        '<html><body><a href="https://job-boards.greenhouse.io/exampleco/jobs/123">Staff Data Scientist</a></body></html>',
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="ExampleCo", careers_url=careers_path.resolve().as_uri())

    called: dict[str, str] = {}

    def fake_discover_greenhouse(company_config: CompanySourceConfig):
        called["token"] = company_config.greenhouse_board_token or ""
        return []

    monkeypatch.setattr(discovery, "_discover_greenhouse", fake_discover_greenhouse)

    discover_company_jobs(company)

    assert called["token"] == "exampleco"


def test_discover_generic_html_auto_detects_ashby(monkeypatch, tmp_path) -> None:
    careers_path = tmp_path / "careers.html"
    careers_path.write_text(
        '<html><body><a href="https://jobs.ashbyhq.com/exampleco/posting-1">Staff Data Scientist</a></body></html>',
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="ExampleCo", careers_url=careers_path.resolve().as_uri())

    called: dict[str, str] = {}

    def fake_discover_ashby(company_config: CompanySourceConfig, enrich_details: bool = True):
        called["slug"] = company_config.ashby_board_slug or ""
        return []

    monkeypatch.setattr(discovery, "_discover_ashby", fake_discover_ashby)

    discover_company_jobs(company)

    assert called["slug"] == "exampleco"
