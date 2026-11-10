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

It also stamps the data's date into each page's structured data and writes
sitemap.xml from the pages that exist.

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

from .update import DEFAULT_SITE, join_and, slugify, taken_clause

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
            halving = f"~{number(s['halving_days'])} days" if s.get("halving_days") else ""
            row += (f"<td>{event(s['first_date'], s['first_model'], s['first_cost'])}</td>"
                    f"<td>{event(s['last_date'], s['last_model'], s['last_cost'])}</td>"
                    f'<td class="pfc-td-num">{number(s["collapse"])}x</td>'
                    f'<td class="pfc-td-num">{esc(halving)}</td>')
        else:
            row += '<td colspan="4">not yet reached</td>'
        rows.append(row + "</tr>")
    return "".join(rows)


def advance_line(a: dict, cap: dict | None, with_variant: bool) -> str:
    """One advance, in the sentence the script's advanceLine() writes."""
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
    variant = with_variant and a.get("variant")
    if not variant:
        s1 = s1[0].upper() + s1[1:]
    out = '<div class="pfc-adv-body">'
    if variant:
        out += f'<span class="pfc-adv-variant">{esc(a["variant"])}: </span>'
    out += esc(s1)
    if a.get("records"):
        out += ('<span class="pfc-adv-rec">' + esc("New cost record for " + join_and(
            [f"{term} {tier_label(t, cap)}" for t in a["records"]]) + ". ") + "</span>")
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


def sitemap(site: Path, updated: str, base_url: str = DEFAULT_SITE) -> str:
    """Every directory of the assembled site that has an index.html, so
    pages added later are listed without touching this module."""
    paths = []
    for index in site.rglob("index.html"):
        rel = index.parent.relative_to(site)
        if rel.parts and rel.parts[0] in SITEMAP_SKIP:
            continue
        paths.append("" if not rel.parts else rel.as_posix() + "/")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in sorted(paths, key=lambda p: (p.count("/"), p)):
        lines.append(f"  <url><loc>{esc(base_url.rstrip('/') + '/' + path)}</loc><lastmod>{esc(updated)}</lastmod></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


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
    if with_sitemap:
        out[site / "sitemap.xml"] = sitemap(site, data["updated"], base_url)
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
        path.write_text(text)
    print(f"prerendered {len(out)} files in {args.site} as of {data['updated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
