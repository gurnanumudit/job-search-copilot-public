# Job Search Copilot

A local-first Python CLI for turning public job listings into a ranked, reviewable application
workflow. It discovers roles, scores fit against a candidate profile, builds application packets,
and uses Playwright to assist with form filling while keeping the final decision with the user.

This repository is a sanitized portfolio build. Every committed profile and job is synthetic;
resumes, browser sessions, generated packets, and personal application data remain local.

The three committed resume examples in `profile/` are tracked files. Git ignore rules do not
protect edits to tracked files: do not commit personal replacements or application answers. Keep
personal work in a private copy and review `git diff` and staged files before sharing changes.

## How it works

| Stage | What the copilot does | Human control |
| --- | --- | --- |
| Discover | Reads public ATS feeds and configured careers pages | Choose companies and filters |
| Score | Ranks roles against explicit preferences and evidence | Review the score explanation |
| Package | Selects a resume lane and prepares draft answers | Edit every generated artifact |
| Inspect | Detects application fields and missing information | Supply or decline sensitive answers |
| Apply | Fills supported forms in a real browser | Review before any final action |
| QA | Records coverage, blockers, and skipped fields | Decide whether the packet is ready |

Supported adapters include Ashby, Greenhouse, Lever, Workday, public JSON feeds, and a conservative
generic-form fallback.

## Safety boundary

- Final submission is off by default and requires an explicit `--submit` flag.
- Legal acknowledgements require a separate explicit flag.
- Missing required fields, resume-upload failures, and unresolved review items block submission.
- CAPTCHA, 2FA, and account gates are reported rather than bypassed.
- LinkedIn scraping is intentionally unsupported.
- Browser session files and generated application artifacts are ignored by Git.

## Quick start

Requires Python 3.11 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

python -m job_agent init
python -m job_agent ingest --file jobs/raw/example_job.md
python -m job_agent score --job-id exampleco-senior-data-scientist
```

The ingested ID is derived from the committed example's company (`ExampleCo`) and title. Use the
ID printed by `ingest` when trying a different listing.

To exercise public-source discovery:

```bash
python -m job_agent probe-sources --companies sources/companies.yaml
python -m job_agent discover --companies sources/companies.yaml --mode two-phase --enrich-top-n 10
python -m job_agent shortlist --limit 10 --min-score 50
```

The committed synthetic listing uses an `example.com` placeholder, not a working application form.
Use the local HTML fixtures in `tests/` to test browser behavior; do not expect the sample URL to
open an ATS.

For a real listing that you have deliberately ingested and reviewed, install Playwright's Chromium
binary, replace `YOUR_JOB_ID` with the ID printed by ingest, and inspect without submitting:

```bash
playwright install chromium
python -m job_agent inspect --job-id YOUR_JOB_ID
python -m job_agent package --job-id YOUR_JOB_ID
python -m job_agent apply --job-id YOUR_JOB_ID --headed
python -m job_agent apply-qa
```

`playwright install chromium` downloads a browser binary. The commands after it can contact the
listing's website. In particular, `package` automatically inspects an apply URL when no saved
inspection exists. The synthetic ingest-and-score walkthrough above is local; neither workflow
enables `--submit`.

## Repository map

| Path | Purpose |
| --- | --- |
| `src/job_agent/` | Discovery, scoring, packaging, browser assistance, and QA |
| `profile/` | Synthetic profile and resume examples |
| `sources/` | Minimal public-source and scoring configuration |
| `jobs/raw/example_job.md` | Synthetic job used for the walkthrough |
| `tests/` | Unit and local-browser behavior tests |

## Verification

```bash
pytest -q
```

The suite covers deterministic scoring and packet generation as well as local HTML fixtures for
form filling, multi-step flows, uploads, account gates, CAPTCHA detection, and submit blocking. It
does not submit real applications or require private accounts.

## Deliberate limits

This is a local decision-support tool, not a hosted job board or autonomous applicant. Public job
sites change frequently, so adapters should be treated as inspected integrations rather than a
promise of permanent compatibility. Generated writing and browser-filled fields always require
human review.
