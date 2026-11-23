"""Post each new frontier advance to Bluesky and Mastodon.

Every advance on the Overall view already has a sentence, a 1200x630 card
image, and a feed entry. This module announces them: one post per base model
per date (reasoning levels together, as on the cards), with the card attached
and a link that opens the dashboard with the model highlighted.

It does nothing unless credentials are in the environment:

    BLUESKY_HANDLE, BLUESKY_APP_PASSWORD
    MASTODON_BASE_URL, MASTODON_ACCESS_TOKEN

A network without credentials is skipped, and with none at all the run exits
having read nothing but its inputs. The rules that keep it from flooding a
timeline:

- A network's first run with credentials marks every advance already in the
  data as posted and posts nothing, so history is never back-posted.
- Only advances dated within --max-age-days are ever posted.
- At most --max-posts go to each network per run, oldest first.
- An advance is recorded in the state file only after the network accepted
  the post, and the file is rewritten after each one.
- A failure on one network never blocks the other and never fails the run;
  it is reported as a GitHub Actions warning annotation.

Standard library only, like the updater.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .render import DEFAULT_IMAGES, card_summary, fmt_cost, fmt_score, group_table, long_date, tier_text
from .update import DEFAULT_OUTPUT, DEFAULT_SITE, join_and, slugify, taken_clause

DEFAULT_STATE = Path("data/social-posted.json")
DEFAULT_HANDLES = Path("data/social-handles.json")
DEFAULT_MAX_POSTS = 4
DEFAULT_MAX_AGE_DAYS = 3

# Bluesky allows 300 graphemes per post. Code points are counted instead: a
# grapheme is at least one code point, so the count can only err on the short
# side, and the standard library has no grapheme segmentation.
BLUESKY_LIMIT = 300
MASTODON_LIMIT = 500
# Mastodon counts every link as 23 characters, whatever its length.
MASTODON_LINK_LENGTH = 23
ALT_LIMIT = 1000
NETWORKS = ("bluesky", "mastodon")
BLUESKY_SERVICE = "https://bsky.social"
SECRET_VARS = ("BLUESKY_APP_PASSWORD", "MASTODON_ACCESS_TOKEN")


# ---- what to post ----

def advance_groups(out: dict) -> list:
    """The Overall advances grouped as the cards are: one group per base model
    per date, each ordered by descending index, oldest group first. A group's
    id is its card's file name without the extension."""
    groups = {}
    for a in out.get("advances") or []:
        groups.setdefault((a["date"], a["base"]), []).append(a)
    result = []
    for (date, base), group in groups.items():
        group = sorted(group, key=lambda a: -a["intelligence_index"])
        result.append(dict(id=f"{date}/{slugify(base)}", date=date, base=base, creator=group[0]["creator"], advances=group))
    result.sort(key=lambda g: (g["date"], g["id"]))
    return result


def card_path(group: dict, images: Path) -> Path:
    return images / "advances" / f"{group['date']}-{slugify(group['base'])}.png"


def link_for(group: dict, site: str) -> str:
    """The dashboard with the group's top model highlighted on the charts."""
    return f"{site.rstrip('/')}/#index~{slugify(group['advances'][0]['model'])}"


def lower_first(s: str) -> str:
    return s[:1].lower() + s[1:]


def level_list(advs: list) -> str:
    """The group's reasoning levels, lowest first. A qualifier every level
    shares ("Max, Default Fallback") is said once, after the levels."""
    levels = [a["variant"] or a["model"] for a in reversed(advs)]
    heads = [v.split(", ", 1)[0] for v in levels]
    tails = {v.split(", ", 1)[1] if ", " in v else "" for v in levels}
    if len(tails) == 1 and "" not in tails:
        return f"{join_and(heads)}; {tails.pop()}"
    return "; ".join(levels) if any("," in v for v in levels) else join_and(levels)


def clauses(group: dict, mention: str = None) -> tuple:
    """The post's sentences: (headline, [(priority, position, text), ...]).
    The headline always goes out. Each optional clause is added, most
    important first, while the post still fits; position is where it sits in
    the finished text. A clause at position 0 replaces the headline with a
    longer form of it."""
    advs = group["advances"]
    records = sorted({t for a in advs for t in a["records"]})
    optional = []
    if len(advs) == 1:
        a = advs[0]
        # The card's own sentence, without the parts carried as clauses below.
        # A price change too small to show at the printed precision reads as
        # the model's arrival on the frontier, not as "moved from $x to $x".
        same_price = a["previous_cost"] and fmt_cost(a["previous_cost"]) == fmt_cost(a["cost_per_task"])
        core = card_summary(dict(a, records=[], open_weights=False, previous_cost=None if same_price else a["previous_cost"]))
        headline = f"{a['model']}: {lower_first(core)}"
        taken = taken_clause(a.get("taken_from") or [], a.get("displaced") or [])
        if taken:
            optional.append((3, 0, headline[:-1] + taken + "."))
    else:
        costs = [a["cost_per_task"] for a in advs]
        n = len(advs)
        what = ("price changes moved the cost frontier at {n} reasoning levels, now {lo} to {hi} per task."
                if all(a["kind"] == "price change" for a in advs)
                else "now on the cost frontier at {n} reasoning levels, from {lo} to {hi} per task.")
        what = what.format(n=n, lo=fmt_cost(min(costs)), hi=fmt_cost(max(costs)))
        headline = f"{group['base']}: {what}"
        ceiling = advs[0].get("ceiling_from")
        if ceiling is not None:
            optional.append((1, 1, f"Pushed the intelligence ceiling from {fmt_score(ceiling)} to {fmt_score(advs[0]['owns_to'])}."))
        optional.append((5, 0, f"{group['base']} ({level_list(advs)}): {what}"))
    if records:
        optional.append((2, 2, "New cost record for " + join_and([tier_text(t) for t in records]) + "."))
    if all(a["open_weights"] for a in advs):
        optional.append((4, 3, "Open weights."))
    if mention:
        optional.append((2.5, 4, f"From {mention}."))
    return headline, sorted(optional)


def truncate_words(text: str, limit: int) -> str:
    """Cut at a word boundary and end with an ellipsis; never mid-word."""
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 1, 0)]
    if not text[len(cut)].isspace() and " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:") + "…"


def fit_body(headline: str, optional: list, limit: int) -> str:
    """The longest body within the limit: the headline, plus each optional
    clause in priority order while it still fits."""
    chosen = {}
    def body(extra=None):
        parts = dict(chosen)
        if extra:
            parts[extra[1]] = extra[2]
        return " ".join([parts.get(0, headline)] + [parts[k] for k in sorted(parts) if k != 0])
    for clause in optional:
        if len(body(clause)) <= limit:
            chosen[clause[1]] = clause[2]
    return truncate_words(body(), limit)


def alt_text(group: dict, body: str) -> str:
    advs = group["advances"]
    alt = body + (f" The card charts every model measured on {long_date(group['date'])} by Intelligence Index against "
                  f"cost per task on a log scale, with the Pareto frontier before and after the advance and "
                  f"{group['base']} highlighted.")
    if len(advs) > 1:
        rows = group_table(advs)[1:]
        alt += " By reasoning level: " + "; ".join(f"{name}, {cost} per task, index {span}" for name, cost, span in rows) + "."
    return truncate_words(alt, ALT_LIMIT)


def compose(group: dict, network: str, site: str = DEFAULT_SITE, handles: dict = None) -> dict:
    """The post for one network: text, link, where the link and any mention
    sit in the text, and the image's alt text.

    On Bluesky the link is shown as the site's host and carries the full
    address in a facet, so the address does not eat the 300-grapheme limit.
    Mastodon needs the address in the text and counts it as 23 characters."""
    handle = ((handles or {}).get(group["creator"]) or {}).get(network)
    mention = None
    if handle:
        mention = "@" + handle.lstrip("@")
    link = link_for(group, site)
    headline, optional = clauses(group, mention)
    if network == "bluesky":
        shown = urllib.parse.urlparse(link).netloc
        body = fit_body(headline, optional, BLUESKY_LIMIT - len(shown) - 1)
    else:
        shown = link
        body = fit_body(headline, optional, MASTODON_LIMIT - MASTODON_LINK_LENGTH - 1)
    text = f"{body}\n{shown}"
    plain_headline, plain_optional = clauses(group)
    return dict(id=group["id"], text=text, link=link, link_text=shown,
                mention=mention if mention and f"From {mention}." in text else None,
                alt=alt_text(group, fit_body(plain_headline, plain_optional, MASTODON_LIMIT)))


def byte_span(text: str, part: str) -> tuple:
    """UTF-8 byte offsets of the last occurrence of part in text, as Bluesky
    facets want them."""
    start = len(text[: text.rindex(part)].encode("utf-8"))
    return start, start + len(part.encode("utf-8"))


# ---- which ones, and when ----

def load_json(path: Path, default):
    return json.loads(path.read_text()) if path and path.exists() else default


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: sorted(set(v)) for k, v in sorted(state.items())}, indent=1) + "\n")


def pending(groups: list, posted: list, today: dt.date, max_age_days: int, max_posts: int) -> list:
    """Groups not yet posted and dated within the age window, oldest first,
    capped at max_posts."""
    done = set(posted)
    oldest = (today - dt.timedelta(days=max_age_days)).isoformat()
    todo = [g for g in groups if g["id"] not in done and oldest <= g["date"] <= today.isoformat()]
    return todo[: max(max_posts, 0)]


# ---- the networks ----

def urllib_http(method: str, url: str, headers: dict, body: bytes = None) -> tuple:
    """(status, response bytes) for one request; an HTTP error status is
    returned, not raised."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def redact(text: str, env: dict) -> str:
    for name in SECRET_VARS:
        if env.get(name):
            text = text.replace(env[name], "***")
    return text


def call(http, method: str, url: str, headers: dict, body: bytes = None, ok=(200,)) -> dict:
    status, raw = http(method, url, headers, body)
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        data = {}
    if status not in ok:
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("message") or data.get("error") or "")[:200]
        raise RuntimeError(f"{method} {urllib.parse.urlparse(url).path} returned {status}" + (f": {detail}" if detail else ""))
    return data if isinstance(data, dict) else {}


def json_call(http, url: str, payload: dict, token: str = None, headers: dict = None, ok=(200,)) -> dict:
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return call(http, "POST", url, h, json.dumps(payload).encode("utf-8"), ok)


class Bluesky:
    name = "bluesky"

    def __init__(self, env: dict, http):
        self.handle = (env.get("BLUESKY_HANDLE") or "").strip().lstrip("@")
        self.password = (env.get("BLUESKY_APP_PASSWORD") or "").strip()
        self.service = (env.get("BLUESKY_SERVICE") or BLUESKY_SERVICE).rstrip("/")
        self.http = http
        self.session = None

    def configured(self) -> bool:
        return bool(self.handle and self.password)

    def login(self) -> None:
        if not self.session:
            self.session = json_call(self.http, f"{self.service}/xrpc/com.atproto.server.createSession",
                                     dict(identifier=self.handle, password=self.password))

    def resolve(self, handle: str) -> str:
        url = f"{self.service}/xrpc/com.atproto.identity.resolveHandle?" + urllib.parse.urlencode({"handle": handle})
        return call(self.http, "GET", url, {}).get("did")

    def post(self, post: dict, image: bytes = None) -> str:
        self.login()
        token, text = self.session["accessJwt"], post["text"]
        start, end = byte_span(text, post["link_text"])
        facets = [dict(index=dict(byteStart=start, byteEnd=end),
                       features=[{"$type": "app.bsky.richtext.facet#link", "uri": post["link"]}])]
        if post.get("mention"):
            # An unresolved handle stays in the text as plain words.
            did = None
            try:
                did = self.resolve(post["mention"].lstrip("@"))
            except RuntimeError as err:
                print(f"::warning::bluesky: could not resolve {post['mention']}: {err}")
            if did:
                start, end = byte_span(text, post["mention"])
                facets.append(dict(index=dict(byteStart=start, byteEnd=end),
                                   features=[{"$type": "app.bsky.richtext.facet#mention", "did": did}]))
        record = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"], "facets": facets,
                  "createdAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        if image:
            blob = call(self.http, "POST", f"{self.service}/xrpc/com.atproto.repo.uploadBlob",
                        {"Content-Type": "image/png", "Authorization": f"Bearer {token}"}, image)["blob"]
            record["embed"] = {"$type": "app.bsky.embed.images",
                               "images": [dict(alt=post["alt"], image=blob, aspectRatio=dict(width=1200, height=630))]}
        made = json_call(self.http, f"{self.service}/xrpc/com.atproto.repo.createRecord",
                         dict(repo=self.session["did"], collection="app.bsky.feed.post", record=record), token)
        return made.get("uri") or "posted"


class Mastodon:
    name = "mastodon"

    def __init__(self, env: dict, http, sleep=time.sleep):
        base = (env.get("MASTODON_BASE_URL") or "").strip().rstrip("/")
        if base and "://" not in base:
            base = "https://" + base
        self.base = base
        self.token = (env.get("MASTODON_ACCESS_TOKEN") or "").strip()
        self.http = http
        self.sleep = sleep

    def configured(self) -> bool:
        return bool(self.base and self.token)

    def upload(self, image: bytes, alt: str) -> str:
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="description"\r\n\r\n{alt}\r\n'.encode("utf-8"),
                 f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="card.png"\r\n'
                 f"Content-Type: image/png\r\n\r\n".encode("utf-8") + image + b"\r\n",
                 f"--{boundary}--\r\n".encode("utf-8")]
        auth = {"Authorization": f"Bearer {self.token}"}
        status, raw = self.http("POST", f"{self.base}/api/v2/media",
                                {**auth, "Content-Type": f"multipart/form-data; boundary={boundary}"}, b"".join(parts))
        if status not in (200, 202):
            raise RuntimeError(f"POST /api/v2/media returned {status}")
        media_id = json.loads(raw.decode("utf-8"))["id"]
        # 202 means the server is still processing the file; a status may
        # only reference it once GET returns 200.
        for _ in range(10):
            if status == 200:
                return media_id
            self.sleep(2)
            status, _raw = self.http("GET", f"{self.base}/api/v1/media/{media_id}", auth, None)
            if status not in (200, 206):
                raise RuntimeError(f"GET /api/v1/media returned {status}")
        raise RuntimeError("media was still processing after 20 seconds")

    def post(self, post: dict, image: bytes = None) -> str:
        payload = dict(status=post["text"], visibility="public", language="en")
        if image:
            payload["media_ids"] = [self.upload(image, post["alt"])]
        # The key makes a retry of the same advance a no-op on the server.
        key = hashlib.sha256(f"{self.base}|{post['id']}".encode("utf-8")).hexdigest()
        made = json_call(self.http, f"{self.base}/api/v1/statuses", payload, self.token, {"Idempotency-Key": key})
        return made.get("url") or "posted"


# ---- the run ----

def show(post: dict, card: Path) -> None:
    print(post["text"])
    print(f"  [link] {post['link']}")
    print(f"  [image] {card}" + ("" if card.exists() else " (missing; the post goes out without it)"))
    print(f"  [alt] {post['alt']}")


def run(out: dict, state_path: Path, images: Path, handles: dict, env: dict, site: str = DEFAULT_SITE,
        today: dt.date = None, max_posts: int = DEFAULT_MAX_POSTS, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        dry_run: bool = False, http=urllib_http, sleep=time.sleep) -> dict:
    """Post what is due on each configured network; returns {network: [ids posted]}.
    A dry run needs no credentials, prints what each network would be sent,
    and writes nothing."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    groups = advance_groups(out)
    state = load_json(state_path, {})
    clients = [Bluesky(env, http), Mastodon(env, http, sleep)]
    if not dry_run and not any(c.configured() for c in clients):
        print("no Bluesky or Mastodon credentials configured; nothing to do")
        return {}
    done = {}
    for client in clients:
        name = client.name
        if not dry_run and not client.configured():
            print(f"{name}: no credentials configured; skipped")
            continue
        if name not in state:
            # The first run: everything already in the data counts as posted.
            print(f"{name}: first run; " + ("would mark" if dry_run else "marked") +
                  f" {len(groups)} existing advances as posted, and posts nothing")
            if not dry_run:
                state[name] = [g["id"] for g in groups]
                save_state(state_path, state)
            continue
        todo = pending(groups, state[name], today, max_age_days, max_posts)
        print(f"{name}: {len(todo)} advance(s) to post")
        done[name] = []
        for group in todo:
            post = compose(group, name, site, handles)
            card = card_path(group, images)
            if dry_run:
                show(post, card)
                continue
            try:
                url = client.post(post, card.read_bytes() if card.exists() else None)
            except Exception as err:  # noqa: BLE001
                # One network's trouble is reported, never raised: the other
                # network still gets its posts and the workflow run stays green.
                print(f"::warning::{name}: could not post {group['id']}: {redact(' '.join(str(err).split()), env)}")
                break
            state[name] = list(state[name]) + [group["id"]]
            save_state(state_path, state)
            done[name].append(group["id"])
            print(f"{name}: posted {group['id']} ({url})")
    return done


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m llm_cost_frontier.social", description=__doc__.splitlines()[0])
    p.add_argument("--data", type=Path, default=DEFAULT_OUTPUT, help="dashboard JSON to read the advances from")
    p.add_argument("--images", type=Path, default=DEFAULT_IMAGES, help="directory holding the rendered cards")
    p.add_argument("--state", type=Path, default=DEFAULT_STATE, help="record of what was posted to each network (read and rewritten)")
    p.add_argument("--handles", type=Path, default=DEFAULT_HANDLES, help="hand-maintained lab handles to mention")
    p.add_argument("--site", default=DEFAULT_SITE, help="base URL used for the link in each post")
    p.add_argument("--max-posts", type=int, default=DEFAULT_MAX_POSTS, help="most posts per network per run")
    p.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS, help="never post an advance older than this")
    p.add_argument("--today", type=dt.date.fromisoformat, default=None, help="date the age window ends on (default: today, UTC)")
    p.add_argument("--dry-run", action="store_true", help="print what would be posted; needs no credentials and writes nothing")
    p.add_argument("--preview", type=int, default=0, metavar="N",
                   help="print the posts for the N most recent advances, whatever the state file says, and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out = json.loads(args.data.read_text())
    handles = load_json(args.handles, {})
    if args.preview:
        for group in advance_groups(out)[-args.preview:]:
            for name in NETWORKS:
                print(f"--- {name}: {group['id']}")
                show(compose(group, name, args.site, handles), card_path(group, args.images))
        return 0
    run(out, args.state, args.images, handles, dict(os.environ), args.site, args.today,
        args.max_posts, args.max_age_days, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
