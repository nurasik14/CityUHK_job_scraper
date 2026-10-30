"""Which region is a job in? Location text -> region keys from config.INTERN_REGIONS.

Job boards write locations freely ("Hong Kong SAR", "SG", "APAC - Remote", "Remote in USA",
"7 Locations"), so this is a keyword match, not a geocoder. A location can be in several
regions ("London; Singapore"). Anything that matches no Asian or European pattern counts
as "americas" (North America & elsewhere). "Remote" counts as worldwide only when it
names no region of its own ("Remote in USA" is North America).
"""

import re

_PATTERNS = {
    "hk": r"hong\s*kong|\bhksar\b|\bhk\b|kowloon|sha tin|kwun tong|wan chai|tsim sha tsui|cyberport",
    "china": (
        r"china|mainland|\bprc\b|shanghai|beijing|shenzhen|guangzhou|hangzhou|chengdu|nanjing|suzhou|"
        r"wuhan|xi'?an\b|tianjin|xiamen|qingdao|dongguan|zhuhai|macau|macao"
    ),
    "apac": (
        r"singapore|\bsg\b|japan|tokyo|osaka|korea|seoul|taiwan|taipei|hsinchu|australia|sydney|melbourne|"
        r"brisbane|new zealand|auckland|malaysia|kuala lumpur|penang|kulim|subang|thailand|bangkok|"
        r"indonesia|jakarta|philippines|manila|taguig|vietnam|ho.?chi.?minh|hanoi|\bindia\b|bangalore|"
        r"bengaluru|hyderabad|pune|mumbai|gurgaon|gurugram|noida|chennai|delhi|gift city|\bapac\b|\basia\b"
    ),
    "europe": (
        r"united kingdom|\buk\b|england|scotland|london|edinburgh|manchester|cambridge,\s*uk|ireland|dublin|"
        r"france|paris|germany|berlin|munich|hamburg|frankfurt|netherlands|amsterdam|zurich|switzerland|"
        r"geneva|spain|madrid|barcelona|poland|warsaw|krakow|portugal|lisbon|sweden|stockholm|austria|"
        r"vienna|belgium|brussels|italy|milan|denmark|copenhagen|finland|helsinki|norway|oslo|czech|"
        r"prague|romania|bucharest|serbia|belgrade|greece|athens|luxembourg|estonia|tallinn|hungary|"
        r"budapest|\beurope\b|\bemea\b"
    ),
}
_COMPILED = {key: re.compile(pattern, re.I) for key, pattern in _PATTERNS.items()}
_REMOTE = re.compile(r"\bremote\b|anywhere|worldwide|\bglobal\b", re.I)
# "Remote in USA", "Remote - Canada": remote, but tied to a country we don't list above.
_NAMED_ELSEWHERE = re.compile(r"\bus\b|\busa\b|united states|america|canada|mexico|brazil|latam", re.I)


def classify(location: str | None) -> set[str]:
    """Region keys for a location string; an empty location is unknown (empty set)."""
    text = (location or "").strip()
    if not text:
        return set()
    regions = {key for key, pattern in _COMPILED.items() if pattern.search(text)}
    if _REMOTE.search(text) and not regions and not _NAMED_ELSEWHERE.search(text):
        regions.add("remote")
    if not regions or _NAMED_ELSEWHERE.search(text):
        regions.add("americas")
    return regions


def in_regions(location: str | None, wanted: set[str] | list[str] | tuple[str, ...]) -> bool:
    """True if the location is in one of the wanted regions. Unknown locations are kept."""
    regions = classify(location)
    return not regions or bool(regions & set(wanted))
