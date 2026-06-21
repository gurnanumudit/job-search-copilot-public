from __future__ import annotations

from pathlib import Path


PROFILE_BANK_EXAMPLE = """# Candidate Profile Bank

## Identity
Name: Example Candidate
Current role: Senior Data Scientist, Product Analytics & Platform
Location preference: Remote or selective hybrid
Work authorization: Authorized to work in the United States

## Core Positioning
Senior data scientist with experience building experimentation systems, product measurement workflows, and self-serve analytics tooling.

## Key Proof Points
- Company-wide experimentation SME for 20+ teams
- Scaled experimentation platform adoption to 500+ experiments
- Built internal AI tools adopted by 300+ developers
- Built a first in-house A/B testing platform
- Designed semantic metrics layer to experimentation platform integration
- Built AI agents for data science workflows with Braintrust evals
- Deep experience in causal inference, metric design, growth analytics, product analytics, Snowflake, dbt, Airflow, Python, SQL

## Resume Lanes
1. Main / Balanced
2. Experimentation / Staff DS
3. AI Tooling / Internal Tools

## Constraints
- Do not invent metrics.
"""

ANSWER_BANK_EXAMPLE = """# Answer Bank

## Work Authorization
Are you authorized to work in the United States?
Answer: Yes.

Will you now or in the future require sponsorship?
Answer: No.

## Salary Expectations
Answer: Flexible depending on level, role scope, location, and total compensation structure.

## Why Are You Looking?
Answer: I am exploring roles that let me operate at the intersection of experimentation, AI-enabled workflows, product measurement, and platform building.
"""

PREFERENCES_EXAMPLE = """candidate:
  name: "Example Candidate"
  email: "candidate@example.com"
  phone: "+1-555-555-5555"
  linkedin: ""
  github: ""
  portfolio: ""
  work_authorization: "authorized_us"
  current_location: "San Francisco, CA"
  relocation_note: "Open to hybrid or remote roles."
  start_date_or_availability: "Within 2-4 weeks"

location_preferences:
  preferred:
    - "San Francisco, CA"
    - "Remote"
  acceptable_hybrid:
    - "San Francisco, CA"
    - "Foster City, CA"
    - "Palo Alto, CA"
    - "Mountain View, CA"
  avoid:
    - "Onsite outside California"

role_lanes:
  - name: "Main"
    resume_file: "profile/main_resume.md"
  - name: "Experimentation"
    resume_file: "profile/experimentation_resume.md"
  - name: "AI Tooling"
    resume_file: "profile/ai_tooling_resume.md"

job_priorities:
  must_have:
    - "senior scope"
    - "high ownership"
    - "product or platform impact"
  strong_positive:
    - "experimentation"
    - "causal inference"
    - "AI tooling"
    - "internal tools"
    - "developer productivity"
    - "self-serve analytics"
    - "product measurement"
    - "growth analytics"
    - "data platform"
  negative:
    - "junior"
    - "intern"
    - "pure dashboarding"
    - "sales analytics only"
    - "manual reporting only"
"""

SCORING_RUBRIC_EXAMPLE = """Role Fit: 0-30
Seniority Fit: 0-20
Domain Fit: 0-15
AI / Platform Fit: 0-15
Comp / Location Fit: 0-10
Visa Fit: 0-10
"""

ROLE_KEYWORDS_EXAMPLE = """include_keywords:
  experimentation:
    - "experimentation"
    - "a/b testing"
    - "ab testing"
    - "causal inference"
    - "measurement science"
    - "growth data scientist"
    - "product data scientist"
    - "metric design"
    - "inference"
    - "platform"
  ai_tooling:
    - "AI tooling"
    - "applied AI"
    - "forward deployed"
    - "agent"
    - "agentic"
    - "LLM"
    - "internal tools"
    - "developer productivity"
    - "AI workflows"
  platform:
    - "data platform"
    - "analytics platform"
    - "semantic metrics"
    - "feature flag"
    - "self-serve analytics"
    - "data infrastructure"
    - "dbt"
    - "Snowflake"
    - "Airflow"

exclude_keywords:
  - "intern"
  - "new grad"
  - "junior"
  - "entry level"
  - "data entry"
  - "sales development"
  - "recruiter"
  - "contract only"
"""

COMPANIES_EXAMPLE = """companies:
  - name: "Replit"
    priority: "high"
    visa_likelihood: "medium"
    greenhouse_board_token: "replit"
"""

EXCLUSIONS_EXAMPLE = """titles:
  - "intern"
  - "junior"
phrases:
  - "must be authorized to work without sponsorship"
locations:
  - "onsite outside california"
"""

JOB_EXAMPLE = """# ExampleCo - Senior Data Scientist

Company: ExampleCo
Title: Senior Data Scientist
Location: San Francisco, CA
Remote Policy: Hybrid
Job URL: https://example.com/jobs/staff-ds
Apply URL: https://example.com/jobs/staff-ds/apply
ATS: greenhouse

## Description
Lead experimentation, product measurement, semantic metrics, and AI-enabled internal workflows for the product analytics organization.
"""


def template_files(root: Path) -> dict[Path, str]:
    return {
        root / "profile" / "profile_bank.md": PROFILE_BANK_EXAMPLE,
        root / "profile" / "profile_bank.example.md": PROFILE_BANK_EXAMPLE,
        root / "profile" / "answer_bank.md": ANSWER_BANK_EXAMPLE,
        root / "profile" / "answer_bank.example.md": ANSWER_BANK_EXAMPLE,
        root / "profile" / "preferences.yaml": PREFERENCES_EXAMPLE,
        root / "profile" / "preferences.example.yaml": PREFERENCES_EXAMPLE,
        root / "profile" / "scoring_rubric.md": SCORING_RUBRIC_EXAMPLE,
        root / "profile" / "main_resume.md": "# Main resume\n",
        root / "profile" / "experimentation_resume.md": "# Experimentation resume\n",
        root / "profile" / "ai_tooling_resume.md": "# AI tooling resume\n",
        root / "sources" / "role_keywords.yaml": ROLE_KEYWORDS_EXAMPLE,
        root / "sources" / "companies.yaml": COMPANIES_EXAMPLE,
        root / "sources" / "exclusions.yaml": EXCLUSIONS_EXAMPLE,
        root / "jobs" / "raw" / "example_job.md": JOB_EXAMPLE,
    }
