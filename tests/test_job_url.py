from pathlib import Path

from job_agent.cli import handle_package
from job_agent.markdown import parse_job_url
from job_agent.models import ApplicationReadiness
from job_agent.paths import ProjectPaths
from job_agent.storage import save_readiness


def test_parse_job_url_extracts_basic_fields_from_local_html(tmp_path) -> None:
    html_path = tmp_path / "job.html"
    html_path.write_text(
        "\n".join(
            [
                "<html>",
                "<head>",
                "<title>Staff Data Scientist - ExampleCo</title>",
                '<meta name="description" content="Lead experimentation and AI tooling for product analytics. Remote in the US.">',
                "</head>",
                "<body>",
                "<h1>Staff Data Scientist</h1>",
                "<p>Lead experimentation and AI tooling for product analytics.</p>",
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )

    job = parse_job_url(html_path.resolve().as_uri())

    assert job.company == "ExampleCo"
    assert job.title == "Staff Data Scientist"
    assert "experimentation" in job.description.lower()
    assert job.remote_policy == "Remote"
    assert job.apply_url is None


def test_handle_package_accepts_job_url_input(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    html_path = tmp_path / "job.html"
    html_path.write_text(
        "\n".join(
            [
                "<html>",
                "<head>",
                "<title>Staff Data Scientist - ExampleCo</title>",
                '<meta name="description" content="Lead experimentation and AI tooling. Remote role.">',
                "</head>",
                "<body>",
                "<h1>Staff Data Scientist</h1>",
                "<p>Lead experimentation and AI tooling.</p>",
                "</body>",
                "</html>",
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
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    message = handle_package(paths, job_url=html_path.resolve().as_uri())

    assert "Generated application packet" in message
    assert (tmp_path / "outputs" / "applications" / "exampleco_staff_data_scientist" / "report.md").exists()
    report_text = (tmp_path / "outputs" / "applications" / "exampleco_staff_data_scientist" / "report.md").read_text(
        encoding="utf-8"
    )
    assert "Application Readiness: not_inspected" in report_text


def test_handle_package_ignores_stale_readiness_when_job_url_has_no_apply_form(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    html_path = tmp_path / "job.html"
    html_path.write_text(
        "\n".join(
            [
                "<html>",
                "<head>",
                "<title>Staff Data Scientist - ExampleCo</title>",
                '<meta name="description" content="Lead experimentation and AI tooling. Remote role.">',
                "</head>",
                "<body>",
                "<h1>Staff Data Scientist</h1>",
                "<p>Lead experimentation and AI tooling.</p>",
                "</body>",
                "</html>",
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
    ]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    save_readiness(
        paths,
        ApplicationReadiness(
            job_id="exampleco-staff-data-scientist",
            readiness_status="ready",
            application_difficulty="easy",
            required_assets=[],
            missing_assets=[],
            estimated_time_minutes=10,
            requirements=[],
        ),
    )

    handle_package(paths, job_url=html_path.resolve().as_uri())

    report_text = (tmp_path / "outputs" / "applications" / "exampleco_staff_data_scientist" / "report.md").read_text(
        encoding="utf-8"
    )
    assert "Application Readiness: not_inspected" in report_text
