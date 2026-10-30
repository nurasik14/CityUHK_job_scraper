"""Which companies to fetch internships from, and through which public job-board API.

Every entry was checked live on 2026-10-30 (the board exists and answers). Companies change
job-board providers now and then; a dead entry only adds a line to the scrape notes.

Tiers:
- "big":  big tech (and regional giants like Grab, TikTok). Shown as "Big tech internship".
- "tech": smaller but solid tech, AI, fintech and quant firms, several with Hong Kong /
          Singapore offices. Shown as "Tech internship".

Providers (all public, read-only JSON meant for embedding job lists on websites):
- greenhouse / lever / ashby / smartrecruiters: `slug` is the company's board name.
- workday: `site` is "<tenant>.<wdN>/<career site>".
- amazon, microsoft, tiktok: each company's own careers search API.
- simplify: the community-curated SimplifyJobs internship list on GitHub (hundreds of
  companies, mostly North America); its big-tech entries are tiered "big".
"""

COMPANIES: list[dict] = [
    # --- Big tech: own careers APIs ---------------------------------------------------
    {"name": "Amazon", "provider": "amazon", "tier": "big"},
    {"name": "Microsoft", "provider": "microsoft", "tier": "big"},
    {"name": "TikTok", "provider": "tiktok", "tier": "big"},
    {"name": "NVIDIA", "provider": "workday", "site": "nvidia.wd5/NVIDIAExternalCareerSite", "tier": "big"},
    {"name": "Adobe", "provider": "workday", "site": "adobe.wd5/external_experienced", "tier": "big"},
    {"name": "Intel", "provider": "workday", "site": "intel.wd1/External", "tier": "big"},
    {"name": "Salesforce", "provider": "workday", "site": "salesforce.wd12/External_Career_Site", "tier": "big"},
    # --- Big tech: public job boards ---------------------------------------------------
    {"name": "Stripe", "provider": "greenhouse", "slug": "stripe", "tier": "big"},
    {"name": "Databricks", "provider": "greenhouse", "slug": "databricks", "tier": "big"},
    {"name": "Airbnb", "provider": "greenhouse", "slug": "airbnb", "tier": "big"},
    {"name": "Cloudflare", "provider": "greenhouse", "slug": "cloudflare", "tier": "big"},
    {"name": "Pinterest", "provider": "greenhouse", "slug": "pinterest", "tier": "big"},
    {"name": "Coinbase", "provider": "greenhouse", "slug": "coinbase", "tier": "big"},
    {"name": "Waymo", "provider": "greenhouse", "slug": "waymo", "tier": "big"},
    {"name": "Anthropic", "provider": "greenhouse", "slug": "anthropic", "tier": "big"},
    {"name": "Palantir", "provider": "lever", "slug": "palantir", "tier": "big"},
    {"name": "Snowflake", "provider": "ashby", "slug": "snowflake", "tier": "big"},
    {"name": "OpenAI", "provider": "ashby", "slug": "openai", "tier": "big"},
    {"name": "Grab", "provider": "smartrecruiters", "slug": "grab", "tier": "big"},
    # --- Tech, AI, fintech and quant firms (several hire in HK / Singapore) -------------
    {"name": "Binance", "provider": "greenhouse", "slug": "binance", "tier": "tech"},
    {"name": "Bybit", "provider": "greenhouse", "slug": "bybit", "tier": "tech"},
    {"name": "OKX", "provider": "greenhouse", "slug": "okx", "tier": "tech"},
    {"name": "Airwallex", "provider": "ashby", "slug": "airwallex", "tier": "tech"},
    {"name": "Animoca Brands", "provider": "lever", "slug": "animocabrands", "tier": "tech"},
    {"name": "Lalamove", "provider": "lever", "slug": "lalamove", "tier": "tech"},
    {"name": "AfterShip", "provider": "greenhouse", "slug": "aftership", "tier": "tech"},
    {"name": "Ninja Van", "provider": "lever", "slug": "ninjavan", "tier": "tech"},
    {"name": "Agoda", "provider": "greenhouse", "slug": "agoda", "tier": "tech"},
    {"name": "Appier", "provider": "greenhouse", "slug": "appier", "tier": "tech"},
    {"name": "Xendit", "provider": "greenhouse", "slug": "xendit", "tier": "tech"},
    {"name": "Amber Group", "provider": "lever", "slug": "ambergroup", "tier": "tech"},
    {"name": "Squarepoint Capital", "provider": "greenhouse", "slug": "squarepointcapital", "tier": "tech"},
    {"name": "Optiver", "provider": "greenhouse", "slug": "optiverus", "tier": "tech"},
    {"name": "IMC Trading", "provider": "greenhouse", "slug": "imc", "tier": "tech"},
    {"name": "Jump Trading", "provider": "greenhouse", "slug": "jumptrading", "tier": "tech"},
    {"name": "Flow Traders", "provider": "greenhouse", "slug": "flowtraders", "tier": "tech"},
    {"name": "Hudson River Trading", "provider": "greenhouse", "slug": "wehrtyou", "tier": "tech"},
    {"name": "Tower Research Capital", "provider": "greenhouse", "slug": "towerresearchcapital", "tier": "tech"},
    {"name": "DRW", "provider": "greenhouse", "slug": "drweng", "tier": "tech"},
    {"name": "Point72", "provider": "greenhouse", "slug": "point72", "tier": "tech"},
    {"name": "Akuna Capital", "provider": "greenhouse", "slug": "akunacapital", "tier": "tech"},
    {"name": "Virtu Financial", "provider": "greenhouse", "slug": "virtu", "tier": "tech"},
    {"name": "Scale AI", "provider": "greenhouse", "slug": "scaleai", "tier": "tech"},
    {"name": "Together AI", "provider": "greenhouse", "slug": "togetherai", "tier": "tech"},
    {"name": "Figma", "provider": "greenhouse", "slug": "figma", "tier": "tech"},
    {"name": "Sierra", "provider": "ashby", "slug": "sierra", "tier": "tech"},
    {"name": "Perplexity", "provider": "ashby", "slug": "perplexity", "tier": "tech"},
    {"name": "Cohere", "provider": "ashby", "slug": "cohere", "tier": "tech"},
    {"name": "Notion", "provider": "ashby", "slug": "notion", "tier": "tech"},
    {"name": "Ramp", "provider": "ashby", "slug": "ramp", "tier": "tech"},
    {"name": "Replit", "provider": "ashby", "slug": "replit", "tier": "tech"},
    {"name": "Modal", "provider": "ashby", "slug": "modal", "tier": "tech"},
    {"name": "Sentry", "provider": "ashby", "slug": "sentry", "tier": "tech"},
    {"name": "Vercel", "provider": "greenhouse", "slug": "vercel", "tier": "tech"},
    {"name": "MongoDB", "provider": "greenhouse", "slug": "mongodb", "tier": "tech"},
    {"name": "Datadog", "provider": "greenhouse", "slug": "datadog", "tier": "tech"},
    {"name": "Duolingo", "provider": "greenhouse", "slug": "duolingo", "tier": "tech"},
    {"name": "Lyft", "provider": "greenhouse", "slug": "lyft", "tier": "tech"},
    {"name": "Roblox", "provider": "greenhouse", "slug": "roblox", "tier": "tech"},
    {"name": "Robinhood", "provider": "greenhouse", "slug": "robinhood", "tier": "tech"},
    {"name": "Verkada", "provider": "greenhouse", "slug": "verkada", "tier": "tech"},
    {"name": "Affirm", "provider": "greenhouse", "slug": "affirm", "tier": "tech"},
    {"name": "Monzo", "provider": "greenhouse", "slug": "monzo", "tier": "tech"},
    {"name": "Bitpanda", "provider": "greenhouse", "slug": "bitpanda", "tier": "tech"},
    {"name": "Keyrock", "provider": "ashby", "slug": "keyrock", "tier": "tech"},
    # --- Community list (many more companies, mostly North America) -------------------
    {"name": "SimplifyJobs list", "provider": "simplify", "tier": "tech"},
]

# Simplify entries from these companies are tiered "big".
BIG_TECH_NAMES = {
    "google", "alphabet", "deepmind", "google deepmind", "meta", "apple", "microsoft", "amazon",
    "amazon web services", "aws", "nvidia", "netflix", "tiktok", "bytedance", "tesla", "salesforce",
    "adobe", "oracle", "ibm", "intel", "uber", "linkedin", "airbnb", "stripe", "databricks", "openai",
    "anthropic", "snowflake", "palantir", "tencent", "alibaba", "huawei", "samsung", "sony", "qualcomm",
    "amd", "cisco", "sap", "spotify", "shopee", "sea", "grab", "coinbase", "bloomberg", "x", "twitter",
    "snap", "snapchat", "dropbox", "pinterest", "cloudflare", "waymo", "paypal", "visa", "mastercard",
}

TIER_SOURCES = {"big": "intern_bigtech", "tech": "intern_tech"}
TIER_SCHEMES = {"big": "Big tech internship", "tech": "Tech internship"}
