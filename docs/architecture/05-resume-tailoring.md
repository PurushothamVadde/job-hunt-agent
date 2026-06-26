# Resume Tailoring Pipeline

Triggered when the user says "tailor my resume for #3" or provides a job URL directly. Produces an ATS-safe PDF customised for that specific job, gated by a HITL preview before writing to disk.

## Flow Diagram

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ffedd5', 'mainBkg': '#ffffff', 'nodeBorder': '#000000', 'clusterBkg': '#fff7ed'}}}%%
flowchart TD
    START(["User: 'tailor my resume for #3' or job URL"])

    subgraph Step1["Step 1 — Fetch and Parse Job Description"]
        FETCH["httpx GET job description URL"]
        PARSE["BeautifulSoup extract text"]
        EXTRACT["GPT-4o extracts:\n- Required skills\n- Preferred skills\n- Seniority level\n- Role keywords\n- Company tone"]
    end

    subgraph Step2["Step 2 — Gap Analysis"]
        DIFF["Diff JD keywords vs master resume"]
        MATCHED["matched_keywords\nalready present in resume"]
        MISS_REQ["missing_required\nrequired in JD, absent from resume"]
        MISS_PREF["missing_preferred\npreferred in JD, absent from resume"]
    end

    subgraph Step3["Step 3 — Resume Rewrite"]
        REWRITE["GPT-4o rewrites:\n- Summary: mirror JD tone + top 3 keywords\n- Bullets: inject keywords, quantify impact\n- Skills: JD-matched skills listed first"]
        KEEP["Untouched sections:\ncontact · education · certifications"]
    end

    subgraph Step4["Step 4 — ATS PDF Export"]
        JINJA["Jinja2 render ats_resume.html\n(single-column, Arial 11pt, 0.75in margins)"]
        WEASY["WeasyPrint HTML → PDF"]
        SAVE["Save to resumes/user_id/tailored/\nFilename: company_role_YYYYMMDD_resume.pdf"]
    end

    subgraph Step5["Step 5 — HITL Preview"]
        PREVIEW["Stream hitl_request event:\ngap_analysis + plain-text preview of changes"]
        DECISION{"User decision"}
        WRITE["Write PDF to disk\nStore path in SQLite applications"]
        REVISE["GPT-4o revises based on\nuser correction text\nLoop back to preview"]
        ABORT["No file written\nUser notified"]
    end

    START --> FETCH --> PARSE --> EXTRACT
    EXTRACT --> DIFF
    DIFF --> MATCHED & MISS_REQ & MISS_PREF
    MATCHED & MISS_REQ & MISS_PREF --> REWRITE
    KEEP -.->|preserved as-is| REWRITE
    REWRITE --> JINJA --> WEASY --> SAVE --> PREVIEW --> DECISION
    DECISION -- "approve" --> WRITE
    DECISION -- "edit"    --> REVISE --> PREVIEW
    DECISION -- "reject"  --> ABORT
```

## Gap Analysis Output

| List | Meaning | Action |
|------|---------|--------|
| `matched_keywords` | Keyword already in master resume | Reorder to appear earlier |
| `missing_required` | Required by JD, missing from resume | Add naturally to bullets/summary |
| `missing_preferred` | Preferred by JD, missing from resume | Add if it can be supported by experience |

## ATS PDF Specification

The Jinja2 template (`ats_resume.html`) enforces these constraints so the PDF passes ATS scanners:

| Property | Value |
|----------|-------|
| Layout | Single column — no tables, text boxes, or columns |
| Font | Arial or Helvetica |
| Body size | 11pt |
| Heading size | 14pt |
| Margins | 0.75 inch all sides |
| Text | All content in `<p>` / `<ul>` tags — fully selectable |
| Section order | Contact → Summary → Skills → Experience → Education → Certifications → Projects |
| Bullets | Plain hyphen (`-`) or Unicode bullet (`•`) |
| Excluded | Headers, footers, page numbers, graphics, columns, decorative borders |

## HITL Event Payload

```json
{
  "type": "hitl_request",
  "action": "write_resume",
  "details": {
    "gap_analysis": {
      "matched_keywords": ["Python", "FastAPI"],
      "missing_required": ["Kubernetes", "Terraform"],
      "missing_preferred": ["Go"]
    },
    "preview": "SUMMARY: Results-driven engineer with 5 years Python...\n\nTOP CHANGES:\n• Added 'Kubernetes' to infra bullet at Acme Corp\n..."
  }
}
```

## Output Storage

```
resumes/
  {user_id}/
    tailored/
      stripe_sre_20250615_resume.pdf
      google_ml_20250620_resume.pdf
```

The tailored PDF path is also stored in `applications.tailored_resume_path` so the Auto-Apply tool always uploads the correct version for that job.

## Implementation Files

| File | Responsibility |
|------|---------------|
| `agent/tools/resume_tailor.py` | Orchestrates all 5 steps, emits HITL event |
| `agent/resume/tailoring.py` | Gap analysis logic + GPT-4o bullet rewrite |
| `agent/resume/pdf_generator.py` | Jinja2 render + WeasyPrint conversion |
| `agent/resume/templates/ats_resume.html` | ATS-compliant HTML template |
