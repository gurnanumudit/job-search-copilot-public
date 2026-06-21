from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


Lane = Literal["Main", "Experimentation", "AI Tooling"]
Priority = Literal["must_apply", "apply", "maybe", "skip"]
ReadinessStatus = Literal["ready", "needs_review", "needs_prep", "blocked"]
Difficulty = Literal["easy", "medium", "hard"]
AnswerConfidence = Literal["high", "medium", "low", "missing"]
SponsorshipCompatibility = Literal["compatible", "unknown", "incompatible"]
SourceProbeStatus = Literal["workflow_ready", "partial", "needs_adapter", "blocked", "not_configured"]


class JobPosting(BaseModel):
    job_id: str
    company: str
    title: str
    location: str | None = None
    remote_policy: str | None = None
    salary_range: str | None = None
    employment_type: str | None = None
    source: str
    job_url: str
    apply_url: str | None = None
    ats: str | None = None
    description: str
    date_found: datetime


class JobScore(BaseModel):
    job_id: str
    fit_score: int = Field(ge=0, le=100)
    priority: Priority
    lane: Lane
    resume_version: str
    reason_summary: str
    why_fit: list[str]
    risks: list[str]
    tailoring_notes: list[str]
    referral_recommended: bool
    sponsorship_compatibility: SponsorshipCompatibility = "unknown"
    disqualifying_reasons: list[str] = Field(default_factory=list)


class ApplicationRequirement(BaseModel):
    job_id: str
    field_label: str
    field_type: str | None = None
    required: bool
    detected_options: list[str] = Field(default_factory=list)
    draft_answer: str | None = None
    answer_confidence: AnswerConfidence
    missing_input: bool
    notes: str | None = None


class ApplicationReadiness(BaseModel):
    job_id: str
    readiness_status: ReadinessStatus
    application_difficulty: Difficulty
    required_assets: list[str]
    missing_assets: list[str]
    estimated_time_minutes: int
    requirements: list[ApplicationRequirement]


class CandidatePreferences(BaseModel):
    candidate: dict[str, Any]
    location_preferences: dict[str, list[str]] = Field(default_factory=dict)
    role_lanes: list[dict[str, str]] = Field(default_factory=list)
    job_priorities: dict[str, list[str]] = Field(default_factory=dict)


class CandidateProfile(BaseModel):
    profile_bank_text: str
    answer_bank_text: str
    preferences: CandidatePreferences
    main_resume_path: Path | None = None
    experimentation_resume_path: Path | None = None
    ai_tooling_resume_path: Path | None = None


class RoleKeywordConfig(BaseModel):
    include_keywords: dict[str, list[str]] = Field(default_factory=dict)
    exclude_keywords: list[str] = Field(default_factory=list)


class CompanySourceConfig(BaseModel):
    name: str
    priority: str | None = None
    visa_likelihood: str | None = None
    ats: str | None = None
    ashby_board_slug: str | None = None
    ashby_jobs_url: str | None = None
    greenhouse_board_token: str | None = None
    lever_company: str | None = None
    jobs_json_url: str | None = None
    careers_url: str | None = None
    include_keywords: list[str] = Field(default_factory=list)
    max_jobs: int | None = None


class SourceCompaniesConfig(BaseModel):
    companies: list[CompanySourceConfig] = Field(default_factory=list)


class ExclusionsConfig(BaseModel):
    titles: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class SourceProbeResult(BaseModel):
    company: str
    status: SourceProbeStatus
    configured_source: str | None = None
    detected_adapter: str | None = None
    source_url: str | None = None
    job_count: int = 0
    sample_titles: list[str] = Field(default_factory=list)
    failure_kind: str | None = None
    failure_hint: str | None = None
    notes: str | None = None


class InspectResult(BaseModel):
    readiness: ApplicationReadiness
    html_snapshot_path: Path | None = None
    screenshot_path: Path | None = None
    blocked_reason: str | None = None


class GeneratedPackage(BaseModel):
    job: JobPosting
    score: JobScore
    readiness: ApplicationReadiness | None = None
    referral_message: str
    tailoring_notes: list[str]
    application_dir: Path
    tailored_resume_path: Path
    final_resume_pdf_path: Path
    application_answers_path: Path
    referral_message_path: Path
    tailoring_notes_path: Path
    resume_provenance_path: Path
    additional_information_path: Path
    cover_letter_path: Path
    cover_letter_pdf_path: Path
    application_plan_path: Path
    report_path: Path


class RawFormField(BaseModel):
    label: str
    field_type: str | None = None
    required: bool = False
    options: list[str] = Field(default_factory=list)
    help_text: str | None = None


class LLMClient:
    def complete_json(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]) -> BaseModel:
        raise NotImplementedError


class MockLLMClient(LLMClient):
    def complete_json(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]) -> BaseModel:
        raise RuntimeError(
            "No live LLM provider is configured. The current implementation uses rule-based generation "
            "for scoring and drafting. Configure a provider later if you want model-backed completions."
        )


class OpenAIChatConfig(BaseModel):
    api_key: str
    model: str = "gpt-4.1-mini"


class OpenAILLMClient(LLMClient):
    def __init__(self, config: OpenAIChatConfig) -> None:
        self.config = config

    def complete_json(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]) -> BaseModel:
        from openai import OpenAI

        client = OpenAI(api_key=self.config.api_key)
        response = client.responses.parse(
            model=self.config.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text_format=schema,
        )
        return response.output_parsed
