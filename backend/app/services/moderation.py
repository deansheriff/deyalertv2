import re

# Group references that need a human look. A match holds the report for review; it is not a verdict.
_GROUP_TERMS = (
    "hausa",
    "fulani",
    "yoruba",
    "igbo",
    "ijaw",
    "tiv",
    "muslim",
    "muslims",
    "christian",
    "christians",
    "aboki",
    "ngbati",
    "herdsman",
    "herdsmen",
)
_GROUP_PATTERN = re.compile(r"\b(" + "|".join(_GROUP_TERMS) + r")\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(\+?234|\b0)[789][01]\d{8}\b")
_NAMED_PERSON_PATTERN = re.compile(
    r"\b(mr|mrs|miss|alhaji|alhaja|chief|pastor|imam|oga|madam)\.?\s+[A-Z][a-z]+"
)


def needs_review(description: str | None) -> bool:
    if not description:
        return False
    return bool(
        _GROUP_PATTERN.search(description)
        or _PHONE_PATTERN.search(description)
        or _NAMED_PERSON_PATTERN.search(description)
    )
