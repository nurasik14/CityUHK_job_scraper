"""Query understanding: notice the place and the company a student names, and boost matches.

"tiktok machine learning intern singapore" names a company and a place. Keyword and vector
search treat those words like any others; here they are recognised and listings that are
*in* that place or *from* that company get a bonus on top of whatever the search gave them
(core/search.py adds it). It is a boost, never a filter: locations in listings are messy
("SF", "SGP", "7 Locations"), so a listing that fails the match can still rank.

Three independent facets, each 0 or 1 for a listing:

- place: the listing's location is in one of the places named ("tokyo or seoul" = either).
  Places come from the gazetteer in core/places.py; a region also accepts what is inside it.
- remote: the query says remote, and the listing's location or text says remote too.
- company: the listing's company is one of the companies named.

Company names are learned from the listings being searched (`CompanyIndex`), plus a small
table of nicknames (COMPANY_ALIASES). A one-word name that is also an ordinary word in other
companies' listings ("visa", "block", "notion") is ignored, so "visa sponsorship" does not
boost the company Visa. ByteDance and TikTok are deliberately *not* aliases of each other:
the job boards list them as separate employers and their listings do not mention each
other (9 of 414 TikTok listings say "ByteDance", 0 of 93 the other way, 2026-11-06 corpus).

Standard library only.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from cityu_jobs.core.places import find_places, in_place

# --- remote ---------------------------------------------------------------------------

_REMOTE_QUERY = re.compile(r"\bremote(ly)?\b|work(ing)? from home|\bwfh\b|远程|遠程|遠端|在家", re.I)
_REMOTE_LOCATION = re.compile(r"\bremote\b|work from home|\banywhere\b|远程|遠程", re.I)
# In the description only clear statements count ("remote sensing" or "remote teams" do not).
_REMOTE_TEXT = re.compile(
    r"\b(fully|100%|completely|entirely) remote\b|\bremote (position|role|internship|opportunity)\b"
    r"|\bwork(ing)? remotely\b|\b(role|position|internship) is (fully )?remote\b",
    re.I,
)

# --- companies ------------------------------------------------------------------------

# Nickname in a query -> company name as listings write it. Only used when that company is
# among the listings. Keep it small: full names already match without help.
COMPANY_ALIASES = {
    "facebook": "Meta",
    "fb": "Meta",
    "aws": "Amazon",
    "msft": "Microsoft",
    "jpmorgan": "JP Morgan Chase",
    "jp morgan": "JP Morgan Chase",
    "jpmc": "JP Morgan Chase",
    "goldman": "Goldman Sachs",
    "sig": "Susquehanna International Group (SIG)",
    "susquehanna": "Susquehanna International Group (SIG)",
    "hpe": "Hewlett Packard Enterprise",
    "amex": "American Express",
    "rbc": "Royal Bank of Canada",
    "p&g": "Procter & Gamble",
    "字节跳动": "ByteDance",
    "字節跳動": "ByteDance",
    "腾讯": "Tencent",
    "騰訊": "Tencent",
    "谷歌": "Google",
    "微软": "Microsoft",
    "微軟": "Microsoft",
    "苹果": "Apple",
    "蘋果": "Apple",
    "亚马逊": "Amazon",
    "亞馬遜": "Amazon",
    "英伟达": "NVIDIA",
    "輝達": "NVIDIA",
    "特斯拉": "Tesla",
}
# Trailing words that are not part of how people say a company's name.
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "llc", "ltd", "limited", "plc", "gmbh",
    "group", "holdings", "technologies", "technology", "securities", "financial", "global", "international", "labs",
}
# Words that are never a company in a query, whatever the listings say: job-search words,
# and ordinary English words that happen to be company names. The second kind is normally
# caught by the rule below, but only if some other company's listing uses the word.
_NEVER = {
    "intern", "internship", "interns", "remote", "hybrid", "summer", "winter", "fall", "spring", "the", "and", "for",
    "man", "plot", "pilot", "cook", "brain", "workshop", "trace", "carrier", "modal", "meter", "light", "arc", "hex",
    "atoms", "clay", "mill", "relay", "neighbor", "faith", "flex", "multiply", "minerals", "emergent", "divergent",
    "abundant", "amplify", "trajectory", "interplay", "uncountable", "subtotal", "morph", "realm", "roam",
}
# A one-word name is ambiguous if at least this many *other* companies use it as a plain
# lowercase word in their listings ("visa sponsorship", "a wide range of"). One stray use is
# not enough: someone always writes "google" or "tiktok" in lowercase.
AMBIGUOUS_MIN_COMPANIES = 2
_MIN_ONE_WORD_CHARS = 3

_WORD = re.compile(r"[^\W_]+(?:[&+'’.-][^\W_]+)*", re.UNICODE)
_LOWER_WORD = re.compile(r"(?<![\w'’-])[a-z][a-z0-9]{2,}(?![\w'’-])")


def _tokens(text: str) -> list[str]:
    return [t.casefold() for t in _WORD.findall(text or "")]


def _variants(name: str) -> set[tuple[str, ...]]:
    """Ways to say a company's name: in full, without "(SIG)", without "Technologies" / "Inc"."""
    variants = set()
    aliases = re.findall(r"\(([^)]+)\)", name)
    base = _tokens(re.sub(r"\([^)]*\)", " ", name))
    if base:
        variants.add(tuple(base))
        trimmed = list(base)
        while len(trimmed) > 1 and trimmed[-1] in _SUFFIXES:
            trimmed.pop()
            variants.add(tuple(trimmed))
    for alias in aliases:
        if _tokens(alias):
            variants.add(tuple(_tokens(alias)))
    return variants


class CompanyIndex:
    """The companies of a set of listings, and how to spot them in a query.

    `listings` are dicts with "company" and optionally "title" and "text" (used only to find
    ambiguous one-word names).
    """

    def __init__(self, listings: Iterable[dict]):
        names: set[str] = set()
        lower_words: dict[str, set[str]] = defaultdict(set)  # plain lowercase word -> companies using it
        docs = []
        for listing in listings:
            company = (listing.get("company") or "").strip()
            if company:
                names.add(company)
            docs.append((company.casefold(), f"{listing.get('title') or ''}\n{listing.get('text') or ''}"))
        by_variant: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for name in names:
            for variant in _variants(name):
                by_variant[variant].add(name.casefold())
        one_word = {v[0] for v in by_variant if len(v) == 1}
        for company, text in docs:
            for word in set(_LOWER_WORD.findall(text)) & one_word:
                lower_words[word].add(company)
        self.ambiguous = set()
        for variant in list(by_variant):
            if len(variant) > 1:
                continue
            word = variant[0]
            others = lower_words.get(word, set()) - by_variant[variant]
            if word in _NEVER or len(word) < _MIN_ONE_WORD_CHARS or len(others) >= AMBIGUOUS_MIN_COMPANIES:
                self.ambiguous.add(word)
                del by_variant[variant]
        known = {name.casefold() for name in names}
        for alias, target in COMPANY_ALIASES.items():
            if target.casefold() in known:
                by_variant[tuple(_tokens(alias)) or (alias,)].add(target.casefold())
        self._by_variant = dict(by_variant)
        self._max_len = max((len(v) for v in by_variant), default=0)
        self._cjk = {v[0]: names_ for v, names_ in by_variant.items() if len(v) == 1 and not v[0].isascii()}

    def find(self, query: str) -> list[tuple[frozenset[str], int, int]]:
        """Companies named in a query: (casefolded company names, start, end), longest names first."""
        words = [(m.group(0).casefold(), m.start(), m.end()) for m in _WORD.finditer(query or "")]
        found, i = [], 0
        while i < len(words):
            for n in range(min(self._max_len, len(words) - i), 0, -1):
                variant = tuple(w for w, _, _ in words[i : i + n])
                if variant in self._by_variant:
                    found.append((frozenset(self._by_variant[variant]), words[i][1], words[i + n - 1][2]))
                    i += n - 1
                    break
            i += 1
        for alias, names in self._cjk.items():  # Chinese has no spaces: look for the alias anywhere
            at = (query or "").find(alias)
            if at != -1 and not any(s <= at < e for _, s, e in found):
                found.append((frozenset(names), at, at + len(alias)))
        return found


# --- the parsed query -----------------------------------------------------------------


@dataclass(frozen=True)
class ParsedQuery:
    """What was recognised in a query. Empty tuples / False when nothing was."""

    text: str
    places: tuple[str, ...] = ()       # keys of core/places.PLACES
    remote: bool = False
    companies: frozenset[str] = frozenset()  # casefolded company names, as the listings write them
    rest: str = ""                     # the query without the recognised words

    @property
    def has_boost(self) -> bool:
        return bool(self.places or self.remote or self.companies)


def parse_query(query: str, companies: CompanyIndex | None = None) -> ParsedQuery:
    text = query or ""
    spans: list[tuple[int, int]] = []
    places = []
    for key, start, end in find_places(text):
        places.append(key)
        spans.append((start, end))
    remote = False
    for match in _REMOTE_QUERY.finditer(text):
        remote = True
        spans.append((match.start(), match.end()))
    named: set[str] = set()
    for names, start, end in companies.find(text) if companies else []:
        if any(s < end and start < e for s, e in spans):
            continue  # a place already claimed these words ("New York" is not a company)
        named |= names
        spans.append((start, end))
    rest, last = [], 0
    for start, end in sorted(spans):
        rest.append(text[last:max(start, last)])
        last = max(end, last)
    rest.append(text[last:])
    return ParsedQuery(text, tuple(dict.fromkeys(places)), remote, frozenset(named), " ".join("".join(rest).split()))


@lru_cache(maxsize=100_000)
def _says_remote(location: str, text: str) -> bool:
    return bool(_REMOTE_LOCATION.search(location) or _REMOTE_TEXT.search(text))


def facets(parsed: ParsedQuery, company: str, location: str, text: str = "") -> tuple[bool, bool, bool]:
    """(place, remote, company): which of the query's facets this listing matches."""
    place = any(in_place(key, location) for key in parsed.places) if parsed.places and location else False
    remote = parsed.remote and _says_remote(location or "", text or "")
    named = bool(parsed.companies) and (company or "").strip().casefold() in parsed.companies
    return place, remote, named


def boost(parsed: ParsedQuery, company: str, location: str, text: str, w_location: float, w_company: float) -> float:
    """The bonus for one listing, in units of "being ranked first by one more search" (see search.py)."""
    place, remote, named = facets(parsed, company, location, text)
    return w_location * (place + remote) + w_company * named
