"""Generic heuristic field map.

When the ATS platform is unknown we inspect each form control's associated
label text and match it against keyword lists.
"""

LABEL_KEYWORDS = {
    "First Name": ["first name", "first"],
    "Last Name": ["last name", "last", "surname"],
    "Email": ["email", "e-mail"],
    "Phone": ["phone", "mobile", "cell"],
    "LinkedIn": ["linkedin"],
    "Resume": ["resume", "cv", "upload"],
}


def classify_label(label_text: str) -> str | None:
    """Return the canonical field name for a label, or ``None`` if unmatched."""
    text = (label_text or "").strip().lower()
    if not text:
        return None
    # Check longer keyword lists first so "first name" wins over "name".
    for field, keywords in LABEL_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return field
    return None
