from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CandidateProfile
from .paths import ProjectPaths
from .profile import answer_bank_map, load_profile


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "n/a", "tbd", "todo"}:
        return ""
    return text


def _bool_value(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return _clean(value)


def _answer(answers: dict[str, str], section: str) -> str:
    for key, value in answers.items():
        if key.lower() == section.lower():
            return _clean(value)
    return ""


def _candidate_value(profile: CandidateProfile, answers: dict[str, str], key: str) -> str:
    candidate = profile.preferences.candidate
    defaults = candidate.get("voluntary_self_identification")
    defaults = defaults if isinstance(defaults, dict) else {}
    if key == "legal_name":
        return _clean(candidate.get("legal_name") or candidate.get("name"))
    if key == "work_authorization":
        return _answer(answers, "Work Authorization") or _bool_value(candidate.get("authorized_to_work_us")) or _clean(candidate.get("work_authorization"))
    if key == "sponsorship":
        return _clean(candidate.get("sponsorship_note")) or _bool_value(candidate.get("requires_sponsorship"))
    if key == "sf_hybrid":
        return _bool_value(candidate.get("willing_sf_hybrid_3_days_per_week"))
    if key == "seattle_hybrid":
        return _bool_value(candidate.get("willing_seattle_hybrid"))
    if key == "salary_expectations":
        return _answer(answers, "Salary Expectations")
    if key == "why_looking":
        return _answer(answers, "Why Are You Looking?")
    if key == "application_source":
        return _answer(answers, "Application Source") or "Company Website"
    if key == "distributed_teams_experience":
        return _answer(answers, "Remote / Distributed Teams Experience")
    if key == "gender":
        return _clean(defaults.get("gender")) if defaults.get("user_confirmed_for_future_applications") is True else ""
    if key == "race":
        return _clean(defaults.get("race")) if defaults.get("user_confirmed_for_future_applications") is True else ""
    if key == "veteran_status":
        return _clean(defaults.get("veteran_status")) if defaults.get("user_confirmed_for_future_applications") is True else ""
    if key == "disability_status":
        return _clean(defaults.get("disability_status")) if defaults.get("user_confirmed_for_future_applications") is True else ""
    return _clean(candidate.get(key))


PREFLIGHT_CHECKS = [
    {
        "key": "legal_name",
        "label": "Legal name",
        "required": True,
        "category": "identity",
        "guidance": "Add candidate.legal_name or candidate.name.",
    },
    {"key": "preferred_name", "label": "Preferred name", "required": False, "category": "identity", "guidance": "Add candidate.preferred_name if desired."},
    {"key": "email", "label": "Email", "required": True, "category": "identity", "guidance": "Add candidate.email."},
    {"key": "phone", "label": "Phone", "required": True, "category": "identity", "guidance": "Add candidate.phone."},
    {"key": "linkedin", "label": "LinkedIn", "required": True, "category": "identity", "guidance": "Add candidate.linkedin."},
    {"key": "github", "label": "GitHub", "required": False, "category": "optional_work_samples", "guidance": "Add candidate.github to auto-fill code sample prompts."},
    {"key": "portfolio", "label": "Portfolio", "required": False, "category": "optional_work_samples", "guidance": "Add candidate.portfolio to auto-fill portfolio/work-sample prompts."},
    {"key": "current_location", "label": "Current location", "required": True, "category": "location", "guidance": "Add candidate.current_location."},
    {"key": "relocation_note", "label": "Relocation note", "required": False, "category": "location", "guidance": "Add candidate.relocation_note."},
    {"key": "start_date_or_availability", "label": "Start date / availability", "required": True, "category": "availability", "guidance": "Add candidate.start_date_or_availability or Answer Bank > Availability."},
    {"key": "work_authorization", "label": "US work authorization", "required": True, "category": "legal_review", "guidance": "Add Answer Bank > Work Authorization."},
    {"key": "sponsorship", "label": "Sponsorship requirement", "required": True, "category": "legal_review", "guidance": "Add candidate.sponsorship_note or requires_sponsorship."},
    {"key": "sf_hybrid", "label": "SF hybrid willingness", "required": False, "category": "location_commitment", "guidance": "Set candidate.willing_sf_hybrid_3_days_per_week to true or false."},
    {"key": "seattle_hybrid", "label": "Seattle hybrid willingness", "required": False, "category": "location_commitment", "guidance": "Set candidate.willing_seattle_hybrid to true or false."},
    {"key": "salary_expectations", "label": "Salary expectations", "required": False, "category": "compensation", "guidance": "Add Answer Bank > Salary Expectations."},
    {"key": "why_looking", "label": "Why looking / motivation", "required": False, "category": "narrative", "guidance": "Add Answer Bank > Why Are You Looking?."},
    {"key": "application_source", "label": "Application source", "required": False, "category": "source", "guidance": "Optional: add Answer Bank > Application Source. Defaults to LinkedIn."},
    {
        "key": "distributed_teams_experience",
        "label": "Remote/distributed teams experience",
        "required": False,
        "recommended": False,
        "category": "experience",
        "guidance": "Optional: add Answer Bank > Remote / Distributed Teams Experience.",
    },
    {"key": "gender", "label": "Gender", "required": False, "category": "voluntary_self_identification", "guidance": "Add confirmed voluntary self-identification defaults if you want these auto-filled."},
    {"key": "race", "label": "Race/ethnicity", "required": False, "category": "voluntary_self_identification", "guidance": "Add confirmed voluntary self-identification defaults if you want these auto-filled."},
    {"key": "veteran_status", "label": "Veteran status", "required": False, "category": "voluntary_self_identification", "guidance": "Add confirmed voluntary self-identification defaults if you want these auto-filled."},
    {"key": "disability_status", "label": "Disability status", "required": False, "category": "voluntary_self_identification", "guidance": "Add confirmed voluntary self-identification defaults if you want these auto-filled."},
]


def build_apply_preflight(profile: CandidateProfile) -> dict[str, Any]:
    answers = answer_bank_map(profile)
    checks: list[dict[str, Any]] = []
    for check in PREFLIGHT_CHECKS:
        value = _candidate_value(profile, answers, str(check["key"]))
        if value:
            status = "configured"
        elif check["required"]:
            status = "missing_required"
        elif check.get("recommended", True):
            status = "missing_recommended"
        else:
            status = "optional_missing"
        checks.append({**check, "status": status, "configured": bool(value)})

    required_missing = [item for item in checks if item["status"] == "missing_required"]
    recommended_missing = [item for item in checks if item["status"] == "missing_recommended"]
    status = "ready"
    if required_missing:
        status = "missing_required"
    elif recommended_missing:
        status = "missing_recommended"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "required_missing_count": len(required_missing),
        "recommended_missing_count": len(recommended_missing),
        "checks": checks,
    }


def render_apply_preflight_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Apply Preflight",
        "",
        f"Generated At: {payload['generated_at']}",
        f"Status: {payload['status']}",
        "",
        "## Summary",
        "",
        f"- Missing required answers: {payload['required_missing_count']}",
        f"- Missing recommended answers: {payload['recommended_missing_count']}",
        "",
        "## Checks",
        "",
        "| Category | Field | Required | Status | Guidance |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in payload["checks"]:
        required = "Yes" if item["required"] else "No"
        guidance = str(item["guidance"]).replace("|", "\\|")
        lines.append(f"| {item['category']} | {item['label']} | {required} | {item['status']} | {guidance} |")
    lines.append("")
    return "\n".join(lines)


def export_apply_preflight(paths: ProjectPaths, profile: CandidateProfile | None = None) -> tuple[Path, Path]:
    payload = build_apply_preflight(profile or load_profile(paths))
    json_path = paths.outputs_dir / "apply_preflight.json"
    md_path = paths.outputs_dir / "apply_preflight.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_apply_preflight_markdown(payload), encoding="utf-8")
    return json_path, md_path
