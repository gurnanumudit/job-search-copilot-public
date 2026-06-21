from __future__ import annotations

import re
import shutil
import json
from html import escape
from datetime import datetime, timezone
from pathlib import Path

from .answers import draft_additional_information, draft_cover_letter, export_control_response
from .models import ApplicationReadiness, ApplicationRequirement, CandidateProfile, GeneratedPackage, JobPosting, JobScore
from .paths import ProjectPaths
from .profile import answer_bank_map
from .scoring import generate_referral_message
from .storage import save_text


def _candidate_resume_filename(profile: CandidateProfile) -> str:
    name = str(profile.preferences.candidate.get("name", "Candidate")).strip() or "Candidate"
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return f"{normalized or 'Candidate'}_Resume.md"


def _candidate_resume_pdf_filename(profile: CandidateProfile) -> str:
    name = str(profile.preferences.candidate.get("name", "Candidate")).strip() or "Candidate"
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return f"{normalized or 'Candidate'}_Resume.pdf"


def _candidate_cover_letter_pdf_filename(profile: CandidateProfile) -> str:
    name = str(profile.preferences.candidate.get("name", "Candidate")).strip() or "Candidate"
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return f"{normalized or 'Candidate'}_Cover_Letter.pdf"


def _base_resume_path(paths: ProjectPaths, score: JobScore) -> Path:
    return paths.profile_dir / score.resume_version


def _master_resume_path(paths: ProjectPaths) -> Path:
    return paths.profile_dir / "master_resume.md"


def _company_resume_dir(paths: ProjectPaths, job: JobPosting) -> Path:
    folder_name = re.sub(r"[^A-Za-z0-9]+", "_", job.company).strip("_") or "Company"
    return paths.resumes_dir / folder_name


def _polished_resume_pdf_source(paths: ProjectPaths, score: JobScore) -> Path | None:
    source_by_resume_version = {
        "ai_tooling_resume.md": "Example Candidate Applied AI Resume.pdf",
        "experimentation_resume.md": "Example Candidate Experimentation Resume.pdf",
        "main_resume.md": "Example_Candidate_Resume.pdf",
    }
    source_name = source_by_resume_version.get(score.resume_version)
    if not source_name:
        return None
    source_path = paths.resumes_dir / source_name
    return source_path if source_path.exists() else None


def _underscore_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "job"


def _application_dir(paths: ProjectPaths, job: JobPosting) -> Path:
    folder_name = f"{_underscore_slug(job.company)}_{_underscore_slug(job.title)}"
    return paths.outputs_applications_dir / folder_name


def _save_text_if_absent(path: Path, content: str) -> Path:
    if path.exists():
        return path
    return save_text(path, content)


def _candidate_contact_line(profile: CandidateProfile) -> str:
    candidate = profile.preferences.candidate
    parts = [
        str(candidate.get("current_location") or "").strip(),
        str(candidate.get("phone") or "").strip(),
        str(candidate.get("email") or "").strip(),
        str(candidate.get("linkedin") or "").replace("https://www.", "").replace("https://", "").strip(),
    ]
    return " | ".join(part for part in parts if part)


def _recommended_action(score: JobScore, readiness: ApplicationReadiness | None) -> str:
    if score.sponsorship_compatibility == "incompatible":
        return "Skip"
    if readiness:
        if readiness.readiness_status == "needs_prep":
            return "Prep before apply"
        if readiness.readiness_status == "needs_review":
            if score.referral_recommended:
                return "Review and apply with referral"
            return "Review before apply"
        if readiness.readiness_status == "ready":
            if score.referral_recommended:
                return "Apply with referral"
            return "Apply"
        if readiness.readiness_status == "blocked":
            return "Blocked"
    if score.priority in {"must_apply", "apply"}:
        if score.referral_recommended:
            return "Inspect application and apply with referral"
        return "Inspect application"
    if score.priority == "maybe":
        return "Review if interested"
    return "Skip"


def _render_tailored_resume(
    job: JobPosting,
    score: JobScore,
    profile: CandidateProfile,
    base_resume_text: str,
) -> tuple[str, list[str]]:
    changes_made = [
        f"Preserved the base resume content from {score.resume_version} without changing factual claims.",
        f"Added a targeted draft header for {job.company} - {job.title}.",
        "Added emphasis notes derived from the job description and tailoring guidance for manual review.",
    ]
    lines = [
        f"# Tailored Resume Draft - {job.company} {job.title}",
        "",
        f"Base Resume Used: {score.lane}",
        "Review Status: Draft for manual review before sending.",
        "",
        "## Targeted Summary Emphasis",
    ]
    lines.extend(f"- {item}" for item in score.why_fit[:4])
    lines.extend(["", "## Tailoring Priorities"])
    lines.extend(f"- {item}" for item in score.tailoring_notes)
    lines.extend(
        [
            "",
            "## Source Resume",
            "",
            base_resume_text.rstrip(),
            "",
        ]
    )
    return "\n".join(lines), changes_made


def _markdown_section(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    heading_line = f"## {heading}".lower()
    capture = False
    captured: list[str] = []
    for line in lines:
        normalized = line.strip().lower()
        if normalized.startswith("## "):
            if capture:
                break
            capture = normalized == heading_line
            continue
        if capture:
            captured.append(line)
    return [line for line in captured if line.strip()]


def _render_final_resume_markdown(
    job: JobPosting,
    score: JobScore,
    profile: CandidateProfile,
    base_resume_text: str,
) -> str:
    candidate = profile.preferences.candidate
    name = str(candidate.get("name") or "Candidate").strip()
    contact = _candidate_contact_line(profile)
    lines = [f"# {name}"]
    if contact:
        lines.extend(["", contact])

    lines.extend(["", "## Summary"])
    summary = _markdown_section(base_resume_text, "Candidate Summary")
    if summary:
        lines.extend(summary)
    else:
        lines.append("Senior Data Scientist focused on experimentation, product measurement, analytics platforms, and AI-enabled workflows.")

    lines.extend(["", f"## Targeted Fit - {job.company} {job.title}"])
    lines.extend(f"- {item}" for item in score.why_fit[:4])

    for heading in ["Experimentation Positioning", "AI Tooling Positioning", "Core Strengths", "Selected Proof Points", "Technical Strengths"]:
        section = _markdown_section(base_resume_text, heading)
        if section:
            lines.extend(["", f"## {heading}"])
            lines.extend(section)

    return "\n".join(lines).strip() + "\n"


def _markdown_to_pdf(markdown_text: str, output_path: Path) -> Path:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("reportlab is required to generate final resume PDFs. Install project dependencies first.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles["Title"].fontName = "Times-Bold"
    styles["Title"].fontSize = 16
    styles["Title"].leading = 17
    styles["Title"].alignment = 1
    styles["Heading2"].fontName = "Times-Bold"
    styles["Heading2"].fontSize = 10.8
    styles["Heading2"].leading = 11.3
    styles["BodyText"].fontName = "Times-Roman"
    styles["BodyText"].fontSize = 8.35
    styles["BodyText"].leading = 9.15
    styles["Bullet"].fontName = "Times-Roman"
    styles["Bullet"].fontSize = 8.05
    styles["Bullet"].leading = 8.85
    styles["Bullet"].leftIndent = 9
    styles["Bullet"].bulletIndent = 2
    styles["Bullet"].spaceAfter = 0
    styles["Italic"].fontName = "Times-Italic"
    styles["Italic"].fontSize = 8.35
    styles["Italic"].leading = 9.15

    contact_style = styles["BodyText"].clone("Contact")
    contact_style.alignment = 1
    contact_style.fontSize = 8.6
    contact_style.leading = 9.4
    company_style = styles["BodyText"].clone("Company")
    company_style.fontName = "Times-Bold"
    company_style.fontSize = 9.25
    company_style.leading = 9.8
    right_style = styles["BodyText"].clone("Right")
    right_style.alignment = 2
    role_style = styles["Italic"].clone("Role")
    role_style.fontName = "Times-BoldItalic"
    role_style.fontSize = 8.25
    role_style.leading = 8.9
    date_style = styles["Italic"].clone("Date")
    date_style.alignment = 2
    date_style.fontSize = 8.25
    date_style.leading = 8.9

    def row(left: str, right: str, left_style, right_style_obj, top_padding: float = 0) -> Table:
        table = Table(
            [[Paragraph(escape(left), left_style), Paragraph(escape(right), right_style_obj)]],
            colWidths=[5.85 * inch, 1.55 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), top_padding),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return table

    def contact_markup(line: str) -> str:
        if "LinkedIn" not in line:
            return escape(line)
        return escape(line).replace("LinkedIn", '<font color="#1155cc"><u>LinkedIn</u></font>')

    story: list = []
    in_header = True
    pending_blank = False
    section_count = 0
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            pending_blank = True
            continue
        if line.startswith("# "):
            story.append(Paragraph(escape(line[2:].strip()), styles["Title"]))
            pending_blank = False
            continue
        if line.startswith("## "):
            in_header = False
            if section_count:
                story.append(Spacer(1, 0.035 * inch))
            story.append(Paragraph(escape(line[3:].strip().upper()), styles["Heading2"]))
            story.append(HRFlowable(width="100%", thickness=0.65, color="black", spaceBefore=0, spaceAfter=1))
            section_count += 1
            pending_blank = False
            continue
        if line.startswith("### "):
            left, _, right = line[4:].partition("|")
            if pending_blank:
                story.append(Spacer(1, 0.018 * inch))
            story.append(row(left.strip(), right.strip(), company_style, right_style))
            pending_blank = False
            continue
        if line.startswith("#### "):
            left, _, right = line[5:].partition("|")
            story.append(row(left.strip(), right.strip(), role_style, date_style))
            pending_blank = False
            continue
        if line.startswith("- "):
            story.append(Paragraph(escape(line[2:].strip()), styles["Bullet"], bulletText="•"))
            pending_blank = False
            continue
        if in_header:
            story.append(Paragraph(contact_markup(line), contact_style))
        else:
            story.append(Paragraph(escape(line), styles["BodyText"]))
        pending_blank = False

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.34 * inch,
        rightMargin=0.34 * inch,
        topMargin=0.28 * inch,
        bottomMargin=0.28 * inch,
    )
    document.build(story)
    return output_path


def _application_answer_text(
    requirement: ApplicationRequirement,
    final_resume_pdf_path: Path | None = None,
    cover_letter_pdf_path: Path | None = None,
) -> str:
    label = requirement.field_label.lower()
    if final_resume_pdf_path and "resume" in label:
        return f"Use {final_resume_pdf_path}"
    if cover_letter_pdf_path and ("cover letter" in label or "letter of interest" in label):
        return f"Use {cover_letter_pdf_path}"
    return requirement.draft_answer or "Missing"


def _render_application_answers(
    readiness: ApplicationReadiness | None,
    final_resume_pdf_path: Path | None = None,
    cover_letter_pdf_path: Path | None = None,
) -> str:
    if not readiness:
        return (
            "# Application Answers\n\n"
            "Application form has not been inspected yet. Run packaging with an application URL to collect fields and draft answers.\n"
        )

    lines = [
        "# Application Answers",
        "",
        f"Readiness: {readiness.readiness_status}",
        f"Estimated Time: {readiness.estimated_time_minutes} minutes",
        "",
    ]
    if not readiness.requirements:
        lines.append("No application fields were detected during inspection.")
        lines.append("")
        return "\n".join(lines)

    for requirement in readiness.requirements:
        lines.extend(
            [
                f"## {requirement.field_label}",
                f"Required: {'Yes' if requirement.required else 'No'}",
                f"Field Type: {requirement.field_type or 'unknown'}",
                f"Draft Answer: {_application_answer_text(requirement, final_resume_pdf_path, cover_letter_pdf_path)}",
                f"Confidence: {requirement.answer_confidence}",
                f"Missing: {'Yes' if requirement.missing_input else 'No'}",
                f"Notes: {requirement.notes or 'None'}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_application_plan_json(
    job: JobPosting,
    score: JobScore,
    profile: CandidateProfile,
    readiness: ApplicationReadiness | None,
    final_resume_pdf_path: Path,
    additional_information_path: Path,
    cover_letter_path: Path,
    cover_letter_pdf_path: Path,
) -> str:
    candidate = profile.preferences.candidate
    defaults = candidate.get("voluntary_self_identification") if isinstance(candidate.get("voluntary_self_identification"), dict) else {}
    parsed_answer_bank = answer_bank_map(profile)
    application_answer_bank = {
        "legal_name": candidate.get("legal_name") or candidate.get("name"),
        "preferred_name": candidate.get("preferred_name"),
        "email": candidate.get("email"),
        "phone": candidate.get("phone"),
        "linkedin": candidate.get("linkedin"),
        "github": candidate.get("github"),
        "portfolio": candidate.get("portfolio"),
        "current_company": candidate.get("current_company"),
        "country": candidate.get("country") or "United States",
        "current_location": candidate.get("current_location"),
        "relocation": candidate.get("relocation_note"),
        "start_date": candidate.get("start_date_or_availability"),
        "timeline_considerations": candidate.get("deadline_note")
        or "No additional deadlines beyond my target start timing.",
        "work_authorization": parsed_answer_bank.get("Work Authorization") or "Yes",
        "sponsorship": candidate.get("sponsorship_note") or "Yes, immigration sponsorship required",
        "office_in_person_25": "Yes"
        if candidate.get("willing_sf_hybrid_3_days_per_week") is True or candidate.get("willing_seattle_hybrid") is True
        else None,
        "sf_hybrid": "Yes" if candidate.get("willing_sf_hybrid_3_days_per_week") is True else None,
        "seattle_hybrid": "Yes" if candidate.get("willing_seattle_hybrid") is True else None,
        "ai_policy": "Yes",
        "previously_interviewed": "Yes"
        if isinstance(candidate.get("previously_interviewed_companies"), list)
        and job.company in (candidate.get("previously_interviewed_companies") or [])
        else "No",
        "salary_expectations": parsed_answer_bank.get("Salary Expectations"),
        "why_looking": parsed_answer_bank.get("Why Are You Looking?"),
        "application_source": parsed_answer_bank.get("Application Source") or "LinkedIn",
        "distributed_teams_experience": parsed_answer_bank.get("Remote / Distributed Teams Experience"),
        "export_control": export_control_response(profile),
        "gender": defaults.get("gender") if defaults else None,
        "hispanic_latino": (defaults.get("hispanic_latino") or "No") if defaults else None,
        "race": defaults.get("race") if defaults else None,
        "veteran_status": defaults.get("veteran_status") if defaults else None,
        "disability_status": defaults.get("disability_status") if defaults else None,
    }
    fill_fields = [
        {"field": "Legal Name", "value": candidate.get("name"), "category": "identity"},
        {"field": "Preferred Name", "value": candidate.get("preferred_name"), "category": "identity"},
        {"field": "Email", "value": candidate.get("email"), "category": "identity"},
        {"field": "Phone Number", "value": candidate.get("phone"), "category": "identity"},
        {"field": "LinkedIn", "value": candidate.get("linkedin"), "category": "identity"},
        {"field": "Current Company", "value": candidate.get("current_company"), "category": "identity"},
        {"field": "Current Location", "value": candidate.get("current_location"), "category": "location"},
        {"field": "Start Date / Availability", "value": candidate.get("start_date_or_availability"), "category": "availability"},
        {"field": "Work Authorization", "value": "Yes", "category": "legal_review"},
        {"field": "Sponsorship Required", "value": "Yes, immigration sponsorship required", "category": "legal_review"},
        {
            "field": "US Office Three Days Per Week",
            "value": "Yes" if candidate.get("willing_sf_hybrid_3_days_per_week") is True else None,
            "category": "location_commitment",
        },
        {"field": "Additional Information", "value_file": str(additional_information_path), "category": "narrative"},
        {"field": "Cover Letter", "value_file": str(cover_letter_path), "category": "narrative"},
    ]
    if defaults:
        fill_fields.extend(
            [
                {"field": "Gender", "value": defaults.get("gender"), "category": "voluntary_self_identification"},
                {"field": "Race", "value": defaults.get("race"), "category": "voluntary_self_identification"},
                {"field": "Veteran Status", "value": defaults.get("veteran_status"), "category": "voluntary_self_identification"},
                {"field": "Disability Status", "value": defaults.get("disability_status"), "category": "voluntary_self_identification"},
            ]
        )
    plan = {
        "job": {
            "company": job.company,
            "title": job.title,
            "job_id": job.job_id,
            "job_url": job.job_url,
            "apply_url": job.apply_url or job.job_url,
            "ats": job.ats,
            "fit_score": score.fit_score,
            "resume_lane": score.lane,
        },
        "artifacts": {
            "resume_pdf": str(final_resume_pdf_path),
            "additional_information": str(additional_information_path),
            "cover_letter": str(cover_letter_path),
            "cover_letter_pdf": str(cover_letter_pdf_path),
        },
        "automation_policy": {
            "fill_optional_narrative_fields": True,
            "upload_resume_when_file_input_is_available": True,
            "submit_requires_explicit_user_approval": True,
            "captcha_requires_user_confirmation": True,
            "legal_acknowledgements_require_review": True,
        },
        "fill_fields": [item for item in fill_fields if item.get("value") or item.get("value_file")],
        "answer_bank": {key: value for key, value in application_answer_bank.items() if value},
        "detected_requirements": [
            requirement.model_copy(
                update={"draft_answer": _application_answer_text(requirement, final_resume_pdf_path, cover_letter_pdf_path)}
            ).model_dump(mode="json")
            for requirement in readiness.requirements
        ]
        if readiness
        else [],
        "review_before_submit": [
            "Confirm resume upload succeeded and the visible filename matches the resume_pdf artifact.",
            "Review generated Additional Information and Cover Letter text before submission.",
            "Confirm legal acknowledgements, work authorization, sponsorship, and location commitments.",
            "Do not submit until the user explicitly approves final submission.",
        ],
    }
    return json.dumps(plan, indent=2) + "\n"


def _render_resume_provenance(
    job: JobPosting,
    score: JobScore,
    base_resume_path: Path,
    tailored_resume_path: Path,
    final_resume_pdf_path: Path,
    polished_resume_pdf_source: Path | None,
    final_resume_decision: list[str],
) -> str:
    return "\n".join(
        [
            "# Resume Provenance",
            "",
            f"Generated At: {datetime.now(timezone.utc).isoformat()}",
            f"Company: {job.company}",
            f"Role: {job.title}",
            f"Job URL: {job.job_url}",
            f"Apply URL: {job.apply_url or job.job_url}",
            f"Selected Lane: {score.lane}",
            f"Base Resume Version: {score.resume_version}",
            f"Base Resume Path: {base_resume_path}",
            f"Generated Resume Path: {tailored_resume_path}",
            f"Polished Resume PDF Source: {polished_resume_pdf_source or 'Not available; generated from markdown fallback.'}",
            f"Final Resume PDF Path: {final_resume_pdf_path}",
            f"Fit Score At Generation: {score.fit_score}",
            f"Sponsorship Compatibility: {score.sponsorship_compatibility}",
            "",
            "## Final Resume Decision",
            *[f"- {item}" for item in final_resume_decision],
            "",
            "## Notes",
            "- The final upload PDF should preserve the polished resume aesthetic.",
            "- A company folder copy is not automatically a bespoke content version.",
            "- Tailoring guidance is kept separately unless it is explicitly promoted into a reviewed resume edit.",
            "- Review the generated resume before submitting an application.",
            "",
        ]
    )


def build_package_report(
    paths: ProjectPaths,
    job: JobPosting,
    score: JobScore,
    profile: CandidateProfile,
    readiness: ApplicationReadiness | None = None,
) -> GeneratedPackage:
    referral_message = generate_referral_message(job, score)
    tailoring_notes = score.tailoring_notes
    application_dir = _application_dir(paths, job)
    base_resume_path = _base_resume_path(paths, score)
    base_resume_text = base_resume_path.read_text(encoding="utf-8") if base_resume_path.exists() else "# Resume draft\n"
    tailored_resume_text, changes_made = _render_tailored_resume(job, score, profile, base_resume_text)
    tailored_resume_path = save_text(application_dir / _candidate_resume_filename(profile), tailored_resume_text)
    master_resume_path = _master_resume_path(paths)
    final_resume_markdown = (
        master_resume_path.read_text(encoding="utf-8")
        if master_resume_path.exists()
        else _render_final_resume_markdown(job, score, profile, base_resume_text)
    )
    company_resume_dir = _company_resume_dir(paths, job)
    final_resume_source_path = save_text(company_resume_dir / "Example_Candidate_Resume_Source.md", final_resume_markdown)
    final_resume_pdf_path = company_resume_dir / _candidate_resume_pdf_filename(profile)
    polished_resume_pdf_source = _polished_resume_pdf_source(paths, score)
    if polished_resume_pdf_source:
        final_resume_pdf_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(polished_resume_pdf_source, final_resume_pdf_path)
        final_resume_decision = [
            f"Selected the polished {score.lane} resume PDF because this role matched that lane.",
            "Made no job-specific content edits to the final upload PDF.",
            "Created the company-folder copy only for application organization and upload safety.",
            "A bespoke resume version should require explicit, role-specific edits plus review before use.",
        ]
    else:
        final_resume_pdf_path = _markdown_to_pdf(final_resume_markdown, final_resume_pdf_path)
        final_resume_decision = [
            f"Selected the {score.lane} resume lane because this role matched that lane.",
            "Generated the final upload PDF from markdown because no polished source PDF was available.",
            "Review visual formatting carefully before using this fallback PDF for an application.",
        ]
    resume_provenance_path = save_text(
        application_dir / "resume_provenance.md",
        _render_resume_provenance(
            job,
            score,
            base_resume_path,
            tailored_resume_path,
            final_resume_pdf_path,
            polished_resume_pdf_source,
            final_resume_decision,
        ),
    )
    additional_information_path = _save_text_if_absent(
        application_dir / "additional_information.md",
        "# Additional Information Draft\n\n" + draft_additional_information(job, profile, score) + "\n",
    )
    cover_letter_path = _save_text_if_absent(
        application_dir / "cover_letter.md",
        "# Cover Letter Draft\n\n" + draft_cover_letter(job, profile, score) + "\n",
    )
    cover_letter_pdf_path = _markdown_to_pdf(
        cover_letter_path.read_text(encoding="utf-8"),
        application_dir / _candidate_cover_letter_pdf_filename(profile),
    )
    application_answers_path = save_text(
        application_dir / "application_answers.md",
        _render_application_answers(readiness, final_resume_pdf_path, cover_letter_pdf_path),
    )
    application_plan_path = save_text(
        application_dir / "application_plan.json",
        _render_application_plan_json(
            job,
            score,
            profile,
            readiness,
            final_resume_pdf_path,
            additional_information_path,
            cover_letter_path,
            cover_letter_pdf_path,
        ),
    )
    referral_message_path = save_text(application_dir / "referral_message.md", referral_message + "\n")
    tailoring_notes_path = save_text(
        application_dir / "tailoring_notes.md",
        "\n".join(["# Tailoring Notes", ""] + [f"- {item}" for item in tailoring_notes]) + "\n",
    )
    suggested_next_action = _recommended_action(score, readiness)

    report_lines = [
        f"# {job.company} - {job.title}",
        "",
        f"Fit Score: {score.fit_score}",
        f"Priority: {score.priority}",
        f"Resume Version: {score.resume_version}",
        f"Sponsorship Compatibility: {score.sponsorship_compatibility}",
        f"Base Resume Used: {score.lane}",
        f"Base Resume Path: {base_resume_path}",
        f"Tailored Resume Path: {tailored_resume_path}",
        f"Final Resume PDF Path: {final_resume_pdf_path}",
        f"Resume Provenance Path: {resume_provenance_path}",
        f"Additional Information Path: {additional_information_path}",
        f"Cover Letter Path: {cover_letter_path}",
        f"Cover Letter PDF Path: {cover_letter_pdf_path}",
        f"Application Plan Path: {application_plan_path}",
        f"Application Packet Path: {application_dir}",
        f"Application Readiness: {readiness.readiness_status if readiness else 'not_inspected'}",
        f"Estimated Application Time: {readiness.estimated_time_minutes if readiness else 'N/A'} minutes",
        "",
        "## Resume Decision / Changes",
    ]
    report_lines.extend(f"- {item}" for item in final_resume_decision)
    report_lines.append(f"- Review draft notes are still available at {tailored_resume_path}.")
    report_lines.append(f"- Wrote final upload resume PDF to {final_resume_pdf_path}.")
    report_lines.append(f"- Wrote final upload resume source to {final_resume_source_path}.")
    report_lines.append(f"- Wrote default Additional Information draft to {additional_information_path}.")
    report_lines.append(f"- Wrote default Cover Letter draft to {cover_letter_path}.")
    report_lines.append(f"- Wrote default Cover Letter PDF to {cover_letter_pdf_path}.")
    report_lines.append(f"- Wrote AI/browser apply plan to {application_plan_path}.")
    report_lines.extend([
        "",
        "## Why Fit",
    ])
    report_lines.extend(f"- {item}" for item in score.why_fit)
    if score.disqualifying_reasons:
        report_lines.extend(["", "## Disqualifying Reasons"])
        report_lines.extend(f"- {item}" for item in score.disqualifying_reasons)
    report_lines.extend(["", "## Risks / Gaps"])
    report_lines.extend(f"- {item}" for item in score.risks)
    report_lines.extend(["", "## Resume Tailoring Notes"])
    report_lines.extend(f"- {item}" for item in tailoring_notes)

    report_lines.extend(
        [
            "",
            "## Application Requirements",
            "| Field | Required | Draft Answer | Confidence | Missing | Notes |",
            "|---|---|---|---|---|---|",
        ]
    )
    if readiness:
        for requirement in readiness.requirements:
            draft_answer = _application_answer_text(requirement, final_resume_pdf_path, cover_letter_pdf_path)
            report_lines.append(
                f"| {requirement.field_label} | {'Yes' if requirement.required else 'No'} | "
                f"{draft_answer.replace('|', '/')} | {requirement.answer_confidence} | "
                f"{'Yes' if requirement.missing_input else 'No'} | {(requirement.notes or 'None').replace('|', '/')} |"
            )
    else:
        report_lines.append("| Not inspected yet | N/A | N/A | missing | Yes | Provide an application URL or run inspect to collect real form requirements. |")

    report_lines.extend(["", "## Missing Prep"])
    if readiness:
        for item in readiness.missing_assets or ["No obvious missing assets detected."]:
            report_lines.append(f"- {item}")
    else:
        report_lines.append("- Inspect the real application form to identify missing prep items.")

    report_lines.extend(["", "## Referral Message", referral_message])
    report_lines.extend(["", "## Suggested Next Action", suggested_next_action])

    report_path = save_text(application_dir / "report.md", "\n".join(report_lines))
    save_text(paths.outputs_packages_dir / f"{job.job_id}_report.md", "\n".join(report_lines))
    save_text(paths.outputs_referrals_dir / f"{job.job_id}.md", referral_message + "\n")
    save_text(paths.outputs_tailoring_dir / f"{job.job_id}.md", "\n".join(f"- {item}" for item in tailoring_notes) + "\n")
    return GeneratedPackage(
        job=job,
        score=score,
        readiness=readiness,
        referral_message=referral_message,
        tailoring_notes=tailoring_notes,
        application_dir=application_dir,
        tailored_resume_path=tailored_resume_path,
        final_resume_pdf_path=final_resume_pdf_path,
        application_answers_path=application_answers_path,
        referral_message_path=referral_message_path,
        tailoring_notes_path=tailoring_notes_path,
        resume_provenance_path=resume_provenance_path,
        additional_information_path=additional_information_path,
        cover_letter_path=cover_letter_path,
        cover_letter_pdf_path=cover_letter_pdf_path,
        application_plan_path=application_plan_path,
        report_path=report_path,
    )
