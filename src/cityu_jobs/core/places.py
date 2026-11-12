"""A small gazetteer: the places a student may name in a search, and how listings write them.

Used by core/query.py to notice "hong kong", "NYC", "新加坡" or "europe" in a query and to
tell whether a listing's location string ("Toronto, ON, Canada", "SF", "SGP", "Remote in
USA") is in that place. It is a keyword list, not a geocoder: job boards write locations
freely, and only the places below are known.

Each place has
- `query`: a regular expression for the *query* (matched ignoring case). `query_cs` is an
  extra one that is case-sensitive, for short words that are only a place in capitals ("US").
- `listing` / `listing_cs`: the same for a listing's *location* string. State and country
  codes ("CA", "ON", "SGP") are case-sensitive, so "Remote in USA" is not in Indiana.
- `within`: the bigger places it belongs to. Asking for a bigger place also accepts
  everything inside it: "europe" matches a listing that only says "Berlin".

It does not go the other way: asking for "london" does not match a listing that only says
"United Kingdom".
"""

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Place:
    key: str
    query: str
    listing: str
    within: tuple[str, ...] = ()
    query_cs: str = ""
    listing_cs: str = ""


def _code(*codes: str) -> str:
    """A state / province code after a comma: "Austin, TX", "US, CA, Santa Clara"."""
    return r",\s*(?:" + "|".join(codes) + r")\b"


def _iso(*codes: str) -> str:
    """A country code standing alone: "SGP", "London, GBR"."""
    return r"(?<![A-Za-z])(?:" + "|".join(codes) + r")(?![A-Za-z])"


_REGIONS = [
    Place("asia", r"\basia\b|\basian\b|亚洲|亞洲", r"\basia\b", ("apac",)),
    Place("apac", r"\bapac\b|asia[- ]pacific|亚太|亞太", r"\bapac\b|asia[- ]pacific"),
    Place("southeast asia", r"south[- ]?east asia|\bsea region\b|\basean\b|东南亚|東南亞", r"south[- ]?east asia|\basean\b", ("asia",)),
    Place("europe", r"\beurope(an)?\b|\beu\b|欧洲|歐洲", r"\beurope\b|\beu\b", ("emea",)),
    Place("emea", r"\bemea\b", r"\bemea\b"),
    Place("middle east", r"middle east|中东|中東", r"middle east", ("emea",)),
    Place("north america", r"north america|北美", r"north america|\bamericas\b"),
    Place("latin america", r"latin america|south america|\blatam\b|南美|拉丁美洲", r"latin america|south america|\blatam\b"),
]

# Countries (and Hong Kong, which students search for like one): key, query, listing, within, ISO code(s).
_COUNTRIES = [
    ("hong kong", r"hong\s*kong|\bhk\b|\bhksar\b|香港", r"hong\s*kong|\bhksar\b|kowloon|cyberport|sha tin|kwun tong|wan chai|tsim sha tsui|香港", ("asia",), ("HK", "HKG")),
    ("china", r"\bchina\b|mainland|\bprc\b|中国|中國|内地|內地|大陆|大陸", r"\bchina\b|mainland|\bprc\b|中国|中國", ("asia",), ("CHN",)),
    ("taiwan", r"\btaiwan\b|台湾|台灣|臺灣", r"\btaiwan\b|台湾|台灣", ("asia",), ("TWN",)),
    ("macau", r"\bmaca[uo]\b|澳门|澳門", r"\bmaca[uo]\b|澳门|澳門", ("asia",), ()),
    ("singapore", r"\bsingapore\b|\bsg\b|新加坡|星加坡", r"\bsingapore\b|新加坡", ("southeast asia",), ("SG", "SGP")),
    ("japan", r"\bjapan\b|日本", r"\bjapan\b|日本", ("asia",), ("JPN",)),
    ("south korea", r"\b(south )?korea\b|韩国|韓國", r"\bkorea\b|韩国|韓國", ("asia",), ("KOR",)),
    ("india", r"\bindia\b|印度", r"\bindia\b", ("asia",), ("IND",)),
    ("malaysia", r"\bmalaysia\b|马来西亚|馬來西亞", r"\bmalaysia\b", ("southeast asia",), ("MYS",)),
    ("thailand", r"\bthailand\b|泰国|泰國", r"\bthailand\b", ("southeast asia",), ("THA",)),
    ("vietnam", r"\bviet\s?nam\b|越南", r"\bviet\s?nam\b", ("southeast asia",), ("VNM",)),
    ("indonesia", r"\bindonesia\b|印尼", r"\bindonesia\b", ("southeast asia",), ("IDN",)),
    ("philippines", r"\bphilippines\b|菲律宾|菲律賓", r"\bphilippines\b", ("southeast asia",), ("PHL",)),
    ("australia", r"\baustralia\b|\baussie\b|澳洲|澳大利亚|澳大利亞", r"\baustralia\b", ("apac",), ("AUS",)),
    ("new zealand", r"new zealand|新西兰|紐西蘭", r"new zealand", ("apac",), ("NZL",)),
    ("united kingdom", r"united kingdom|\buk\b|\bu\.k\.|great britain|\bbritain\b|\bengland\b|英国|英國",
     r"united kingdom|\bengland\b|\bscotland\b|\bwales\b|great britain", ("europe",), ("UK", "GBR", "GB")),
    ("ireland", r"\bireland\b|爱尔兰|愛爾蘭", r"\bireland\b", ("europe",), ("IRL",)),
    ("france", r"\bfrance\b|法国|法國", r"\bfrance\b", ("europe",), ("FRA",)),
    ("germany", r"\bgermany\b|德国|德國", r"\bgermany\b|deutschland", ("europe",), ("DEU",)),
    ("netherlands", r"\bnetherlands\b|\bholland\b|荷兰|荷蘭", r"\bnetherlands\b", ("europe",), ("NLD",)),
    ("switzerland", r"\bswitzerland\b|瑞士", r"\bswitzerland\b", ("europe",), ("CHE",)),
    ("spain", r"\bspain\b|西班牙", r"\bspain\b", ("europe",), ("ESP",)),
    ("italy", r"\bitaly\b|意大利|義大利", r"\bitaly\b", ("europe",), ("ITA",)),
    ("poland", r"\bpoland\b|波兰|波蘭", r"\bpoland\b", ("europe",), ("POL",)),
    ("sweden", r"\bsweden\b|瑞典", r"\bsweden\b", ("europe",), ("SWE",)),
    ("denmark", r"\bdenmark\b|丹麦|丹麥", r"\bdenmark\b", ("europe",), ("DNK",)),
    ("norway", r"\bnorway\b|挪威", r"\bnorway\b", ("europe",), ("NOR",)),
    ("finland", r"\bfinland\b|芬兰|芬蘭", r"\bfinland\b", ("europe",), ("FIN",)),
    ("belgium", r"\bbelgium\b|比利时|比利時", r"\bbelgium\b", ("europe",), ("BEL",)),
    ("austria", r"\baustria\b|奥地利|奧地利", r"\baustria\b", ("europe",), ("AUT",)),
    ("portugal", r"\bportugal\b|葡萄牙", r"\bportugal\b", ("europe",), ("PRT",)),
    ("luxembourg", r"\bluxembourg\b|卢森堡|盧森堡", r"\bluxembourg\b", ("europe",), ("LUX",)),
    ("czech republic", r"\bczech(ia| republic)?\b|捷克", r"\bczech", ("europe",), ("CZE",)),
    ("romania", r"\bromania\b", r"\bromania\b", ("europe",), ("ROU",)),
    ("serbia", r"\bserbia\b", r"\bserbia\b", ("europe",), ("SRB",)),
    ("hungary", r"\bhungary\b", r"\bhungary\b", ("europe",), ("HUN",)),
    ("greece", r"\bgreece\b|希腊|希臘", r"\bgreece\b", ("europe",), ("GRC",)),
    ("estonia", r"\bestonia\b", r"\bestonia\b", ("europe",), ()),
    ("armenia", r"\barmenia\b", r"\barmenia\b", (), ()),
    ("israel", r"\bisrael\b|以色列", r"\bisrael\b", ("middle east",), ("ISR",)),
    ("united arab emirates", r"\buae\b|united arab emirates|\bemirates\b|阿联酋", r"united arab emirates|\buae\b", ("middle east",), ()),
    ("saudi arabia", r"saudi( arabia)?|沙特", r"saudi arabia", ("middle east",), ("SAU",)),
    ("canada", r"\bcanada\b|\bcanadian\b|加拿大", r"\bcanada\b", ("north america",), ("CAN",)),
    ("mexico", r"\bmexico\b|墨西哥", r"\bmexico\b", ("north america", "latin america"), ("MEX",)),
    ("brazil", r"\bbra[sz]il\b|巴西", r"\bbra[sz]il\b", ("latin america",), ("BRA",)),
    ("argentina", r"\bargentina\b|阿根廷", r"\bargentina\b", ("latin america",), ("ARG",)),
    ("colombia", r"\bcolombia\b", r"\bcolombia\b", ("latin america",), ()),
    ("costa rica", r"costa rica", r"costa rica", ("latin america",), ("CRI",)),
]

# The United States: "us" is only a place in capitals ("US", "U.S."), or after "in the".
_USA = Place(
    "united states",
    r"united states|\busa\b|\bu\.s\.a?\.?|\bamerica\b|\bstateside\b|\bin (the )?us\b|美国|美國",
    r"united states|\busa\b|\bu\.s\.",
    ("north america",),
    query_cs=r"\bUS\b",
    listing_cs=_iso("US", "USA"),
)

# US states: name, code, extra listing pattern.
_US_STATES = [
    ("alabama", "AL"), ("alaska", "AK"), ("arizona", "AZ"), ("arkansas", "AR"), ("california", "CA"),
    ("colorado", "CO"), ("connecticut", "CT"), ("delaware", "DE"), ("florida", "FL"), ("georgia", "GA"),
    ("hawaii", "HI"), ("idaho", "ID"), ("illinois", "IL"), ("indiana", "IN"), ("iowa", "IA"),
    ("kansas", "KS"), ("kentucky", "KY"), ("louisiana", "LA"), ("maine", "ME"), ("maryland", "MD"),
    ("massachusetts", "MA"), ("michigan", "MI"), ("minnesota", "MN"), ("mississippi", "MS"), ("missouri", "MO"),
    ("montana", "MT"), ("nebraska", "NE"), ("nevada", "NV"), ("new hampshire", "NH"), ("new jersey", "NJ"),
    ("new mexico", "NM"), ("new york state", "NY"), ("north carolina", "NC"), ("north dakota", "ND"), ("ohio", "OH"),
    ("oklahoma", "OK"), ("oregon", "OR"), ("pennsylvania", "PA"), ("rhode island", "RI"), ("south carolina", "SC"),
    ("south dakota", "SD"), ("tennessee", "TN"), ("texas", "TX"), ("utah", "UT"), ("vermont", "VT"),
    ("virginia", "VA"), ("washington state", "WA"), ("west virginia", "WV"), ("wisconsin", "WI"), ("wyoming", "WY"),
]
_STATE_NAME = {  # names that need care because a city or another state shares them
    "new york state": (r"new york state|upstate new york|纽约州", r"new york"),
    "washington state": (r"washington state|\bwashington\b(?!,?\s*d\.?\s*c)", r"\bwashington\b(?!,?\s*d\.?\s*c)"),
    "virginia": (r"(?<!west )\bvirginia\b", r"(?<!west )\bvirginia\b"),
}
# Canadian provinces: name, code.
_CA_PROVINCES = [
    ("ontario", "ON"), ("british columbia", "BC"), ("quebec", "QC"), ("alberta", "AB"),
    ("manitoba", "MB"), ("saskatchewan", "SK"), ("nova scotia", "NS"), ("new brunswick", "NB"),
]

# Cities: key, query, listing, within.
_CITIES = [
    # Greater China
    ("shenzhen", r"\bshenzhen\b|深圳", r"\bshenzhen\b|深圳", ("china",)),
    ("beijing", r"\bbeijing\b|\bpeking\b|北京", r"\bbeijing\b|北京", ("china",)),
    ("shanghai", r"\bshanghai\b|上海", r"\bshanghai\b|上海", ("china",)),
    ("guangzhou", r"\bguangzhou\b|广州|廣州", r"\bguangzhou\b|广州|廣州", ("china",)),
    ("hangzhou", r"\bhangzhou\b|杭州", r"\bhangzhou\b|杭州", ("china",)),
    ("chengdu", r"\bchengdu\b|成都", r"\bchengdu\b|成都", ("china",)),
    ("nanjing", r"\bnanjing\b|南京", r"\bnanjing\b|南京", ("china",)),
    ("suzhou", r"\bsuzhou\b|苏州|蘇州", r"\bsuzhou\b|苏州|蘇州", ("china",)),
    ("wuhan", r"\bwuhan\b|武汉|武漢", r"\bwuhan\b|武汉|武漢", ("china",)),
    ("xi'an", r"\bxi'?an\b|西安", r"\bxi'?an\b|西安", ("china",)),
    ("taipei", r"\btaipei\b|台北|臺北", r"\btaipei\b|台北|臺北", ("taiwan",)),
    ("hsinchu", r"\bhsinchu\b|新竹", r"\bhsinchu\b|新竹", ("taiwan",)),
    # Rest of Asia-Pacific
    ("tokyo", r"\btokyo\b|东京|東京", r"\btokyo\b|东京|東京", ("japan",)),
    ("osaka", r"\bosaka\b|大阪", r"\bosaka\b|大阪", ("japan",)),
    ("seoul", r"\bseoul\b|首尔|首爾", r"\bseoul\b|首尔|首爾", ("south korea",)),
    ("bangalore", r"\bbangalore\b|\bbengaluru\b", r"\bbangalore\b|\bbengaluru\b", ("india",)),
    ("hyderabad", r"\bhyderabad\b", r"\bhyderabad\b", ("india",)),
    ("mumbai", r"\bmumbai\b|\bbombay\b", r"\bmumbai\b", ("india",)),
    ("delhi", r"\b(new )?delhi\b|\bgurgaon\b|\bgurugram\b|\bnoida\b", r"\bdelhi\b|\bgurgaon\b|\bgurugram\b|\bnoida\b", ("india",)),
    ("pune", r"\bpune\b", r"\bpune\b", ("india",)),
    ("chennai", r"\bchennai\b", r"\bchennai\b", ("india",)),
    ("kuala lumpur", r"kuala lumpur|\bkl\b", r"kuala lumpur|\bpenang\b", ("malaysia",)),
    ("bangkok", r"\bbangkok\b|曼谷", r"\bbangkok\b", ("thailand",)),
    ("jakarta", r"\bjakarta\b", r"\bjakarta\b", ("indonesia",)),
    ("manila", r"\bmanila\b", r"\bmanila\b|\btaguig\b", ("philippines",)),
    ("ho chi minh city", r"ho chi minh|\bsaigon\b|\bhanoi\b", r"ho.?chi.?minh|\bhanoi\b", ("vietnam",)),
    ("sydney", r"\bsydney\b|悉尼|雪梨", r"\bsydney\b|new south wales", ("australia",)),
    ("melbourne", r"\bmelbourne\b|墨尔本|墨爾本", r"\bmelbourne\b(?!,\s*FL)", ("australia",)),
    ("brisbane", r"\bbrisbane\b", r"\bbrisbane\b", ("australia",)),
    ("auckland", r"\bauckland\b", r"\bauckland\b", ("new zealand",)),
    # Europe and the Middle East
    ("london", r"\blondon\b|伦敦|倫敦", r"\blondon\b(?!,\s*(?:ON|Ontario))", ("united kingdom",)),
    ("cambridge", r"\bcambridge\b|剑桥|劍橋", r"\bcambridge\b", ()),
    ("edinburgh", r"\bedinburgh\b", r"\bedinburgh\b", ("united kingdom",)),
    ("manchester", r"\bmanchester\b", r"\bmanchester\b(?!,\s*NH)", ("united kingdom",)),
    ("bristol", r"\bbristol\b", r"\bbristol,\s*(?:UK|United Kingdom|England)", ("united kingdom",)),
    ("dublin", r"\bdublin\b", r"\bdublin\b(?!,\s*(?:CA|OH))", ("ireland",)),
    ("paris", r"\bparis\b|巴黎", r"\bparis\b", ("france",)),
    ("berlin", r"\bberlin\b|柏林", r"\bberlin\b", ("germany",)),
    ("munich", r"\bmunich\b|\bmünchen\b|慕尼黑", r"\bmunich\b|\bmünchen\b", ("germany",)),
    ("frankfurt", r"\bfrankfurt\b", r"\bfrankfurt\b", ("germany",)),
    ("hamburg", r"\bhamburg\b", r"\bhamburg\b", ("germany",)),
    ("amsterdam", r"\bamsterdam\b", r"\bamsterdam\b", ("netherlands",)),
    ("zurich", r"\bz[uü]rich\b|苏黎世|蘇黎世", r"\bz[uü]rich\b", ("switzerland",)),
    ("geneva", r"\bgeneva\b", r"\bgeneva\b", ("switzerland",)),
    ("madrid", r"\bmadrid\b", r"\bmadrid\b", ("spain",)),
    ("barcelona", r"\bbarcelona\b", r"\bbarcelona\b", ("spain",)),
    ("milan", r"\bmilan\b", r"\bmilan\b", ("italy",)),
    ("warsaw", r"\bwarsaw\b|\bkrak[oó]w\b|\bgda[nń]sk\b", r"\bwarsaw\b|\bkrak[oó]w\b|\bgda[nń]sk\b", ("poland",)),
    ("stockholm", r"\bstockholm\b", r"\bstockholm\b", ("sweden",)),
    ("copenhagen", r"\bcopenhagen\b", r"\bcopenhagen\b", ("denmark",)),
    ("helsinki", r"\bhelsinki\b", r"\bhelsinki\b", ("finland",)),
    ("oslo", r"\boslo\b", r"\boslo\b", ("norway",)),
    ("brussels", r"\bbrussels\b", r"\bbrussels\b", ("belgium",)),
    ("vienna", r"\bvienna\b", r"\bvienna\b(?!,\s*VA)", ("austria",)),
    ("lisbon", r"\blisbon\b", r"\blisbon\b", ("portugal",)),
    ("prague", r"\bprague\b", r"\bprague\b", ("czech republic",)),
    ("budapest", r"\bbudapest\b", r"\bbudapest\b", ("hungary",)),
    ("bucharest", r"\bbucharest\b", r"\bbucharest\b", ("romania",)),
    ("belgrade", r"\bbelgrade\b", r"\bbelgrade\b", ("serbia",)),
    ("athens", r"\bathens\b", r"\bathens\b(?!,\s*GA)", ("greece",)),
    ("tallinn", r"\btallinn\b", r"\btallinn\b", ("estonia",)),
    ("tel aviv", r"tel[- ]aviv", r"tel[- ]aviv", ("israel",)),
    ("dubai", r"\bdubai\b|\babu dhabi\b|迪拜|杜拜", r"\bdubai\b|\babu dhabi\b", ("united arab emirates",)),
    ("riyadh", r"\briyadh\b", r"\briyadh\b", ("saudi arabia",)),
    # Canada
    ("toronto", r"\btoronto\b|多伦多|多倫多", r"\btoronto\b|\bmarkham\b|\bmississauga\b", ("ontario",)),
    ("ottawa", r"\bottawa\b", r"\bottawa\b|\bkanata\b", ("ontario",)),
    ("waterloo", r"\bwaterloo\b|\bkitchener\b", r"\bwaterloo\b|\bkitchener\b", ("ontario",)),
    ("vancouver", r"\bvancouver\b|温哥华|溫哥華", r"\bvancouver\b(?!,\s*WA)|\bburnaby\b", ("british columbia",)),
    ("montreal", r"\bmontr[eé]al\b|蒙特利尔", r"\bmontr[eé]al\b", ("quebec",)),
    ("calgary", r"\bcalgary\b", r"\bcalgary\b", ("alberta",)),
    # United States
    ("new york city", r"new york(?! state)|\bnyc\b|\bny\b|manhattan|brooklyn|纽约|紐約",
     r"\bnyc\b|new york city|new york,\s*(?:ny|new york)|\bnew york\b(?!,)|manhattan|brooklyn", ("new york state",)),
    ("san francisco", r"san francisco|\bsf\b|旧金山|舊金山|三藩市", r"san francisco|\bsf\b", ("bay area",)),
    ("san jose", r"\bsan jose\b", r"(?<!costa rica, )\bsan jose\b(?!,\s*costa rica)", ("bay area",)),
    ("palo alto", r"\bpalo alto\b", r"\bpalo alto\b", ("bay area",)),
    ("mountain view", r"\bmountain view\b", r"\bmountain view\b", ("bay area",)),
    ("sunnyvale", r"\bsunnyvale\b", r"\bsunnyvale\b", ("bay area",)),
    ("santa clara", r"\bsanta clara\b", r"\bsanta clara\b", ("bay area",)),
    ("cupertino", r"\bcupertino\b", r"\bcupertino\b", ("bay area",)),
    ("menlo park", r"\bmenlo park\b", r"\bmenlo park\b", ("bay area",)),
    ("los angeles", r"los angeles|洛杉矶|洛杉磯", r"los angeles|\bel segundo\b|\bsanta monica\b|\bculver city\b|\bburbank\b", ("california",)),
    ("san diego", r"\bsan diego\b", r"\bsan diego\b", ("california",)),
    ("irvine", r"\birvine\b", r"\birvine\b", ("california",)),
    ("seattle", r"\bseattle\b|西雅图|西雅圖", r"\bseattle\b|\bbellevue\b|\bredmond\b|\bkirkland\b", ("washington state",)),
    ("boston", r"\bboston\b|波士顿|波士頓", r"\bboston\b|\bcambridge,\s*(?:MA|Massachusetts)|\bsomerville\b", ("massachusetts",)),
    ("chicago", r"\bchicago\b|芝加哥", r"\bchicago\b", ("illinois",)),
    ("austin", r"\baustin\b", r"\baustin\b", ("texas",)),
    ("dallas", r"\bdallas\b", r"\bdallas\b|\bplano\b|\birving\b|\brichardson\b", ("texas",)),
    ("houston", r"\bhouston\b", r"\bhouston\b", ("texas",)),
    ("washington dc", r"washington,?\s*d\.?\s*c\.?|\bd\.c\.|华盛顿特区", r"washington,?\s*d\.?\s*c|district of columbia", ("united states",)),
    ("atlanta", r"\batlanta\b", r"\batlanta\b", ("georgia",)),
    ("miami", r"\bmiami\b", r"\bmiami\b", ("florida",)),
    ("denver", r"\bdenver\b", r"\bdenver\b|\bboulder\b", ("colorado",)),
    ("pittsburgh", r"\bpittsburgh\b", r"\bpittsburgh\b", ("pennsylvania",)),
    ("philadelphia", r"\bphiladelphia\b|\bphilly\b", r"\bphiladelphia\b", ("pennsylvania",)),
    ("portland", r"\bportland\b", r"\bportland\b|\bhillsboro\b", ("oregon",)),
    ("phoenix", r"\bphoenix\b", r"\bphoenix\b|\btempe\b|\bscottsdale\b|\bchandler\b", ("arizona",)),
    ("detroit", r"\bdetroit\b", r"\bdetroit\b|\bann arbor\b", ("michigan",)),
    ("minneapolis", r"\bminneapolis\b", r"\bminneapolis\b", ("minnesota",)),
    ("raleigh", r"\braleigh\b|research triangle", r"\braleigh\b|\bdurham\b|\bcary\b", ("north carolina",)),
    ("salt lake city", r"salt lake city", r"salt lake city", ("utah",)),
]

_BAY_AREA = Place(
    "bay area",
    r"bay area|silicon valley|湾区|灣區|硅谷|矽谷",
    r"bay area|silicon valley|\bsouth sf\b|\bsan mateo\b|\bredwood city\b|\bfoster city\b|\bburlingame\b|\bsan bruno\b"
    r"|\bmilpitas\b|\bfremont\b|\blos altos\b|\boakland\b|\bberkeley\b|\bsan carlos\b|\bemeryville\b",
    ("california",),
)
# Listings write Los Angeles as a bare "LA" ("LA", "SF; LA"); after a comma "LA" is Louisiana.
_LA_LISTING_CS = r"(?:^|;\s*)LA(?=\s*(?:;|$))"


def _build() -> dict[str, Place]:
    places = list(_REGIONS) + [_USA, _BAY_AREA]
    for key, query, listing, within, codes in _COUNTRIES:
        places.append(Place(key, query, listing, within, listing_cs=_iso(*codes) if codes else ""))
    for name, code in _US_STATES:
        query, listing = _STATE_NAME.get(name, (rf"\b{name}\b", rf"\b{name}\b"))
        places.append(Place(name, query, listing, ("united states",), listing_cs=_code(code)))
    for name, code in _CA_PROVINCES:
        places.append(Place(name, rf"\b{name}\b|\bqu[eé]bec\b" if name == "quebec" else rf"\b{name}\b",
                            rf"\b{name}\b|\bqu[eé]bec\b" if name == "quebec" else rf"\b{name}\b", ("canada",), listing_cs=_code(code)))
    for key, query, listing, within in _CITIES:
        places.append(Place(key, query, listing, within, listing_cs=_LA_LISTING_CS if key == "los angeles" else "",
                            query_cs=r"\bLA\b" if key == "los angeles" else ""))
    by_key = {p.key: p for p in places}
    assert len(by_key) == len(places), "duplicate place key"
    for p in places:
        for parent in p.within:
            assert parent in by_key, f"{p.key}: unknown parent {parent}"
    return by_key


PLACES: dict[str, Place] = _build()

_QUERY = {k: (re.compile(p.query, re.I), re.compile(p.query_cs) if p.query_cs else None) for k, p in PLACES.items()}


def _descendants(key: str) -> set[str]:
    found, frontier = {key}, [key]
    while frontier:
        parent = frontier.pop()
        for p in PLACES.values():
            if parent in p.within and p.key not in found:
                found.add(p.key)
                frontier.append(p.key)
    return found


def _listing_matcher(key: str) -> tuple[re.Pattern, re.Pattern | None]:
    inside = sorted(_descendants(key))
    insensitive = re.compile("|".join(f"(?:{PLACES[k].listing})" for k in inside), re.I)
    sensitive = [PLACES[k].listing_cs for k in inside if PLACES[k].listing_cs]
    return insensitive, re.compile("|".join(f"(?:{s})" for s in sensitive)) if sensitive else None


_LISTING = {key: _listing_matcher(key) for key in PLACES}


def find_places(query: str) -> list[tuple[str, int, int]]:
    """Places named in a query: (key, start, end) of each match, in query order."""
    found = []
    for key, (insensitive, sensitive) in _QUERY.items():
        for pattern in (insensitive, sensitive):
            if pattern is None:
                continue
            for match in pattern.finditer(query):
                found.append((key, match.start(), match.end()))
    # A longer match wins over a shorter one inside it ("new york state" over "new york").
    found.sort(key=lambda f: (f[1], -(f[2] - f[1])))
    kept: list[tuple[str, int, int]] = []
    for key, start, end in found:
        if any(start >= s and end <= e and (start, end) != (s, e) for _, s, e in kept):
            continue
        if (key, start, end) not in kept:
            kept.append((key, start, end))
    return kept


@lru_cache(maxsize=200_000)
def in_place(key: str, location: str) -> bool:
    """True if a listing's location string is in the place `key` (or in a place inside it)."""
    insensitive, sensitive = _LISTING[key]
    return bool(insensitive.search(location) or (sensitive and sensitive.search(location)))
