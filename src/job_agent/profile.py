from __future__ import annotations

from pathlib import Path

import yaml

from .markdown import extract_candidate_answers
from .models import CandidatePreferences, CandidateProfile, ExclusionsConfig, RoleKeywordConfig, SourceCompaniesConfig
from .paths import ProjectPaths


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_profile(paths: ProjectPaths) -> CandidateProfile:
    profile_bank_path = paths.profile_dir / "profile_bank.md"
    if not profile_bank_path.exists():
        profile_bank_path = paths.profile_dir / "profile_bank.example.md"

    answer_bank_path = paths.profile_dir / "answer_bank.md"
    if not answer_bank_path.exists():
        answer_bank_path = paths.profile_dir / "answer_bank.example.md"

    preferences_path = paths.profile_dir / "preferences.yaml"
    if not preferences_path.exists():
        preferences_path = paths.profile_dir / "preferences.example.yaml"

    preferences_data = yaml.safe_load(_read_text_if_exists(preferences_path)) or {}
    intake_data = yaml.safe_load(_read_text_if_exists(paths.profile_dir / "application_intake.yaml")) or {}
    candidate = dict(preferences_data.get("candidate") or {})
    for key, value in (intake_data.get("candidate_identity") or {}).items():
        candidate.setdefault(key, value)
    for key, value in (intake_data.get("application_defaults") or {}).items():
        candidate.setdefault(key, value)
    if intake_data.get("voluntary_self_identification"):
        candidate.setdefault("voluntary_self_identification", intake_data["voluntary_self_identification"])
    preferences_data["candidate"] = candidate

    return CandidateProfile(
        profile_bank_text=_read_text_if_exists(profile_bank_path),
        answer_bank_text=_read_text_if_exists(answer_bank_path),
        preferences=CandidatePreferences.model_validate(preferences_data),
        main_resume_path=paths.profile_dir / "main_resume.md",
        experimentation_resume_path=paths.profile_dir / "experimentation_resume.md",
        ai_tooling_resume_path=paths.profile_dir / "ai_tooling_resume.md",
    )


def load_role_keywords(paths: ProjectPaths) -> RoleKeywordConfig:
    path = paths.sources_dir / "role_keywords.yaml"
    data = yaml.safe_load(_read_text_if_exists(path)) or {}
    return RoleKeywordConfig.model_validate(data)


def load_companies(paths: ProjectPaths, config_path: Path | None = None) -> SourceCompaniesConfig:
    path = config_path or (paths.sources_dir / "companies.yaml")
    data = yaml.safe_load(_read_text_if_exists(path)) or {}
    return SourceCompaniesConfig.model_validate(data)


def load_exclusions(paths: ProjectPaths) -> ExclusionsConfig:
    path = paths.sources_dir / "exclusions.yaml"
    data = yaml.safe_load(_read_text_if_exists(path)) or {}
    return ExclusionsConfig.model_validate(data)


def answer_bank_map(profile: CandidateProfile) -> dict[str, str]:
    return extract_candidate_answers(profile.answer_bank_text)
