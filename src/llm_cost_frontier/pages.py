"""Generate the per-metric pages from the dashboard's index page.

Each capability, and the Decision Index, gets a real page at /<key>/ with its own title, meta
description, canonical URL, social card, structured data, and Atom feed
link, so a metric-specific frontier can be shared, indexed, and subscribed
to directly. The pages are the root index.html with the head rewritten,
asset paths lifted one level, and data attributes that tell the
dashboard script which metric the page is about and where the site
root is.

The generated files are committed; tests/test_update.py checks they
match what this module produces, so editing site/index.html without
re-running `python -m llm_cost_frontier.pages` fails the suite.
"""
import json
import sys
from pathlib import Path

from .update import DEFAULT_SITE, METRIC_PAGES

SITE_DIR = Path(__file__).resolve().parents[2] / "site"

HOME_TITLE = "LLM Frontier: the cost of LLM capability over time"
HOME_DESCRIPTION = ("The cheapest way to reach each level of LLM capability, and how fast that cost is falling. "
                    "Refreshed every three hours from Artificial Analysis measurements.")
HOME_OG_DESCRIPTION = "The cheapest way to reach each level of LLM capability, and how fast that cost is falling."
HOME_IMAGE_ALT = "Scatter of LLM Intelligence Index against measured cost per task, with the current Pareto frontier drawn through the cheapest models"

# What someone searching for a metric's page would call the job, for the
# page title: "Cheapest LLMs for coding: cost vs Terminal-Bench 2.1".
SEARCH_USE = {
    "coding": "coding",
    "agentic": "agentic tool use",
    "longcontext": "long context",
    "instruction": "instruction following",
    "knowledge": "factual recall",
    "science": "scientific reasoning",
    "knowledgework": "knowledge work",
    "multimodal": "multimodal input",
    "decision": "decisions",
}


def metric_title(cap: dict) -> str:
    use = SEARCH_USE.get(cap["key"], cap["label"].lower())
    return f"Cheapest LLMs for {use}: cost vs {cap['metric']} · LLM Frontier"


def metric_description(cap: dict) -> str:
    return (f"The cheapest way to reach each level of {cap['metric']}, and how fast that cost is falling. "
            "Refreshed every three hours from Artificial Analysis measurements.")


def json_ld(cap: dict | None = None) -> str:
    """The page's structured data: the site, and the dataset the page shows.
    The home page describes the whole dataset and each metric page its own
    slice of it. dateModified is left to the prerender step, which knows the
    data's date. No license is asserted: the code is BSD-3-Clause, but the
    measurements are Artificial Analysis's."""
    url = f"{DEFAULT_SITE}/{cap['key']}/" if cap else f"{DEFAULT_SITE}/"
    dataset = {
        "@type": "Dataset",
        "@id": url + "#dataset",
        "name": f"LLM Frontier: cost of reaching each level of {cap['metric']}" if cap else "LLM Frontier: cost of LLM capability over time",
        "description": metric_description(cap) if cap else HOME_DESCRIPTION,
        "url": url,
        "creator": {"@type": "Organization", "name": "CatalystNeuro", "url": "https://catalystneuro.com"},
        "isBasedOn": cap["url"] if cap and cap.get("url") else "https://artificialanalysis.ai/",
        "isAccessibleForFree": True,
        "distribution": [{"@type": "DataDownload", "encodingFormat": "application/json",
                          "contentUrl": f"{DEFAULT_SITE}/data/llm-frontier.json"}],
    }
    if cap:
        dataset["isPartOf"] = {"@id": f"{DEFAULT_SITE}/#dataset"}
    graph = [{"@type": "WebSite", "@id": f"{DEFAULT_SITE}/#website", "name": "LLM Frontier", "url": f"{DEFAULT_SITE}/",
              "publisher": {"@type": "Organization", "name": "CatalystNeuro", "url": "https://catalystneuro.com"}},
             dataset]
    return ('<script type="application/ld+json">'
            + json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False, separators=(",", ":"))
            + "</script>")


def metric_page(template: str, cap: dict) -> str:
    key, label = cap["key"], cap["label"]
    s = template
    swaps = [
        (f"<title>{HOME_TITLE}</title>", f"<title>{metric_title(cap)}</title>"),
        (f'<meta name="description" content="{HOME_DESCRIPTION}">',
         f'<meta name="description" content="{metric_description(cap)}">'),
        (f'<meta property="og:title" content="{HOME_TITLE}">',
         f'<meta property="og:title" content="{label} · LLM Frontier">'),
        (f'<meta property="og:description" content="{HOME_OG_DESCRIPTION}">',
         f'<meta property="og:description" content="The cheapest way to reach each level of {cap["metric"]}, and how fast that cost is falling.">'),
        ('<meta property="og:image" content="https://llm-frontier.catalystneuro.com/images/frontier-card.png">',
         f'<meta property="og:image" content="{DEFAULT_SITE}/images/{key}-card.png">'),
        (f'<meta property="og:image:alt" content="{HOME_IMAGE_ALT}">',
         f'<meta property="og:image:alt" content="Scatter of {cap["metric"]} against measured cost, with the current Pareto frontier drawn through the cheapest models">'),
        ('<meta property="og:url" content="https://llm-frontier.catalystneuro.com/">',
         f'<meta property="og:url" content="{DEFAULT_SITE}/{key}/">'),
        (f'<meta name="twitter:title" content="{HOME_TITLE}">',
         f'<meta name="twitter:title" content="{label} · LLM Frontier">'),
        (f'<meta name="twitter:description" content="{HOME_OG_DESCRIPTION}">',
         f'<meta name="twitter:description" content="The cheapest way to reach each level of {cap["metric"]}, and how fast that cost is falling.">'),
        ('<meta name="twitter:image" content="https://llm-frontier.catalystneuro.com/images/frontier-card.png">',
         f'<meta name="twitter:image" content="{DEFAULT_SITE}/images/{key}-card.png">'),
        ('<link rel="canonical" href="https://llm-frontier.catalystneuro.com/">',
         f'<link rel="canonical" href="{DEFAULT_SITE}/{key}/">'),
        (json_ld(), json_ld(cap)),
        ('<link rel="icon" type="image/svg+xml" href="favicon.svg">',
         '<link rel="icon" type="image/svg+xml" href="../favicon.svg">'),
        ('<link rel="alternate" type="application/atom+xml" title="Frontier advances" href="feed.xml">',
         f'<link rel="alternate" type="application/atom+xml" title="{cap["metric"]} advances" href="../feed-{key}.xml">'),
        ('<link rel="stylesheet" href="style.css">', '<link rel="stylesheet" href="../style.css">'),
        ("<body>", f'<body data-cap="{key}" data-root="../">'),
        ('<script src="js/llm-frontier-dashboard.js" defer></script>',
         '<script src="../js/llm-frontier-dashboard.js" defer></script>'),
        ('<script src="js/llm-frontier-extras.js" defer></script>',
         '<script src="../js/llm-frontier-extras.js" defer></script>'),
    ]
    for old, new in swaps:
        assert old in s, f"template drifted: {old[:60]}"
        s = s.replace(old, new, 1)
    # Every root-relative link in the shared chrome lifts one level.
    for old, new in [
        ('class="site-title"><a href="./"', 'class="site-title"><a href="../"'),
        ('<a href="./" aria-current="page">Dashboard</a>', '<a href="../">Dashboard</a>'),
        ('href="cn-logo.png"', 'href="../cn-logo.png"'),
        ('src="cn-logo.png"', 'src="../cn-logo.png"'),
        ('href="methodology/"', 'href="../methodology/"'),
        ('href="models/"', 'href="../models/"'),
        ('href="about/"', 'href="../about/"'),
        ('href="notes/"', 'href="../notes/"'),
        ('href="data/llm-frontier.json"', 'href="../data/llm-frontier.json"'),
        ('href="feed.xml"', f'href="../feed-{key}.xml"'),
    ]:
        s = s.replace(old, new)
    return s


def generate() -> dict:
    template = (SITE_DIR / "index.html").read_text()
    return {SITE_DIR / c["key"] / "index.html": metric_page(template, c) for c in METRIC_PAGES}


def main(argv=None):
    for path, content in generate().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"wrote {path.relative_to(SITE_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
