from __future__ import annotations

import re
from collections import Counter

from .models import CandidateProfile, JobPosting, JobScore, Lane, Priority, RoleKeywordConfig, SponsorshipCompatibility


ROLE_KEYWORDS = {
    "role_fit": [
        "data scientist",
        "product data scientist",
        "experimentation",
        "measurement",
        "analytics",
        "causal inference",
        "ai tooling",
        "applied ai",
        "platform",
    ],
    "seniority": ["staff", "senior staff", "principal", "lead", "senior"],
    "domain": [
        "fintech",
        "consumer",
        "growth",
        "product",
        "experimentation",
        "ai",
        "developer",
        "platform",
        "data product",
    ],
    "ai_platform": [
        "agent",
        "llm",
        "semantic metrics",
        "self-serve",
        "internal tools",
        "developer productivity",
        "measurement platform",
        "workflow",
    ],
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    normalized = _normalize(text)
    return [keyword for keyword in keywords if keyword.lower() in normalized]


def _exclusion_hits(text: str, keywords: list[str]) -> list[str]:
    normalized = _normalize(text)
    hits: list[str] = []
    for keyword in keywords:
        normalized_keyword = keyword.lower().strip()
        if not normalized_keyword:
            continue
        if " " in normalized_keyword:
            if normalized_keyword in normalized:
                hits.append(keyword)
            continue
        if re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized):
            hits.append(keyword)
    return hits


def _location_score(job: JobPosting, profile: CandidateProfile) -> tuple[int, list[str]]:
    preferences = profile.preferences.location_preferences
    location_text = _normalize(" ".join(filter(None, [job.location, job.remote_policy])))
    matches: list[str] = []
    if not location_text:
        return 5, ["Location missing from posting."]

    for label, points in (("preferred", 10), ("acceptable_hybrid", 7)):
        values = preferences.get(label, [])
        for value in values:
            if value.lower() in location_text:
                matches.append(f"{label.replace('_', ' ')} match: {value}")
                return points, matches
    for value in preferences.get("avoid", []):
        if value.lower() in location_text:
            return 2, [f"Location conflicts with preference: {value}"]
    if "remote" in location_text:
        return 8, ["Remote role matches preferred geography flexibility."]
    return 5, ["Location is possible but not a strong stated preference."]


def _profile_requires_sponsorship(profile: CandidateProfile) -> bool:
    work_authorization = _normalize(str(profile.preferences.candidate.get("work_authorization", "")))
    return any(
        token in work_authorization
        for token in ["h1b", "sponsorship", "visa_transfer_required", "transfer_required"]
    )


def _sponsorship_compatibility(
    job: JobPosting,
    profile: CandidateProfile,
) -> tuple[int, SponsorshipCompatibility, list[str], list[str], list[str]]:
    text = _normalize(f"{job.title} {job.description}")
    positive: list[str] = []
    risks: list[str] = []
    disqualifying_reasons: list[str] = []
    requires_sponsorship = _profile_requires_sponsorship(profile)

    negative_patterns = [
        r"\bno sponsorship\b",
        r"\bwithout sponsorship\b",
        r"\bwill not sponsor\b",
        r"\bdoes not sponsor\b",
        r"\bdoes not provide sponsorship\b",
        r"\bnot eligible for sponsorship\b",
        r"\bnot support visa sponsorship\b",
        r"\bmust be authorized to work .* without (the need for )?(visa )?sponsorship\b",
    ]
    if any(re.search(pattern, text) for pattern in negative_patterns):
        if requires_sponsorship:
            reason = "Posting states that visa sponsorship or immigration sponsorship is not supported."
            disqualifying_reasons.append(reason)
            risks.append(reason)
            return 0, "incompatible", positive, risks, disqualifying_reasons
        positive.append("Posting says sponsorship is not offered, but the profile does not require it.")
        return 8, "compatible", positive, risks, disqualifying_reasons

    if "visa" in text or "sponsorship" in text or "h1b" in text:
        positive.append("Posting references visa or sponsorship explicitly.")
        return 8, "compatible", positive, risks, disqualifying_reasons

    if requires_sponsorship:
        risks.append("Visa support is not explicit in the posting.")
        return 5, "unknown", positive, risks, disqualifying_reasons
    return 5, "compatible", positive, risks, disqualifying_reasons


def _priority_from_score(score: int) -> Priority:
    if score >= 85:
        return "must_apply"
    if score >= 70:
        return "apply"
    if score >= 50:
        return "maybe"
    return "skip"


def _title_role_adjustment(title: str) -> tuple[int, list[str], list[str]]:
    title_text = _normalize(title)
    positives: list[str] = []
    risks: list[str] = []

    if "data scientist" in title_text or "applied data science" in title_text:
        positives.append("Title is directly aligned with the target Data Scientist path.")
        return 30, positives, risks
    if "data science" in title_text or "people research data scientist" in title_text:
        positives.append("Title is directly aligned with the target Data Scientist path.")
        return 24, positives, risks
    if "data engineer" in title_text or "analytics engineer" in title_text:
        positives.append("Title is adjacent to the target Data Scientist path.")
        return 14, positives, risks
    if "software engineer" in title_text or "systems engineer" in title_text or "engineering manager" in title_text:
        risks.append("Title is software-engineering oriented rather than a direct Data Scientist role.")
        return -16, positives, risks
    return 0, positives, risks


def choose_lane(job: JobPosting, role_keywords: RoleKeywordConfig) -> Lane:
    text = _normalize(f"{job.title} {job.description}")
    experimentation_hits = 0
    ai_tooling_hits = 0
    main_hits = 0

    for keyword in role_keywords.include_keywords.get("experimentation", []):
        if keyword.lower() in text:
            experimentation_hits += 2
            main_hits += 1
    for keyword in role_keywords.include_keywords.get("ai_tooling", []):
        if keyword.lower() in text:
            ai_tooling_hits += 2
            main_hits += 1
    for keyword in role_keywords.include_keywords.get("platform", []):
        if keyword.lower() in text:
            main_hits += 1
            experimentation_hits += 1
            ai_tooling_hits += 1

    if experimentation_hits > ai_tooling_hits and experimentation_hits >= main_hits:
        return "Experimentation"
    if ai_tooling_hits > experimentation_hits and ai_tooling_hits >= main_hits:
        return "AI Tooling"
    return "Main"


def resume_version_for_lane(lane: Lane, profile: CandidateProfile) -> str:
    mapping = {
        "Main": profile.main_resume_path,
        "Experimentation": profile.experimentation_resume_path,
        "AI Tooling": profile.ai_tooling_resume_path,
    }
    target = mapping[lane]
    return target.name if target else f"{lane.lower().replace(' ', '_')}_resume.md"


def generate_tailoring_notes(job: JobPosting, lane: Lane, hits: Counter[str]) -> list[str]:
    notes = [
        f"Open with a summary aligned to {job.company}'s {job.title} scope.",
        f"Use the {lane} resume lane as the base version.",
    ]
    if hits["experimentation"]:
        notes.append("Move experimentation, causal inference, and metric-design bullets higher.")
    if hits["ai_tooling"]:
        notes.append("Highlight AI tooling, agents, or workflow automation outcomes early.")
    if hits["platform"]:
        notes.append("Emphasize platform, semantic metrics, and self-serve tooling work.")
    if "developer" in _normalize(job.description):
        notes.append("Translate internal tooling impact into developer productivity and adoption language.")
    return notes


def generate_referral_message(job: JobPosting, score: JobScore) -> str:
    return (
        f"Hi {{name}}, I came across the {job.title} role at {job.company} and it looks closely aligned "
        f"with my background in experimentation, product measurement, and AI-enabled platform work. "
        f"I've led company-wide experimentation programs, built self-serve analytics tooling, and shipped "
        f"internal AI workflows. If you're open to it, I'd appreciate a quick conversation about the team "
        f"and whether a referral would make sense. Based on my review this role is a {score.priority.replace('_', ' ')} fit."
    )


def score_job(job: JobPosting, profile: CandidateProfile, role_keywords: RoleKeywordConfig) -> JobScore:
    text = _normalize(f"{job.title} {job.description}")
    title_text = _normalize(job.title)
    hits = Counter[str]()

    exclude_hits = _exclusion_hits(text, role_keywords.exclude_keywords)
    role_fit_hits = _keyword_hits(text, ROLE_KEYWORDS["role_fit"])
    seniority_hits = _keyword_hits(title_text, ROLE_KEYWORDS["seniority"])
    domain_hits = _keyword_hits(text, ROLE_KEYWORDS["domain"])
    ai_platform_hits = _keyword_hits(text, ROLE_KEYWORDS["ai_platform"])

    for keyword in role_keywords.include_keywords.get("experimentation", []):
        if keyword.lower() in text:
            hits["experimentation"] += 1
    for keyword in role_keywords.include_keywords.get("ai_tooling", []):
        if keyword.lower() in text:
            hits["ai_tooling"] += 1
    for keyword in role_keywords.include_keywords.get("platform", []):
        if keyword.lower() in text:
            hits["platform"] += 1

    role_fit = min(30, 10 + len(role_fit_hits) * 3 + hits["experimentation"] * 2 + hits["ai_tooling"] * 2)
    seniority_fit = min(20, 6 + len(seniority_hits) * 7)
    if "staff" not in title_text and "principal" not in title_text and "lead" not in title_text and "senior" not in title_text:
        seniority_fit = max(0, seniority_fit - 8)
    domain_fit = min(15, 4 + len(domain_hits) * 2)
    ai_platform_fit = min(15, 3 + len(ai_platform_hits) * 2 + hits["platform"] + hits["ai_tooling"])
    comp_location_fit, location_reasons = _location_score(job, profile)
    visa_fit, sponsorship_compatibility, visa_reasons, visa_risks, disqualifying_reasons = _sponsorship_compatibility(
        job, profile
    )
    title_adjustment, title_positive_reasons, title_risks = _title_role_adjustment(job.title)

    total = role_fit + seniority_fit + domain_fit + ai_platform_fit + comp_location_fit + visa_fit + title_adjustment
    if exclude_hits:
        total = max(0, total - 25)
    total = min(100, max(0, total))

    lane = choose_lane(job, role_keywords)
    resume_version = resume_version_for_lane(lane, profile)
    priority = _priority_from_score(total)

    why_fit = []
    why_fit.extend(title_positive_reasons[:1])
    if role_fit_hits:
        why_fit.append(f"Role language overlaps with target strengths: {', '.join(role_fit_hits[:4])}.")
    if seniority_hits:
        why_fit.append(f"Seniority signal in title: {', '.join(seniority_hits)}.")
    if hits["experimentation"]:
        why_fit.append("Posting emphasizes experimentation or measurement rigor.")
    if hits["ai_tooling"]:
        why_fit.append("Posting values AI tooling, agents, or workflow automation.")
    if hits["platform"]:
        why_fit.append("Platform and self-serve analytics themes are present.")
    why_fit.extend(location_reasons[:1])
    why_fit.extend(visa_reasons[:1])

    risks = []
    if exclude_hits:
        risks.append(f"Exclusion keywords detected: {', '.join(exclude_hits[:4])}.")
    risks.extend(title_risks[:1])
    if seniority_fit < 10:
        risks.append("Seniority signal is weaker than a Staff or Principal target.")
    if ai_platform_fit < 6 and lane == "AI Tooling":
        risks.append("AI tooling evidence is limited relative to the preferred lane.")
    risks.extend(visa_risks[:1])

    if sponsorship_compatibility == "incompatible":
        total = 0
        priority = "skip"
        risks = disqualifying_reasons + risks
        reason_summary = (
            f"{job.company} {job.title} is not relevant because the posting says sponsorship is not supported "
            "and the profile requires immigration sponsorship."
        )
        tailoring_notes = ["Do not pursue unless the employer confirms immigration sponsorship is available."]
    else:
        reason_summary = (
            f"{job.company} {job.title} scored {total}/100 with strongest alignment in "
            f"{lane.lower()} work and the main tradeoffs around {', '.join(risks[:2]).lower() if risks else 'execution detail'}."
        )
        tailoring_notes = generate_tailoring_notes(job, lane, hits)

    return JobScore(
        job_id=job.job_id,
        fit_score=total,
        priority="skip" if sponsorship_compatibility == "incompatible" else priority,
        lane=lane,
        resume_version=resume_version,
        reason_summary=reason_summary,
        why_fit=why_fit or ["General overlap with senior data science and platform work."],
        risks=risks or ["Review exact scope, compensation, and sponsorship expectations manually."],
        tailoring_notes=tailoring_notes,
        referral_recommended=sponsorship_compatibility != "incompatible" and priority in {"must_apply", "apply"},
        sponsorship_compatibility=sponsorship_compatibility,
        disqualifying_reasons=disqualifying_reasons,
    )
