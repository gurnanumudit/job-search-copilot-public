from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .paths import ProjectPaths
from .storage import save_text


class ApplyRunnerError(RuntimeError):
    pass


SUPPORTED_ATS = {"ashby", "greenhouse", "lever", "workday", "generic"}


def application_dir_for_job(paths: ProjectPaths, job_id: str) -> Path:
    application_dirs = paths.outputs_applications_dir.iterdir() if paths.outputs_applications_dir.exists() else []
    for path in sorted(application_dirs):
        plan_path = path / "application_plan.json"
        if not plan_path.exists():
            continue
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if plan.get("job", {}).get("job_id") == job_id:
            return path
    raise FileNotFoundError(f"No application_plan.json found for job_id={job_id}. Run package first.")


def load_application_plan(application_dir: Path) -> dict[str, Any]:
    plan_path = application_dir / "application_plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"{plan_path} does not exist. Run package first.")
    return json.loads(plan_path.read_text(encoding="utf-8"))


def plan_value(plan: dict[str, Any], field: str) -> str | None:
    for item in plan.get("fill_fields", []):
        if str(item.get("field", "")).lower() == field.lower():
            if item.get("value_file"):
                return Path(item["value_file"]).read_text(encoding="utf-8").split("\n\n", 1)[-1].strip()
            value = item.get("value")
            return str(value).strip() if value is not None else None
    return None


def plan_artifact(plan: dict[str, Any], name: str) -> Path:
    value = plan.get("artifacts", {}).get(name)
    if not value:
        raise ApplyRunnerError(f"Application plan is missing artifacts.{name}.")
    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"Planned artifact does not exist: {path}")
    return path


def optional_plan_artifact(plan: dict[str, Any], name: str) -> Path | None:
    value = plan.get("artifacts", {}).get(name)
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


STATE_CODE_TO_NAME = {
    "CA": "California",
    "MA": "Massachusetts",
    "NY": "New York",
    "TX": "Texas",
    "WA": "Washington",
}


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned:
            continue
        normalized = normalize_label(cleaned)
        if normalized in seen:
            continue
        seen.add(normalized)
        kept.append(cleaned)
    return kept


def _location_preference_terms(plan: dict[str, Any]) -> list[str]:
    location = plan_value(plan, "Current Location") or ""
    if not location:
        return []
    parts = [part.strip() for part in re.split(r"[,/]", location) if part.strip()]
    terms = [location, *parts]
    match = re.search(r"\b([A-Z]{2})\b", location)
    if match:
        state_code = match.group(1).upper()
        terms.append(state_code)
        if state_code in STATE_CODE_TO_NAME:
            terms.append(STATE_CODE_TO_NAME[state_code])
    normalized = normalize_label(location)
    if "california" in normalized or ", ca" in normalized:
        terms.extend(["California", "CA"])
    if "new york" in normalized or ", ny" in normalized:
        terms.extend(["New York", "NY"])
    if "washington" in normalized or ", wa" in normalized or "seattle" in normalized:
        terms.extend(["Washington", "WA", "Seattle"])
    return _dedupe_preserve_order(terms)


def infer_ats_from_url(url: str | None) -> str | None:
    if not url:
        return None
    hostname = urlparse(url).hostname or ""
    if "ashbyhq.com" in hostname:
        return "ashby"
    if "greenhouse.io" in hostname:
        return "greenhouse"
    if "lever.co" in hostname:
        return "lever"
    if "workdayjobs.com" in hostname or "myworkday.com" in hostname:
        return "workday"
    return None


def resolve_ats(plan: dict[str, Any], requested_ats: str = "auto") -> str:
    if requested_ats != "auto":
        if requested_ats not in SUPPORTED_ATS:
            raise ApplyRunnerError(
                f"ATS '{requested_ats}' is not implemented yet. Available: auto, ashby, greenhouse, lever, workday, generic."
            )
        return requested_ats

    job = plan.get("job", {})
    for detected in [job.get("ats"), infer_ats_from_url(job.get("apply_url")), infer_ats_from_url(job.get("job_url"))]:
        if detected in SUPPORTED_ATS:
            return detected
    return "generic"


def split_full_name(full_name: str | None) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def render_review_summary(plan: dict[str, Any], review: dict[str, Any]) -> str:
    metrics = review_metrics(review)
    upload_report = review.get("file_upload_report") or {}
    upload_status = upload_report.get("status") or "not_checked"
    lines = [
        "# Apply Review",
        "",
        f"Generated At: {datetime.now(timezone.utc).isoformat()}",
        f"Company: {plan.get('job', {}).get('company')}",
        f"Role: {plan.get('job', {}).get('title')}",
        f"Apply URL: {plan.get('job', {}).get('apply_url')}",
        f"Mode: {review['mode']}",
        f"Status: {review['status']}",
        f"Detected Field Coverage: {metrics['detected_fields_filled']}/{metrics['detected_fields_total']} filled",
        f"Unfilled Required Visible Fields: {metrics['unfilled_required_visible_fields']}",
        f"Unfilled Optional/Unknown Visible Fields: {metrics['unfilled_optional_or_unknown_visible_fields']}",
        f"Optional No-Value Policy Fields: {metrics['optional_no_value_fields']}",
        f"Resume Upload: {upload_status}",
        "",
        "## Completed Actions",
    ]
    lines.extend(f"- {item}" for item in review.get("completed_actions", []))
    if upload_report:
        lines.extend(["", "## Resume Upload Report"])
        lines.append(f"- Status: {upload_status}")
        if upload_report.get("expected_file_name"):
            lines.append(f"- Expected File: {upload_report['expected_file_name']}")
        if upload_report.get("artifact_path"):
            lines.append(f"- Artifact: {upload_report['artifact_path']}")
    if review.get("field_fill_report"):
        lines.extend(["", "## Field Fill Report", "", "| Field | Status | Reason |", "| --- | --- | --- |"])
        for item in review["field_fill_report"]:
            field = str(item.get("field", "")).replace("|", "\\|")
            status = str(item.get("status", "")).replace("|", "\\|")
            reason = str(item.get("reason", "")).replace("|", "\\|")
            lines.append(f"| {field} | {status} | {reason} |")
    if review.get("unfilled_fields"):
        lines.extend(["", "## Unfilled Visible Fields"])
        for item in review["unfilled_fields"]:
            label = str(item.get("label") or "Unknown field")
            field_type = str(item.get("type") or "unknown")
            required = "required" if item.get("required") else "optional/unknown"
            lines.append(f"- {label} ({field_type}, {required})")
    if review.get("optional_no_value_fields"):
        lines.extend(["", "## Optional Fields Left Blank By Policy"])
        for item in review["optional_no_value_fields"]:
            label = str(item.get("label") or "Unknown field")
            answer_key = str(item.get("answer_key") or "unknown")
            reason = str(item.get("reason") or "No configured truthful answer.")
            lines.append(f"- {label} (`{answer_key}`): {reason}")
    if review.get("answer_gap_recommendations"):
        lines.extend(["", "## Answer Gap Recommendations"])
        for item in review["answer_gap_recommendations"]:
            field = str(item.get("field") or "Unknown field")
            recommendation = str(item.get("recommendation") or "Review manually.")
            answer_key = item.get("suggested_answer_key")
            suffix = f" Suggested key: `{answer_key}`." if answer_key else ""
            lines.append(f"- {field}: {recommendation}{suffix}")
    lines.extend(["", "## Remaining Review Items"])
    lines.extend(f"- {item}" for item in review.get("remaining_review_items", []))
    if review.get("confirmation_text"):
        lines.extend(["", "## Confirmation", review["confirmation_text"]])
    if review.get("screenshot_path"):
        lines.extend(["", f"Screenshot: {review['screenshot_path']}"])
    if review.get("html_path"):
        lines.append(f"HTML: {review['html_path']}")
    return "\n".join(lines) + "\n"


def review_metrics(review: dict[str, Any]) -> dict[str, int]:
    field_fill_report = review.get("field_fill_report") or []
    unfilled_fields = review.get("unfilled_fields") or []
    optional_no_value_fields = review.get("optional_no_value_fields") or []
    upload_status = (review.get("file_upload_report") or {}).get("status")
    filled = sum(1 for item in field_fill_report if item.get("status") == "filled")
    skipped = sum(1 for item in field_fill_report if item.get("status") == "skipped")
    return {
        "completed_actions": len(review.get("completed_actions") or []),
        "detected_fields_total": len(field_fill_report),
        "detected_fields_filled": filled,
        "detected_fields_skipped": skipped,
        "unfilled_visible_fields": len(unfilled_fields),
        "unfilled_required_visible_fields": sum(1 for item in unfilled_fields if item.get("required") is True),
        "unfilled_optional_or_unknown_visible_fields": sum(1 for item in unfilled_fields if item.get("required") is not True),
        "optional_no_value_fields": len(optional_no_value_fields),
        "resume_upload_verified": 1 if upload_status in {"uploaded", "uploaded_unverified"} else 0,
        "resume_upload_missing": 1 if upload_status in {"missing_input", "not_uploaded"} else 0,
    }


def render_review_json(plan: dict[str, Any], review: dict[str, Any], resolved_ats: str) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job": plan.get("job", {}),
        "resolved_ats": resolved_ats,
        "mode": review.get("mode"),
        "status": review.get("status"),
        "metrics": review_metrics(review),
        "completed_actions": review.get("completed_actions", []),
        "file_upload_report": review.get("file_upload_report", {}),
        "field_fill_report": review.get("field_fill_report", []),
        "unfilled_fields": review.get("unfilled_fields", []),
        "optional_no_value_fields": review.get("optional_no_value_fields", []),
        "answer_gap_recommendations": review.get("answer_gap_recommendations", []),
        "remaining_review_items": review.get("remaining_review_items", []),
        "confirmation_text": review.get("confirmation_text"),
        "artifacts": {
            "screenshot_path": review.get("screenshot_path"),
            "html_path": review.get("html_path"),
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def review_paths(application_dir: Path) -> tuple[Path, Path]:
    review_dir = application_dir / "apply_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    return review_dir / "review.png", review_dir / "review.html"


def initial_review_items(fill_legal_acknowledgements: bool) -> list[str]:
    remaining_review_items = [
        "Review all filled fields visually before final submission.",
        "Final submit requires explicit user approval.",
    ]
    if not fill_legal_acknowledgements:
        remaining_review_items.append("Legal acknowledgements/certifications were left for user review.")
    return remaining_review_items


SUBMIT_REVIEW_GATE_ITEMS = {
    "Review all filled fields visually before final submission.",
    "Final submit requires explicit user approval.",
}


SUBMIT_ACCEPTED_SKIP_REASONS = {
    "handled by ATS adapter",
    "file upload handled by ATS adapter",
    "legal acknowledgement left for explicit review",
    "handled by demographic group answer",
    "optional field intentionally left blank by no-value policy",
}


OPTIONAL_NO_VALUE_ANSWER_KEYS = {"github", "portfolio", "event_attendance"}
OPTIONAL_NO_VALUE_REASON = "optional field intentionally left blank by no-value policy"


def ensure_submit_allowed(submit: bool, fill_legal_acknowledgements: bool) -> None:
    if submit and not fill_legal_acknowledgements:
        raise ApplyRunnerError(
            "Refusing to submit without --fill-legal-acknowledgements. "
            "Review the application first, then rerun with that flag if you approve those fields."
        )


def _resolve_optional_path(path: str | Path | None, *, must_exist: bool = False) -> Path | None:
    if path is None:
        return None
    resolved = Path(path).expanduser()
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Browser session path does not exist: {resolved}")
    return resolved


async def _new_browser_page(
    playwright,
    *,
    headless: bool,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
):
    storage_state = _resolve_optional_path(storage_state_path, must_exist=True)
    profile_dir = _resolve_optional_path(user_data_dir)
    if storage_state and profile_dir:
        raise ApplyRunnerError("Use either --storage-state or --user-data-dir, not both.")

    if profile_dir:
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = await playwright.chromium.launch_persistent_context(str(profile_dir), headless=headless)
        page = context.pages[0] if context.pages else await context.new_page()
        return None, context, page, f"Using persistent browser profile: {profile_dir}"

    browser = await playwright.chromium.launch(headless=headless)
    context_kwargs = {"storage_state": str(storage_state)} if storage_state else {}
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    action = f"Loaded browser storage state: {storage_state}" if storage_state else None
    return browser, context, page, action


async def _close_browser_session(browser, context) -> None:
    await context.close()
    if browser is not None:
        await browser.close()


async def _goto_application_page(page, apply_url: str, *, timeout: int = 60000) -> None:
    try:
        await page.goto(apply_url, wait_until="networkidle", timeout=timeout)
    except Exception as exc:
        if exc.__class__.__name__ != "TimeoutError":
            raise
        await page.goto(apply_url, wait_until="domcontentloaded", timeout=timeout)
        await page.wait_for_timeout(3000)


async def _save_review_artifacts(page, screenshot_path: Path, html_path: Path) -> None:
    await page.screenshot(path=str(screenshot_path), full_page=True)
    html_path.write_text(await page.content(), encoding="utf-8")


async def _detect_application_blocker(page) -> dict[str, str] | None:
    return await page.evaluate(
        """
        () => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const bodyText = norm(document.body?.innerText || document.body?.textContent || '');
          const attrText = Array.from(document.querySelectorAll('iframe, [id], [class], [name], [aria-label], [title]'))
            .map((node) => [
              node.getAttribute('src'),
              node.getAttribute('id'),
              node.getAttribute('class'),
              node.getAttribute('name'),
              node.getAttribute('aria-label'),
              node.getAttribute('title'),
            ].filter(Boolean).join(' '))
            .join(' ')
            .toLowerCase();
          const combined = `${bodyText} ${attrText}`;
          const isVisible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const hasPassword = Boolean(document.querySelector('input[type="password"]'));
          const hasVerificationInput = Boolean(
            document.querySelector('input[autocomplete="one-time-code"], input[name*="code" i], input[id*="code" i]')
          );

          const visibleText = Array.from(document.querySelectorAll('body *'))
            .filter((node) => isVisible(node))
            .map((node) => node.innerText || node.textContent || '')
            .join(' ')
            .toLowerCase();
          const humanVerificationTextTokens = [
            "i'm not a robot",
            'verify you are human',
            'verify that you are human',
            'human verification',
            'security check',
            'bot detection',
            'complete the captcha',
            'captcha required',
            'hcaptcha',
          ];
          const visibleChallengeIframe = Array.from(document.querySelectorAll('iframe'))
            .filter((iframe) => isVisible(iframe))
            .some((iframe) => {
              const title = norm(iframe.getAttribute('title'));
              const src = norm(iframe.getAttribute('src'));
              return title.includes('challenge') && (title.includes('captcha') || src.includes('captcha'));
            });
          if (visibleChallengeIframe) {
            return {
              status: 'blocked_human_verification_required',
              message: 'Human verification or CAPTCHA step detected.',
            };
          }
          for (const token of humanVerificationTextTokens) {
            if (visibleText.includes(token) || bodyText.includes(token)) {
              return {
                status: 'blocked_human_verification_required',
                message: 'Human verification or CAPTCHA step detected.',
              };
            }
          }
          const twoFactorTokens = [
            'two-factor authentication',
            'two factor authentication',
            'multi-factor authentication',
            'verification code',
            'one-time code',
            'one time code',
            'authenticator app',
            'verify your identity',
          ];
          for (const token of twoFactorTokens) {
            if (combined.includes(token) || hasVerificationInput) {
              return {
                status: 'blocked_human_verification_required',
                message: 'Two-factor or identity verification step detected.',
              };
            }
          }
          const accountGatePhrases = [
            'sign in to apply',
            'sign in to continue',
            'sign into your account',
            'sign in to your account',
            'log in to apply',
            'login to apply',
            'log in to continue',
            'create account to apply',
            'create an account to apply',
            'candidate home account',
          ];
          for (const phrase of accountGatePhrases) {
            if (bodyText.includes(phrase)) {
              return {
                status: 'blocked_account_required',
                message: 'Account sign-in or candidate portal step detected.',
              };
            }
          }
          if (hasPassword && (bodyText.includes('forgot password') || bodyText.includes('sign in') || bodyText.includes('log in'))) {
            return {
              status: 'blocked_account_required',
              message: 'Login form detected.',
            };
          }
          return null;
        }
        """
    )


async def _blocked_review_if_present(
    page,
    *,
    actions: list[str],
    remaining_review_items: list[str],
    screenshot_path: Path,
    html_path: Path,
    submit: bool,
    field_fill_report: list[dict[str, str]] | None = None,
    unfilled_fields: list[dict[str, Any]] | None = None,
    file_upload_report: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    blocker = await _detect_application_blocker(page)
    if not blocker:
        return None
    message = blocker["message"]
    remaining_review_items.append(message)
    await _save_review_artifacts(page, screenshot_path, html_path)
    return {
        "mode": "submit" if submit else "review_only",
        "status": blocker["status"],
        "completed_actions": actions,
        "file_upload_report": file_upload_report or {},
        "field_fill_report": field_fill_report or [],
        "unfilled_fields": unfilled_fields or [],
        "remaining_review_items": remaining_review_items,
        "confirmation_text": message,
        "screenshot_path": str(screenshot_path),
        "html_path": str(html_path),
    }


async def _blocked_review_result(
    page,
    *,
    status: str,
    message: str,
    actions: list[str],
    remaining_review_items: list[str],
    screenshot_path: Path,
    html_path: Path,
    submit: bool,
    field_fill_report: list[dict[str, str]] | None = None,
    unfilled_fields: list[dict[str, Any]] | None = None,
    file_upload_report: dict[str, Any] | None = None,
    optional_no_value_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    remaining_review_items.append(message)
    await _save_review_artifacts(page, screenshot_path, html_path)
    return {
        "mode": "submit" if submit else "review_only",
        "status": status,
        "completed_actions": actions,
        "file_upload_report": file_upload_report or {},
        "field_fill_report": field_fill_report or [],
        "unfilled_fields": unfilled_fields or [],
        "optional_no_value_fields": optional_no_value_fields or [],
        "remaining_review_items": remaining_review_items,
        "confirmation_text": message,
        "screenshot_path": str(screenshot_path),
        "html_path": str(html_path),
    }


def _field_labels(fields: list[dict[str, Any]], *, limit: int = 5) -> str:
    labels = [str(item.get("label") or item.get("field") or "Unknown field") for item in fields]
    shown = labels[:limit]
    suffix = f" and {len(labels) - limit} more" if len(labels) > limit else ""
    return ", ".join(shown) + suffix


def _filled_field_labels(field_fill_report: list[dict[str, Any]]) -> set[str]:
    return {
        normalize_label(str(item.get("field") or ""))
        for item in field_fill_report
        if item.get("status") == "filled" and str(item.get("field") or "").strip()
    }


def _unfilled_field_labels(unfilled_fields: list[dict[str, Any]]) -> set[str]:
    return {
        normalize_label(str(item.get("label") or item.get("field") or ""))
        for item in unfilled_fields
        if str(item.get("label") or item.get("field") or "").strip()
    }


def _prune_resolved_review_items(
    remaining_review_items: list[str],
    field_fill_report: list[dict[str, Any]],
    unfilled_fields: list[dict[str, Any]],
) -> None:
    filled_labels = _filled_field_labels(field_fill_report)
    unfilled_labels = _unfilled_field_labels(unfilled_fields)
    kept: list[str] = []
    for item in remaining_review_items:
        match = re.match(r"Detected field not filled automatically: (.*?) \(", item)
        if match:
            normalized_label = normalize_label(match.group(1))
            if normalized_label in filled_labels or normalized_label not in unfilled_labels:
                continue
        kept.append(item)
    remaining_review_items[:] = list(dict.fromkeys(kept))


def _submit_blockers(
    *,
    remaining_review_items: list[str],
    field_fill_report: list[dict[str, Any]],
    unfilled_fields: list[dict[str, Any]],
    file_upload_report: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    upload_status = str(file_upload_report.get("status") or "")
    if upload_status in {"missing_input", "not_uploaded"}:
        blockers.append(f"Resume upload is not complete: {upload_status}.")

    required_fields = [item for item in unfilled_fields if item.get("required") is True]
    if required_fields:
        blockers.append(f"Unfilled required visible fields remain: {_field_labels(required_fields)}.")

    optional_fields = [item for item in unfilled_fields if item.get("required") is not True]
    if optional_fields:
        blockers.append(f"Unfilled optional/unknown visible fields remain: {_field_labels(optional_fields)}.")

    unfilled_labels = _unfilled_field_labels(unfilled_fields)
    actionable_skips = [
        item
        for item in field_fill_report
        if item.get("status") == "skipped" and str(item.get("reason") or "") not in SUBMIT_ACCEPTED_SKIP_REASONS
        and normalize_label(str(item.get("field") or "")) in unfilled_labels
    ]
    if actionable_skips:
        blockers.append(f"Actionable skipped fields remain: {_field_labels(actionable_skips)}.")

    unresolved_review_items = [
        item
        for item in remaining_review_items
        if item not in SUBMIT_REVIEW_GATE_ITEMS and not item.startswith("Submit blocked:")
    ]
    blockers.extend(unresolved_review_items)
    return list(dict.fromkeys(blockers))


async def _blocked_submit_review_if_incomplete(
    page,
    *,
    submit: bool,
    actions: list[str],
    remaining_review_items: list[str],
    screenshot_path: Path,
    html_path: Path,
    field_fill_report: list[dict[str, Any]],
    unfilled_fields: list[dict[str, Any]],
    file_upload_report: dict[str, Any],
    optional_no_value_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    _prune_resolved_review_items(remaining_review_items, field_fill_report, unfilled_fields)
    if not submit:
        return None

    blockers = _submit_blockers(
        remaining_review_items=remaining_review_items,
        field_fill_report=field_fill_report,
        unfilled_fields=unfilled_fields,
        file_upload_report=file_upload_report,
    )
    if not blockers:
        return None

    blocked_items = [f"Submit blocked: {item}" for item in blockers]
    remaining_review_items[:] = list(dict.fromkeys([*remaining_review_items, *blocked_items]))
    await _save_review_artifacts(page, screenshot_path, html_path)
    return {
        "mode": "submit",
        "status": "blocked_submit_review_required",
        "completed_actions": actions,
        "file_upload_report": file_upload_report,
        "field_fill_report": field_fill_report,
        "unfilled_fields": unfilled_fields,
        "optional_no_value_fields": optional_no_value_fields or [],
        "remaining_review_items": remaining_review_items,
        "confirmation_text": "Submit blocked because the application review is incomplete.",
        "screenshot_path": str(screenshot_path),
        "html_path": str(html_path),
    }


async def _verify_resume_upload(page, resume_pdf: Path, actions: list[str]) -> dict[str, Any]:
    expected_file_name = resume_pdf.name
    file_inputs = await page.evaluate(
        """
        () => {
          const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const labelFor = (element) => {
            const explicit = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null;
            if (explicit) return norm(explicit.innerText || explicit.textContent);
            const aria = element.getAttribute('aria-label');
            if (aria) return norm(aria);
            const parentLabel = element.closest('label');
            if (parentLabel) return norm(parentLabel.innerText || parentLabel.textContent);
            return norm(element.name || element.id || 'File input');
          };
          return Array.from(document.querySelectorAll('input[type="file"]')).map((element, index) => ({
            index,
            id: element.id || '',
            name: element.name || '',
            label: labelFor(element),
            accept: element.getAttribute('accept') || '',
            visible: isVisible(element),
            file_count: element.files ? element.files.length : 0,
            file_names: Array.from(element.files || []).map((file) => file.name),
          }));
        }
        """
    )
    action_seen = any(action.startswith("Uploaded resume:") for action in actions)
    matched_input = next(
        (item for item in file_inputs if expected_file_name in (item.get("file_names") or [])),
        None,
    )
    if matched_input:
        status = "uploaded"
    elif action_seen:
        status = "uploaded_unverified"
    elif file_inputs:
        status = "not_uploaded"
    else:
        status = "missing_input"
    return {
        "status": status,
        "artifact_path": str(resume_pdf),
        "expected_file_name": expected_file_name,
        "upload_action_seen": action_seen,
        "matched_input": matched_input,
        "file_inputs": file_inputs,
    }


async def _audit_unfilled_fields(page) -> list[dict[str, Any]]:
    return await page.evaluate(
        """
        () => {
          const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const labelFor = (element) => {
            const explicit = element.id ? document.querySelector(`label[for="${element.id}"]`) : null;
            if (explicit) return norm(explicit.innerText || explicit.textContent);
            const aria = element.getAttribute('aria-label');
            if (aria) return norm(aria);
            const placeholder = element.getAttribute('placeholder');
            if (placeholder) return norm(placeholder);
            const parentLabel = element.closest('label');
            if (parentLabel) return norm(parentLabel.innerText || parentLabel.textContent);
            let node = element.parentElement;
            for (let depth = 0; depth < 3 && node; depth += 1) {
              const text = norm(node.innerText || node.textContent);
              if (text && text.length <= 180) return text;
              node = node.parentElement;
            }
            return norm(element.name || element.id || 'Unknown field');
          };
          const scopeIds = new WeakMap();
          let scopeCounter = 0;
          const scopeForGroupedControl = (element) => {
            let scope = element.closest('fieldset') || element.closest('[role="radiogroup"]') || element.closest('[role="group"]');
            if (scope) return scope;
            let node = element.parentElement;
            for (let depth = 0; depth < 4 && node; depth += 1) {
              const count = node.querySelectorAll('input[type="radio"], input[type="checkbox"]').length;
              if (count > 1) return node;
              node = node.parentElement;
            }
            return null;
          };
          const scopeId = (scope) => {
            if (!scope) return 'none';
            if (!scopeIds.has(scope)) {
              scopeCounter += 1;
              scopeIds.set(scope, scopeCounter);
            }
            return String(scopeIds.get(scope));
          };
          const groupedLabel = (element, scope) => {
            const legend = scope?.querySelector('legend');
            if (legend) {
              const text = norm(legend.innerText || legend.textContent);
              if (text) return text;
            }
            return labelFor(element);
          };
          const isCustomTrigger = (element) => {
            if (!element) return false;
            const role = (element.getAttribute('role') || '').toLowerCase();
            return role === 'combobox'
              || element.getAttribute('aria-haspopup') === 'listbox'
              || element.hasAttribute('aria-controls')
              || element.hasAttribute('aria-expanded')
              || element.hasAttribute('aria-autocomplete');
          };
          const controls = Array.from(document.querySelectorAll('input, textarea, select'));
          const unfilledControls = controls
            .filter((element) => {
              const type = (element.getAttribute('type') || element.tagName || '').toLowerCase();
              if (['hidden', 'file', 'submit', 'button', 'checkbox', 'radio'].includes(type)) return false;
              if (isCustomTrigger(element)) return false;
              if (element.disabled || element.readOnly || !isVisible(element)) return false;
              return !norm(element.value);
            })
            .map((element) => ({
              label: labelFor(element),
              type: (element.getAttribute('type') || element.tagName || '').toLowerCase(),
              required: Boolean(element.required || element.getAttribute('aria-required') === 'true'),
            }))
            .filter((item, index, items) => item.label && items.findIndex((other) => other.label === item.label) === index);
          const groupedInputs = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'))
            .filter((element) => !element.disabled && isVisible(element) && (element.required || element.getAttribute('aria-required') === 'true'));
          const groupedMap = new Map();
          for (const element of groupedInputs) {
            const scope = scopeForGroupedControl(element);
            const type = (element.getAttribute('type') || '').toLowerCase();
            const groupName = element.getAttribute('name');
            const siblingCount = scope ? scope.querySelectorAll('input[type="radio"], input[type="checkbox"]').length : 1;
            const key = groupName
              ? `name:${type}:${groupName}`
              : siblingCount > 1
                ? `scope:${type}:${scopeId(scope)}`
                : `self:${type}:${labelFor(element)}`;
            const current = groupedMap.get(key) || {
              label: groupedLabel(element, scope),
              type,
              required: false,
              checked: false,
            };
            current.required = current.required || Boolean(element.required || element.getAttribute('aria-required') === 'true');
            current.checked = current.checked || Boolean(element.checked);
            if (!current.label) current.label = groupedLabel(element, scope);
            groupedMap.set(key, current);
          }
          const groupedControls = Array.from(groupedMap.values())
            .filter((item) => item.required && !item.checked)
            .map(({ checked, ...item }) => item)
            .filter((item, index, items) => item.label && items.findIndex((other) => other.label === item.label) === index);
          return unfilledControls.concat(groupedControls);
        }
        """
    )


async def _audit_unchecked_grouped_fields(page) -> list[dict[str, Any]]:
    return await page.evaluate(
        """
        () => {
          const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const labelFor = (element) => {
            const explicit = element.id ? document.querySelector(`label[for="${element.id}"]`) : null;
            if (explicit) return norm(explicit.innerText || explicit.textContent);
            const aria = element.getAttribute('aria-label');
            if (aria) return norm(aria);
            const parentLabel = element.closest('label');
            if (parentLabel) return norm(parentLabel.innerText || parentLabel.textContent);
            return norm(element.name || element.id || 'Unknown grouped field');
          };
          const scopeIds = new WeakMap();
          let scopeCounter = 0;
          const scopeForGroupedControl = (element) => {
            let scope = element.closest('fieldset') || element.closest('[role="radiogroup"]') || element.closest('[role="group"]');
            if (scope) return scope;
            let node = element.parentElement;
            for (let depth = 0; depth < 4 && node; depth += 1) {
              const count = node.querySelectorAll('input[type="radio"], input[type="checkbox"]').length;
              if (count > 1) return node;
              node = node.parentElement;
            }
            return null;
          };
          const scopeId = (scope) => {
            if (!scope) return 'none';
            if (!scopeIds.has(scope)) {
              scopeCounter += 1;
              scopeIds.set(scope, scopeCounter);
            }
            return String(scopeIds.get(scope));
          };
          const groupedLabel = (element, scope) => {
            const legend = scope?.querySelector('legend');
            if (legend) {
              const text = norm(legend.innerText || legend.textContent);
              if (text) return text;
            }
            return labelFor(element);
          };
          const groupedInputs = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'))
            .filter((element) => !element.disabled && isVisible(element));
          const groupedMap = new Map();
          for (const element of groupedInputs) {
            const scope = scopeForGroupedControl(element);
            const type = (element.getAttribute('type') || '').toLowerCase();
            const groupName = element.getAttribute('name');
            const siblingCount = scope ? scope.querySelectorAll('input[type="radio"], input[type="checkbox"]').length : 1;
            const key = groupName
              ? `name:${type}:${groupName}`
              : siblingCount > 1
                ? `scope:${type}:${scopeId(scope)}`
                : `self:${type}:${labelFor(element)}`;
            const current = groupedMap.get(key) || {
              label: groupedLabel(element, scope),
              type,
              required: false,
              checked: false,
            };
            current.required = current.required || Boolean(element.required || element.getAttribute('aria-required') === 'true');
            current.checked = current.checked || Boolean(element.checked);
            if (!current.label) current.label = groupedLabel(element, scope);
            groupedMap.set(key, current);
          }
          return Array.from(groupedMap.values())
            .filter((item) => !item.checked)
            .map(({ checked, ...item }) => item)
            .filter((item, index, items) => item.label && items.findIndex((other) => other.label === item.label) === index);
        }
        """
    )


async def _audit_unselected_custom_controls(page) -> list[dict[str, Any]]:
    return await page.evaluate(
        """
        () => {
          const norm = (text) => (text || '').replace(/\\s+/g, ' ').trim();
          const normLower = (text) => norm(text).toLowerCase();
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const isCustomTrigger = (element) => {
            if (!element) return false;
            const role = (element.getAttribute('role') || '').toLowerCase();
            return role === 'combobox'
              || element.getAttribute('aria-haspopup') === 'listbox'
              || element.hasAttribute('aria-controls')
              || element.hasAttribute('aria-expanded')
              || element.hasAttribute('aria-autocomplete');
          };
          const labelFor = (element) => {
            const labelledBy = element.getAttribute('aria-labelledby');
            const labelledText = labelledBy ? labelledBy.split(/\\s+/).map((id) => document.getElementById(id)?.innerText || document.getElementById(id)?.textContent || '').join(' ') : '';
            if (labelledText) return norm(labelledText);
            const explicit = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null;
            if (explicit) return norm(explicit.innerText || explicit.textContent);
            const aria = element.getAttribute('aria-label');
            if (aria) return norm(aria);
            let node = element.parentElement;
            for (let depth = 0; depth < 3 && node; depth += 1) {
              const text = norm(node.innerText || node.textContent);
              if (text && text.length <= 180) return text;
              node = node.parentElement;
            }
            return norm(element.name || element.id || 'Custom dropdown');
          };
          const placeholderTokens = ['select', 'choose', 'start typing', 'type here', 'please select', ''];
          const currentValueText = (element) => {
            const tag = (element.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea') return normLower(element.value || element.getAttribute('placeholder') || '');
            return normLower(element.innerText || element.textContent || '');
          };
          const hasCustomValue = (element) => {
            const scope = element.closest('.select') || element.closest('[role="group"]') || element.parentElement?.parentElement;
            if (!scope) return false;
            const valueContainer = scope.querySelector('.select__value-container--has-value, .select__single-value, [class*="singleValue"]');
            return Boolean(valueContainer && isVisible(valueContainer) && norm(valueContainer.innerText || valueContainer.textContent));
          };
          return Array.from(document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"], [aria-controls], [aria-expanded], input[aria-autocomplete]'))
            .filter((element) => !element.disabled && isVisible(element) && isCustomTrigger(element))
            .filter((element) => {
              if (hasCustomValue(element)) return false;
              const text = currentValueText(element);
              return !text || placeholderTokens.some((token) => token && (text === token || text.includes(token)));
            })
            .map((element) => ({
              label: labelFor(element),
              type: 'custom_select',
              required: Boolean(element.required || element.getAttribute('aria-required') === 'true'),
            }))
            .filter((item, index, items) => item.label && items.findIndex((other) => other.label === item.label) === index);
        }
        """
    )


async def _click_submit_button(page, names: list[str]) -> None:
    for name in names:
        button = page.get_by_role("button", name=name)
        if await button.count() == 1:
            await button.click()
            return
    submit = page.locator('button[type="submit"], input[type="submit"]').first
    if await submit.count() > 0:
        await submit.click()
        return
    raise ApplyRunnerError("Submit button was not found.")


async def _visible_option_count(page) -> int:
    return int(
        await page.evaluate(
            """
            () => {
              const isVisible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              return Array.from(document.querySelectorAll('[role="option"], [role="listbox"] *, [data-value], li'))
                .filter((node) => isVisible(node))
                .length;
            }
            """
        )
    )


async def _click_next_step_button(page, actions: list[str]) -> bool:
    clicked_label = await page.evaluate(
        """
        () => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const labelFor = (element) => norm(
            element.innerText
            || element.textContent
            || element.getAttribute('aria-label')
            || element.value
            || ''
          );
          const allowed = new Set([
            'next',
            'next step',
            'continue',
            'continue application',
            'save and continue',
            'review',
            'review application',
          ]);
          const disallowedTokens = ['submit', 'apply', 'send application', 'final'];
          const candidates = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a[role="button"]'))
            .filter((element) => !element.disabled && isVisible(element))
            .map((element) => ({ element, label: labelFor(element) }))
            .filter((item) => item.label && allowed.has(item.label))
            .filter((item) => !disallowedTokens.some((token) => item.label.includes(token)));
          if (!candidates.length) return null;
          candidates[0].element.click();
          return candidates[0].label;
        }
        """
    )
    if not clicked_label:
        return False
    actions.append(f"Advanced application step: {clicked_label}")
    await page.wait_for_timeout(1000)
    return True


async def _fill_input_by_name(page, name: str, value: str, action_label: str, actions: list[str]) -> None:
    locator = page.locator(f'input[name="{name}"]')
    if await locator.count() == 1:
        await locator.fill(value)
        actions.append(action_label)


async def _set_first_value_by_selectors(page, selectors: list[str], value: str | None, action_label: str, actions: list[str]) -> bool:
    if not value:
        return False
    updated = await page.evaluate(
        """
        ({ selectors, value }) => {
          for (const selector of selectors) {
            const element = document.querySelector(selector);
            if (!element || element.disabled || element.readOnly) continue;
            element.focus?.();
            element.value = value;
            element.setAttribute('value', value);
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          }
          return false;
        }
        """,
        {"selectors": selectors, "value": value},
    )
    if updated:
        actions.append(action_label)
    return bool(updated)


async def _fill_textarea_by_name(page, name: str, value: str, action_label: str, actions: list[str]) -> None:
    locator = page.locator(f'textarea[name="{name}"]')
    if await locator.count() == 1:
        await locator.fill(value)
        actions.append(action_label)


async def _fill_first_present(page, selectors: list[str], value: str | None, action_label: str, actions: list[str]) -> bool:
    if not value:
        return False
    for selector in selectors:
        locator = page.locator(selector).first
        if await locator.count() > 0:
            await locator.fill(value)
            actions.append(action_label)
            return True
    return False


async def _fill_field_by_label(
    page,
    label_contains: str,
    value: str | None,
    actions: list[str],
    action_label: str | None = None,
    only_empty: bool = False,
) -> bool:
    if not value:
        return False
    label_pattern = re.sub(r"\s+", " ", label_contains.replace("*", " ")).strip()
    try:
        labelled = page.get_by_label(re.compile(re.escape(label_pattern), re.IGNORECASE)).first
        if await labelled.count() > 0:
            tag_and_type = await labelled.evaluate(
                """
                (element) => ({
                  tag: (element.tagName || '').toLowerCase(),
                  type: (element.getAttribute('type') || '').toLowerCase(),
                  value: element.value || '',
                  isCustomTrigger:
                    (element.getAttribute('role') || '').toLowerCase() === 'combobox'
                    || element.hasAttribute('aria-autocomplete')
                    || element.getAttribute('aria-haspopup') === 'listbox'
                    || element.hasAttribute('aria-controls'),
                })
                """
            )
            if not (
                tag_and_type["tag"] == "input"
                and tag_and_type["type"] in {"file", "hidden", "submit", "button", "checkbox", "radio"}
            ) and not tag_and_type.get("isCustomTrigger"):
                if not only_empty or not normalize_label(str(tag_and_type.get("value") or "")):
                    await labelled.fill(value)
                    actions.append(action_label or f"Filled {label_contains}")
                    return True
    except Exception:
        pass
    filled = await page.evaluate(
        """
        ({ labelContains, value, onlyEmpty }) => {
          const norm = (text) => (text || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const target = norm(labelContains);
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const setValue = (element) => {
            if (!element || element.disabled || element.readOnly || !isVisible(element)) return false;
            const tag = (element.tagName || '').toLowerCase();
            const type = (element.getAttribute('type') || '').toLowerCase();
            if (tag === 'input' && ['file', 'hidden', 'submit', 'button', 'checkbox', 'radio'].includes(type)) return false;
            if (
              tag === 'input'
              && (
                element.getAttribute('role') === 'combobox'
                || element.hasAttribute('aria-autocomplete')
                || element.getAttribute('aria-haspopup') === 'listbox'
                || element.hasAttribute('aria-controls')
              )
            ) return false;
            if (onlyEmpty && norm(element.value)) return false;
            element.focus();
            element.value = value;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          const controls = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea'));
          for (const element of controls) {
            const direct = [
              element.getAttribute('aria-label'),
              element.getAttribute('placeholder'),
              element.name,
              element.id,
            ].filter(Boolean).join(' ');
            if (norm(direct).includes(target) && setValue(element)) return true;
          }
          const labels = Array.from(document.querySelectorAll('label, div, p, span')).filter((node) => {
            const text = norm(node.innerText || node.textContent);
            return text.includes(target) && text.length <= Math.max(240, target.length + 120);
          });
          for (const label of labels) {
            const explicit = label.getAttribute('for') ? document.getElementById(label.getAttribute('for')) : null;
            if (setValue(explicit)) return true;
            const scoped = label.querySelector('input:not([type="hidden"]):not([type="file"]), textarea');
            if (setValue(scoped)) return true;
            let node = label;
            for (let depth = 0; depth < 4 && node; depth += 1) {
              const nearby = node.querySelector?.('input:not([type="hidden"]):not([type="file"]), textarea');
              if (setValue(nearby)) return true;
              node = node.parentElement;
            }
          }
          return false;
        }
        """,
        {"labelContains": label_contains, "value": value, "onlyEmpty": only_empty},
    )
    if filled:
        actions.append(action_label or f"Filled {label_contains}")
    return bool(filled)


async def _select_option_by_label(page, label_contains: str, option_text: str | None, actions: list[str]) -> bool:
    if not option_text:
        return False
    selected = await page.evaluate(
        """
        ({ labelContains, optionText }) => {
          const norm = (text) => (text || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const target = norm(labelContains);
          const wanted = norm(optionText);
          const setSelect = (select) => {
            if (!select || select.disabled) return false;
            const options = Array.from(select.options || []);
            const option =
              options.find((item) => norm(item.text) === wanted)
              || options.find((item) => norm(item.text).includes(wanted));
            if (!option) return false;
            select.value = option.value;
            select.dispatchEvent(new Event('input', { bubbles: true }));
            select.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          const labels = Array.from(document.querySelectorAll('label, div, p, span')).filter((node) => {
            const text = norm(node.innerText || node.textContent);
            return text.includes(target) && text.length <= Math.max(240, target.length + 120);
          });
          for (const label of labels) {
            const explicit = label.getAttribute('for') ? document.getElementById(label.getAttribute('for')) : null;
            if (setSelect(explicit)) return true;
            let node = label;
            for (let depth = 0; depth < 4 && node; depth += 1) {
              const nearby = node.querySelector?.('select');
              if (setSelect(nearby)) return true;
              node = node.parentElement;
            }
          }
          return false;
        }
        """,
        {"labelContains": label_contains, "optionText": option_text},
    )
    if selected:
        actions.append(f"Selected {option_text} for {label_contains}")
    return bool(selected)


async def _select_best_option_by_label(
    page,
    label_contains: str,
    preferred_terms: list[str],
    actions: list[str],
    *,
    allow_first_nonempty_fallback: bool = True,
) -> bool:
    normalized_terms = _dedupe_preserve_order(preferred_terms)
    if not normalized_terms:
        return False
    option_text = await page.evaluate(
        """
        ({ labelContains, preferredTerms, allowFirstNonemptyFallback }) => {
          const norm = (text) => (text || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const target = norm(labelContains);
          const preferred = preferredTerms.map(norm).filter(Boolean);
          const isVisible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const isPlaceholder = (text) => {
            const value = norm(text);
            return !value || value === 'select' || value === 'select...' || value.startsWith('select ') || value === 'choose' || value.startsWith('choose ');
          };
          const collectSelects = (node) => {
            const found = [];
            const explicit = node.getAttribute?.('for') ? document.getElementById(node.getAttribute('for')) : null;
            if (explicit?.tagName === 'SELECT') found.push(explicit);
            let current = node;
            for (let depth = 0; depth < 4 && current; depth += 1) {
              const scoped = Array.from(current.querySelectorAll?.('select') || []);
              for (const item of scoped) found.push(item);
              current = current.parentElement;
            }
            return found.filter((select, index, items) => select && isVisible(select) && items.indexOf(select) === index);
          };
          const promptNodes = Array.from(document.querySelectorAll('label, legend, div, p, span, h1, h2, h3, h4')).filter((node) => {
            const text = norm(node.innerText || node.textContent);
            return text.includes(target) && text.length <= Math.max(280, target.length + 160);
          });
          const candidateSelects = [];
          for (const node of promptNodes) {
            for (const select of collectSelects(node)) {
              candidateSelects.push(select);
            }
          }
          const selects = candidateSelects.length
            ? candidateSelects.filter((select, index, items) => items.indexOf(select) === index)
            : Array.from(document.querySelectorAll('select')).filter((select) => isVisible(select));
          for (const select of selects) {
            const options = Array.from(select.options || [])
              .map((option) => ({ text: (option.text || '').trim(), normalized: norm(option.text) }))
              .filter((option) => option.text && !isPlaceholder(option.text));
            for (const term of preferred) {
              const exact = options.find((option) => option.normalized === term || term.includes(option.normalized));
              if (exact) return exact.text;
            }
            for (const term of preferred) {
              const partial = options.find((option) => option.normalized.includes(term) || term.includes(option.normalized));
              if (partial) return partial.text;
            }
            if (allowFirstNonemptyFallback && options.length) {
              return options[0].text;
            }
          }
          return null;
        }
        """,
        {
            "labelContains": label_contains,
            "preferredTerms": normalized_terms,
            "allowFirstNonemptyFallback": allow_first_nonempty_fallback,
        },
    )
    if not option_text:
        return False
    return await _select_option_by_label(page, label_contains, str(option_text), actions)


async def _upload_file_by_keywords(
    page,
    file_path: Path,
    keywords: list[str],
    actions: list[str],
    action_label: str,
    *,
    allow_single_fallback: bool = True,
) -> bool:
    inputs = page.locator('input[type="file"]')
    count = await inputs.count()
    if count == 0:
        return False
    matched_index = await page.evaluate(
        """
        ({ keywords, allowSingleFallback }) => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const visibleText = (node) => norm(node?.innerText || node?.textContent || '');
          const shortText = (text, maxLength = 220) => {
            const value = norm(text);
            return value.length <= maxLength ? value : '';
          };
          const labelTextFor = (input) => {
            const parts = [
              input.getAttribute('id'),
              input.getAttribute('name'),
              input.getAttribute('aria-label'),
              input.getAttribute('title'),
              input.getAttribute('data-testid'),
              input.getAttribute('accept'),
            ];
            if (input.labels) {
              parts.push(...Array.from(input.labels).map((label) => visibleText(label)));
            }
            const explicit = input.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null;
            parts.push(visibleText(explicit));
            parts.push(visibleText(input.closest('label')));
            parts.push(shortText(input.previousElementSibling?.innerText || input.previousElementSibling?.textContent, 160));
            const parentTag = (input.parentElement?.tagName || '').toLowerCase();
            if (!['form', 'body', 'html'].includes(parentTag) && input.parentElement?.querySelectorAll('input[type="file"]').length <= 1) {
              parts.push(shortText(input.parentElement?.innerText || input.parentElement?.textContent, 220));
            }
            const fieldScope = input.closest('fieldset, [role="group"], [data-testid], li, section, div');
            if (fieldScope?.querySelectorAll('input[type="file"]').length <= 1) {
              parts.push(shortText(fieldScope?.innerText || fieldScope?.textContent, 220));
            }
            return norm(parts.filter(Boolean).join(' '));
          };
          const normalizedKeywords = keywords.map(norm).filter(Boolean);
          const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
          let best = null;
          for (const [index, input] of inputs.entries()) {
            const label = labelTextFor(input);
            const score = normalizedKeywords.reduce((total, keyword) => {
              if (!keyword) return total;
              if (label === keyword) return total + 100;
              if (label.includes(keyword)) return total + 20;
              return total;
            }, 0);
            if (score > 0 && (!best || score > best.score)) {
              best = { index, score };
            }
          }
          if (best) return best.index;
          return allowSingleFallback && inputs.length === 1 ? 0 : null;
        }
        """,
        {"keywords": keywords, "allowSingleFallback": allow_single_fallback},
    )
    if matched_index is not None:
        await inputs.nth(int(matched_index)).set_input_files(str(file_path))
        actions.append(action_label)
        return True
    return False


async def _upload_cover_letter_if_available(page, plan: dict[str, Any], actions: list[str]) -> bool:
    cover_letter_pdf = optional_plan_artifact(plan, "cover_letter_pdf")
    if not cover_letter_pdf:
        return False
    return await _upload_file_by_keywords(
        page,
        cover_letter_pdf,
        ["cover letter", "letter of interest"],
        actions,
        f"Uploaded cover letter: {cover_letter_pdf}",
        allow_single_fallback=False,
    )


async def _wait_for_ashby_resume_parsing(page, actions: list[str]) -> None:
    try:
        await page.wait_for_timeout(200)
        await page.wait_for_function(
            """
            () => {
              const isVisible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              return !Array.from(document.querySelectorAll('body *')).some((node) => {
                const text = (node.innerText || node.textContent || '').toLowerCase();
                return text.includes('parsing your resume') && isVisible(node);
              });
            }
            """,
            timeout=20000,
        )
        actions.append("Waited for Ashby resume parsing")
    except Exception:
        actions.append("Ashby resume parsing wait timed out")


async def _wait_for_resume_processing(page, actions: list[str], platform_label: str = "resume") -> None:
    try:
        await page.wait_for_timeout(200)
        await page.wait_for_function(
            """
            () => {
              const isVisible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const processingTokens = [
                'analyzing resume',
                'analysing resume',
                'parsing resume',
                'parsing your resume',
                'processing resume',
                'uploading resume',
              ];
              return !Array.from(document.querySelectorAll('body *')).some((node) => {
                const text = (node.innerText || node.textContent || '').toLowerCase();
                return processingTokens.some((token) => text.includes(token)) && isVisible(node);
              });
            }
            """,
            timeout=30000,
        )
        actions.append(f"Waited for {platform_label} resume processing")
    except Exception:
        actions.append(f"{platform_label} resume processing wait timed out")


async def _click_option_button(page, prompt_contains: str, option_text: str, actions: list[str]) -> bool:
    clicked = await page.evaluate(
        """
        ({ promptContains, optionText }) => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const prompt = norm(promptContains);
          const option = norm(optionText);
          const buttons = Array.from(document.querySelectorAll('button'));
          const promptNode = Array.from(document.querySelectorAll('body *')).find((node) => norm(node.innerText).includes(prompt));
          if (!promptNode) return false;
          const promptY = promptNode.getBoundingClientRect().top;
          const candidates = buttons
            .map((button) => ({ button, rect: button.getBoundingClientRect(), text: norm(button.innerText) }))
            .filter((item) => item.text === option && item.rect.top >= promptY && item.rect.top < promptY + 260)
            .sort((a, b) => a.rect.top - b.rect.top);
          if (!candidates.length) return false;
          candidates[0].button.click();
          return true;
        }
        """,
        {"promptContains": prompt_contains, "optionText": option_text},
    )
    if clicked:
        actions.append(f"Selected {option_text} for {prompt_contains}")
    return bool(clicked)


async def _choose_grouped_option_by_label(
    page,
    prompt_contains: str,
    option_text: str | None,
    actions: list[str],
    action_label: str | None = None,
) -> bool:
    if not option_text:
        return False
    selected = await page.evaluate(
        """
        ({ promptContains, optionText }) => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const prompt = norm(promptContains);
          const option = norm(optionText);
          const isVisible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const optionMatches = (text) => {
            const value = norm(text);
            return value === option || value.includes(option);
          };
          const labelText = (input) => {
            const explicit = input.id ? document.querySelector(`label[for="${input.id}"]`) : null;
            return explicit?.innerText || input.closest('label')?.innerText || input.parentElement?.innerText || input.value || '';
          };
          const setSelect = (select) => {
            if (!select || select.disabled || !isVisible(select)) return false;
            const selectedOption = Array.from(select.options || []).find((item) => optionMatches(item.text));
            if (!selectedOption) return false;
            select.value = selectedOption.value;
            select.dispatchEvent(new Event('input', { bubbles: true }));
            select.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          const clickControl = (control) => {
            if (!control || control.disabled || !isVisible(control)) return false;
            if (control.tagName === 'INPUT') {
              const type = (control.getAttribute('type') || '').toLowerCase();
              if (['radio', 'checkbox'].includes(type) && optionMatches(labelText(control))) {
                control.click();
                return true;
              }
            }
            const role = (control.getAttribute('role') || '').toLowerCase();
            const text = control.innerText || control.textContent || control.getAttribute('aria-label') || control.value || '';
            if (['button', 'radio', 'checkbox'].includes(role) || control.tagName === 'BUTTON') {
              if (optionMatches(text)) {
                control.click();
                return true;
              }
            }
            return false;
          };
          const promptNodes = Array.from(document.querySelectorAll('legend, label, div, p, span, h1, h2, h3, h4')).filter((node) => {
            const text = norm(node.innerText || node.textContent);
            return text.includes(prompt) && text.length <= Math.max(280, prompt.length + 160);
          });
          for (const promptNode of promptNodes) {
            const scopes = [
              promptNode.closest('fieldset'),
              promptNode.closest('[role="radiogroup"]'),
              promptNode.closest('[role="group"]'),
              promptNode.parentElement,
              promptNode.parentElement?.parentElement,
            ].filter(Boolean);
            for (const scope of scopes) {
              const selects = Array.from(scope.querySelectorAll('select'));
              for (const select of selects) {
                if (setSelect(select)) return true;
              }
              const controls = Array.from(scope.querySelectorAll('input[type="radio"], input[type="checkbox"], button, [role="button"], [role="radio"], [role="checkbox"]'));
              for (const control of controls) {
                if (clickControl(control)) return true;
              }
            }
            const promptY = promptNode.getBoundingClientRect().top;
            const nearbyControls = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"], button, [role="button"], [role="radio"], [role="checkbox"]'))
              .filter((control) => {
                const rect = control.getBoundingClientRect();
                return rect.top >= promptY && rect.top < promptY + 320;
              });
            for (const control of nearbyControls) {
              if (clickControl(control)) return true;
            }
          }
          return false;
        }
        """,
        {"promptContains": prompt_contains, "optionText": option_text},
    )
    if selected:
        actions.append(action_label or f"Selected {option_text} for {prompt_contains}")
    return bool(selected)


def _option_candidates_for_answer(label: str, answer: str) -> list[str]:
    normalized_label = normalize_label(label)
    normalized_answer = normalize_label(answer)
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        cleaned = value.strip()
        if cleaned and normalize_label(cleaned) not in {normalize_label(item) for item in candidates}:
            candidates.append(cleaned)

    if normalized_label == "country" or normalized_label.endswith(" country") or normalized_label.startswith("country "):
        add("United States")
        add("United States of America")
        add("US")

    if ("hispanic" in normalized_label or "latino" in normalized_label) and "race" not in normalized_label:
        if "no" in normalized_answer or "asian" in normalized_answer:
            add("No")
            add("No, not Hispanic or Latino")

    if "sponsorship" in normalized_label or "visa" in normalized_label:
        if any(token in normalized_answer for token in ["h1b", "sponsorship", "sponsor", "require"]):
            add("Yes")

    if "relocation" in normalized_label or "relocate" in normalized_label:
        if any(token in normalized_answer for token in ["open", "moving", "relocat", "yes"]):
            add("Yes")

    if "in-person" in normalized_label or "office" in normalized_label or "25%" in normalized_label:
        if _yes_no_answer(answer) == "Yes" or "yes" in normalized_answer:
            add("Yes")

    if "ai policy" in normalized_label or "candidate ai" in normalized_label:
        if _yes_no_answer(answer) == "Yes" or "yes" in normalized_answer:
            add("Yes")

    if "interviewed" in normalized_label:
        if _yes_no_answer(answer) == "No" or "no" in normalized_answer:
            add("No")
        elif _yes_no_answer(answer) == "Yes":
            add("Yes")

    if ("veteran" in normalized_label or "military status" in normalized_label) and "not a protected veteran" in normalized_answer:
        add("I am not a protected veteran")
        add("Not a protected veteran")
        add("I am not a veteran")
        add("No")

    if ("race" in normalized_label or "ethnicity" in normalized_label) and "asian" in normalized_answer:
        add("Asian (Not Hispanic or Latino)")
        add("Asian")

    if "disability" in normalized_label and ("no" in normalized_answer or "do not have" in normalized_answer):
        add("No, I don't have a disability")
        add("No, I don't have a disability and have not had one in the past")
        add("No")

    yes_no = _yes_no_answer(answer)
    if yes_no:
        add(yes_no)
    add(answer)
    return candidates


async def _select_custom_option_by_label(
    page,
    prompt_contains: str,
    option_text: str | None,
    actions: list[str],
    action_label: str | None = None,
) -> bool:
    if not option_text:
        return False
    candidate_options = _option_candidates_for_answer(prompt_contains, option_text)
    label_pattern = re.sub(r"\s+", " ", prompt_contains.replace("*", " ")).strip()
    if label_pattern:
        try:
            controls = page.get_by_label(re.compile(re.escape(label_pattern), re.IGNORECASE))
            count = min(await controls.count(), 5)
        except Exception:
            count = 0
        for index in range(count):
            control = controls.nth(index)
            try:
                is_custom = await control.evaluate(
                    """
                    (element) => {
                      const role = (element.getAttribute('role') || '').toLowerCase();
                      return role === 'combobox'
                        || element.getAttribute('aria-haspopup') === 'listbox'
                        || element.hasAttribute('aria-controls')
                        || element.hasAttribute('aria-expanded')
                        || element.hasAttribute('aria-autocomplete');
                    }
                    """
                )
            except Exception:
                continue
            if not is_custom:
                continue
            for candidate_option in candidate_options:
                try:
                    await control.click(timeout=1500)
                    try:
                        await control.fill(candidate_option, timeout=1500)
                    except Exception:
                        await page.keyboard.type(candidate_option, delay=10)
                    await page.wait_for_timeout(250)
                    option = page.get_by_role("option", name=re.compile(re.escape(candidate_option), re.IGNORECASE))
                    if await option.count() > 0:
                        await option.first.click(timeout=2000)
                    else:
                        await page.keyboard.press("Enter")
                    await page.wait_for_timeout(250)
                    if await _custom_control_has_value(page, prompt_contains):
                        actions.append(action_label or f"Selected {candidate_option} for {prompt_contains}")
                        return True
                except Exception:
                    continue
    for candidate_option in candidate_options:
        opened = await page.evaluate(
            """
            ({ promptContains }) => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const prompt = norm(promptContains);
          const isVisible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const setInputValue = (element) => {
            const tag = (element?.tagName || '').toLowerCase();
            if (!['input', 'textarea'].includes(tag)) return;
            element.focus();
            element.value = '';
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
          };
          const isCustomTrigger = (element) => {
            if (!element) return false;
            const role = (element.getAttribute('role') || '').toLowerCase();
            return role === 'combobox'
              || element.getAttribute('aria-haspopup') === 'listbox'
              || element.hasAttribute('aria-controls')
              || element.hasAttribute('aria-expanded')
              || element.hasAttribute('aria-autocomplete');
          };
          const openTrigger = (trigger) => {
            if (!trigger || !isVisible(trigger)) return false;
            if (!isCustomTrigger(trigger)) return false;
            trigger.focus?.();
            trigger.click();
            setInputValue(trigger);
            return true;
          };
          const triggerSelectors = [
            '[role="combobox"]',
            '[aria-haspopup="listbox"]',
            '[aria-controls]',
            '[aria-expanded]',
            'input[aria-autocomplete]',
          ];
          const accessibleText = (node) => {
            const labelledBy = node.getAttribute('aria-labelledby');
            const labelledText = labelledBy ? labelledBy.split(/\\s+/).map((id) => document.getElementById(id)?.innerText || document.getElementById(id)?.textContent || '').join(' ') : '';
            return [
              labelledText,
              node.getAttribute('aria-label'),
              node.getAttribute('placeholder'),
              node.getAttribute('name'),
              node.getAttribute('id'),
              node.innerText,
              node.textContent,
            ].filter(Boolean).join(' ');
          };
          const directTriggers = Array.from(document.querySelectorAll(triggerSelectors.join(','))).filter((node) => {
            return norm(accessibleText(node)).includes(prompt);
          });
          for (const trigger of directTriggers) {
            if (openTrigger(trigger)) return true;
          }

          const promptNodes = Array.from(document.querySelectorAll('label, legend, div, p, span, h1, h2, h3, h4')).filter((node) => {
            const text = norm(node.innerText || node.textContent);
            return text.includes(prompt) && text.length <= Math.max(280, prompt.length + 160);
          });
          for (const promptNode of promptNodes) {
            const explicit = promptNode.getAttribute('for') ? document.getElementById(promptNode.getAttribute('for')) : null;
            if (openTrigger(explicit)) return true;
            const scopes = [
              promptNode.closest('fieldset'),
              promptNode.closest('[role="group"]'),
              promptNode.parentElement,
              promptNode.parentElement?.parentElement,
            ].filter(Boolean);
            for (const scope of scopes) {
              const trigger = scope.querySelector(triggerSelectors.join(','));
              if (openTrigger(trigger)) return true;
            }
          }
          return false;
        }
        """,
            {"promptContains": prompt_contains},
        )
        if not opened:
            continue

        try:
            await page.wait_for_timeout(200)
            active_is_custom_trigger = await page.evaluate(
                """
                () => {
                  const element = document.activeElement;
                  if (!element) return false;
                  const role = (element.getAttribute('role') || '').toLowerCase();
                  return role === 'combobox'
                    || element.getAttribute('aria-haspopup') === 'listbox'
                    || element.hasAttribute('aria-controls')
                    || element.hasAttribute('aria-expanded')
                    || element.hasAttribute('aria-autocomplete');
                }
                """
            )
        except Exception:
            continue
        if not active_is_custom_trigger:
            continue
        try:
            await page.keyboard.press("Control+A")
            await page.keyboard.type(candidate_option, delay=10)
        except Exception:
            pass
        try:
            await page.wait_for_timeout(300)
            selected = await page.evaluate(
                """
            ({ candidateOptions }) => {
              const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
              const wantedOptions = candidateOptions.map(norm).filter(Boolean);
              const isVisible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const optionMatches = (text) => {
                const value = norm(text);
                return wantedOptions.some((wanted) => value === wanted || value.includes(wanted));
              };
              const active = document.activeElement;
              const controlledListId = active?.getAttribute?.('aria-controls');
              const controlledList = controlledListId ? document.getElementById(controlledListId) : null;
              const candidateRoots = [
                controlledList,
                active?.closest?.('.select-shell')?.querySelector?.('[role="listbox"]'),
                active?.closest?.('.select__container')?.querySelector?.('[role="listbox"]'),
                document.querySelector('[role="listbox"]'),
                document,
              ].filter(Boolean);
              const seen = new Set();
              const candidates = candidateRoots.flatMap((root) => Array.from(root.querySelectorAll?.('[role="option"], [role="listbox"] *, [data-value], li, button') || []))
                .filter((node) => isVisible(node))
                .filter((node) => {
                  if (seen.has(node)) return false;
                  seen.add(node);
                  return true;
                })
                .filter((node) => optionMatches(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('data-value') || ''));
              if (!candidates.length) return false;
              candidates[0].click();
              return true;
            }
            """,
                {"candidateOptions": candidate_options},
            )
        except Exception:
            continue
        if not selected:
            try:
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(250)
                selected = await _custom_control_has_value(page, prompt_contains)
            except Exception:
                selected = False
        if selected:
            actions.append(action_label or f"Selected {candidate_option} for {prompt_contains}")
            return True
    return False


async def _custom_control_has_value(page, prompt_contains: str) -> bool:
    return bool(
        await page.evaluate(
            """
            ({ promptContains }) => {
              const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
              const prompt = norm(promptContains);
              const isVisible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const isCustomTrigger = (element) => {
                if (!element) return false;
                const role = (element.getAttribute('role') || '').toLowerCase();
                return role === 'combobox'
                  || element.getAttribute('aria-haspopup') === 'listbox'
                  || element.hasAttribute('aria-controls')
                  || element.hasAttribute('aria-expanded')
                  || element.hasAttribute('aria-autocomplete');
              };
              const hasValue = (element) => {
                if (!element || !isCustomTrigger(element)) return false;
                const scope = element.closest('.select-shell') || element.closest('.select__container') || element.closest('.select') || element.closest('[role="group"]') || element.parentElement?.parentElement;
                const selectedValue = scope?.querySelector('.select__single-value, [class*="singleValue"]');
                if (selectedValue && isVisible(selectedValue) && norm(selectedValue.innerText || selectedValue.textContent)) return true;
                const selectedContainer = scope?.querySelector('.select__value-container--has-value, [class*="value-container--has-value"]');
                if (selectedContainer && isVisible(selectedContainer)) {
                  const selectedText = norm(selectedContainer.innerText || selectedContainer.textContent);
                  if (selectedText && !['select', 'select...', 'choose', 'choose...', 'select one'].includes(selectedText)) return true;
                }
                const tag = (element.tagName || '').toLowerCase();
                if ((tag === 'input' || tag === 'textarea') && norm(element.value)) {
                  const looksLikeReactSelect = Boolean(scope?.querySelector('.select__input, [class*="input-container"]'));
                  if (!looksLikeReactSelect) return true;
                }
                if (!['input', 'textarea'].includes(tag)) {
                  const elementText = norm(element.innerText || element.textContent);
                  if (elementText && !['select', 'select...', 'choose', 'choose...', 'select one'].includes(elementText)) return true;
                }
                const valueContainer = scope?.querySelector('.select__value-container--has-value, .select__single-value, [class*="singleValue"]');
                return Boolean(valueContainer && isVisible(valueContainer) && norm(valueContainer.innerText || valueContainer.textContent));
              };
              const triggerSelectors = '[role="combobox"], [aria-haspopup="listbox"], [aria-controls], [aria-expanded], input[aria-autocomplete]';
              const accessibleText = (node) => {
                const labelledBy = node.getAttribute('aria-labelledby');
                const labelledText = labelledBy ? labelledBy.split(/\\s+/).map((id) => document.getElementById(id)?.innerText || document.getElementById(id)?.textContent || '').join(' ') : '';
                return [
                  labelledText,
                  node.getAttribute('aria-label'),
                  node.getAttribute('placeholder'),
                  node.getAttribute('name'),
                  node.getAttribute('id'),
                  node.innerText,
                  node.textContent,
                ].filter(Boolean).join(' ');
              };
              const directTriggers = Array.from(document.querySelectorAll(triggerSelectors)).filter((node) => {
                return norm(accessibleText(node)).includes(prompt);
              });
              if (directTriggers.some(hasValue)) return true;
              const promptNodes = Array.from(document.querySelectorAll('label, legend, div, p, span, h1, h2, h3, h4')).filter((node) => {
                const text = norm(node.innerText || node.textContent);
                return text.includes(prompt) && text.length <= Math.max(280, prompt.length + 160);
              });
              for (const promptNode of promptNodes) {
                const explicit = promptNode.getAttribute('for') ? document.getElementById(promptNode.getAttribute('for')) : null;
                if (hasValue(explicit)) return true;
                const scopes = [
                  promptNode.closest('fieldset'),
                  promptNode.closest('[role="group"]'),
                  promptNode.parentElement,
                  promptNode.parentElement?.parentElement,
                ].filter(Boolean);
                for (const scope of scopes) {
                  const trigger = scope.querySelector?.(triggerSelectors);
                  if (hasValue(trigger)) return true;
                }
              }
              return false;
            }
            """,
            {"promptContains": prompt_contains},
        )
    )


async def _set_radio_by_label(page, label_text: str, actions: list[str]) -> bool:
    selected = await page.evaluate(
        """
        ({ labelText }) => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const target = norm(labelText);
          const inputs = Array.from(document.querySelectorAll('input[type="radio"]'));
          for (const input of inputs) {
            const label = document.querySelector(`label[for="${input.id}"]`);
            const nearby = label?.innerText || input.parentElement?.innerText || '';
            const nearbyNorm = norm(nearby);
            if (nearbyNorm && (nearbyNorm.includes(target) || target.includes(nearbyNorm))) {
              input.click();
              return true;
            }
          }
          return false;
        }
        """,
        {"labelText": label_text},
    )
    if selected:
        actions.append(f"Selected radio option: {label_text}")
    return bool(selected)


async def _check_checkbox_by_label(page, label_contains: str, actions: list[str]) -> bool:
    checked = await page.evaluate(
        """
        ({ labelContains }) => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const target = norm(labelContains);
          const inputs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
          for (const input of inputs) {
            const label = document.querySelector(`label[for="${input.id}"]`);
            const text = label?.innerText || input.name || input.parentElement?.innerText || '';
            if (norm(text).includes(target)) {
              if (!input.checked) input.click();
              return true;
            }
          }
          return false;
        }
        """,
        {"labelContains": label_contains},
    )
    if checked:
        actions.append(f"Checked: {label_contains}")
    return bool(checked)


async def _fill_demographic_fields(page, plan: dict[str, Any], actions: list[str]) -> None:
    for field in ["Gender", "Race", "Veteran Status", "Disability Status"]:
        value = plan_value(plan, field)
        if value:
            if not await _set_radio_by_label(page, value, actions):
                await _select_option_by_label(page, field, value, actions)


async def _fill_common_yes_no_fields(page, actions: list[str]) -> None:
    await _click_option_button(page, "authorized to work", "Yes", actions)
    await _click_option_button(page, "legally authorized", "Yes", actions)
    await _click_option_button(page, "require sponsorship", "Yes", actions)
    await _click_option_button(page, "visa sponsorship", "Yes", actions)
    await _click_option_button(page, "three days per week", "Yes", actions)


LEGAL_ACKNOWLEDGEMENT_TOKENS = [
    "arbitration",
    "certify",
    "certification",
    "i agree",
    "i confirm",
    "terms",
    "privacy",
    "consent",
    "background check",
    "signature",
    "captcha",
    "recaptcha",
]

ADDITIONAL_INFORMATION_LABELS = [
    "additional information",
    "additional info",
    "anything else",
    "anything else you would like us to know",
    "anything you would like us to know",
    "comments",
    "other information",
    "why are you interested",
    "why do you want",
    "why this company",
    "why this role",
    "why join",
    "what excites you",
    "what interests you",
    "motivation",
    "tell us why",
    "tell us about your interest",
]

DIRECT_ADDITIONAL_INFORMATION_LABELS = [
    "additional information",
    "additional info",
    "anything else",
    "anything else you would like us to know",
    "anything you would like us to know",
    "comments",
    "other information",
]

COVER_LETTER_LABELS = [
    "cover letter",
    "letter of interest",
]

GENERIC_SKIP_LABEL_TOKENS = [
    "resume",
    "upload your resume",
    "legal name",
    "preferred name",
    "first name",
    "last name",
    "full name",
    "email",
    "phone",
    "mobile",
    "linkedin",
    "current location",
    "currently located",
    "start date",
    "pick date",
    "start typing",
    "additional information",
    "cover letter",
]


def _is_legal_acknowledgement(label: str) -> bool:
    normalized = normalize_label(label)
    return any(token in normalized for token in LEGAL_ACKNOWLEDGEMENT_TOKENS)


def _is_generic_duplicate(label: str) -> bool:
    normalized = normalize_label(label)
    return any(token in normalized for token in GENERIC_SKIP_LABEL_TOKENS)


DEMOGRAPHIC_OPTION_LABELS = [
    "male",
    "female",
    "decline to self-identify",
    "hispanic or latino",
    "white (not hispanic or latino)",
    "black or african american (not hispanic or latino)",
    "native hawaiian or other pacific islander (not hispanic or latino)",
    "asian (not hispanic or latino)",
    "american indian or alaska native (not hispanic or latino)",
    "two or more races (not hispanic or latino)",
    "i identify as one or more of the classifications of protected veteran listed above",
    "i am not a protected veteran",
    "i decline to self-identify for protected veteran status",
    "yes, i have a disability, or have had one in the past",
    "no, i don't have a disability and have not had one in the past",
    "no, i don’t have a disability and have not had one in the past",
    "i do not want to answer",
]


def _is_demographic_option_label(label: str) -> bool:
    normalized = normalize_label(label)
    return any(normalized == option or normalized.startswith(f"{option} ") for option in DEMOGRAPHIC_OPTION_LABELS)


def _draft_answer(requirement: dict[str, Any]) -> str | None:
    answer = requirement.get("draft_answer")
    if answer is None:
        return None
    text = str(answer).strip()
    if not text or normalize_label(text) in {"missing", "n/a", "none"}:
        return None
    return text


ANSWER_BANK_ALIASES = [
    ("legal_name", ["legal name", "full name", "first and last name", "your name"]),
    ("preferred_name", ["preferred name", "preferred first name"]),
    ("email", ["email", "email address"]),
    ("phone", ["phone", "phone number", "mobile", "mobile number"]),
    ("linkedin", ["linkedin", "linkedin profile", "linkedin url"]),
    ("github", ["github", "github profile", "github url"]),
    ("portfolio", ["portfolio", "personal website", "website"]),
    ("country", ["country", "phone country", "country code"]),
    ("current_company", ["current company", "current employer", "employer"]),
    ("relocation", ["relocation", "relocate", "open to moving", "willing to move"]),
    ("current_location", ["current location", "where are you currently located", "location (city)", "city"]),
    ("start_date", ["start date", "availability", "when can you start", "earliest", "start working"]),
    ("timeline_considerations", ["deadline", "timeline consideration", "timeline considerations"]),
    ("work_authorization", ["authorized to work", "legally authorized", "work authorization"]),
    ("export_control", ["export control", "citizen or legal permanent resident", "countries of which you are a citizen", "obtained such status"]),
    ("sponsorship", ["require sponsorship", "visa sponsorship", "sponsorship"]),
    ("office_in_person_25", ["in-person", "one of our offices", "25% of the time"]),
    ("sf_hybrid", ["san francisco office", "sf office", "three days per week", "3 days per week"]),
    ("seattle_hybrid", ["seattle hybrid", "seattle office"]),
    ("ai_policy", ["ai policy", "candidate ai", "ai partnership guidelines"]),
    ("previously_interviewed", ["have you ever interviewed", "interviewed at"]),
    ("salary_expectations", ["salary expectations", "compensation expectations", "desired salary", "expected compensation"]),
    ("why_looking", ["why are you looking", "why are you interested", "why do you want", "motivation"]),
    ("application_source", ["how did you hear", "hear about us", "where did you hear", "how did you find", "application source", "referral source", "source type"]),
    ("distributed_teams_experience", ["remote and geo-dispersed global teams", "geo-dispersed global teams", "distributed global teams", "remote global teams", "remote teams"]),
    ("event_attendance", ["which event did you attend", "what event did you attend", "event attended", "attended an on-campus or virtual event"]),
    ("additional_information", ADDITIONAL_INFORMATION_LABELS),
    ("cover_letter", COVER_LETTER_LABELS),
    ("gender", ["gender"]),
    ("race", ["race", "ethnicity"]),
    ("veteran_status", ["veteran", "protected veteran", "military status"]),
    ("disability_status", ["disability"]),
]

AUDITED_FIELD_ANSWER_HINTS = [
    ("portfolio", ["best work", "work sample", "work samples", "sample work", "project url", "project link"]),
    ("github", ["code sample", "code samples", "source code"]),
    ("relocation", ["move for this role", "moving for this role"]),
    ("current_company", ["current employer", "current company"]),
    ("current_location", ["location preference", "preferred location", "preferred office", "office preference", "office location"]),
    ("country", ["phone country", "country code"]),
    ("office_in_person_25", ["work in-person", "work in person", "office 25"]),
    ("ai_policy", ["application ai policy", "ai usage policy"]),
    ("export_control", ["export control", "permanent resident status", "citizen or legal permanent resident"]),
    ("previously_interviewed", ["previously interviewed", "interview history"]),
    ("application_source", ["heard about this job", "learn about this role", "found this role", "found this job"]),
    ("distributed_teams_experience", ["remote and geo-dispersed global teams", "geo-dispersed global teams", "distributed teams", "remote global teams"]),
    ("event_attendance", ["which event did you attend", "virtual event", "on-campus event", "campus event"]),
    ("additional_information", ["anything not covered", "more context", "tell us more"]),
]


FILL_FIELD_ANSWER_KEYS = {
    "Legal Name": "legal_name",
    "Preferred Name": "preferred_name",
    "Email": "email",
    "Phone Number": "phone",
    "LinkedIn": "linkedin",
    "Current Company": "current_company",
    "Country": "country",
    "Current Location": "current_location",
    "Start Date / Availability": "start_date",
    "Timeline Considerations": "timeline_considerations",
    "Work Authorization": "work_authorization",
    "Export Control": "export_control",
    "Sponsorship Required": "sponsorship",
    "Office In Person 25 Percent": "office_in_person_25",
    "AI Policy": "ai_policy",
    "Previously Interviewed": "previously_interviewed",
    "US Office Three Days Per Week": "sf_hybrid",
    "Application Source": "application_source",
    "Additional Information": "additional_information",
    "Cover Letter": "cover_letter",
    "Gender": "gender",
    "Hispanic/Latino": "hispanic_latino",
    "Race": "race",
    "Veteran Status": "veteran_status",
    "Disability Status": "disability_status",
}


def _answer_bank_from_plan(plan: dict[str, Any]) -> dict[str, str]:
    answers = {str(key): str(value).strip() for key, value in (plan.get("answer_bank") or {}).items() if str(value).strip()}
    for field, key in FILL_FIELD_ANSWER_KEYS.items():
        if key not in answers:
            value = plan_value(plan, field)
            if value:
                answers[key] = value
    return answers


def _yes_no_answer(value: str) -> str | None:
    normalized = normalize_label(value).rstrip(".")
    if normalized.startswith("yes"):
        return "Yes"
    if normalized.startswith("no"):
        return "No"
    if any(token in normalized for token in ["h1b", "require sponsorship", "requires sponsorship", "visa sponsorship"]):
        return "Yes"
    if any(token in normalized for token in ["open to moving", "open to relocate", "open to relocation"]):
        return "Yes"
    if normalized in {"true", "checked"}:
        return "Yes"
    if normalized == "false":
        return "No"
    return None


async def _fill_detected_requirement(
    page,
    requirement: dict[str, Any],
    actions: list[str],
    fill_legal_acknowledgements: bool,
) -> tuple[bool, str]:
    label = str(requirement.get("field_label") or "").strip()
    field_type = normalize_label(str(requirement.get("field_type") or "text"))
    answer = _draft_answer(requirement)
    if not label:
        return False, "missing field label"
    if _is_legal_acknowledgement(label) and not fill_legal_acknowledgements:
        return False, "legal acknowledgement left for explicit review"
    if _is_demographic_option_label(label):
        return False, "handled by demographic group answer"
    if not answer:
        answer_key = _answer_key_for_label(label, fill_legal_acknowledgements=fill_legal_acknowledgements)
        if requirement.get("required") is not True and answer_key in OPTIONAL_NO_VALUE_ANSWER_KEYS:
            return False, OPTIONAL_NO_VALUE_REASON
        return False, "missing draft answer"
    if requirement.get("missing_input") is True:
        return False, "draft answer marked as missing"
    if "file" in field_type or "resume" in normalize_label(label):
        return False, "file upload handled by ATS adapter"
    if _is_generic_duplicate(label):
        return False, "handled by ATS adapter"

    if field_type in {"radio", "button"}:
        for option in _option_candidates_for_answer(label, answer):
            if await _choose_grouped_option_by_label(page, label, option, actions):
                return True, "filled detected grouped option field"
            if await _select_custom_option_by_label(page, label, option, actions):
                return True, "filled detected custom option field"
            if await _click_option_button(page, label, option, actions):
                return True, "filled detected option field"
        if await _set_radio_by_label(page, answer, actions):
            return True, "filled detected radio field"
    if field_type in {"select", "dropdown"}:
        for option in _option_candidates_for_answer(label, answer):
            if await _choose_grouped_option_by_label(page, label, option, actions):
                return True, "filled detected grouped select field"
            if await _select_custom_option_by_label(page, label, option, actions):
                return True, "filled detected custom select field"
            if await _select_option_by_label(page, label, option, actions):
                return True, "filled detected select field"
    if field_type in {"checkbox"}:
        if normalize_label(answer).startswith(("yes", "true", "checked")):
            if await _choose_grouped_option_by_label(page, label, "Yes", actions):
                return True, "checked detected grouped checkbox"
            if await _check_checkbox_by_label(page, label, actions):
                return True, "checked detected checkbox"
        return False, "checkbox answer was not affirmative"
    if await _fill_field_by_label(page, label, answer, actions, f"Filled detected field: {label}"):
        return True, "filled detected text field"
    return False, "matching field was not found on page"


async def _fill_detected_requirements(
    page,
    plan: dict[str, Any],
    actions: list[str],
    remaining: list[str],
    fill_legal_acknowledgements: bool,
) -> list[dict[str, str]]:
    report: list[dict[str, str]] = []
    for requirement in plan.get("detected_requirements", []):
        if not isinstance(requirement, dict):
            continue
        field = str(requirement.get("field_label") or "Unknown field")
        filled, reason = await _fill_detected_requirement(page, requirement, actions, fill_legal_acknowledgements)
        status = "filled" if filled else "skipped"
        report.append({"field": field, "status": status, "reason": reason})
        if (
            not filled
            and not _is_generic_duplicate(field)
            and not str(reason).startswith("file upload")
            and reason not in SUBMIT_ACCEPTED_SKIP_REASONS
            and reason != OPTIONAL_NO_VALUE_REASON
        ):
            remaining.append(f"Detected field not filled automatically: {field} ({reason}).")
    return report


async def _fill_answer_bank_fields(
    page,
    plan: dict[str, Any],
    actions: list[str],
    fill_legal_acknowledgements: bool,
) -> list[dict[str, str]]:
    answers = _answer_bank_from_plan(plan)
    report: list[dict[str, str]] = []
    attempted_labels: set[str] = set()
    for answer_key, aliases in ANSWER_BANK_ALIASES:
        answer = answers.get(answer_key)
        if not answer:
            continue
        for label in aliases:
            if label in attempted_labels:
                continue
            attempted_labels.add(label)
            if _is_legal_acknowledgement(label) and not fill_legal_acknowledgements:
                continue

            filled = False
            yes_no = _yes_no_answer(answer)
            for option in _option_candidates_for_answer(label, answer):
                if filled:
                    break
                filled = await _choose_grouped_option_by_label(page, label, option, actions)
            if not filled and not yes_no:
                filled = await _fill_field_by_label(
                    page,
                    label,
                    answer,
                    actions,
                    f"Filled answer-bank field: {label}",
                    only_empty=True,
                )
            for option in _option_candidates_for_answer(label, answer):
                if filled:
                    break
                filled = await _choose_grouped_option_by_label(page, label, option, actions)
                if not filled:
                    filled = await _select_custom_option_by_label(page, label, option, actions)
                if not filled:
                    filled = await _click_option_button(page, label, option, actions)
                if not filled:
                    filled = await _select_option_by_label(page, label, option, actions)
            if not filled and yes_no:
                filled = await _fill_field_by_label(
                    page,
                    label,
                    answer,
                    actions,
                    f"Filled answer-bank field: {label}",
                    only_empty=True,
                )
            if filled:
                report.append({"field": label, "status": "filled", "reason": f"filled from answer_bank.{answer_key}"})
                break
    return report


def _answer_key_for_label(label: str, *, fill_legal_acknowledgements: bool) -> str | None:
    normalized = normalize_label(label)
    if _is_legal_acknowledgement(normalized) and not fill_legal_acknowledgements:
        return None
    if normalized == "country":
        return "country"
    if "race" in normalized or "ethnicity" in normalized:
        return "race"
    if ("hispanic" in normalized or "latino" in normalized) and "race" not in normalized:
        return "hispanic_latino"
    for answer_key, aliases in ANSWER_BANK_ALIASES:
        if any(alias in normalized or normalized in alias for alias in aliases):
            return answer_key
    for answer_key, hints in AUDITED_FIELD_ANSWER_HINTS:
        if any(hint in normalized for hint in hints):
            return answer_key
    return None


def _label_matches_answer_alias(label: str, answer_key: str) -> bool:
    normalized = normalize_label(label)
    for key, aliases in ANSWER_BANK_ALIASES:
        if key == answer_key:
            return any(alias in normalized or normalized in alias for alias in aliases)
    return False


def _review_answer_gap_recommendations(
    plan: dict[str, Any],
    review: dict[str, Any],
    *,
    fill_legal_acknowledgements: bool,
) -> list[dict[str, Any]]:
    answers = _answer_bank_from_plan(plan)
    recommendations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, field: str, recommendation: str, *, answer_key: str | None = None, reason: str | None = None) -> None:
        normalized_field = normalize_label(field or "Unknown field")
        key = (source, normalized_field)
        if key in seen:
            return
        seen.add(key)
        recommendations.append(
            {
                "source": source,
                "field": field or "Unknown field",
                "suggested_answer_key": answer_key,
                "reason": reason,
                "recommendation": recommendation,
            }
        )

    upload_status = str((review.get("file_upload_report") or {}).get("status") or "")
    if upload_status in {"missing_input", "not_uploaded"}:
        add(
            "resume_upload",
            "Resume",
            "Improve the platform upload selector or retry with an authenticated browser session if the file input is hidden behind an account step.",
            reason=upload_status,
        )

    filled_answer_keys = {
        str(item.get("reason") or "").rsplit(".", 1)[-1]
        for item in review.get("field_fill_report") or []
        if item.get("status") == "filled" and "answer_bank." in str(item.get("reason") or "")
    }
    unfilled_labels = _unfilled_field_labels(review.get("unfilled_fields") or [])

    for field in review.get("unfilled_fields") or []:
        label = str(field.get("label") or "Unknown field")
        answer_key = _answer_key_for_label(label, fill_legal_acknowledgements=fill_legal_acknowledgements)
        if answer_key and not answers.get(answer_key):
            add(
                "unfilled_field",
                label,
                f"Add a reusable `{answer_key}` answer to profile preferences or profile/answer_bank.md so future forms can fill it automatically.",
                answer_key=answer_key,
                reason="mapped answer key has no configured answer",
            )
        elif answer_key:
            add(
                "unfilled_field",
                label,
                "A reusable answer already exists, so improve the label alias or selector handling for this platform field.",
                answer_key=answer_key,
                reason="answer exists but field stayed empty",
            )
        else:
            add(
                "unfilled_field",
                label,
                "Teach the answer bank a new reusable answer or add a field-label alias before expecting self-serve completion.",
                reason="no answer-bank alias matched this label",
            )

    for item in review.get("field_fill_report") or []:
        if item.get("status") != "skipped":
            continue
        reason = str(item.get("reason") or "")
        if reason in SUBMIT_ACCEPTED_SKIP_REASONS:
            continue
        field = str(item.get("field") or "Unknown field")
        if normalize_label(field) not in unfilled_labels:
            continue
        answer_key = _answer_key_for_label(field, fill_legal_acknowledgements=fill_legal_acknowledgements)
        if answer_key in filled_answer_keys:
            continue
        if answer_key and answers.get(answer_key):
            recommendation = "A reusable answer exists, so improve the runner selector or option matching for this detected field."
        elif answer_key:
            recommendation = f"Add a reusable `{answer_key}` answer so this detected field can be filled automatically."
        else:
            recommendation = "Add a reusable answer-bank alias or improve inspection so this field maps to a known answer."
        add("skipped_detected_field", field, recommendation, answer_key=answer_key, reason=reason)

    return recommendations


def _apply_optional_no_value_policy(
    plan: dict[str, Any],
    unfilled_fields: list[dict[str, Any]],
    *,
    fill_legal_acknowledgements: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    answers = _answer_bank_from_plan(plan)
    kept: list[dict[str, Any]] = []
    policy_fields: list[dict[str, Any]] = []
    for field in unfilled_fields:
        label = str(field.get("label") or "").strip()
        answer_key = _answer_key_for_label(label, fill_legal_acknowledgements=fill_legal_acknowledgements)
        if field.get("required") is not True and answer_key in OPTIONAL_NO_VALUE_ANSWER_KEYS and not answers.get(answer_key):
            policy_fields.append(
                {
                    "label": label or "Unknown field",
                    "type": field.get("type") or "unknown",
                    "required": False,
                    "answer_key": answer_key,
                    "reason": "No configured truthful value; optional field left blank for final review.",
                }
            )
            continue
        kept.append(field)
    return kept, policy_fields


async def _fill_unfilled_audited_fields(
    page,
    plan: dict[str, Any],
    actions: list[str],
    fill_legal_acknowledgements: bool,
    skip_direct_answer_keys: set[str] | None = None,
) -> list[dict[str, str]]:
    answers = _answer_bank_from_plan(plan)
    report: list[dict[str, str]] = []
    audited_fields = await _audit_unfilled_fields(page)
    audited_fields.extend(await _audit_unchecked_grouped_fields(page))
    audited_fields.extend(await _audit_unselected_custom_controls(page))
    seen_labels: set[str] = set()
    for field in audited_fields:
        label = str(field.get("label") or "").strip()
        normalized_label = normalize_label(label)
        if normalized_label in seen_labels:
            continue
        seen_labels.add(normalized_label)
        field_type = normalize_label(str(field.get("type") or "text"))
        answer_key = _answer_key_for_label(label, fill_legal_acknowledgements=fill_legal_acknowledgements)
        if not label or not answer_key:
            continue
        answer = answers.get(answer_key)
        if not answer:
            continue

        filled = False
        yes_no = _yes_no_answer(answer)
        if field_type in {"radio", "checkbox"}:
            for option in _option_candidates_for_answer(label, answer):
                if filled:
                    break
                filled = await _choose_grouped_option_by_label(page, label, option, actions)
            if not filled and field_type == "checkbox" and yes_no == "Yes":
                filled = await _check_checkbox_by_label(page, label, actions)
        elif field_type in {"select", "custom_select"}:
            for option in _option_candidates_for_answer(label, answer):
                if filled:
                    break
                filled = await _select_custom_option_by_label(page, label, option, actions)
                if not filled:
                    filled = await _select_option_by_label(page, label, option, actions)
        else:
            filled = await _fill_field_by_label(
                page,
                label,
                answer,
                actions,
                f"Filled audited field: {label}",
                only_empty=True,
            )

        if filled:
            report.append({"field": label, "status": "filled", "reason": f"filled from final_audit.answer_bank.{answer_key}"})
    return report


async def _fill_text_field_from_labels(
    page,
    labels: list[str],
    value: str | None,
    actions: list[str],
    action_prefix: str,
) -> bool:
    if not value:
        return False
    for label in labels:
        if await _fill_field_by_label(
            page,
            label,
            value,
            actions,
            f"{action_prefix}: {label}",
            only_empty=True,
        ):
            return True
    return False


async def _run_generic_fill_passes(
    page,
    plan: dict[str, Any],
    resume_pdf: Path,
    actions: list[str],
    remaining: list[str],
    fill_legal_acknowledgements: bool,
    legal_checkbox_labels: list[str],
    *,
    max_steps: int = 4,
) -> list[dict[str, str]]:
    report: list[dict[str, str]] = []
    resume_missing_recorded = False
    for _ in range(max_steps):
        report.extend(await _fill_detected_requirements(page, plan, actions, remaining, fill_legal_acknowledgements))
        answer_bank_report = await _fill_answer_bank_fields(page, plan, actions, fill_legal_acknowledgements)
        report.extend(answer_bank_report)
        filled_answer_keys = {
            str(item.get("reason", "")).rsplit(".", 1)[-1]
            for item in answer_bank_report
            if str(item.get("reason", "")).startswith("filled from answer_bank.")
        }

        if fill_legal_acknowledgements:
            for label in legal_checkbox_labels:
                await _check_checkbox_by_label(page, label, actions)

        report.extend(
            await _fill_unfilled_audited_fields(
                page,
                plan,
                actions,
                fill_legal_acknowledgements,
                skip_direct_answer_keys=filled_answer_keys,
            )
        )

        if not any(action.startswith("Uploaded resume:") for action in actions) and await _upload_file_by_keywords(
            page,
            resume_pdf,
            ["resume", "cv"],
            actions,
            f"Uploaded resume: {resume_pdf}",
        ):
            resume_missing_recorded = True
            manual_upload_message = "Resume input was not found; upload manually."
            while manual_upload_message in remaining:
                remaining.remove(manual_upload_message)
            await _upload_cover_letter_if_available(page, plan, actions)

        if not await _click_next_step_button(page, actions):
            break

    if not resume_missing_recorded:
        upload_report = await _verify_resume_upload(page, resume_pdf, actions)
        if upload_report.get("status") in {"missing_input", "not_uploaded"}:
            message = "Resume input was not found; upload manually."
            if message not in remaining:
                remaining.append(message)
    return report


async def _refill_core_identity_fields(page, plan: dict[str, Any], actions: list[str], *, action_suffix: str = "") -> None:
    suffix = f" {action_suffix}" if action_suffix else ""
    await _fill_first_present(
        page,
        ['input[name="name"]', 'input[autocomplete="name"]'],
        plan_value(plan, "Legal Name"),
        f"Re-filled legal name{suffix}",
        actions,
    )
    await _fill_first_present(
        page,
        ['input[name="email"]', 'input[type="email"]'],
        plan_value(plan, "Email"),
        f"Re-filled email{suffix}",
        actions,
    )
    await _fill_first_present(
        page,
        ['input[name="phone"]', 'input[type="tel"]'],
        plan_value(plan, "Phone Number"),
        f"Re-filled phone{suffix}",
        actions,
    )
    linkedin = plan_value(plan, "LinkedIn")
    if linkedin and not await _fill_first_present(
        page,
        ['input[name="urls[LinkedIn]"]', 'input[name*="linkedin" i]', 'input[id*="linkedin" i]'],
        linkedin,
        f"Re-filled LinkedIn{suffix}",
        actions,
    ):
        await _fill_field_by_label(page, "LinkedIn", linkedin, actions, f"Re-filled LinkedIn{suffix}")
    current_company = plan_value(plan, "Current Company")
    if current_company:
        await _fill_field_by_label(page, "Current Company", current_company, actions, f"Filled current company{suffix}")
    current_location = plan_value(plan, "Current Location")
    if current_location:
        await _fill_field_by_label(page, "Current Location", current_location, actions, f"Re-filled current location{suffix}")


async def _fill_greenhouse_form(page, plan: dict[str, Any], resume_pdf: Path, actions: list[str], remaining: list[str]) -> None:
    first_name, last_name = split_full_name(plan_value(plan, "Legal Name"))
    await _fill_first_present(
        page,
        ['#first_name', 'input[name="first_name"]', 'input[name="job_application[first_name]"]'],
        first_name,
        "Filled first name",
        actions,
    )
    await _fill_first_present(
        page,
        ['#last_name', 'input[name="last_name"]', 'input[name="job_application[last_name]"]'],
        last_name,
        "Filled last name",
        actions,
    )
    await _fill_first_present(
        page,
        ['#email', 'input[name="email"]', 'input[name="job_application[email]"]', 'input[type="email"]'],
        plan_value(plan, "Email"),
        "Filled email",
        actions,
    )
    await _fill_first_present(
        page,
        ['#phone', 'input[name="phone"]', 'input[name="job_application[phone]"]', 'input[type="tel"]'],
        plan_value(plan, "Phone Number"),
        "Filled phone",
        actions,
    )

    linkedin = plan_value(plan, "LinkedIn")
    if linkedin and not await _fill_field_by_label(page, "LinkedIn", linkedin, actions):
        await _fill_first_present(page, ['input[name*="linkedin" i]', 'input[id*="linkedin" i]'], linkedin, "Filled LinkedIn", actions)

    uploaded = await _upload_file_by_keywords(page, resume_pdf, ["resume", "cv"], actions, f"Uploaded resume: {resume_pdf}")
    if not uploaded:
        remaining.append("Resume input was not found; upload manually.")
    else:
        await _upload_cover_letter_if_available(page, plan, actions)

    additional_information = plan_value(plan, "Additional Information")
    if additional_information:
        await _fill_text_field_from_labels(
            page,
            DIRECT_ADDITIONAL_INFORMATION_LABELS,
            additional_information,
            actions,
            "Filled additional information",
        )

    cover_letter = plan_value(plan, "Cover Letter")
    if cover_letter:
        await _fill_text_field_from_labels(page, COVER_LETTER_LABELS, cover_letter, actions, "Filled cover letter")

    await _fill_common_yes_no_fields(page, actions)
    await _fill_demographic_fields(page, plan, actions)


async def _fill_lever_form(page, plan: dict[str, Any], resume_pdf: Path, actions: list[str], remaining: list[str]) -> None:
    await _fill_first_present(
        page,
        ['input[name="name"]', 'input[autocomplete="name"]'],
        plan_value(plan, "Legal Name"),
        "Filled legal name",
        actions,
    )
    await _fill_first_present(
        page,
        ['input[name="email"]', 'input[type="email"]'],
        plan_value(plan, "Email"),
        "Filled email",
        actions,
    )
    await _fill_first_present(
        page,
        ['input[name="phone"]', 'input[type="tel"]'],
        plan_value(plan, "Phone Number"),
        "Filled phone",
        actions,
    )

    linkedin = plan_value(plan, "LinkedIn")
    if linkedin:
        filled_linkedin = await _fill_first_present(
            page,
            ['input[name="urls[LinkedIn]"]', 'input[name*="linkedin" i]', 'input[id*="linkedin" i]'],
            linkedin,
            "Filled LinkedIn",
            actions,
        )
        if not filled_linkedin:
            await _fill_field_by_label(page, "LinkedIn", linkedin, actions)

    uploaded = await _upload_file_by_keywords(page, resume_pdf, ["resume", "cv"], actions, f"Uploaded resume: {resume_pdf}")
    if not uploaded:
        remaining.append("Resume input was not found; upload manually.")
    else:
        await _wait_for_resume_processing(page, actions, "Lever")
        await _refill_core_identity_fields(page, plan, actions, action_suffix="after resume processing")
        await _upload_cover_letter_if_available(page, plan, actions)

    additional_information = plan_value(plan, "Additional Information")
    if additional_information:
        filled = await _fill_first_present(
            page,
            ['textarea[name="comments"]', 'textarea[name="additional_information"]'],
            additional_information,
            "Filled additional information",
            actions,
        )
        if not filled:
            await _fill_text_field_from_labels(
                page,
                DIRECT_ADDITIONAL_INFORMATION_LABELS,
                additional_information,
                actions,
                "Filled additional information",
            )

    cover_letter = plan_value(plan, "Cover Letter")
    if cover_letter:
        await _fill_text_field_from_labels(page, COVER_LETTER_LABELS, cover_letter, actions, "Filled cover letter")

    location_terms = _location_preference_terms(plan)
    if location_terms:
        for location_label in ["which location are you applying for", "location applying for", "location"]:
            if await _select_best_option_by_label(page, location_label, location_terms, actions):
                break

    application_source = plan_value(plan, "Application Source")
    if application_source:
        filled_source = await _choose_grouped_option_by_label(
            page,
            "how did you hear about us",
            application_source,
            actions,
            "Selected application source",
        )
        if not filled_source:
            filled_source = await _choose_grouped_option_by_label(page, "application source", application_source, actions)
        if not filled_source:
            filled_source = await _select_option_by_label(page, "how did you hear about us", application_source, actions)
        if not filled_source:
            await _check_checkbox_by_label(page, application_source, actions)

    await _fill_common_yes_no_fields(page, actions)
    await _fill_demographic_fields(page, plan, actions)
    await _refill_core_identity_fields(page, plan, actions, action_suffix="after Lever pass")


async def _fill_generic_form(page, plan: dict[str, Any], resume_pdf: Path, actions: list[str], remaining: list[str]) -> None:
    full_name = plan_value(plan, "Legal Name")
    first_name, last_name = split_full_name(full_name)
    if not await _fill_field_by_label(page, "Full Name", full_name, actions, "Filled full name"):
        await _fill_field_by_label(page, "Legal Name", full_name, actions, "Filled legal name")
    await _fill_field_by_label(page, "First Name", first_name, actions, "Filled first name")
    await _fill_field_by_label(page, "Last Name", last_name, actions, "Filled last name")
    await _fill_field_by_label(page, "Email", plan_value(plan, "Email"), actions, "Filled email")
    await _fill_field_by_label(page, "Phone", plan_value(plan, "Phone Number"), actions, "Filled phone")
    await _fill_field_by_label(page, "Mobile", plan_value(plan, "Phone Number"), actions, "Filled mobile")
    await _fill_field_by_label(page, "LinkedIn", plan_value(plan, "LinkedIn"), actions, "Filled LinkedIn")
    await _fill_field_by_label(page, "Current Location", plan_value(plan, "Current Location"), actions, "Filled current location")
    await _fill_field_by_label(
        page,
        "Start Date",
        plan_value(plan, "Start Date / Availability"),
        actions,
        "Filled start date / availability",
    )

    uploaded = await _upload_file_by_keywords(page, resume_pdf, ["resume", "cv"], actions, f"Uploaded resume: {resume_pdf}")
    if not uploaded:
        remaining.append("Resume input was not found; upload manually.")
    else:
        await _upload_cover_letter_if_available(page, plan, actions)

    additional_information = plan_value(plan, "Additional Information")
    if additional_information:
        await _fill_text_field_from_labels(
            page,
            DIRECT_ADDITIONAL_INFORMATION_LABELS,
            additional_information,
            actions,
            "Filled additional information",
        )

    cover_letter = plan_value(plan, "Cover Letter")
    if cover_letter:
        await _fill_text_field_from_labels(page, COVER_LETTER_LABELS, cover_letter, actions, "Filled cover letter")

    await _fill_common_yes_no_fields(page, actions)
    await _fill_demographic_fields(page, plan, actions)


async def _click_workday_start_button(page, actions: list[str]) -> None:
    for name in ["Apply Manually", "Apply", "Start", "Continue"]:
        button = page.get_by_role("button", name=name)
        if await button.count() == 1:
            await button.click()
            await page.wait_for_timeout(1500)
            actions.append(f"Clicked Workday start button: {name}")
            return


async def _ashby_form_visible(page) -> bool:
    count_only_checks = [
        page.get_by_role("button", name="Upload file").first,
        page.get_by_role("button", name="Upload File").first,
        page.get_by_role("button", name="Submit Application").first,
    ]
    for locator in count_only_checks:
        try:
            if await locator.count() > 0:
                return True
        except Exception:
            continue

    locator_checks = [
        page.locator('input[name="_systemfield_name"]').first,
        page.locator('input[name="_systemfield_email"]').first,
        page.locator('input[type="file"]#_systemfield_resume').first,
        page.locator('input[type="tel"]').first,
        page.locator("textarea").first,
    ]
    for locator in locator_checks:
        try:
            if await locator.count() > 0 and await locator.is_visible():
                return True
        except Exception:
            continue
    return False


async def _click_first_visible_by_role(page, role: str, name: str) -> bool:
    locator = page.get_by_role(role, name=name).first
    if await locator.count() == 0:
        return False
    if not await locator.is_visible():
        return False
    await locator.click()
    return True


async def _enter_ashby_application_form(page, actions: list[str]) -> bool:
    if await _ashby_form_visible(page):
        return True

    attempts = [
        ("tab", "Application", "Clicked Ashby application tab"),
        ("link", "Application", "Clicked Ashby application link"),
        ("button", "Apply for this Job", "Clicked Ashby apply CTA"),
        ("link", "Apply for this Job", "Clicked Ashby apply CTA"),
        ("button", "Apply", "Clicked Ashby apply CTA"),
    ]
    for role, name, action in attempts:
        try:
            clicked = await _click_first_visible_by_role(page, role, name)
        except Exception:
            clicked = False
        if not clicked:
            continue
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1500)
        actions.append(f"{action}: {name}")
        if await _ashby_form_visible(page):
            return True

    clicked_fallback = await page.evaluate(
        """
        () => {
          const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const isVisible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const candidates = Array.from(document.querySelectorAll('a, button, [role="button"], [role="tab"]'))
            .filter((node) => isVisible(node))
            .map((node) => ({
              node,
              label: norm(node.innerText || node.textContent || node.getAttribute('aria-label')),
            }));
          const match = candidates.find(
            (item) =>
              item.label === 'application'
              || item.label === 'apply for this job'
              || item.label === 'apply'
          );
          if (!match) return null;
          match.node.click();
          return match.label;
        }
        """
    )
    if clicked_fallback:
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1500)
        actions.append(f"Clicked Ashby application entry: {clicked_fallback}")

    return await _ashby_form_visible(page)


async def _workday_blocker_message(page) -> str | None:
    return await page.evaluate(
        """
        () => {
          const text = (document.body?.innerText || '').toLowerCase().replace(/\\s+/g, ' ').trim();
          const blockers = [
            ['sign in', 'Workday sign-in step detected.'],
            ['create account', 'Workday account creation step detected.'],
            ['candidate home account', 'Workday candidate account step detected.'],
            ['forgot password', 'Workday login form detected.'],
            ['verify your email', 'Workday email verification step detected.'],
          ];
          for (const [token, message] of blockers) {
            if (text.includes(token)) return message;
          }
          return null;
        }
        """
    )


async def apply_ashby(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    ensure_submit_allowed(submit, fill_legal_acknowledgements)
    plan = load_application_plan(application_dir)
    apply_url = plan.get("job", {}).get("apply_url")
    if not apply_url:
        raise ApplyRunnerError("Application plan is missing job.apply_url.")

    resume_pdf = plan_artifact(plan, "resume_pdf")
    additional_information = plan_value(plan, "Additional Information")
    cover_letter = plan_value(plan, "Cover Letter")
    answer_bank = _answer_bank_from_plan(plan)
    screenshot_path, html_path = review_paths(application_dir)

    actions: list[str] = []
    remaining_review_items = initial_review_items(fill_legal_acknowledgements)
    field_fill_report: list[dict[str, str]] = []
    unfilled_fields: list[dict[str, Any]] = []
    optional_no_value_fields: list[dict[str, Any]] = []
    file_upload_report: dict[str, Any] = {}

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc

    async with async_playwright() as playwright:
        browser, context, page, session_action = await _new_browser_page(
            playwright,
            headless=headless,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
        if session_action:
            actions.append(session_action)
        try:
            await _goto_application_page(page, apply_url)
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
            )
            if blocked_review:
                return blocked_review
            if not await _enter_ashby_application_form(page, actions):
                return await _blocked_review_result(
                    page,
                    status="blocked_application_entry_required",
                    message="Ashby application form did not open automatically; review the entry step before submission.",
                    actions=actions,
                    remaining_review_items=remaining_review_items,
                    screenshot_path=screenshot_path,
                    html_path=html_path,
                    submit=submit,
                )

            await _fill_input_by_name(
                page,
                "_systemfield_name",
                plan_value(plan, "Legal Name") or "",
                "Filled legal name",
                actions,
            )
            await _fill_input_by_name(
                page,
                "_systemfield_email",
                plan_value(plan, "Email") or "",
                "Filled email",
                actions,
            )

            preferred = plan_value(plan, "Preferred Name") or answer_bank.get("preferred_name")
            if preferred:
                await page.evaluate(
                    """
                    ({ value }) => {
                      const inputs = Array.from(document.querySelectorAll('input[type="text"]'));
                      const input = inputs.find(
                        (el) => el.name !== '_systemfield_name' && el.placeholder === 'Type here...'
                      );
                      if (input) {
                        input.value = value;
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                      }
                    }
                    """,
                    {"value": preferred},
                )
                actions.append("Filled preferred name")

            phone = plan_value(plan, "Phone Number") or answer_bank.get("phone")
            if phone:
                await _fill_first_present(
                    page,
                    ['input[type="tel"]'],
                    phone,
                    "Filled phone",
                    actions,
                )

            linkedin = plan_value(plan, "LinkedIn") or answer_bank.get("linkedin")
            if linkedin:
                await _fill_field_by_label(
                    page,
                    "LinkedIn Profile",
                    linkedin,
                    actions,
                    "Filled LinkedIn profile",
                    only_empty=True,
                )

            resume_input = page.locator('input[type="file"]#_systemfield_resume')
            if await resume_input.count() == 1:
                await resume_input.set_input_files(str(resume_pdf))
                actions.append(f"Uploaded resume: {resume_pdf}")
                await _wait_for_ashby_resume_parsing(page, actions)
                if preferred:
                    await _set_first_value_by_selectors(
                        page,
                        ['input[placeholder="Type here..."]'],
                        preferred,
                        "Re-filled preferred name after resume parsing",
                        actions,
                    )
                if phone:
                    await _fill_first_present(
                        page,
                        ['input[type="tel"]'],
                        phone,
                        "Re-filled phone after resume parsing",
                        actions,
                    )
                await _set_first_value_by_selectors(
                    page,
                    ['input[name="_systemfield_name"]'],
                    plan_value(plan, "Legal Name"),
                    "Re-filled legal name after resume parsing",
                    actions,
                )
                await _set_first_value_by_selectors(
                    page,
                    ['input[name="_systemfield_email"]', 'input[type="email"]'],
                    plan_value(plan, "Email"),
                    "Re-filled email after resume parsing",
                    actions,
                )
                if linkedin:
                    await _fill_field_by_label(
                        page,
                        "LinkedIn Profile",
                        linkedin,
                        actions,
                        "Re-filled LinkedIn profile after resume parsing",
                        only_empty=True,
                    )
                await _upload_cover_letter_if_available(page, plan, actions)
            else:
                remaining_review_items.append("Resume input was not found; upload manually.")

            location = plan_value(plan, "Current Location")
            if location:
                location_input = page.locator('input[role="combobox"][placeholder="Start typing..."]')
                if await location_input.count() == 1:
                    await location_input.fill(location)
                    await page.wait_for_timeout(500)
                    if await _visible_option_count(page) > 0:
                        await location_input.press("Enter")
                    actions.append("Filled current location")

            availability = plan_value(plan, "Start Date / Availability")
            if availability:
                start_input = page.locator('input[placeholder="Pick date..."]')
                if await start_input.count() == 1:
                    await start_input.fill(availability)
                    actions.append("Filled start date / availability")

            await _click_option_button(page, "authorized to work", "Yes", actions)
            await _click_option_button(page, "require sponsorship", "Yes", actions)
            await _click_option_button(page, "three days per week", "Yes", actions)
            await _click_option_button(page, "2-3 times a week", "Yes", actions)
            await _click_option_button(page, "work from our new york", "Yes", actions)
            await _check_checkbox_by_label(page, "work from our new york", actions)

            if additional_information:
                filled_additional_information = False
                textareas = page.locator("textarea")
                textarea_count = await textareas.count()
                for index in range(textarea_count):
                    placeholder = await textareas.nth(index).get_attribute("placeholder")
                    if placeholder and "type here" in placeholder.lower():
                        await textareas.nth(index).fill(additional_information)
                        actions.append("Filled additional information")
                        filled_additional_information = True
                        break
                if not filled_additional_information:
                    await _fill_text_field_from_labels(
                        page,
                        DIRECT_ADDITIONAL_INFORMATION_LABELS,
                        additional_information,
                        actions,
                        "Filled additional information",
                    )

            if cover_letter:
                await _fill_text_field_from_labels(page, COVER_LETTER_LABELS, cover_letter, actions, "Filled cover letter")

            for field in ["Gender", "Race", "Veteran Status", "Disability Status"]:
                value = plan_value(plan, field)
                if value:
                    await _set_radio_by_label(page, value, actions)

            field_fill_report = await _fill_detected_requirements(
                page,
                plan,
                actions,
                remaining_review_items,
                fill_legal_acknowledgements,
            )
            file_upload_report = await _verify_resume_upload(page, resume_pdf, actions)
            unfilled_fields = await _audit_unfilled_fields(page)
            unfilled_fields, optional_no_value_fields = _apply_optional_no_value_policy(
                plan,
                unfilled_fields,
                fill_legal_acknowledgements=fill_legal_acknowledgements,
            )
            if not await _ashby_form_visible(page):
                return await _blocked_review_result(
                    page,
                    status="blocked_application_entry_required",
                    message="Ashby application form is not visibly open after autofill; review the entry step before submission.",
                    actions=actions,
                    remaining_review_items=remaining_review_items,
                    screenshot_path=screenshot_path,
                    html_path=html_path,
                    submit=submit,
                    field_fill_report=field_fill_report,
                    unfilled_fields=unfilled_fields,
                    file_upload_report=file_upload_report,
                    optional_no_value_fields=optional_no_value_fields,
                )
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
            )
            if blocked_review:
                return blocked_review
            blocked_submit_review = await _blocked_submit_review_if_incomplete(
                page,
                submit=submit,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
                optional_no_value_fields=optional_no_value_fields,
            )
            if blocked_submit_review:
                return blocked_submit_review
            await _save_review_artifacts(page, screenshot_path, html_path)

            confirmation_text = None
            if submit:
                await _click_submit_button(page, ["Submit Application"])
                await page.wait_for_timeout(5000)
                confirmation_text = (await page.locator("body").inner_text())[:2000]
                actions.append("Submitted application")
                status = "submitted"
            else:
                status = "ready_for_review"

            return {
                "mode": "submit" if submit else "review_only",
                "status": status,
                "completed_actions": actions,
                "file_upload_report": file_upload_report,
                "field_fill_report": field_fill_report,
                "unfilled_fields": unfilled_fields,
                "optional_no_value_fields": optional_no_value_fields,
                "remaining_review_items": [] if submit else remaining_review_items,
                "confirmation_text": confirmation_text,
                "screenshot_path": str(screenshot_path),
                "html_path": str(html_path),
            }
        finally:
            await _close_browser_session(browser, context)


async def apply_greenhouse(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    ensure_submit_allowed(submit, fill_legal_acknowledgements)
    plan = load_application_plan(application_dir)
    apply_url = plan.get("job", {}).get("apply_url")
    if not apply_url:
        raise ApplyRunnerError("Application plan is missing job.apply_url.")

    resume_pdf = plan_artifact(plan, "resume_pdf")
    screenshot_path, html_path = review_paths(application_dir)
    actions: list[str] = []
    remaining_review_items = initial_review_items(fill_legal_acknowledgements)
    field_fill_report: list[dict[str, str]] = []
    unfilled_fields: list[dict[str, Any]] = []
    optional_no_value_fields: list[dict[str, Any]] = []

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc

    async with async_playwright() as playwright:
        browser, context, page, session_action = await _new_browser_page(
            playwright,
            headless=headless,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
        if session_action:
            actions.append(session_action)
        try:
            await _goto_application_page(page, apply_url)
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
            )
            if blocked_review:
                return blocked_review
            await _fill_greenhouse_form(page, plan, resume_pdf, actions, remaining_review_items)
            field_fill_report = await _run_generic_fill_passes(
                page,
                plan,
                resume_pdf,
                actions,
                remaining_review_items,
                fill_legal_acknowledgements,
                ["i certify", "i agree"],
            )
            file_upload_report = await _verify_resume_upload(page, resume_pdf, actions)
            unfilled_fields = await _audit_unfilled_fields(page)
            unfilled_fields, optional_no_value_fields = _apply_optional_no_value_policy(
                plan,
                unfilled_fields,
                fill_legal_acknowledgements=fill_legal_acknowledgements,
            )
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
            )
            if blocked_review:
                return blocked_review
            blocked_submit_review = await _blocked_submit_review_if_incomplete(
                page,
                submit=submit,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
                optional_no_value_fields=optional_no_value_fields,
            )
            if blocked_submit_review:
                return blocked_submit_review
            await _save_review_artifacts(page, screenshot_path, html_path)

            confirmation_text = None
            if submit:
                await _click_submit_button(page, ["Submit Application", "Submit application", "Submit"])
                await page.wait_for_timeout(5000)
                confirmation_text = (await page.locator("body").inner_text())[:2000]
                actions.append("Submitted application")
                status = "submitted"
            else:
                status = "ready_for_review"

            return {
                "mode": "submit" if submit else "review_only",
                "status": status,
                "completed_actions": actions,
                "file_upload_report": file_upload_report,
                "field_fill_report": field_fill_report,
                "unfilled_fields": unfilled_fields,
                "optional_no_value_fields": optional_no_value_fields,
                "remaining_review_items": [] if submit else remaining_review_items,
                "confirmation_text": confirmation_text,
                "screenshot_path": str(screenshot_path),
                "html_path": str(html_path),
            }
        finally:
            await _close_browser_session(browser, context)


async def apply_lever(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    ensure_submit_allowed(submit, fill_legal_acknowledgements)
    plan = load_application_plan(application_dir)
    apply_url = plan.get("job", {}).get("apply_url")
    if not apply_url:
        raise ApplyRunnerError("Application plan is missing job.apply_url.")

    resume_pdf = plan_artifact(plan, "resume_pdf")
    screenshot_path, html_path = review_paths(application_dir)
    actions: list[str] = []
    remaining_review_items = initial_review_items(fill_legal_acknowledgements)
    field_fill_report: list[dict[str, str]] = []
    unfilled_fields: list[dict[str, Any]] = []
    optional_no_value_fields: list[dict[str, Any]] = []

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc

    async with async_playwright() as playwright:
        browser, context, page, session_action = await _new_browser_page(
            playwright,
            headless=headless,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
        if session_action:
            actions.append(session_action)
        try:
            await _goto_application_page(page, apply_url)
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
            )
            if blocked_review:
                return blocked_review
            await _fill_lever_form(page, plan, resume_pdf, actions, remaining_review_items)
            field_fill_report = await _run_generic_fill_passes(
                page,
                plan,
                resume_pdf,
                actions,
                remaining_review_items,
                fill_legal_acknowledgements,
                ["i certify", "i agree"],
            )
            await _refill_core_identity_fields(page, plan, actions, action_suffix="after final Lever recovery")
            file_upload_report = await _verify_resume_upload(page, resume_pdf, actions)
            unfilled_fields = await _audit_unfilled_fields(page)
            unfilled_fields, optional_no_value_fields = _apply_optional_no_value_policy(
                plan,
                unfilled_fields,
                fill_legal_acknowledgements=fill_legal_acknowledgements,
            )
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
            )
            if blocked_review:
                return blocked_review
            blocked_submit_review = await _blocked_submit_review_if_incomplete(
                page,
                submit=submit,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
                optional_no_value_fields=optional_no_value_fields,
            )
            if blocked_submit_review:
                return blocked_submit_review
            await _save_review_artifacts(page, screenshot_path, html_path)

            confirmation_text = None
            if submit:
                await _click_submit_button(page, ["Submit application", "Submit Application", "Submit"])
                await page.wait_for_timeout(5000)
                confirmation_text = (await page.locator("body").inner_text())[:2000]
                actions.append("Submitted application")
                status = "submitted"
            else:
                status = "ready_for_review"

            return {
                "mode": "submit" if submit else "review_only",
                "status": status,
                "completed_actions": actions,
                "file_upload_report": file_upload_report,
                "field_fill_report": field_fill_report,
                "unfilled_fields": unfilled_fields,
                "optional_no_value_fields": optional_no_value_fields,
                "remaining_review_items": [] if submit else remaining_review_items,
                "confirmation_text": confirmation_text,
                "screenshot_path": str(screenshot_path),
                "html_path": str(html_path),
            }
        finally:
            await _close_browser_session(browser, context)


async def apply_generic(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    ensure_submit_allowed(submit, fill_legal_acknowledgements)
    plan = load_application_plan(application_dir)
    apply_url = plan.get("job", {}).get("apply_url")
    if not apply_url:
        raise ApplyRunnerError("Application plan is missing job.apply_url.")

    resume_pdf = plan_artifact(plan, "resume_pdf")
    screenshot_path, html_path = review_paths(application_dir)
    actions: list[str] = []
    remaining_review_items = initial_review_items(fill_legal_acknowledgements)
    field_fill_report: list[dict[str, str]] = []
    unfilled_fields: list[dict[str, Any]] = []
    optional_no_value_fields: list[dict[str, Any]] = []

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc

    async with async_playwright() as playwright:
        browser, context, page, session_action = await _new_browser_page(
            playwright,
            headless=headless,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
        if session_action:
            actions.append(session_action)
        try:
            await _goto_application_page(page, apply_url)
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
            )
            if blocked_review:
                return blocked_review
            await _fill_generic_form(page, plan, resume_pdf, actions, remaining_review_items)
            field_fill_report = await _run_generic_fill_passes(
                page,
                plan,
                resume_pdf,
                actions,
                remaining_review_items,
                fill_legal_acknowledgements,
                ["i certify", "i agree"],
            )
            file_upload_report = await _verify_resume_upload(page, resume_pdf, actions)
            unfilled_fields = await _audit_unfilled_fields(page)
            unfilled_fields, optional_no_value_fields = _apply_optional_no_value_policy(
                plan,
                unfilled_fields,
                fill_legal_acknowledgements=fill_legal_acknowledgements,
            )
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
            )
            if blocked_review:
                return blocked_review
            blocked_submit_review = await _blocked_submit_review_if_incomplete(
                page,
                submit=submit,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
                optional_no_value_fields=optional_no_value_fields,
            )
            if blocked_submit_review:
                return blocked_submit_review
            await _save_review_artifacts(page, screenshot_path, html_path)

            confirmation_text = None
            if submit:
                await _click_submit_button(page, ["Submit Application", "Submit application", "Submit", "Apply"])
                await page.wait_for_timeout(5000)
                confirmation_text = (await page.locator("body").inner_text())[:2000]
                actions.append("Submitted application")
                status = "submitted"
            else:
                status = "ready_for_review"

            return {
                "mode": "submit" if submit else "review_only",
                "status": status,
                "completed_actions": actions,
                "file_upload_report": file_upload_report,
                "field_fill_report": field_fill_report,
                "unfilled_fields": unfilled_fields,
                "optional_no_value_fields": optional_no_value_fields,
                "remaining_review_items": [] if submit else remaining_review_items,
                "confirmation_text": confirmation_text,
                "screenshot_path": str(screenshot_path),
                "html_path": str(html_path),
            }
        finally:
            await _close_browser_session(browser, context)


async def apply_workday(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    ensure_submit_allowed(submit, fill_legal_acknowledgements)
    plan = load_application_plan(application_dir)
    apply_url = plan.get("job", {}).get("apply_url")
    if not apply_url:
        raise ApplyRunnerError("Application plan is missing job.apply_url.")

    resume_pdf = plan_artifact(plan, "resume_pdf")
    screenshot_path, html_path = review_paths(application_dir)
    actions: list[str] = []
    remaining_review_items = initial_review_items(fill_legal_acknowledgements)
    field_fill_report: list[dict[str, str]] = []
    unfilled_fields: list[dict[str, Any]] = []
    optional_no_value_fields: list[dict[str, Any]] = []

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc

    async with async_playwright() as playwright:
        browser, context, page, session_action = await _new_browser_page(
            playwright,
            headless=headless,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
        if session_action:
            actions.append(session_action)
        try:
            await _goto_application_page(page, apply_url)
            await _click_workday_start_button(page, actions)
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
            )
            if blocked_review:
                return blocked_review

            await _fill_generic_form(page, plan, resume_pdf, actions, remaining_review_items)
            field_fill_report = await _run_generic_fill_passes(
                page,
                plan,
                resume_pdf,
                actions,
                remaining_review_items,
                fill_legal_acknowledgements,
                ["i certify", "i agree"],
            )
            file_upload_report = await _verify_resume_upload(page, resume_pdf, actions)
            unfilled_fields = await _audit_unfilled_fields(page)
            unfilled_fields, optional_no_value_fields = _apply_optional_no_value_policy(
                plan,
                unfilled_fields,
                fill_legal_acknowledgements=fill_legal_acknowledgements,
            )
            blocked_review = await _blocked_review_if_present(
                page,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                submit=submit,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
            )
            if blocked_review:
                return blocked_review
            blocked_submit_review = await _blocked_submit_review_if_incomplete(
                page,
                submit=submit,
                actions=actions,
                remaining_review_items=remaining_review_items,
                screenshot_path=screenshot_path,
                html_path=html_path,
                field_fill_report=field_fill_report,
                unfilled_fields=unfilled_fields,
                file_upload_report=file_upload_report,
                optional_no_value_fields=optional_no_value_fields,
            )
            if blocked_submit_review:
                return blocked_submit_review
            await _save_review_artifacts(page, screenshot_path, html_path)

            confirmation_text = None
            if submit:
                await _click_submit_button(page, ["Submit Application", "Submit application", "Submit", "Apply"])
                await page.wait_for_timeout(5000)
                confirmation_text = (await page.locator("body").inner_text())[:2000]
                actions.append("Submitted application")
                status = "submitted"
            else:
                status = "ready_for_review"

            return {
                "mode": "submit" if submit else "review_only",
                "status": status,
                "completed_actions": actions,
                "file_upload_report": file_upload_report,
                "field_fill_report": field_fill_report,
                "unfilled_fields": unfilled_fields,
                "optional_no_value_fields": optional_no_value_fields,
                "remaining_review_items": [] if submit else remaining_review_items,
                "confirmation_text": confirmation_text,
                "screenshot_path": str(screenshot_path),
                "html_path": str(html_path),
            }
        finally:
            await _close_browser_session(browser, context)


def apply_ashby_sync(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        apply_ashby(
            paths,
            application_dir,
            submit=submit,
            headless=headless,
            fill_legal_acknowledgements=fill_legal_acknowledgements,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
    )


def apply_greenhouse_sync(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        apply_greenhouse(
            paths,
            application_dir,
            submit=submit,
            headless=headless,
            fill_legal_acknowledgements=fill_legal_acknowledgements,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
    )


def apply_lever_sync(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        apply_lever(
            paths,
            application_dir,
            submit=submit,
            headless=headless,
            fill_legal_acknowledgements=fill_legal_acknowledgements,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
    )


def apply_generic_sync(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        apply_generic(
            paths,
            application_dir,
            submit=submit,
            headless=headless,
            fill_legal_acknowledgements=fill_legal_acknowledgements,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
    )


def apply_workday_sync(
    paths: ProjectPaths,
    application_dir: Path,
    *,
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        apply_workday(
            paths,
            application_dir,
            submit=submit,
            headless=headless,
            fill_legal_acknowledgements=fill_legal_acknowledgements,
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
        )
    )


def run_apply(
    paths: ProjectPaths,
    *,
    job_id: str,
    ats: str = "auto",
    submit: bool = False,
    headless: bool = True,
    fill_legal_acknowledgements: bool = False,
    storage_state_path: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any]:
    application_dir = application_dir_for_job(paths, job_id)
    plan = load_application_plan(application_dir)
    resolved_ats = resolve_ats(plan, ats.lower())
    runner = {
        "ashby": apply_ashby_sync,
        "greenhouse": apply_greenhouse_sync,
        "lever": apply_lever_sync,
        "workday": apply_workday_sync,
        "generic": apply_generic_sync,
    }[resolved_ats]
    review = runner(
        paths,
        application_dir,
        submit=submit,
        headless=headless,
        fill_legal_acknowledgements=fill_legal_acknowledgements,
        storage_state_path=storage_state_path,
        user_data_dir=user_data_dir,
    )
    review["answer_gap_recommendations"] = _review_answer_gap_recommendations(
        plan,
        review,
        fill_legal_acknowledgements=fill_legal_acknowledgements,
    )
    save_text(
        application_dir / "apply_review.md",
        render_review_summary(plan, review),
    )
    save_text(
        application_dir / "apply_review.json",
        render_review_json(plan, review, resolved_ats),
    )
    return review


async def capture_browser_session(
    *,
    url: str,
    output_path: str | Path,
    headless: bool = False,
    wait_seconds: int = 120,
) -> Path:
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(wait_seconds * 1000)
            await context.storage_state(path=str(output))
        finally:
            await context.close()
            await browser.close()
    return output


def capture_browser_session_sync(
    *,
    url: str,
    output_path: str | Path,
    headless: bool = False,
    wait_seconds: int = 120,
) -> Path:
    return asyncio.run(
        capture_browser_session(
            url=url,
            output_path=output_path,
            headless=headless,
            wait_seconds=wait_seconds,
        )
    )
