from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def src(self) -> Path:
        return self.root / "src"

    @property
    def profile_dir(self) -> Path:
        return self.root / "profile"

    @property
    def resumes_dir(self) -> Path:
        user_resume_dir = self.root.parent / "Resume"
        if user_resume_dir.exists():
            return user_resume_dir
        return self.root / "resumes"

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def jobs_raw_dir(self) -> Path:
        return self.root / "jobs" / "raw"

    @property
    def jobs_processed_dir(self) -> Path:
        return self.root / "jobs" / "processed"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def scores_dir(self) -> Path:
        return self.data_dir / "scores"

    @property
    def readiness_dir(self) -> Path:
        return self.data_dir / "readiness"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def outputs_packages_dir(self) -> Path:
        return self.outputs_dir / "packages"

    @property
    def outputs_applications_dir(self) -> Path:
        return self.outputs_dir / "applications"

    @property
    def outputs_referrals_dir(self) -> Path:
        return self.outputs_dir / "referral_messages"

    @property
    def outputs_tailoring_dir(self) -> Path:
        return self.outputs_dir / "tailoring_notes"

    @property
    def outputs_debug_dir(self) -> Path:
        return self.outputs_dir / "inspection_debug"

    @property
    def ranked_jobs_csv(self) -> Path:
        return self.outputs_dir / "ranked_jobs.csv"

    @property
    def application_requirements_csv(self) -> Path:
        return self.outputs_dir / "application_requirements.csv"

    @property
    def source_health_csv(self) -> Path:
        return self.outputs_dir / "source_health.csv"

    @property
    def source_health_md(self) -> Path:
        return self.outputs_dir / "source_health.md"

    @property
    def shortlist_md(self) -> Path:
        return self.outputs_dir / "shortlist.md"

    @property
    def latest_discovery_job_ids(self) -> Path:
        return self.outputs_dir / "latest_discovery_job_ids.txt"



def discover_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for path in [current, *current.parents]:
        if (path / "pyproject.toml").exists() and (path / "src" / "job_agent").exists():
            return path
    return current
