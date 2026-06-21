import json

from job_agent.cli import handle_probe_sources
from job_agent.discovery import probe_company_source
from job_agent.models import CompanySourceConfig
from job_agent.paths import ProjectPaths


def test_probe_generic_json_source_reports_workflow_ready(tmp_path) -> None:
    json_path = tmp_path / "jobs.json"
    json_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "title": "Staff Data Scientist",
                        "description": "Lead experimentation platform work.",
                        "absolute_url": "https://example.com/jobs/staff-ds",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="ExampleCo", jobs_json_url=json_path.resolve().as_uri())

    probe = probe_company_source(company)

    assert probe.status == "workflow_ready"
    assert probe.detected_adapter == "generic_json"
    assert probe.job_count == 1
    assert probe.sample_titles == ["Staff Data Scientist"]


def test_probe_careers_page_detects_ats_link_without_deep_fetch(tmp_path) -> None:
    careers_path = tmp_path / "careers.html"
    careers_path.write_text(
        '<html><body><a href="https://jobs.ashbyhq.com/exampleco/posting-1">Staff Data Scientist</a></body></html>',
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="ExampleCo", careers_url=careers_path.resolve().as_uri())

    probe = probe_company_source(company)

    assert probe.status == "partial"
    assert probe.detected_adapter == "ashby"
    assert probe.job_count == 1


def test_probe_careers_page_detects_browser_needed(tmp_path) -> None:
    careers_path = tmp_path / "careers.html"
    careers_path.write_text("<html><body><h1>Careers</h1><div id='jobs-root'></div></body></html>", encoding="utf-8")
    company = CompanySourceConfig(name="ClientRenderedCo", careers_url=careers_path.resolve().as_uri())

    probe = probe_company_source(company)

    assert probe.status == "needs_adapter"
    assert probe.detected_adapter == "browser_needed"
    assert probe.failure_kind == "no_job_links_found"


def test_probe_careers_page_detects_json_ld_job_postings(tmp_path) -> None:
    careers_path = tmp_path / "careers.html"
    careers_path.write_text(
        "\n".join(
            [
                "<html><body>",
                '<script type="application/ld+json">',
                json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": "Lead Data Scientist"}),
                "</script>",
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )
    company = CompanySourceConfig(name="StructuredCo", careers_url=careers_path.resolve().as_uri())

    probe = probe_company_source(company)

    assert probe.status == "workflow_ready"
    assert probe.detected_adapter == "structured_json"
    assert probe.job_count == 1
    assert probe.sample_titles == ["Lead Data Scientist"]


def test_handle_probe_sources_writes_csv_and_markdown_reports(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    json_path = tmp_path / "jobs.json"
    json_path.write_text(
        json.dumps({"jobs": [{"title": "Staff Data Scientist", "absolute_url": "https://example.com/jobs/1"}]}),
        encoding="utf-8",
    )
    companies_path = tmp_path / "sources" / "companies.yaml"
    companies_path.parent.mkdir(parents=True, exist_ok=True)
    companies_path.write_text(
        "\n".join(
            [
                "companies:",
                "  - name: ExampleCo",
                f"    jobs_json_url: '{json_path.resolve().as_uri()}'",
            ]
        ),
        encoding="utf-8",
    )

    message = handle_probe_sources(paths, str(companies_path))

    csv_text = paths.source_health_csv.read_text(encoding="utf-8")
    markdown_text = paths.source_health_md.read_text(encoding="utf-8")
    assert "1 ready" in message
    assert "ExampleCo" in csv_text
    assert "workflow_ready" in csv_text
    assert "# Source Health" in markdown_text
