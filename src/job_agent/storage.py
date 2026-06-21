from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import ApplicationReadiness, JobPosting, JobScore
from .paths import ProjectPaths


ModelT = TypeVar("ModelT", bound=BaseModel)


def ensure_directories(paths: ProjectPaths) -> None:
    directories = [
        paths.profile_dir,
        paths.sources_dir,
        paths.jobs_raw_dir,
        paths.jobs_processed_dir,
        paths.jobs_dir,
        paths.scores_dir,
        paths.readiness_dir,
        paths.outputs_dir,
        paths.outputs_packages_dir,
        paths.outputs_applications_dir,
        paths.outputs_referrals_dir,
        paths.outputs_tailoring_dir,
        paths.outputs_debug_dir,
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def _write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _read_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def save_job(paths: ProjectPaths, job: JobPosting) -> Path:
    target = paths.jobs_dir / f"{job.job_id}.json"
    _write_model(target, job)
    return target


def load_job(paths: ProjectPaths, job_id: str) -> JobPosting:
    return _read_model(paths.jobs_dir / f"{job_id}.json", JobPosting)


def list_jobs(paths: ProjectPaths) -> list[JobPosting]:
    return sorted(
        (_read_model(path, JobPosting) for path in paths.jobs_dir.glob("*.json")),
        key=lambda job: job.date_found,
    )


def save_score(paths: ProjectPaths, score: JobScore) -> Path:
    target = paths.scores_dir / f"{score.job_id}.json"
    _write_model(target, score)
    return target


def load_score(paths: ProjectPaths, job_id: str) -> JobScore:
    return _read_model(paths.scores_dir / f"{job_id}.json", JobScore)


def list_scores(paths: ProjectPaths) -> list[JobScore]:
    return sorted(
        (_read_model(path, JobScore) for path in paths.scores_dir.glob("*.json")),
        key=lambda score: score.job_id,
    )


def save_readiness(paths: ProjectPaths, readiness: ApplicationReadiness) -> Path:
    target = paths.readiness_dir / f"{readiness.job_id}.json"
    _write_model(target, readiness)
    return target


def load_readiness(paths: ProjectPaths, job_id: str) -> ApplicationReadiness:
    return _read_model(paths.readiness_dir / f"{job_id}.json", ApplicationReadiness)


def list_readiness(paths: ProjectPaths) -> list[ApplicationReadiness]:
    return sorted(
        (_read_model(path, ApplicationReadiness) for path in paths.readiness_dir.glob("*.json")),
        key=lambda readiness: readiness.job_id,
    )


def save_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def save_snapshot(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
