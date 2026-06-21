from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import JobPosting


KEY_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z /_-]+):\s*(?P<value>.+?)\s*$")
USER_AGENT = "job-search-copilot/0.1 (+local-first)"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "job"


def parse_markdown_sections(text: str, level: int = 2) -> OrderedDict[str, str]:
    heading_prefix = "#" * level + " "
    sections: OrderedDict[str, list[str]] = OrderedDict()
    current: str | None = None
    for line in text.splitlines():
        if line.startswith(heading_prefix):
            current = line[len(heading_prefix) :].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return OrderedDict((name, "\n".join(lines).strip()) for name, lines in sections.items())


def parse_job_markdown(path: Path) -> JobPosting:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    metadata: dict[str, str] = {}
    description_lines: list[str] = []
    in_description = False

    for line in lines:
        stripped = line.strip()
        if stripped.lower() in {"## description", "# description"}:
            in_description = True
            continue
        if in_description:
            description_lines.append(line)
            continue
        match = KEY_VALUE_RE.match(stripped)
        if match:
            metadata[match.group("key").strip().lower()] = match.group("value").strip()

    heading = next((line[2:].strip() for line in lines if line.startswith("# ")), path.stem.replace("_", " ").title())
    title = metadata.get("title")
    company = metadata.get("company")
    if not title or not company:
        if " - " in heading:
            company_part, title_part = [part.strip() for part in heading.split(" - ", 1)]
            company = company or company_part
            title = title or title_part
        else:
            title = title or heading
            company = company or "Unknown Company"

    description = "\n".join(description_lines).strip() or text
    job_url = metadata.get("job url", metadata.get("job_url", f"file://{path.resolve()}"))
    apply_url = metadata.get("apply url", metadata.get("apply_url"))
    job_id = metadata.get("job id", metadata.get("job_id")) or slugify(f"{company}-{title}")

    return JobPosting(
        job_id=job_id,
        company=company,
        title=title,
        location=metadata.get("location"),
        remote_policy=metadata.get("remote policy", metadata.get("remote_policy")),
        salary_range=metadata.get("salary range", metadata.get("salary_range")),
        employment_type=metadata.get("employment type", metadata.get("employment_type")),
        source="markdown_file",
        job_url=job_url,
        apply_url=apply_url,
        ats=metadata.get("ats"),
        description=description,
        date_found=datetime.now(timezone.utc),
    )


def parse_job_url(url: str) -> JobPosting:
    html = _load_text(url)
    parser = JobPageParser()
    parser.feed(html)

    title_text = parser.best_title() or _fallback_title_from_url(url)
    company, title = _infer_company_and_title(title_text, url)
    description = parser.best_description() or title_text
    location = parser.meta_value("job:location") or parser.meta_value("location")
    remote_policy = _infer_remote_policy(description, location)
    apply_url = parser.application_url(url)

    return JobPosting(
        job_id=slugify(f"{company}-{title}"),
        company=company,
        title=title,
        location=location,
        remote_policy=remote_policy,
        salary_range=None,
        employment_type=None,
        source="job_url",
        job_url=url,
        apply_url=apply_url,
        ats=_infer_ats(url),
        description=description,
        date_found=datetime.now(timezone.utc),
    )


def _load_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _fallback_title_from_url(url: str) -> str:
    parsed = urlparse(url)
    stem = Path(parsed.path).stem or parsed.netloc or "Unknown Role"
    return stem.replace("-", " ").replace("_", " ").title()


def _infer_company_and_title(title_text: str, url: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", " ", title_text).strip()
    for delimiter in (" - ", " | ", " @ "):
        if delimiter in cleaned:
            left, right = [part.strip() for part in cleaned.split(delimiter, 1)]
            if _looks_like_role(left) and not _looks_like_role(right):
                return right or _infer_company_from_url(url), left or "Unknown Title"
            if _looks_like_role(right) and not _looks_like_role(left):
                return left or _infer_company_from_url(url), right or "Unknown Title"
            if len(left.split()) <= len(right.split()):
                return right or _infer_company_from_url(url), left or "Unknown Title"
            return left or _infer_company_from_url(url), right or "Unknown Title"
    if " at " in cleaned.lower():
        parts = re.split(r"\s+at\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            return parts[1].strip() or _infer_company_from_url(url), parts[0].strip() or "Unknown Title"
    return _infer_company_from_url(url), cleaned or "Unknown Title"


def _infer_company_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host:
        return host.split(".")[0].replace("-", " ").title()
    path = Path(parsed.path).stem
    return path.replace("-", " ").title() or "Unknown Company"


def _looks_like_role(value: str) -> bool:
    lowered = value.lower()
    role_tokens = [
        "scientist",
        "engineer",
        "analyst",
        "manager",
        "director",
        "lead",
        "principal",
        "staff",
        "intern",
        "developer",
        "researcher",
    ]
    return any(token in lowered for token in role_tokens)


def _infer_remote_policy(description: str, location: str | None) -> str | None:
    haystack = " ".join(filter(None, [location, description])).lower()
    if "remote" in haystack:
        return "Remote"
    if "hybrid" in haystack:
        return "Hybrid"
    if "onsite" in haystack or "on-site" in haystack:
        return "Onsite"
    return None


def _infer_ats(url: str) -> str | None:
    lowered = url.lower()
    if "greenhouse" in lowered:
        return "greenhouse"
    if "lever.co" in lowered:
        return "lever"
    if "ashby" in lowered:
        return "ashby"
    return None


class JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._title_parts: list[str] = []
        self._body_parts: list[str] = []
        self._meta: dict[str, str] = {}
        self._in_title = False
        self._skip_depth = 0
        self._current_link_href: str | None = None
        self._current_link_text: list[str] = []
        self._links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            attributes = {key.lower(): value for key, value in attrs if key and value}
            key = attributes.get("property") or attributes.get("name")
            content = attributes.get("content")
            if key and content:
                self._meta[key.lower()] = content.strip()
            return
        if tag == "a":
            attributes = {key.lower(): value for key, value in attrs if key and value}
            href = attributes.get("href")
            if href:
                self._current_link_href = href.strip()
                self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag == "a" and self._current_link_href:
            text = re.sub(r"\s+", " ", "".join(self._current_link_text)).strip()
            self._links.append((self._current_link_href, text))
            self._current_link_href = None
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        normalized = re.sub(r"\s+", " ", data).strip()
        if not normalized:
            return
        if self._in_title:
            self._title_parts.append(normalized)
        else:
            self._body_parts.append(normalized)
        if self._current_link_href is not None:
            self._current_link_text.append(normalized)

    def best_title(self) -> str | None:
        for key in ("og:title", "twitter:title"):
            value = self._meta.get(key)
            if value:
                return unescape(value)
        if self._title_parts:
            return unescape(" ".join(self._title_parts))
        for key in ("title",):
            value = self._meta.get(key)
            if value:
                return unescape(value)
        return None

    def best_description(self) -> str | None:
        for key in ("og:description", "description", "twitter:description"):
            value = self._meta.get(key)
            if value:
                return unescape(value)
        if not self._body_parts:
            return None
        return unescape(" ".join(self._body_parts[:300]))

    def meta_value(self, name: str) -> str | None:
        value = self._meta.get(name.lower())
        return unescape(value) if value else None

    def application_url(self, page_url: str) -> str | None:
        for key in ("job:apply_url", "apply_url"):
            value = self.meta_value(key)
            if value:
                return value
        for href, text in self._links:
            haystack = f"{href} {text}".lower()
            if "apply" in haystack or "job application" in haystack:
                if href.startswith(("http://", "https://", "file://")):
                    return href
                parsed = urlparse(page_url)
                if parsed.scheme == "file":
                    return str((Path(parsed.path).parent / href).resolve().as_uri())
        return None


def extract_candidate_answers(answer_bank_text: str) -> dict[str, str]:
    sections = parse_markdown_sections(answer_bank_text, level=2)
    answers: dict[str, str] = {}
    for section_name, section_body in sections.items():
        value = ""
        for line in section_body.splitlines():
            stripped = line.strip()
            if stripped.startswith("Answer:"):
                value = stripped.split(":", 1)[1].strip()
                break
            if stripped.startswith("Draft:"):
                value = stripped.split(":", 1)[1].strip()
                break
        if not value:
            value = section_body.strip()
        answers[section_name.strip()] = value.strip()
    return answers
