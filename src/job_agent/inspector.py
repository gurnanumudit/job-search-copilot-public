from __future__ import annotations

import asyncio
import re

from .answers import build_readiness, generate_answer_for_field
from .models import CandidateProfile, InspectResult, JobPosting, JobScore, RawFormField
from .paths import ProjectPaths
from .storage import save_snapshot


FIELD_JS = """
() => {
  const elements = Array.from(document.querySelectorAll("input, textarea, select"));
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const norm = (text) => (text || "").replace(/\\s+/g, " ").trim();
  const findLabel = (el) => {
    const id = el.getAttribute("id");
    if (id) {
      const direct = document.querySelector(`label[for="${id}"]`);
      if (direct && norm(direct.innerText)) return norm(direct.innerText);
    }
    const aria = el.getAttribute("aria-label");
    if (aria && norm(aria)) return norm(aria);
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = norm(labelledBy.split(/\\s+/).map((part) => document.getElementById(part)?.innerText || "").join(" "));
      if (text) return text;
    }
    const wrapped = el.closest("label");
    if (wrapped && norm(wrapped.innerText)) return norm(wrapped.innerText);
    const parent = el.parentElement;
    if (parent && norm(parent.innerText) && norm(parent.innerText).length <= 180) return norm(parent.innerText);
    const placeholder = el.getAttribute("placeholder");
    return norm(placeholder || "");
  };
  const optionLabel = (el) => {
    const explicit = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
    if (explicit && norm(explicit.innerText)) return norm(explicit.innerText);
    const wrapped = el.closest("label");
    if (wrapped && norm(wrapped.innerText)) return norm(wrapped.innerText);
    return norm(el.getAttribute("value") || el.getAttribute("aria-label") || "");
  };
  const nativeFields = elements
    .filter((el) => visible(el))
    .filter((el) => !["hidden", "file", "submit", "button", "radio", "checkbox"].includes((el.getAttribute("type") || "").toLowerCase()))
    .map((el) => {
      const label = findLabel(el);
      const required = el.hasAttribute("required") || el.getAttribute("aria-required") === "true" || /\\*/.test(label);
      const options = el.tagName.toLowerCase() === "select"
        ? Array.from(el.querySelectorAll("option")).map((option) => option.textContent.trim()).filter(Boolean)
        : [];
      return {
        label,
        field_type: el.getAttribute("type") || el.tagName.toLowerCase(),
        required,
        options,
        help_text: el.getAttribute("placeholder") || "",
      };
    });
  const groupedFields = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'))
    .filter((el) => visible(el))
    .reduce((groups, el) => {
      const scope = el.closest("fieldset") || el.closest('[role="radiogroup"]') || el.closest('[role="group"]') || el.closest("section") || el.parentElement;
      const legend = scope?.querySelector("legend");
      const scopeLabel = norm(legend?.innerText || legend?.textContent)
        || norm(scope?.getAttribute("aria-label") || "")
        || findLabel(el);
      const type = (el.getAttribute("type") || "").toLowerCase();
      const key = `${type}:${el.getAttribute("name") || scopeLabel || findLabel(el)}`;
      if (!groups[key]) {
        groups[key] = {
          label: scopeLabel,
          field_type: type,
          required: false,
          options: [],
          help_text: "",
        };
      }
      groups[key].required = groups[key].required || el.hasAttribute("required") || el.getAttribute("aria-required") === "true" || /\\*/.test(scopeLabel);
      const option = optionLabel(el);
      if (option && !groups[key].options.includes(option)) groups[key].options.push(option);
      return groups;
    }, {});
  const customControls = Array.from(document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"], [aria-controls], input[aria-autocomplete]'))
    .filter((el) => visible(el))
    .filter((el) => !["input", "textarea", "select"].includes(el.tagName.toLowerCase()) || el.getAttribute("role") === "combobox")
    .map((el) => {
      const label = findLabel(el);
      const controlledId = el.getAttribute("aria-controls");
      const controlled = controlledId ? document.getElementById(controlledId) : null;
      const options = controlled
        ? Array.from(controlled.querySelectorAll('[role="option"], li, [data-value]')).map((option) => norm(option.innerText || option.textContent || option.getAttribute("data-value"))).filter(Boolean)
        : [];
      return {
        label,
        field_type: "custom_select",
        required: el.hasAttribute("required") || el.getAttribute("aria-required") === "true" || /\\*/.test(label),
        options,
        help_text: el.getAttribute("placeholder") || "",
      };
    });
  return nativeFields.concat(Object.values(groupedFields), customControls);
}
"""


def normalize_fields(raw_fields: list[dict]) -> list[RawFormField]:
    seen: set[tuple[str, str | None]] = set()
    fields: list[RawFormField] = []
    for item in raw_fields:
        label = re.sub(r"\s+", " ", (item.get("label") or "").strip())
        if not label:
            continue
        key = (label.lower(), item.get("field_type"))
        if key in seen:
            continue
        seen.add(key)
        fields.append(
            RawFormField(
                label=label,
                field_type=item.get("field_type"),
                required=bool(item.get("required")),
                options=[option for option in item.get("options", []) if option],
                help_text=item.get("help_text"),
            )
        )
    return fields


def _blocked_by_policy(url: str) -> str | None:
    lowered = url.lower()
    if "linkedin.com" in lowered:
        return "LinkedIn logged-in inspection is intentionally unsupported."
    return None


async def inspect_application_form(
    paths: ProjectPaths,
    job: JobPosting,
    profile: CandidateProfile,
    score: JobScore | None = None,
) -> InspectResult:
    blocked_reason = _blocked_by_policy(job.apply_url or job.job_url)
    if blocked_reason:
        readiness = build_readiness(job, [], blocked=True)
        return InspectResult(readiness=readiness, blocked_reason=blocked_reason)

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Install project dependencies first.") from exc

    debug_dir = paths.outputs_debug_dir / job.job_id
    debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = debug_dir / "application.png"
    html_path = debug_dir / "application.html"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            try:
                await page.goto(job.apply_url or job.job_url, wait_until="networkidle", timeout=45000)
            except PlaywrightTimeoutError:
                await page.goto(job.apply_url or job.job_url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)
            html = await page.content()
            save_snapshot(html_path, html)
            await page.screenshot(path=str(screenshot_path), full_page=True)

            if await _looks_like_login_gate(page_text=_normalize_text(await page.text_content("body") or "")):
                readiness = build_readiness(job, [], blocked=True)
                return InspectResult(
                    readiness=readiness,
                    html_snapshot_path=html_path,
                    screenshot_path=screenshot_path,
                    blocked_reason="Login required before application inspection.",
                )

            raw_fields = await page.evaluate(FIELD_JS)
            fields = normalize_fields(raw_fields)
            if not fields:
                readiness = build_readiness(job, [], blocked=True)
                return InspectResult(
                    readiness=readiness,
                    html_snapshot_path=html_path,
                    screenshot_path=screenshot_path,
                    blocked_reason="No application form fields were detected. This page may be a job description, not the application form.",
                )
            requirements = [generate_answer_for_field(field, profile, job, score) for field in fields]
            readiness = build_readiness(job, requirements, blocked=False)
            return InspectResult(
                readiness=readiness,
                html_snapshot_path=html_path,
                screenshot_path=screenshot_path,
            )
        finally:
            await browser.close()


def inspect_application_form_sync(
    paths: ProjectPaths,
    job: JobPosting,
    profile: CandidateProfile,
    score: JobScore | None = None,
) -> InspectResult:
    return asyncio.run(inspect_application_form(paths, job, profile, score))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


async def _looks_like_login_gate(page_text: str) -> bool:
    triggers = [
        "sign in",
        "log in",
        "create an account",
        "continue with google",
        "password",
        "login required",
    ]
    return any(trigger in page_text for trigger in triggers)
