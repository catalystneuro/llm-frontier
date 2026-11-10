"""Render the dashboard's data into an assembled site directory as plain HTML.

The dashboard script draws every number on the page from
data/llm-frontier.json, so a reader without JavaScript (a crawler, a link
preview service, an answer engine) sees a page with no data in it. This
module runs after the site is assembled for deployment and writes the same
content the script writes on load into the same elements: the metric tabs,
the lead paragraphs, the "cheapest model at or above" table, the tier
records table, the first page of frontier advances, and the update date.
The script replaces each of these when it runs, with identical markup in
the default view, so nothing changes for a reader with JavaScript.

It also stamps the data's date into each page's structured data, writes a
static page per model under /models/ (with an index of all of them), and
writes sitemap.xml from the pages that exist.

Only the default view of each page is rendered: cost axis, current index
version, first page of advances. The charts stay script-drawn.

Standard library only. Usage:

    python -m llm_cost_frontier.prerender --site _site --data build/llm-frontier.json
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .update import DEFAULT_SITE, MIN_HALVING_SPAN_DAYS, join_and, slugify, split_variant, taken_clause

ADV_DAYS_PER_PAGE = 6  # the dashboard's first page of advances
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
# Directories of the assembled site that are not pages of their own.
SITEMAP_SKIP = {"previews", "images", "data", "js"}


# ---- formatting, matching the dashboard script ----

def esc(text) -> str:
    return html.escape(str(text), quote=True)


def fixed(v: float, digits: int) -> str:
    """JavaScript's Number.toFixed: half rounds away from zero on the exact
    binary value, where Python's format rounds half to even."""
    q = Decimal(1).scaleb(-digits)
    return str(Decimal(v).quantize(q, rounding=ROUND_HALF_UP))


def number(v) -> str:
    """A number as JavaScript prints it: 1.0 is "1", 14.6 is "14.6"."""
    return str(int(v)) if float(v) == int(v) else repr(float(v))


def fmt_cost(c: float) -> str:
    return "$" + (fixed(c, 2) if c >= 0.1 else fixed(c, 3) if c >= 0.01 else fixed(c, 4))


def fmt_date(d: str) -> str:
    y, m, day = d.split("-")
    return f"{MONTHS[int(m) - 1]} {int(day)}, {y}"


def fmt_updated(data: dict) -> str:
    """The moment the data last changed. The script shows it in the reader's
    own time zone; the static text can only give UTC."""
    stamp = data.get("updated_at")
    try:
        t = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return fmt_date(data["updated"])
    hour = t.hour % 12 or 12
    return f"{MONTHS[t.month - 1]} {t.day}, {t.year} at {hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'} UTC"


def fmt_score(v: float, cap: dict | None) -> str:
    return fixed(v, 1) + ("%" if cap and cap["percent"] else "")


def tier_label(t, cap: dict | None) -> str:
    return f"≥ {t}" + ("%" if cap and cap["percent"] else "")


def cost_unit(cap: dict | None) -> str:
    return (cap or {}).get("unit") or "per task"


def metric_name(cap: dict | None) -> str:
    return cap["metric"] if cap else "Index"


# ---- the page's view of the data ----

def cap_index(data: dict, cap: dict) -> int:
    return [c["key"] for c in data["capabilities"]].index(cap["key"])


def own(cap: dict | None) -> bool:
    return bool(cap and cap.get("own"))


def page_models(data: dict, cap: dict | None) -> list:
    """The active tab's models as dicts, with the tab's score."""
    rows = (data.get("decision_models") or []) if own(cap) else data["models"]
    n_eras = len(page_eras(data, cap))
    i = None if cap is None or own(cap) else cap_index(data, cap)
    out = []
    for m in rows:
        score = m[3] if i is None else ((m[8] or [None] * (i + 1))[i] if len(m) > 8 else None)
        out.append(dict(name=m[0], creator=m[1], date=m[2], score=score, cost=m[4], retired=bool(m[5]),
                        open=None if m[6] is None else bool(m[6]),
                        era=m[9] if len(m) > 9 and m[9] is not None else n_eras,
                        kind=m[13] if len(m) > 13 else None))
    return out


def page_eras(data: dict, cap: dict | None) -> list:
    if own(cap):
        return (data.get("decision_eras") or []) if data.get("decision_era_snapshots") else []
    return data.get("eras") or []


def era_snaps(data: dict, cap: dict | None):
    return data.get("decision_era_snapshots") if own(cap) else data.get("era_snapshots")


def era_of(date: str, eras: list) -> int:
    return sum(1 for e in eras if e[0] <= date)


def era_label(view: int, eras: list) -> str:
    if not eras:
        return ""
    if view >= len(eras):
        return eras[-1][2] or ""
    return (eras[0][3] or "") if view == 0 else (eras[view - 1][2] or "")


def name_span(name: str, known: set, target: str | None = None) -> str:
    """A model name as the script's nameLink() writes it."""
    t = target or name
    if t in known:
        return (f'<span class="pfc-name-link" role="button" tabindex="0" '
                f'title="Highlight {esc(t)} on the charts">{esc(name)}</span>')
    return f"<span>{esc(name)}</span>"


# ---- the pieces ----

def tabs_html(data: dict, cap: dict | None, root: str) -> str:
    tabs = [("Overall", None)] + [(c["label"], c["key"]) for c in data.get("capabilities") or []]
    if len(tabs) == 1:
        return ""
    cur = cap["key"] if cap else None
    out = []
    for label, key in tabs:
        if key == cur:
            out.append(f'<span class="pfc-tab" role="tab" aria-selected="true">{esc(label)}</span>')
        else:
            href = (root or "./") if key is None else f"{root}{key}/"
            out.append(f'<a class="pfc-tab" role="tab" aria-selected="false" href="{esc(href)}">{esc(label)}</a>')
    return "".join(out)


def frontier_lead(data: dict, cap: dict) -> str:
    models = page_models(data, cap)
    n = sum(1 for m in models if m["score"] is not None)
    txt = cap["blurb"]
    metric = cap["metric"]
    hit, at = None, -1
    for cand in (metric, re.sub(r"^AA[ -]", "", metric), re.sub(r"-AA$", "", metric)):
        at = txt.find(cand)
        if at >= 0:
            hit = cand
            break
    if cap.get("url") and at >= 0:
        out = esc(txt[:at]) + f'<a href="{esc(cap["url"])}">{esc(hit)}</a>' + esc(txt[at + len(hit):])
    else:
        out = esc(txt)
    if own(cap):
        dc = data.get("decision_counts") or {}
        since = min([m["date"] for m in models] + [data["updated"]])
        live = dc.get("live", n)
        eras = page_eras(data, cap)
        ver = era_label(len(eras), eras)
        out += esc(f" {live} contestants" + (f" measured under {metric} {ver}" if ver else "")
                   + f": {dc.get('decision', 0)} decision models and {dc.get('llm', 0)} language models. "
                   f"Tracked since {fmt_date(since)}; earlier frontiers and cost records fill in as observations accumulate.")
    else:
        out += esc(f" Measured for {n} of {len(models)} tracked models.")
    return out


def records_lead(cap: dict | None) -> str:
    text = (f"The cheapest cost {cost_unit(cap)} achieved by any released model at or above each "
            f"{'Intelligence Index' if cap is None else metric_name(cap)} tier, by release date. "
            "Each step is a model that set a new low for its tier.")
    if own(cap):
        text = text.replace("released model", "measured model").replace("by release date", "by date first measured")
    return esc(text)


def advances_lead(cap: dict) -> str:
    metric = metric_name(cap)
    if own(cap):
        return esc(f"Each entry is a date on which a model became the cheapest way to reach some level of the {metric}, "
                   f"through a new contestant or a price change, derived from observed {metric} scores and costs. "
                   "Release dates are not published for decision models, so every contestant is dated by its first "
                   "observation, and the first entries are the frontier as observation began.")
    return esc(f"Each entry is a date on which a model became the cheapest way to reach some level of {metric}, "
               "through a release or a price change, derived from release dates, observed prices, and the latest "
               "measured scores. The Atom feed covers the Overall view only.")


def cap_table_rows(data: dict, cap: dict) -> str:
    """The "cheapest model at or above" table of a metric page."""
    n_eras = len(page_eras(data, cap))
    models = page_models(data, cap)
    known = {m["name"] for m in models}
    live = [m for m in models if not m["retired"] and m["score"] is not None and m["era"] == n_eras and m["cost"] is not None]
    if not live:
        return ""
    hi = int(max(m["score"] for m in live) // 10) * 10
    rows = []
    for t in (hi, hi - 10, hi - 20, hi - 30):
        cands = [m for m in live if m["score"] >= t]
        if t <= 0 or not cands:
            continue
        best = cands[0]
        for m in cands[1:]:
            if m["cost"] < best["cost"]:
                best = m
        kind = None if not best["kind"] else "decision model" if best["kind"] == "decision" else "language model"
        license_ = None if best["open"] is None else "open weights" if best["open"] else "proprietary"
        facts = " · ".join(x for x in (best["creator"], kind, license_) if x)
        rows.append(
            f'<tr><td class="pfc-td-tier">{esc(tier_label(t, cap))}</td>'
            f'<td><div class="pfc-ev"><div class="pfc-ev-top">{name_span(best["name"], known)}</div>'
            f'<div class="pfc-ev-model">{esc(facts)}</div></div></td>'
            f'<td class="pfc-td-num">{fmt_cost(best["cost"])}</td>'
            f'<td class="pfc-td-num">{fmt_score(best["score"], cap)}</td></tr>')
    return "".join(rows)


def tier_summary_for(data: dict, cap: dict | None) -> tuple:
    """(tiers, summary) for the page's default view: the rebased series when
    the tab has eras, as the script chooses."""
    if cap is None:
        tiers, summary, rebased = data["tiers"], data.get("tier_summary") or {}, data.get("tier_summary_rebased")
    else:
        key = cap["key"]
        tiers = (data.get("cap_tiers") or {}).get(key) or []
        summary = (data.get("cap_tier_summary") or {}).get(key) or {}
        rebased = (data.get("cap_tier_summary_rebased") or {}).get(key) if data.get("tier_summary_rebased") else None
    if page_eras(data, cap) and rebased:
        summary = rebased
    return tiers, summary


def tier_table_rows(data: dict, cap: dict | None) -> str:
    tiers, summary = tier_summary_for(data, cap)
    known = {m["name"] for m in page_models(data, cap)}

    def event(date, model, cost):
        return (f'<div class="pfc-ev"><div class="pfc-ev-top"><span class="pfc-ev-cost">{fmt_cost(cost)}</span>'
                f' · {esc(fmt_date(date))}</div><div class="pfc-ev-model">{name_span(model, known)}</div></div>')

    rows = []
    for t in reversed(tiers):
        s = summary.get(str(t))
        row = f'<tr><td class="pfc-td-tier">{esc(tier_label(t, cap))}</td>'
        if s:
            # The same rule as the dashboard script: a halving time needs
            # records spanning MIN_HALVING_SPAN_DAYS, checked from the
            # summary's own dates so older data files read the same way.
            span = (dt.date.fromisoformat(s["last_date"]) - dt.date.fromisoformat(s["first_date"])).days
            if s.get("halving_days") and span >= MIN_HALVING_SPAN_DAYS:
                halving = f"~{number(s['halving_days'])} days"
            elif s.get("collapse", 0) > 1:
                halving = f'<span class="pfc-td-note">records span under {MIN_HALVING_SPAN_DAYS} days</span>'
            else:
                halving = ""
            row += (f"<td>{event(s['first_date'], s['first_model'], s['first_cost'])}</td>"
                    f"<td>{event(s['last_date'], s['last_model'], s['last_cost'])}</td>"
                    f'<td class="pfc-td-num">{number(s["collapse"])}x</td>'
                    f'<td class="pfc-td-num">{halving}</td>')
        else:
            row += '<td colspan="4">not yet reached</td>'
        rows.append(row + "</tr>")
    return "".join(rows)


def advance_sentences(a: dict, cap: dict | None, with_variant: bool) -> tuple:
    """One advance as (variant or None, sentence, record note or ""), in the
    words the script's advanceLine() writes."""
    term = "index" if cap is None else metric_name(cap)
    unit = cost_unit(cap)
    lo, hi = fmt_score(a["owns_from"], cap), fmt_score(a["owns_to"], cap)
    span = f"{term} {hi}" if fixed(a["owns_to"], 1) == fixed(a["owns_from"], 1) else f"{term} {lo} to {hi}"
    ceiling = "the intelligence ceiling" if cap is None else f"the {metric_name(cap)} ceiling"
    if a["kind"] == "price change" and a.get("previous_cost"):
        s1 = (f"price moved from {fmt_cost(a['previous_cost'])} to {fmt_cost(a['cost_per_task'])} {unit}; "
              f"now the cheapest way to reach {span}")
    elif a.get("ceiling_from") is not None:
        s1 = f"pushed {ceiling} from {fmt_score(a['ceiling_from'], cap)} to {hi}, at {fmt_cost(a['cost_per_task'])} {unit}"
    else:
        s1 = f"now the cheapest way to reach {span} at {fmt_cost(a['cost_per_task'])} {unit}"
    s1 += taken_clause(a.get("taken_from") or [], a.get("displaced") or []) + ". "
    variant = a.get("variant") if with_variant else None
    if not variant:
        s1 = s1[0].upper() + s1[1:]
    rec = ""
    if a.get("records"):
        rec = "New cost record for " + join_and([f"{term} {tier_label(t, cap)}" for t in a["records"]]) + ". "
    return variant, s1, rec


def advance_line(a: dict, cap: dict | None, with_variant: bool) -> str:
    variant, s1, rec = advance_sentences(a, cap, with_variant)
    out = '<div class="pfc-adv-body">'
    if variant:
        out += f'<span class="pfc-adv-variant">{esc(variant)}: </span>'
    out += esc(s1)
    if rec:
        out += f'<span class="pfc-adv-rec">{esc(rec)}</span>'
    return out + "</div>"


def advances_html(data: dict, cap: dict | None, images_root: str) -> str:
    """The first page of the advances list, grouped by day and base model."""
    advances = (data.get("advances") if cap is None else (data.get("cap_advances") or {}).get(cap["key"])) or []
    if not advances:
        return '<p class="pfc-lead">No advances recorded for this metric.</p>'
    known = {m["name"] for m in page_models(data, cap)}
    eras = page_eras(data, cap)
    snaps = era_snaps(data, cap)
    days, by_day = [], {}
    for a in advances:
        if a["date"] not in by_day:
            by_day[a["date"]] = []
            days.append(a["date"])
        by_day[a["date"]].append(a)
    out = []
    shown_era = len(eras)
    for d in days[:ADV_DAYS_PER_PAGE]:
        d_era = era_of(d, eras)
        if d_era < shown_era and snaps:
            shown_era = d_era
            ver = era_label(d_era, eras)
            versioned = cap is None or own(cap)
            out.append('<div class="pfc-adv-era">' + esc(
                f"Measured under {metric_name(cap) if own(cap) else 'Intelligence Index'}{' ' + ver if ver else ''}, "
                f"through {fmt_date(snaps[d_era][-1][0])}. {'Scores and costs' if versioned else 'Costs'} "
                "from this version are not comparable with later ones.") + "</div>")
        groups, by_base = [], {}
        for a in by_day[d]:
            key = a.get("base") or a["model"]
            if key not in by_base:
                by_base[key] = []
                groups.append(key)
            by_base[key].append(a)
        col = ""
        for key in groups:
            group = by_base[key]
            single = len(group) == 1 and not group[0].get("variant")
            head = name_span(group[0]["model"] if single else group[0]["base"], known, group[0]["model"])
            kinds = []
            for a in group:
                if a["kind"] not in kinds:
                    kinds.append(a["kind"])
            head += "".join(f'<span class="pfc-adv-kind">{esc(k)}</span>' for k in kinds)
            if group[0].get("open_weights"):
                head += '<span class="pfc-adv-kind pfc-adv-open">open weights</span>'
            card = (f"{images_root}images/advances/{'' if cap is None else cap['key'] + '/'}"
                    f"{group[0]['date']}-{slugify(group[0].get('base') or group[0]['model'])}.png")
            head += f'<a class="pfc-adv-kind pfc-adv-card" href="{esc(card)}" target="_blank" rel="noopener">chart card</a>'
            col += (f'<div class="pfc-adv-item"><div class="pfc-adv-head">{head}</div>'
                    + "".join(advance_line(a, cap, not single) for a in group) + "</div>")
        out.append(f'<div class="pfc-adv-day"><div class="pfc-adv-date">{esc(fmt_date(d))}</div><div>{col}</div></div>')
    return "".join(out)


# ---- writing into a page ----

def set_inner(page: str, selector: str, inner: str, every: bool = False) -> str:
    """Replace the contents of the element whose opening tag matches
    `selector` (a regex for one attribute, such as r'id="pfc-advances"').
    Nested elements of the same tag are skipped over, so a second run
    replaces what the first wrote. A page without the element is returned
    unchanged."""
    pos = 0
    while True:
        m = re.compile(r"<([a-zA-Z0-9]+)\b[^>]*\b" + selector + r"[^>]*>").search(page, pos)
        if not m:
            return page
        tag = m.group(1)
        depth, at = 1, m.end()
        for t in re.compile(rf"<(/?){tag}\b[^>]*>").finditer(page, m.end()):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                at = t.start()
                break
        else:
            return page
        page = page[:m.end()] + inner + page[at:]
        if not every:
            return page
        pos = m.end() + len(inner)


def unhide(page: str, selector: str) -> str:
    """Drop the `hidden` attribute from the element matching `selector`."""
    def fix(m):
        return re.sub(r"\s+hidden(?=[\s>])", "", m.group(0))
    return re.compile(r"<[a-zA-Z0-9]+\b[^>]*\b" + selector + r"[^>]*>").sub(fix, page, count=1)


def stamp_json_ld(page: str, updated: str) -> str:
    """Set dateModified on the page's Dataset to the data's date."""
    def fix(m):
        try:
            doc = json.loads(m.group(2))
        except ValueError:
            return m.group(0)
        for node in doc.get("@graph") or [doc]:
            if node.get("@type") == "Dataset":
                node["dateModified"] = updated
        return m.group(1) + json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + m.group(3)
    return re.sub(r'(<script type="application/ld\+json">)(.*?)(</script>)', fix, page, flags=re.S)


def render_page(page: str, data: dict, cap: dict | None) -> str:
    """One dashboard page (the home page, or a metric's) with its data in it."""
    root = "../" if cap else ""
    page = set_inner(page, r'id="pfc-cap-tabs"', tabs_html(data, cap, root))
    page = set_inner(page, r'class="pfc-updated"', esc(fmt_updated(data)), every=True)
    page = set_inner(page, r'class="pfc-count"', esc(data["counts"]["total"]), every=True)
    page = set_inner(page, r'id="pfc-records-lead"', records_lead(cap))
    if cap:
        page = set_inner(page, r'id="pfc-frontier-lead"', frontier_lead(data, cap))
        page = set_inner(page, r'id="pfc-adv-lead"', advances_lead(cap))
        rows = cap_table_rows(data, cap)
        if rows:
            page = unhide(page, r'id="pfc-cap-table-wrap"')
            page = set_inner(page, r'id="pfc-cap-metric-name"', esc(cap["metric"]))
            page = set_inner(page, r'id="pfc-cap-cost-head"', esc("Cost " + cost_unit(cap)))
            page = set_inner(page, r'id="pfc-cap-table"', rows)
    page = set_inner(page, r'id="pfc-tier-table"', tier_table_rows(data, cap))
    page = set_inner(page, r'id="pfc-advances"', advances_html(data, cap, root))
    return stamp_json_ld(page, data["updated"])


def sitemap(site: Path, updated: str, base_url: str = DEFAULT_SITE, also: list = ()) -> str:
    """Every directory of the assembled site that has an index.html, so
    pages added later are listed without touching this module; `also` names
    index.html files this run is about to write."""
    paths = set()
    for index in list(site.rglob("index.html")) + [p for p in also if p.name == "index.html"]:
        rel = index.parent.relative_to(site)
        if rel.parts and rel.parts[0] in SITEMAP_SKIP:
            continue
        paths.add("" if not rel.parts else rel.as_posix() + "/")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in sorted(paths, key=lambda p: (p.count("/"), p)):
        lines.append(f"  <url><loc>{esc(base_url.rstrip('/') + '/' + path)}</loc><lastmod>{esc(updated)}</lastmod></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


# ---- a page per model ----

def fmt_time(s: float) -> str:
    if s >= 5400:
        return fixed(s / 3600, 1) + "h"
    if s >= 90:
        return fixed(s / 60, 1) + "m"
    if s < 1:
        return fixed(s * 1000, 0) + "ms"
    return (fixed(s, 0) if s >= 10 else fixed(s, 1)) + "s"


def main_models(data: dict) -> list:
    """The main models with everything a model page shows, each with a
    unique slug: the dashboard's slug of the name, numbered on a clash."""
    n_eras = len(data.get("eras") or [])
    out, seen = [], {}
    for m in data["models"]:
        slug = slugify(m[0]) or "model"
        seen[slug] = seen.get(slug, 0) + 1
        base, variant = split_variant(m[0])
        out.append(dict(
            name=m[0], creator=m[1], date=m[2], score=m[3], cost=m[4], retired=bool(m[5]),
            open=None if m[6] is None else bool(m[6]), hist=m[7] or [], caps=m[8] if len(m) > 8 and m[8] else [],
            era=m[9] if len(m) > 9 and m[9] is not None else n_eras,
            time=m[10] if len(m) > 10 else None, tps=m[11] if len(m) > 11 else None, ttft=m[12] if len(m) > 12 else None,
            slug=slug if seen[slug] == 1 else f"{slug}-{seen[slug]}", hash=slug, base=base, variant=variant))
    return out


def current_frontier(models: list, n_eras: int) -> list:
    """The frontier the chart draws today, lowest score first: the models
    measured under the current index that no other such model beats on both
    score and cost. Each entry is (model, the score the range starts above)."""
    cur = [m for m in models if m["era"] == n_eras and m["score"] is not None and m["cost"] is not None]
    front = [m for m in cur if not any(
        o["score"] >= m["score"] and o["cost"] <= m["cost"] and (o["score"] > m["score"] or o["cost"] < m["cost"]) for o in cur)]
    front.sort(key=lambda m: (m["score"], m["cost"]))
    out, below = [], 0.0
    for m in front:
        out.append((m, below))
        below = m["score"]
    return out


def lift(fragment: str, root: str) -> str:
    """Point the site chrome's relative links at the site root from a page
    `root` levels down."""
    def fix(m):
        attr, url = m.group(1), m.group(2)
        if re.match(r"(?:[a-z][a-z0-9+.-]*:|/|#)", url):
            return m.group(0)
        return f'{attr}="{root if url == "./" else root + url}"'
    return re.sub(r'\b(href|src)="([^"]*)"', fix, fragment)


def chrome(home: str, root: str) -> tuple:
    """(font links, header, footer) of the site, taken from the home page so
    a model page always wears the current chrome."""
    header = home[home.index('<header class="site-header">'):home.index("</header>") + len("</header>")]
    footer = home[home.index('<footer class="site-footer">'):home.index("</footer>") + len("</footer>")]
    # The home page's h1 is the site title; a model page's h1 is the model.
    header = re.sub(r'<h1 class="site-title">(.*?)</h1>', r'<div class="site-title">\1</div>', header, flags=re.S)
    header = header.replace(' aria-current="page"', "")
    header = lift(header, root).replace(f'href="{root}models/"', f'href="{root}models/" aria-current="page"')
    fonts = "\n".join(line.strip() for line in home[:home.index("</head>")].splitlines()
                      if "fonts.googleapis.com" in line or "fonts.gstatic.com" in line)
    return fonts, header, lift(footer, root)


def page_shell(home: str, root: str, path: str, title: str, description: str, body: str) -> str:
    fonts, header, footer = chrome(home, root)
    url = f"{DEFAULT_SITE}/{path}"
    image = f"{DEFAULT_SITE}/images/frontier-card.png"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(description)}">
  <meta property="og:title" content="{esc(title)}">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:image" content="{image}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:url" content="{esc(url)}">
  <meta property="og:site_name" content="LLM Frontier">
  <meta property="og:type" content="website">
  <link rel="canonical" href="{esc(url)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(title)}">
  <meta name="twitter:description" content="{esc(description)}">
  <meta name="twitter:image" content="{image}">
  <link rel="icon" type="image/svg+xml" href="{root}favicon.svg">
  {fonts}
  <link rel="stylesheet" href="{root}style.css">
  <link rel="stylesheet" href="{root}models.css">
</head>
<body>
  {header}

  <main class="container prose mdl">
{body}
  </main>

  {footer}
</body>
</html>
"""


def model_link(m: dict, here: str = "../") -> str:
    return f'<a href="{here}{esc(m["slug"])}/">{esc(m["name"])}</a>'


def version_phrase(m: dict, data: dict) -> tuple:
    """(label of the index version the model's score is from, whether that
    is the current one, the last day of that version or None)."""
    eras = data.get("eras") or []
    label = era_label(m["era"], eras)
    current = m["era"] >= len(eras)
    snaps = data.get("era_snapshots") or []
    end = None if current or m["era"] >= len(snaps) else snaps[m["era"]][-1][0]
    return label, current, end


def model_page(m: dict, data: dict, models: list, frontier: list, home: str) -> str:
    root = "../../"
    eras = data.get("eras") or []
    label, current, era_end = version_phrase(m, data)
    index_name = "Intelligence Index" + (f" {label}" if label else "")
    cur_label = era_label(len(eras), eras)
    license_ = None if m["open"] is None else "open weights" if m["open"] else "proprietary"
    status = "retired from live benchmarking" if m["retired"] else "live"

    # ---- where it stands on the frontier
    on = next(((fm, below) for fm, below in frontier if fm is m), None)
    if not current:
        standing = (f"{esc(m['name'])} was not re-scored under the current Intelligence Index"
                    + (f" ({esc(cur_label)})" if cur_label else "") + ", so it is not on the current chart. "
                    f"Its score and cost are from {esc(index_name)}"
                    + (f", which ran through {esc(fmt_date(era_end))}" if era_end else "") + ". "
                    "Scores and measured costs from different index versions are not comparable.")
        headline = f"Last measured under {index_name}"
    elif on:
        lo, hi = fixed(on[1], 1), fixed(m["score"], 1)
        span = f"Intelligence Index {hi}" if lo == hi else f"any Intelligence Index level above {lo} and up to {hi}"
        top = on[0] is frontier[-1][0]
        standing = (f"{esc(m['name'])} is on the current Pareto frontier: it is the cheapest way to reach {span} "
                    f"among the models measured under {esc(index_name)}, at {fmt_cost(m['cost'])} per task."
                    + (" It is also the highest-scoring model measured." if top else ""))
        headline = "On the current Pareto frontier"
    else:
        better = next((fm for fm, _ in frontier if fm["score"] >= m["score"]), None)
        standing = f"{esc(m['name'])} is not on the current Pareto frontier"
        if better:
            standing += (f": {model_link(better)} reaches Intelligence Index {fixed(better['score'], 1)} "
                         f"for {fmt_cost(better['cost'])} per task, against {fixed(m['score'], 1)} for {fmt_cost(m['cost'])} here")
        standing += "."
        headline = "Not on the current frontier"

    # ---- facts
    facts = [("Intelligence Index", fixed(m["score"], 1), label or ""),
             ("Cost per task", fmt_cost(m["cost"]), "measured by Artificial Analysis")]
    if m["time"]:
        facts.append(("Time per task", fmt_time(m["time"]), "end to end"))
    if m["tps"]:
        facts.append(("Output speed", f"{number(m['tps'])} tok/s", "median"))
    if m["ttft"]:
        facts.append(("First answer token", fmt_time(m["ttft"]), "median"))
    tiles = "".join(f'<div class="mdl-fact"><div class="mdl-fact-k">{esc(k)}</div><div class="mdl-fact-v">{esc(v)}</div>'
                    f'<div class="mdl-fact-n">{esc(n)}</div></div>' for k, v, n in facts)

    subtitle = " \u00b7 ".join(x for x in (m["creator"], license_, f"released {fmt_date(m['date'])}", status) if x)
    body = [f'    <div class="page-head">\n      <p class="mdl-crumb"><a href="../">Models</a> / {esc(m["creator"])}</p>\n'
            f'      <h1>{esc(m["name"])}</h1>\n      <p class="subtitle">{esc(subtitle)}</p>\n    </div>',
            f'    <div class="mdl-facts">{tiles}</div>',
            f'    <p class="mdl-standing"><strong>{esc(headline)}.</strong> {standing}</p>',
            f'    <p><a class="mdl-cta" href="{root}#index~{esc(m["hash"])}">See {esc(m["name"])} on the frontier chart</a></p>']

    # ---- cost records and frontier advances
    _, summary = tier_summary_for(data, None)
    records = []
    for t in data.get("tiers") or []:
        for r in (data.get("tier_cost") or {}).get(str(t)) or []:
            if r[2] == m["name"]:
                holds = (summary.get(str(t)) or {}).get("last_model") == m["name"] and era_of(r[0], eras) == len(eras)
                records.append(f"<li>{esc(fmt_date(r[0]))}: cheapest model at Intelligence Index {esc(tier_label(t, None))}, "
                               f"at {fmt_cost(r[1])} per task under {esc('Intelligence Index ' + era_label(era_of(r[0], eras), eras)).strip()}"
                               + (" (the current record)" if holds else "") + ".</li>")
    advances = [a for a in data.get("advances") or [] if a["model"] == m["name"]]
    if records or advances:
        body.append("    <h2>Frontier history</h2>")
    if advances:
        items = "".join(f"<li>{esc(fmt_date(a['date']))}: {esc(''.join(advance_sentences(a, None, False)[1:]).strip())}</li>" for a in advances)
        body.append(f'    <p>Dates on which this model became the cheapest way to reach some level of the index:</p>\n    <ul class="mdl-events">{items}</ul>')
    if records:
        body.append(f'    <p>Tier cost records it set:</p>\n    <ul class="mdl-events">{"".join(records)}</ul>')

    # ---- capability scores
    caps = [(c, m["caps"][i]) for i, c in enumerate(data.get("capabilities") or [])
            if not c.get("own") and i < len(m["caps"]) and m["caps"][i] is not None]
    if caps:
        rows = "".join(
            f'<tr><td><a href="{root}{esc(c["key"])}/#{esc(c["key"])}~{esc(m["hash"])}">{esc(c["label"])}</a></td>'
            f'<td>{esc(c["metric"])}</td><td class="pfc-td-num">{fmt_score(v, c)}</td></tr>' for c, v in caps)
        body.append('    <h2>Capability scores</h2>\n    <p>The latest measured value on each evaluation the dashboard tracks. '
                    'Each links to that evaluation\'s frontier with this model highlighted.</p>\n'
                    '    <div class="pfc-table-wrap"><table class="pfc-table mdl-table"><thead><tr><th>Capability</th><th>Evaluation</th>'
                    f'<th class="num">Score</th></tr></thead><tbody>{rows}</tbody></table></div>')

    # ---- price history
    hist, last = [], None
    for h in m["hist"]:
        key = (h[1], h[2] if len(h) > 2 else None)
        if key != last:
            hist.append(h)
            last = key
    if len(hist) > 1:
        rows = "".join(
            f'<tr><td>{esc(fmt_date(h[0]))}</td><td class="pfc-td-num">{fmt_cost(h[1])}</td>'
            f'<td class="pfc-td-num">{fixed(h[2], 1) if len(h) > 2 and h[2] is not None else ""}</td>'
            f'<td>{esc(era_label(era_of(h[0], eras), eras))}</td></tr>' for h in hist)
        body.append('    <h2>Cost and score history</h2>\n    <p>Every dated change in the measured cost per task or the index score. '
                    'A new index version re-scores every model on a different evaluation suite, so rows from different '
                    'versions are not comparable.</p>\n'
                    '    <div class="pfc-table-wrap"><table class="pfc-table mdl-table"><thead><tr><th>Date</th><th class="num">Cost per task</th>'
                    f'<th class="num">Index</th><th>Index version</th></tr></thead><tbody>{rows}</tbody></table></div>')

    # ---- related models
    siblings = [o for o in models if o is not m and o["creator"] == m["creator"] and o["base"] == m["base"] and (o["variant"] or m["variant"])]
    if siblings:
        items = "".join(f"<li>{model_link(o)} <span class=\"mdl-dim\">Index {fixed(o['score'], 1)} at {fmt_cost(o['cost'])}</span></li>"
                        for o in sorted(siblings, key=lambda o: o["cost"]))
        body.append(f'    <h2>Other reasoning levels of {esc(m["base"])}</h2>\n    <ul class="mdl-links">{items}</ul>')
    others = [o for o in models if o is not m and o["creator"] == m["creator"] and o not in siblings]
    if others:
        items = "".join(f"<li>{model_link(o)}</li>" for o in sorted(others, key=lambda o: (o["date"], o["name"]), reverse=True))
        body.append(f'    <h2>Other {esc(m["creator"])} models</h2>\n    <ul class="mdl-links">{items}</ul>')

    body.append(f'    <p class="mdl-foot">Data as of {esc(fmt_date(data["updated"]))}, from <a href="https://artificialanalysis.ai/">Artificial Analysis</a>\'s '
                f'measured cost per Intelligence Index task. The <a href="{root}methodology/">methodology</a> explains every number.</p>')

    title = f"{m['name']}: cost per task and Intelligence Index \u00b7 LLM Frontier"
    description = (f"{m['name']} by {m['creator']}: {index_name} score {fixed(m['score'], 1)} at a measured {fmt_cost(m['cost'])} per task, "
                   f"released {fmt_date(m['date'])}. " + ("On the current Pareto frontier." if on and current else
                                                         "Not on the current Pareto frontier." if current else "Retired before the current index version."))
    return page_shell(home, root, f"models/{m['slug']}/", title, description, "\n".join(body))


def models_index(data: dict, models: list, home: str) -> str:
    eras = data.get("eras") or []
    labs = {}
    for m in models:
        labs.setdefault(m["creator"], []).append(m)
    order = sorted(labs, key=lambda c: (-len(labs[c]), c))
    jump = " \u00b7 ".join(f'<a href="#{esc(slugify(c))}">{esc(c)}</a> <span class="mdl-dim">{len(labs[c])}</span>' for c in order)
    body = ['    <div class="page-head">\n      <h1>Models</h1>\n'
            f'      <p class="subtitle">Every model the dashboard tracks, {len(models)} in all, with its Intelligence Index score and '
            'measured cost per task. Each has a page of its own.</p>\n    </div>',
            f'    <p class="mdl-jump">{jump}</p>']
    for c in order:
        rows = "".join(
            f'<tr><td>{model_link(m, "")}</td><td>{esc(fmt_date(m["date"]))}</td>'
            f'<td class="pfc-td-num">{fixed(m["score"], 1)}</td><td>{esc(era_label(m["era"], eras))}</td>'
            f'<td class="pfc-td-num">{fmt_cost(m["cost"])}</td><td>{"retired" if m["retired"] else "live"}</td></tr>'
            for m in sorted(labs[c], key=lambda m: (m["date"], m["name"]), reverse=True))
        body.append(f'    <h2 id="{esc(slugify(c))}">{esc(c)}</h2>\n'
                    '    <div class="pfc-table-wrap"><table class="pfc-table mdl-table"><thead><tr><th>Model</th><th>Released</th>'
                    '<th class="num">Index</th><th>Index version</th><th class="num">Cost per task</th><th>Status</th></tr></thead>'
                    f'<tbody>{rows}</tbody></table></div>')
    body.append(f'    <p class="mdl-foot">Data as of {esc(fmt_date(data["updated"]))}. Scores from different index versions are not comparable; '
                'the <a href="../methodology/">methodology</a> explains why.</p>')
    return page_shell(home, "../", "models/", "All tracked LLMs: Intelligence Index and cost per task \u00b7 LLM Frontier",
                      f"All {len(models)} language models the LLM Frontier tracks, by lab, with each model's Intelligence Index score and "
                      "measured cost per task from Artificial Analysis.", "\n".join(body))


def model_pages(site: Path, data: dict, home: str) -> dict:
    """{path: html} for /models/ and every /models/<slug>/."""
    models = main_models(data)
    frontier = current_frontier(models, len(data.get("eras") or []))
    out = {site / "models" / "index.html": models_index(data, models, home)}
    for m in models:
        out[site / "models" / m["slug"] / "index.html"] = model_page(m, data, models, frontier, home)
    return out


def prerender(site: Path, data: dict, with_sitemap: bool = True, base_url: str = DEFAULT_SITE) -> dict:
    """Everything this step writes, as {path: text}. Nothing is written
    here, so a failure partway leaves the site as it was assembled."""
    out = {}
    home = site / "index.html"
    out[home] = render_page(home.read_text(), data, None)
    for cap in data.get("capabilities") or []:
        path = site / cap["key"] / "index.html"
        if path.exists():
            out[path] = render_page(path.read_text(), data, cap)
    out.update(model_pages(site, data, out[home]))
    if with_sitemap:
        out[site / "sitemap.xml"] = sitemap(site, data["updated"], base_url, also=list(out))
    return out


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="llm-cost-frontier-prerender", description=__doc__.splitlines()[0])
    p.add_argument("--site", type=Path, required=True, help="assembled site directory to rewrite in place")
    p.add_argument("--data", type=Path, required=True, help="the dashboard JSON the site serves")
    p.add_argument("--base-url", default=DEFAULT_SITE, help="public URL of the site, for the sitemap")
    p.add_argument("--no-sitemap", action="store_true", help="skip sitemap.xml (for previews, which are not the canonical site)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    data = json.loads(args.data.read_text())
    out = prerender(args.site, data, with_sitemap=not args.no_sitemap, base_url=args.base_url)
    for path, text in out.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"prerendered {len(out)} files in {args.site} as of {data['updated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
