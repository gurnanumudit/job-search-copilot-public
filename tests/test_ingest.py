from pathlib import Path

from job_agent.markdown import parse_job_markdown


def test_parse_job_markdown_extracts_basic_fields() -> None:
    path = Path(__file__).parent / "fixtures" / "jobs" / "sample_job.md"
    job = parse_job_markdown(path)
    assert job.company == "Replit"
    assert job.title == "Data Scientist"
    assert job.apply_url == "https://jobs.example.com/replit-ds/apply"
    assert job.job_id == "replit-data-scientist"
