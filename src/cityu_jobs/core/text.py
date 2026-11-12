"""The compact text an embedding model (and the reranker) reads about a listing.

Version 1 embedded the whole listing: title, company, job nature, department, full
description, requirements. The retrieval evaluation (eval/README.md) showed two problems:

- Length bias. A fifth of the internship listings have long descriptions (4,000+
  characters, much of it company boilerplate); the rest have ~170 characters. Vector search
  returned almost only the long ones.
- No location. Where the job is was not in the text at all.

Version 2 is short and has the same shape for every listing:

    title / company / location / job type / department / the first ~500 characters of the
    description (and requirements) after company boilerplate is removed

Boilerplate is removed by rules only (no AI), and conservatively: when in doubt a paragraph
stays. Two kinds of rule:

1. Fixed patterns: equal-opportunity statements, accommodation / privacy / legal notices,
   pay-transparency and benefits lists, "About <company>" style sections.
2. Repetition: a long paragraph that one company uses under several different job titles
   says something about the company, not about this job ("At Amazon, our tech isn't just a
   tool, ..." opens ~150 Amazon listings). `repeated_paragraphs()` finds them in the set of
   listings being embedded.

`chunk_texts()` is the alternative that was evaluated: split the cleaned description into
~500-character pieces, put the header on each, and score a listing by its best piece.

Everything here works on plain dicts (`listing_fields(job)` in core/models.py makes one from
a Job), so the evaluation in eval/ runs the same code on its corpus file. Standard library only.
"""

import re
from collections import defaultdict
from collections.abc import Iterable

# Bump when the text below changes shape. The embedding cache also stores a hash of the
# exact text that was embedded (core/embeddings.py), so old vectors are redone either way.
EMBED_TEXT_VERSION = 2

EXCERPT_CHARS = 500   # description characters kept in the compact text
CHUNK_CHARS = 500     # size of one piece in chunk_texts()
CHUNK_OVERLAP = 50    # characters shared by neighbouring pieces
LOCATION_CHARS = 150  # "US, Oregon, Hillsboro; US, California, Folsom; ..." can be very long

# A paragraph counts as company boilerplate when the same company uses it under at least
# this many different job titles, and it is at least this long (short lines such as
# "Requirements:" or "- Python" are left alone).
REPEAT_MIN_TITLES = 3
REPEAT_MIN_CHARS = 80

HEADER_FIELDS = ("title", "company", "location", "job_type", "department")

_SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip().casefold()


def paragraphs(text: str) -> list[str]:
    """Non-empty lines of `text`. Job boards put one paragraph, heading or bullet per line."""
    return [line.strip() for line in (text or "").replace("\xa0", " ").split("\n") if line.strip()]


# --- rule 1: fixed patterns -----------------------------------------------------------

# A paragraph containing one of these is a legal / HR notice, not a description of the job.
_NOTICE = re.compile(
    r"equal[- ](employment[- ])?opportunity"
    r"|\beeo\b|affirmative action"
    r"|without regard to|regardless of (race|age|gender|sex)"
    r"|(race|religion|colou?r),? (religion|colou?r|sex|creed|national origin)"
    r"|reasonable accommodations?|request(ing)? (an? )?accommodations?|accommodations? (for|to) (applicants|candidates|individuals)"
    r"|privacy (policy|notice|statement|page)|candidate privacy|applicant privacy|terms of service"
    r"|background (check|screening)|e-verify|drug[- ](free|test|screen)"
    r"|pay transparency|salary range|pay range|base (pay|salary) (range|for)|compensation range|hourly (rate|range|wage) (for|is|of|will)"
    r"|401\s?\(?k\)?|medical, dental|dental,? (and )?vision|health insurance|paid time off|parental leave|mental health benefits"
    r"|by (submitting|applying)|does not accept unsolicited|recruit(ing|ment) (agenc|fraud|scam)"
    r"|uses? ai tools in (its|our) recruiting|ai use guidelines for interviews"
    r"|applications? (will be|are) (reviewed|accepted) on (a|an) (rolling|ongoing) basis"
    r"|this posting is for an existing vacancy",
    re.I,
)
# A line that is only a link, an e-mail address or tracking hashtags ("#LI-Hybrid").
_LINK_ONLY = re.compile(r"^(https?://\S+|www\.\S+|\S+@\S+\.\S+|(#[\w-]+\s*)+)$", re.I)

# Headings that open a boilerplate section. `{company}` is replaced by the listing's company.
# After a "lead" heading (company introductions, usually at the top) at most LEAD_PARAGRAPHS
# paragraphs are dropped, because the job's own description often follows without a heading.
# After a "tail" heading (benefits, legal) everything up to the next heading is dropped.
_LEAD_SECTION = (
    r"about (us|the company|the firm|{company})|who we are|our (company|story|mission|values|culture)"
    r"|why (join )?(us|{company})\??|life at .{{1,40}}|a world-changing company"
)
_TAIL_SECTION = (
    r"(our |intern )?benefits( (and|&) perks)?|perks( (and|&) benefits)?|what we offer|what's in it for you\??"
    r"|compensation( (and|&) benefits)?|(eu |us )?salary( range)?|pay (range|transparency)"
    r"|equal (employment )?opportunit(y|ies)( employer| statement)?|eeo( statement)?|diversity(,? equity)?( (and|&) inclusion)?"
    r"|accommodations?|privacy( notice| policy)?|legal( notice)?|posting statement|additional information"
    r"|how to apply|to apply, please submit the following|application process|the process|interview process"
)
LEAD_PARAGRAPHS = 1
# Notice patterns are only trusted on paragraphs up to this length: a very long paragraph
# that mentions "equal opportunity" somewhere may be the whole description on one line.
NOTICE_MAX_CHARS = 600
_BULLET = re.compile(r"^([-•*·▪●◦–]|\d+[.)])\s")


def _is_heading(line: str) -> bool:
    """Short, no sentence end, not a bullet: "Responsibilities", "What you'll do:", "About the Team"."""
    if len(line) > 60 or _BULLET.match(line) or len(line.split()) > 8:
        return False
    return not line.rstrip().endswith((".", "!", "?", ";", ","))


def _section_patterns(company: str) -> tuple[re.Pattern, re.Pattern]:
    name = re.escape(_norm(company)) if company and company.strip() else r"\x00"  # \x00: matches nothing real
    return tuple(
        re.compile(r"^(" + pattern.format(company=name) + r")\s*:?$", re.I) for pattern in (_LEAD_SECTION, _TAIL_SECTION)
    )


# --- rule 2: repetition ---------------------------------------------------------------


def _key(company: str, paragraph: str) -> str:
    return _norm(company or "") + "\x1f" + _norm(paragraph)


def repeated_paragraphs(listings: Iterable[dict], min_titles: int = REPEAT_MIN_TITLES) -> frozenset[str]:
    """Paragraphs a company repeats under `min_titles` or more different job titles.

    Counting titles, not listings: companies post one job once per city, and that must not
    turn the job's own description into "boilerplate". Returns opaque keys for `clean_body`.
    """
    titles: dict[str, set[str]] = defaultdict(set)
    for listing in listings:
        title = _norm(listing.get("title") or "")
        seen = set()
        for field in ("description", "requirements"):
            for paragraph in paragraphs(listing.get(field) or ""):
                if len(paragraph) >= REPEAT_MIN_CHARS:
                    seen.add(_key(listing.get("company") or "", paragraph))
        for key in seen:
            titles[key].add(title)
    return frozenset(key for key, names in titles.items() if len(names) >= min_titles)


# --- the texts ------------------------------------------------------------------------


def header(listing: dict) -> str:
    """Title, company, location, job type, department: one per line, empty ones skipped."""
    parts = []
    for field in HEADER_FIELDS:
        value = _SPACE.sub(" ", str(listing.get(field) or "")).strip()
        if value:
            parts.append(value[:LOCATION_CHARS] if field == "location" else value)
    return "\n".join(parts)


def clean_body(listing: dict, boilerplate: frozenset[str] = frozenset(), strip: bool = True) -> str:
    """Description + requirements, one paragraph per line, with boilerplate removed.

    If the rules would remove everything, the text is returned unstripped: a listing never
    ends up with an empty body because of them.
    """
    lines = paragraphs(listing.get("description") or "") + paragraphs(listing.get("requirements") or "")
    if not strip:
        return "\n".join(lines)
    company = listing.get("company") or ""
    lead, tail = _section_patterns(company)
    kept, skip = [], 0  # skip: paragraphs still to drop after a boilerplate heading
    for line in lines:
        if _is_heading(line):
            heading = _norm(line)
            skip = LEAD_PARAGRAPHS if lead.match(heading) else len(lines) if tail.match(heading) else 0
            if skip:
                continue
        if skip:
            skip -= 1
            continue
        if _LINK_ONLY.match(line) or (len(line) <= NOTICE_MAX_CHARS and _NOTICE.search(line)):
            continue
        if len(line) >= REPEAT_MIN_CHARS and _key(company, line) in boilerplate:
            continue
        kept.append(line)
    return "\n".join(kept or lines)


def _cut(text: str, start: int, size: int) -> int:
    """End of a piece of about `size` characters from `start`, moved back to a word boundary."""
    end = start + size
    if end >= len(text):
        return len(text)
    space = max(text.rfind(" ", start, end + 1), text.rfind("\n", start, end + 1))
    return space if space > start + size // 2 else end


def excerpt(text: str, max_chars: int = EXCERPT_CHARS) -> str:
    """The first `max_chars` characters, cut at a word boundary."""
    return text[: _cut(text, 0, max_chars)].rstrip()


def compact_text(
    listing: dict,
    boilerplate: frozenset[str] = frozenset(),
    max_chars: int = EXCERPT_CHARS,
    strip: bool = True,
) -> str:
    """Header + the first `max_chars` characters of the cleaned description. What gets embedded."""
    head, body = header(listing), excerpt(clean_body(listing, boilerplate, strip), max_chars)
    return f"{head}\n{body}" if head and body else head or body


def chunk_texts(
    listing: dict,
    boilerplate: frozenset[str] = frozenset(),
    size: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP,
    strip: bool = True,
) -> list[str]:
    """The whole cleaned description in pieces of ~`size` characters, the header on each.

    Neighbours share ~`overlap` characters. The first piece equals `compact_text(...,
    max_chars=size)`. A listing with a short description has exactly one piece.
    """
    head, body = header(listing), clean_body(listing, boilerplate, strip)
    pieces, start = [], 0
    while True:
        end = _cut(body, start, size)
        pieces.append(body[start:end].strip())
        if end >= len(body):
            break
        start = max(end - overlap, start + 1)
        space = body.find(" ", start, end)  # start the next piece at a word, not mid-word
        start = space + 1 if space != -1 else start
    if len(pieces) > 1 and len(pieces[-1]) <= overlap * 2:
        pieces.pop()  # a tail that is almost all overlap adds nothing
    return [f"{head}\n{p}" if head and p else head or p for p in pieces]


def max_similarity(similarities: Iterable[float]) -> float:
    """A listing's score in chunk mode: the cosine of its best piece."""
    return max(similarities)
