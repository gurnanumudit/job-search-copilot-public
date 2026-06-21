import json

from job_agent import cli
from job_agent.apply_preflight import build_apply_preflight, export_apply_preflight
from job_agent.models import CandidatePreferences, CandidateProfile
from job_agent.paths import ProjectPaths


def _profile(*, github: str = "", portfolio: str = "") -> CandidateProfile:
    return CandidateProfile(
        profile_bank_text="# Profile\n",
        answer_bank_text=(
            "# Answer Bank\n\n"
            "## Work Authorization\nAnswer: Yes.\n\n"
            "## Salary Expectations\nAnswer: Flexible depending on scope.\n\n"
            "## Why Are You Looking?\nAnswer: I am looking for experimentation and AI tooling roles.\n"
        ),
        preferences=CandidatePreferences.model_validate(
            {
                "candidate": {
                    "name": "Example Candidate",
                    "preferred_name": "Example",
                    "email": "candidate@example.com",
                    "phone": "555-555-5555",
                    "linkedin": "https://www.linkedin.com/in/example-candidate",
                    "github": github,
                    "portfolio": portfolio,
                    "current_location": "San Francisco, CA",
                    "relocation_note": "Open to moving.",
                    "start_date_or_availability": "Next month",
                    "requires_sponsorship": True,
                    "sponsorship_note": "Requires immigration sponsorship.",
                    "willing_sf_hybrid_3_days_per_week": True,
                    "willing_seattle_hybrid": True,
                    "voluntary_self_identification": {
                        "gender": "Male",
                        "race": "Asian",
                        "veteran_status": "I am not a protected veteran",
                        "disability_status": "No, I don't have a disability",
                        "user_confirmed_for_future_applications": True,
                    },
                },
                "location_preferences": {"preferred": ["San Francisco, CA"]},
                "role_lanes": [],
                "job_priorities": {},
            }
        ),
    )


def test_apply_preflight_reports_missing_recommended_optional_answers() -> None:
    payload = build_apply_preflight(_profile())
    missing = {item["key"]: item for item in payload["checks"] if item["status"] != "configured"}

    assert payload["status"] == "missing_recommended"
    assert payload["required_missing_count"] == 0
    assert missing["github"]["status"] == "missing_recommended"
    assert missing["portfolio"]["status"] == "missing_recommended"


def test_apply_preflight_ready_when_optional_work_samples_are_configured() -> None:
    payload = build_apply_preflight(_profile(github="https://github.com/example", portfolio="https://example.com"))

    assert payload["status"] == "ready"
    assert payload["required_missing_count"] == 0
    assert payload["recommended_missing_count"] == 0


def test_apply_preflight_treats_distributed_teams_experience_as_optional_context() -> None:
    payload = build_apply_preflight(_profile(github="https://github.com/example", portfolio="https://example.com"))
    status_by_key = {item["key"]: item["status"] for item in payload["checks"]}

    assert status_by_key["distributed_teams_experience"] == "optional_missing"


def test_export_apply_preflight_writes_json_and_markdown(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)

    json_path, md_path = export_apply_preflight(paths, _profile())

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    md_text = md_path.read_text(encoding="utf-8")
    assert payload["status"] == "missing_recommended"
    assert "# Apply Preflight" in md_text
    assert "Portfolio" in md_text


def test_handle_apply_preflight_exports_report(tmp_path) -> None:
    paths = ProjectPaths(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "preferences.yaml").write_text(
        """
candidate:
  name: "Example Candidate"
  email: "candidate@example.com"
  phone: "555-555-5555"
  linkedin: "https://www.linkedin.com/in/example-candidate"
  current_location: "San Francisco, CA"
  start_date_or_availability: "Next month"
  sponsorship_note: "Requires immigration sponsorship."
""",
        encoding="utf-8",
    )
    (profile_dir / "answer_bank.md").write_text("# Answer Bank\n\n## Work Authorization\nAnswer: Yes.\n", encoding="utf-8")

    message = cli.handle_apply_preflight(paths)

    assert "apply_preflight.json" in message
    assert (tmp_path / "outputs" / "apply_preflight.json").exists()
    assert (tmp_path / "outputs" / "apply_preflight.md").exists()
