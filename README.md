# job-search-copilot

Local-first CLI for ingesting job descriptions, scoring fit against a candidate profile, inspecting application forms in read-only mode, and exporting a lightweight tracker.

> Work in progress: this repository is an active build, not a polished release. The author will continue updating it over time. It is being shared for portfolio and code-review purposes, and it should not be treated as a ready-to-download or production-ready tool.

This public repo is intentionally sanitized. It ships with example profile files only and excludes private resumes, generated application packets, local browser sessions, and other personal job-search artifacts.

## Public-safe setup

```bash
cp profile/preferences.example.yaml profile/preferences.yaml
cp profile/profile_bank.example.md profile/profile_bank.md
cp profile/answer_bank.example.md profile/answer_bank.md
cp profile/application_intake.example.yaml profile/application_intake.yaml
```

Then replace the placeholder values in your local copies only. Do not commit `profile/preferences.yaml`, `profile/application_intake.yaml`, `resumes/`, `outputs/`, or `.sessions/`.

## Quick start

```bash
python -m job_agent init
python -m job_agent probe-sources --companies sources/top5_companies.yaml
python -m job_agent probe-sources --companies sources/top50_companies.yaml
python -m job_agent probe-sources --companies sources/qa_sample_companies.yaml
python -m job_agent discover --companies sources/top5_companies.yaml --mode fast
python -m job_agent discover --companies sources/top5_companies.yaml --mode two-phase --enrich-top-n 25
python -m job_agent discover --companies sources/qa_sample_companies.yaml --mode two-phase --enrich-top-n 20 --source-status workflow_ready,partial
python -m job_agent discover --companies sources/top50_companies.yaml --mode two-phase --enrich-top-n 25 --source-status workflow_ready,partial
python -m job_agent enrich --top-n 25 --min-score 60
python -m job_agent shortlist --limit 20 --min-score 50
python -m job_agent discover --companies sources/companies.yaml
python -m job_agent ingest --file jobs/raw/example_job.md
python -m job_agent score --job-id example-company-senior-data-scientist
python -m job_agent inspect --job-id example-company-senior-data-scientist
python -m job_agent package --job-id example-company-senior-data-scientist
python -m job_agent apply-preflight
python -m job_agent apply --job-id example-company-senior-data-scientist --headed
python -m job_agent apply-qa
python -m job_agent apply-gaps
python -m job_agent apply-batch --status not_run --limit 5
python -m job_agent apply --job-id example-company-senior-data-scientist --ats generic --headed
python -m job_agent apply --job-id example-company-senior-data-scientist --ats workday --headed
python -m job_agent capture-session --url "https://company.wd5.myworkdayjobs.com/login" --output .sessions/workday.json --wait-seconds 120
python -m job_agent apply --job-id example-company-senior-data-scientist --storage-state .sessions/workday.json --headed
python -m job_agent apply-batch --status blocked_account_required --storage-state .sessions/workday.json --limit 5
python -m job_agent apply --job-id example-company-senior-data-scientist --submit --fill-legal-acknowledgements
python -m job_agent package --file jobs/raw/example_job.md --apply-url "https://company.example/apply"
python -m job_agent package --job-url "https://company.example/jobs/staff-data-scientist"
python -m job_agent export --format csv
python -m job_agent export --format csv --scope all
```

## Notes

- The tool does not auto-submit applications.
- `apply` currently supports Ashby, Greenhouse, Lever, Workday, and a generic form fallback for unknown/simple application pages: it opens the application, fills known fields from `application_plan.json`, uploads the selected resume PDF, fills optional narrative text when present, saves a screenshot/HTML review artifact, and stops before submit by default. Use `--ats auto` unless you need to override platform detection.
- Workday support handles visible application forms and reports account/sign-in gates as `blocked_account_required`, so those applications can be retried with an authenticated browser-session workflow later instead of being mixed into generic manual review.
- For login-gated application flows, `apply` and `apply-batch` can reuse an authenticated browser session with `--storage-state path/to/state.json` or `--user-data-dir path/to/profile`. This is intended for candidate portals such as Workday where the user signs in once and the runner then handles form filling and resume upload before final review.
- `capture-session` opens a login/candidate-portal URL, waits while the user signs in, and saves a Playwright storage-state JSON that can be reused by `apply --storage-state` or `apply-batch --storage-state`.
- `--storage-state` and `--user-data-dir` are mutually exclusive. Use `--storage-state` for a portable Playwright session JSON, or `--user-data-dir` when you want the same persistent Chromium profile reused across runs.
- After ATS-specific filling, `apply` runs a generic pass over `detected_requirements` and the packet `answer_bank` so optional/custom fields with approved draft answers are filled when they can be matched by label. The generated `apply_review.md` includes a field-fill report showing which detected or answer-bank fields were filled or skipped and why.
- `inspect` and `apply` both handle more than plain text inputs: native selects, grouped radio/checkbox questions, and custom combobox-style dropdowns are surfaced as application requirements when labels/options are visible.
- Incomplete apply reviews include structured answer-gap recommendations, making it clear whether the next improvement is adding a reusable answer, teaching a new label alias, improving a platform selector, or retrying with an authenticated session. `apply-qa` rolls these up across packets with gap counts, sources, and the top recommendation, and `apply-gaps` writes `outputs/apply_gap_backlog.json` plus `outputs/apply_gap_backlog.md` as a prioritized repair backlog for company-level scaling.
- Generated packets include an `application_source` answer, defaulting to `Company Website` unless overridden in `profile/answer_bank.md`, for fields such as “How did you hear about us?”
- Multi-step applications can advance through safe non-final `Next`, `Continue`, or `Review` steps, repeating field filling on later pages while still refusing final submission without explicit approval.
- Before saving review artifacts, `apply` re-audits visible empty fields and makes one final safe recovery pass for labels that can be mapped to packet answers, such as work-sample/portfolio prompts, optional relocation radio groups, or custom location/office dropdowns. Unrelated opt-in checkboxes are not clicked unless a known answer mapping exists.
- Optional narrative prompts such as “Why are you interested?”, “What excites you?”, “Anything else?”, and “Letter of Interest” are matched to the generated Additional Information or Cover Letter drafts when the form exposes a compatible text field.
- `apply_review.md` also includes an unfilled visible-fields audit, which helps catch page fields that were not present in the precomputed packet or could not be matched safely.
- `apply_review.md` and `apply_review.json` include a resume upload report. QA treats `missing_input` or `not_uploaded` resume status as `needs_manual_field_review` rather than `ready_for_final_review`.
- `apply-qa` reports optional no-value policy blanks separately from unfilled fields, so final review can distinguish intentional truthful blanks from runner misses.
- Cross-platform blocker detection classifies login/account gates as `blocked_account_required` and CAPTCHA/2FA/human-verification gates as `blocked_human_verification_required`, with screenshots and HTML saved for review.
- Submit mode has a final review gate: even when `--submit` is provided, the runner refuses to click the final submit button if resume upload is missing, required fields are empty, optional/unknown fields are still blank, actionable skipped fields remain, or review blockers are present.
- Each run also writes `apply_review.json` with machine-readable coverage metrics for company-level QA and batch reporting.
- `apply-qa` exports `outputs/apply_review_qa.csv` and `outputs/apply_review_qa.md`, classifying every application packet as `not_run`, `ready_for_final_review`, `needs_manual_field_review`, `needs_optional_field_review`, `needs_runner_improvement`, `blocked_account_required`, `blocked_human_verification_required`, `blocked_submit_review_required`, or `submitted`.
- `apply-batch` runs review-mode application filling across packets selected by QA status, refreshes `apply-qa`, and never submits applications.
- Application packets include an `application_plan.json` for AI/browser runners. The plan includes the selected resume PDF path, cover-letter PDF path, optional narrative drafts, reusable answer-bank defaults, demographic defaults when the user has approved them, and a hard rule that final submission requires explicit user approval.
- `apply-preflight` writes `outputs/apply_preflight.json` and `outputs/apply_preflight.md`, checking whether the local profile has the required and recommended reusable answers needed for self-serve filling before browsers open. Missing GitHub/portfolio/work-sample answers are treated as recommended coverage gaps because they commonly appear as optional application fields; when such optional fields appear during apply and no truthful value is configured, they are recorded under `optional_no_value_fields` rather than blocking final-submit readiness.
- Optional narrative fields such as Additional Information, motivation prompts, and cover letters are drafted by default instead of being left blank.
- Resume and cover-letter uploads are designed to be automated by browser runners that support file inputs. The runner matches resume/CV inputs using file-input attributes plus visible labels and nearby field text, and only uploads cover-letter PDFs when a cover-letter-specific file input is detected. Pages with multiple upload controls such as “Cover Letter” and “Resume” can therefore be handled without user upload help. If the active browser surface cannot operate the native file picker, the user may still need to upload manually.
- Arbitration/certification acknowledgements are intentionally gated behind `--fill-legal-acknowledgements`, and final submission requires both `--submit` and that explicit legal-review flag.
- LinkedIn logged-in scraping is intentionally unsupported.
- CAPTCHA bypassing is intentionally unsupported.
- `inspect` uses Playwright and may require `playwright install chromium` after dependency install.
- `discover` supports public company sources via Ashby, Greenhouse, Lever, generic JSON job feeds, and a basic HTML careers-page fallback.
- `sources/top5_companies.yaml` is the first company-level workflow pack for OpenAI, Anthropic, Meta, Stripe, and Databricks.
- `sources/top50_companies.yaml` is the first 50-company target list.
- `sources/qa_sample_companies.yaml` is a smaller repeatable QA set for OpenAI, Anthropic, Replit, Ramp, and Perplexity.
- `sources/company_workflow_backlog.md` tracks the top-50 expansion backlog and the adapter status for each company.
- Run `probe-sources` before large discovery runs to generate `outputs/source_health.csv` and `outputs/source_health.md`.
- Use `discover --mode fast` for a quick scan, `discover --mode two-phase --enrich-top-n 25` to enrich only the strongest matches, and `discover --mode deep` only for small trusted source sets.
- Use `--source-status workflow_ready,partial` after probing a large list so blocked or adapter-needed sources do not stall the run.
- `shortlist` writes `outputs/shortlist.md` from the current ranked CSV, with role score, resume lane, sponsorship status, source health, next action, and apply link.
- `export --format csv` defaults to the latest discovery run using `outputs/latest_discovery_job_ids.txt`; use `--scope all` only when you intentionally want every historical stored job.
- Application packets include `resume_provenance.md` so each generated resume records the base resume, selected lane, generated path, job URL, and score at generation time.
