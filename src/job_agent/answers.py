from __future__ import annotations

import re

from .markdown import parse_markdown_sections
from .models import ApplicationReadiness, ApplicationRequirement, CandidateProfile, JobPosting, JobScore, RawFormField


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _normalize_field_label(text: str) -> str:
    return _normalize(re.sub(r"[*:]+", " ", text))


def _is_voluntary_demographic(label: str) -> bool:
    normalized = _normalize_field_label(label)
    return any(
        token in normalized
        for token in [
            "eeo",
            "gender",
            "ethnicity",
            "race",
            "hispanic",
            "latino",
            "asian",
            "white",
            "black or african american",
            "native hawaiian",
            "pacific islander",
            "american indian",
            "alaska native",
            "two or more races",
            "veteran",
            "military status",
            "disability",
        ]
    )


def _clean_candidate_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    normalized = _normalize(cleaned)
    placeholder_values = {
        "",
        "example candidate",
        "candidate@example.com",
        "+1-555-555-5555",
        "555-555-5555",
        "tbd",
        "todo",
        "n/a",
    }
    if normalized in placeholder_values or "example.com" in normalized:
        return None
    return cleaned


def _find_best_project(profile: CandidateProfile, job: JobPosting, score: JobScore | None = None) -> str | None:
    sections = parse_markdown_sections(profile.profile_bank_text, level=3)
    text = _normalize(f"{job.title} {job.description}")
    ranked_sections = list(sections.items())
    if score:
        if score.lane == "Experimentation":
            ranked_sections.sort(key=lambda item: "experiment" not in item[0].lower())
        elif score.lane == "AI Tooling":
            ranked_sections.sort(key=lambda item: "ai" not in item[0].lower() and "tool" not in item[0].lower())
    for title, body in ranked_sections:
        if any(token in text for token in _normalize(title).split()):
            return f"{title}: {body.replace('Description:', '').strip()}"
    if ranked_sections:
        title, body = ranked_sections[0]
        return f"{title}: {body.replace('Description:', '').strip()}"
    return None


def draft_additional_information(job: JobPosting, profile: CandidateProfile, score: JobScore | None = None) -> str:
    company = job.company
    role = job.title
    fit_points = (score.why_fit[:2] if score else []) or [
        "the role lines up with my background in experimentation, product measurement, and AI/data tooling"
    ]
    fit_sentence = " ".join(point.rstrip(".") + "." for point in fit_points)
    return (
        f"I am genuinely excited about the work happening at {company} and would love the chance to contribute to it. "
        f"What draws me to this {role} role is the opportunity to work on important product questions with strong teams, "
        "and to bring a practical data science approach to experimentation, measurement, and decision-making.\n\n"
        "In my recent work, I have spent a lot of time building and scaling experimentation systems, partnering closely "
        "with product and engineering, and creating AI/data tools that make analysis faster and more reliable. "
        f"{fit_sentence} I would be excited to bring that same product-minded, high-ownership approach to this team."
    )


def draft_cover_letter(job: JobPosting, profile: CandidateProfile, score: JobScore | None = None) -> str:
    candidate_name = str(profile.preferences.candidate.get("name") or "").strip()
    signoff = f"\n\nBest,\n{candidate_name}" if candidate_name else ""
    return (
        f"Hi {job.company} team,\n\n"
        f"I am excited to apply for the {job.title} role. The opportunity stands out to me because it connects directly "
        "to the kind of work I have enjoyed most: using rigorous data science to help product and engineering teams learn "
        "faster, measure what matters, and make better decisions.\n\n"
        "In prior roles, I have led experimentation and product measurement work across multiple teams, helped scale "
        "self-serve experimentation systems, and built AI/data tooling that improves how teams analyze and review their "
        "work. I enjoy "
        "roles where the data scientist is close to the product, trusted to shape measurement strategy, and hands-on enough "
        "to build the systems that make better decision-making repeatable.\n\n"
        f"I would be excited to bring that background to {job.company} and learn more about how I could help the team."
        f"{signoff}"
    )


def export_control_response(profile: CandidateProfile) -> str | None:
    candidate = profile.preferences.candidate
    citizenship_countries = candidate.get("citizenship_countries")
    if isinstance(citizenship_countries, list):
        cleaned = [str(country).strip() for country in citizenship_countries if str(country).strip()]
    else:
        cleaned = []
    if not cleaned:
        return None

    citizenship_text = ", ".join(cleaned)
    permanent_resident = candidate.get("us_legal_permanent_resident")
    approved_i140 = candidate.get("approved_i140") is True

    response = (
        f"Citizen of {citizenship_text}. "
        "I am not a U.S. legal permanent resident, so no permanent resident status date applies."
    )
    if permanent_resident is True:
        response = f"Citizen of {citizenship_text}. I am a U.S. legal permanent resident."
    elif approved_i140:
        response += " I do have an approved I-140."
    return response


def _voluntary_self_identification_answer(label: str, profile: CandidateProfile) -> tuple[str | None, str | None]:
    defaults = profile.preferences.candidate.get("voluntary_self_identification")
    if not isinstance(defaults, dict) or defaults.get("user_confirmed_for_future_applications") is not True:
        return None, None
    normalized = _normalize_field_label(label)
    race_option_tokens = [
        "asian",
        "white",
        "black or african american",
        "native hawaiian",
        "pacific islander",
        "american indian",
        "alaska native",
        "two or more races",
    ]
    if ("hispanic" in normalized or "latino" in normalized) and not any(
        token in normalized for token in race_option_tokens
    ):
        configured = _clean_candidate_value(defaults.get("hispanic_latino"))
        if configured:
            return configured, "User-approved voluntary self-identification default."
        race = _normalize(str(defaults.get("race") or ""))
        if race and "hispanic" not in race and "latino" not in race:
            return "No", "Inferred from user-approved race default; review before submission."
    if "gender" in normalized or normalized in {"male", "female"}:
        return _clean_candidate_value(defaults.get("gender")), "User-approved voluntary self-identification default."
    if (
        "race" in normalized
        or "ethnicity" in normalized
        or "asian" in normalized
        or "hispanic" in normalized
        or "latino" in normalized
        or "white" in normalized
        or "black or african american" in normalized
        or "native hawaiian" in normalized
        or "pacific islander" in normalized
        or "american indian" in normalized
        or "alaska native" in normalized
        or "two or more races" in normalized
    ):
        return _clean_candidate_value(defaults.get("race")), "User-approved voluntary self-identification default."
    if "veteran" in normalized:
        return _clean_candidate_value(defaults.get("veteran_status")), "User-approved voluntary self-identification default."
    if "military status" in normalized:
        return _clean_candidate_value(defaults.get("veteran_status")), "User-approved voluntary self-identification default."
    if "disability" in normalized:
        return _clean_candidate_value(defaults.get("disability_status")), "User-approved voluntary self-identification default."
    return None, None


def generate_answer_for_field(
    field: RawFormField,
    profile: CandidateProfile,
    job: JobPosting,
    score: JobScore | None = None,
) -> ApplicationRequirement:
    label = field.label.strip() or "Unknown field"
    normalized = _normalize_field_label(label)
    answers = parse_markdown_sections(profile.answer_bank_text, level=2)
    prefs = profile.preferences.candidate
    missing = False
    notes: str | None = None
    answer: str | None = None
    confidence = "missing"

    if "preferred name" in normalized and not field.required:
        answer = _clean_candidate_value(prefs.get("preferred_name"))
        confidence = "high" if answer else "missing"
        missing = False
    elif any(token in normalized for token in ["first name", "last name", "full name", "name"]):
        answer = _clean_candidate_value(prefs.get("name"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif "email" in normalized:
        answer = _clean_candidate_value(prefs.get("email"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif "phone" in normalized or "mobile" in normalized:
        answer = _clean_candidate_value(prefs.get("phone"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif "linkedin" in normalized:
        answer = _clean_candidate_value(prefs.get("linkedin"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif (
        "export control" in normalized
        or "citizen or legal permanent resident" in normalized
        or "countries of which you are a citizen" in normalized
        or "obtained such status" in normalized
    ):
        answer = export_control_response(profile)
        confidence = "high" if answer else "missing"
        missing = answer is None
        notes = "Legal answer derived from trusted profile facts. Review before submission."
    elif "github" in normalized:
        answer = _clean_candidate_value(prefs.get("github"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif normalized == "country" or normalized.endswith(" country") or normalized.startswith("country "):
        answer = _clean_candidate_value(prefs.get("country")) or "United States"
        confidence = "high"
    elif "portfolio" in normalized or "website" in normalized:
        answer = _clean_candidate_value(prefs.get("portfolio"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif "replit profile" in normalized or ("profile" in normalized and job.company.lower() in normalized):
        answer = None
        confidence = "missing"
        missing = True
        notes = "Company-specific profile link is not configured locally."
    elif "resume" in normalized:
        resume_name = score.resume_version if score else "resume file"
        answer = f"Use {resume_name}"
        confidence = "high"
    elif "cover letter" in normalized:
        answer = draft_cover_letter(job, profile, score)
        confidence = "medium"
        notes = "Generated draft. Review tone before submission."
    elif "authorized to work" in normalized or "work authorization" in normalized:
        answer = _extract_answer(answers, "Work Authorization", default="Yes.")
        confidence = "high"
        notes = "Legal answer. Review before submission."
    elif "sponsorship" in normalized or "visa" in normalized:
        answer = _find_line_answer(answers.get("Work Authorization", ""), "require sponsorship")
        if not answer:
            answer = "Yes, I would require immigration sponsorship."
        confidence = "high"
        notes = "Legal answer. Review before submission."
    elif "salary" in normalized or "compensation" in normalized:
        answer = _extract_answer(answers, "Salary Expectations")
        confidence = "high" if answer else "missing"
        missing = answer is None
        notes = "Compensation answers should be reviewed before submission."
    elif "why this company" in normalized or f"why {job.company.lower()}" in normalized:
        answer = _extract_answer(answers, f"Why {job.company}?") or draft_additional_information(job, profile, score)
        confidence = "medium"
        notes = "Generated draft. Review tone before submission."
    elif "additional information" in normalized or "anything else" in normalized or "motivation" in normalized:
        answer = draft_additional_information(job, profile, score)
        confidence = "medium"
        notes = "Optional narrative field. Fill by default and review before submission."
    elif "cover" in normalized and "letter" in normalized:
        answer = draft_cover_letter(job, profile, score)
        confidence = "medium"
        notes = "Generated draft. Review tone before submission."
    elif "why this role" in normalized or "why are you looking" in normalized:
        answer = _extract_answer(answers, "Why Are You Looking?")
        confidence = "medium" if answer else "missing"
        missing = answer is None
    elif "relevant project" in normalized or "project" in normalized or "tell us about" in normalized:
        answer = _find_best_project(profile, job, score)
        confidence = "medium" if answer else "missing"
        missing = answer is None
    elif (
        "when can you start" in normalized
        or "start a new role" in normalized
        or "start date" in normalized
        or "pick date" in normalized
        or "earliest" in normalized and "start" in normalized
        or "start working" in normalized
    ):
        answer = _extract_answer(answers, "Availability") or _clean_candidate_value(prefs.get("start_date_or_availability"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif "deadline" in normalized or "timeline consideration" in normalized:
        answer = _clean_candidate_value(prefs.get("deadline_note")) or "No additional deadlines beyond my target start timing."
        confidence = "medium"
        notes = "Timeline answer should be reviewed before submission."
    elif (
        "currently located" in normalized
        or "current location" in normalized
        or "where are you located" in normalized
        or "location city" in normalized
        or normalized == "start typing..."
    ):
        answer = _extract_answer(answers, "Location") or _clean_candidate_value(prefs.get("current_location"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif (
        "three days per week" in normalized
        or "3 days per week" in normalized
        or "work from our us office" in normalized
        or "in-person" in normalized and "office" in normalized
        or "25%" in normalized and "office" in normalized
    ):
        answer = _find_line_answer(answers.get("Work Authorization", ""), "san francisco office")
        if not answer and prefs.get("willing_sf_hybrid_3_days_per_week") is True:
            answer = "Yes."
        confidence = "high" if answer else "missing"
        missing = answer is None
        notes = "Location commitment. Review before submission."
    elif "willing to work from the office" in normalized or "office(s) listed on the job description" in normalized:
        if prefs.get("willing_sf_hybrid_3_days_per_week") is True or prefs.get("willing_seattle_hybrid") is True:
            answer = "Yes."
        confidence = "high" if answer else "missing"
        missing = answer is None
        notes = "Office attendance commitment. Review before submission."
    elif "ai policy" in normalized or "candidate ai" in normalized or "ai partnership guidelines" in normalized:
        answer = "Yes."
        confidence = "medium"
        notes = "AI-use policy acknowledgement. Review the linked policy before submission."
    elif "interviewed" in normalized and job.company.lower() in normalized:
        previously_interviewed = prefs.get("previously_interviewed_companies")
        if isinstance(previously_interviewed, list):
            answer = "Yes." if any(_normalize(str(company)) == _normalize(job.company) for company in previously_interviewed) else "No."
        else:
            answer = "No."
        confidence = "low"
        notes = "Defaulted from local application history. Confirm before final submission."
    elif "current company" in normalized or "current employer" in normalized:
        answer = _clean_candidate_value(prefs.get("current_company"))
        confidence = "high" if answer else "missing"
        missing = answer is None
    elif "location" in normalized or "relocation" in normalized:
        answer = _clean_candidate_value(prefs.get("current_location"))
        if not answer:
            preferred = profile.preferences.location_preferences.get("preferred", [])
            answer = preferred[0] if preferred else None
        confidence = "medium" if answer else "missing"
        missing = answer is None
    elif "have you used" in normalized and "product" in normalized:
        answer = "Needs manual review based on actual product familiarity."
        confidence = "low"
        notes = "Do not auto-fill product usage claims."
    elif _is_voluntary_demographic(label):
        answer, notes = _voluntary_self_identification_answer(label, profile)
        confidence = "high" if answer else "missing"
        missing = False
        notes = notes or "Voluntary demographic question. Leave for manual choice."

    return ApplicationRequirement(
        job_id=job.job_id,
        field_label=label,
        field_type=field.field_type,
        required=field.required,
        detected_options=field.options,
        draft_answer=answer,
        answer_confidence=confidence,
        missing_input=missing,
        notes=notes,
    )


def _extract_answer(sections: dict[str, str], section_name: str, default: str | None = None) -> str | None:
    for name, body in sections.items():
        if _normalize(name) == _normalize(section_name):
            return _find_prefixed_value(body) or body.strip() or default
    return default


def _find_prefixed_value(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Answer:") or stripped.startswith("Draft:"):
            return stripped.split(":", 1)[1].strip()
    return None


def _find_line_answer(text: str, contains: str) -> str | None:
    normalized_contains = _normalize(contains)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if normalized_contains in _normalize(line):
            for offset in range(index, min(index + 3, len(lines))):
                value = _find_prefixed_value(lines[offset])
                if value:
                    return value
    return None


def build_readiness(job: JobPosting, requirements: list[ApplicationRequirement], blocked: bool = False) -> ApplicationReadiness:
    required_assets: list[str] = []
    missing_assets: list[str] = []
    missing_required_assets: list[str] = []
    normalized_requirements: list[ApplicationRequirement] = []

    for requirement in requirements:
        if requirement.required:
            required_assets.append(requirement.field_label)

        has_draft_answer = bool((requirement.draft_answer or "").strip())
        missing_required_answer = (
            requirement.required
            and not _is_voluntary_demographic(requirement.field_label)
            and (requirement.answer_confidence == "missing" or not has_draft_answer)
        )
        missing_input = requirement.missing_input or missing_required_answer
        normalized_requirements.append(requirement.model_copy(update={"missing_input": missing_input}))
        if missing_input:
            missing_assets.append(requirement.field_label)
            if requirement.required:
                missing_required_assets.append(requirement.field_label)

    needs_review = any(
        requirement.answer_confidence in {"low", "medium"} or (requirement.notes and "review" in requirement.notes.lower())
        for requirement in requirements
    )
    if blocked:
        status = "blocked"
    elif missing_required_assets:
        status = "needs_prep"
    elif missing_assets or needs_review:
        status = "needs_review"
    else:
        status = "ready"

    required_count = sum(1 for requirement in requirements if requirement.required)
    if required_count <= 8:
        difficulty = "easy"
    elif required_count <= 16:
        difficulty = "medium"
    else:
        difficulty = "hard"

    estimated_time = max(10, required_count * 3 + len(missing_required_assets) * 5 + (len(missing_assets) - len(missing_required_assets)) * 2)
    return ApplicationReadiness(
        job_id=job.job_id,
        readiness_status=status,
        application_difficulty=difficulty,
        required_assets=required_assets,
        missing_assets=missing_assets,
        estimated_time_minutes=estimated_time,
        requirements=normalized_requirements,
    )
