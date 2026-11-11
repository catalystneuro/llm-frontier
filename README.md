# LLM Frontier

This repository tracks how cheaply a given level of large language model capability can be bought, and how that price changes over time. Every three hours it reads Artificial Analysis's measured cost per Intelligence Index task for every model they benchmark, merges the result into a cumulative history, and rebuilds two artifacts: the JSON that the dashboard at [llm-frontier.catalystneuro.com](https://llm-frontier.catalystneuro.com/) renders, and an Atom feed of frontier advances.

The background, the method, and the argument for why the cheap end of the range matters are in the post [What Happens When the Cost of Intelligence Drops 100x](https://catalystneuro.com/blog/cost-of-intelligence-drops-100x/).

## Outputs

| File | Contents |
|---|---|
| `data/history.json` | Cumulative per-model record: release date, creator, open weights flag, retired flag, and every observed `(date, cost, index)` |
| `data/price-events.json` | Hand-maintained price changes from before nightly observation began |
| `data/overrides.json` | Hand-maintained corrections to upstream fields, currently the open weights flag; applied to the outputs, never to the history |
| `data/decision-history.json` | Cumulative record of the Decision Index contestants, in the same shape as the history; costs are USD per 1,000 tasks |
| `data/eras.json` | Hand-declared index era boundaries: dates on which the source recomposed the Intelligence Index, breaking comparability of scores and measured costs |
| `data/decision-eras.json` | The same for the Decision Index, which is versioned separately |
| `build/llm-frontier.json` | What the dashboard renders: model rows, frontier snapshots, tier records, halving times, and frontier advances |
| `build/feed.xml` | Atom feed of the last 60 frontier advances |
| `build/images/advances/` | A 1200x630 social card per advancing model per date, reasoning levels grouped, named `{date}-{base model}.png`; capability advances get theirs under `advances/{capability}/` |
| `build/images/frontier-card.png` | A social card of the current frontier, for the dashboard page's link preview |

The dashboard itself lives in `site/` and is served from this repository via GitHub Pages at [llm-frontier.catalystneuro.com](https://llm-frontier.catalystneuro.com/): a deploy workflow copies `site/` and the `build/` artifacts into the Pages tree on every push that touches them, and the update workflow redeploys after each data commit. Every same-repository pull request gets a live preview of the site at `/previews/pr-<number>/`, linked by a sticky comment on the PR and removed when it closes. The old catalystneuro.com/llm-cost-frontier URLs redirect here.

The dashboard script draws every number from the data file, so the deploy also runs `python -m llm_cost_frontier.prerender` on the assembled site: it writes the metric tabs, the tier tables, and the first page of advances into the pages as plain HTML (the script redraws the same markup on load), stamps the data's date into each page's structured data, writes a static page per model under `/models/` (score, cost, frontier standing, capability scores, price history), and writes `sitemap.xml` from the pages that exist. A reader without JavaScript, such as a crawler or a link preview, then sees the numbers. `site/robots.txt` keeps crawlers out of the pull request previews.

## Method

Any model page on artificialanalysis.ai embeds the full comparison dataset for every model they currently benchmark, including `intelligenceIndexCostPerTask`, the average billed cost to run one task from their evaluation suite with the input, reasoning, and answer tokens the run actually used. The updater fetches one page, parses that payload, and merges it into the history.

Beyond the aggregate Intelligence Index, the updater also records a set of per-capability scores from the same payload, each chosen because it maps onto a class of application better than the aggregate does: Terminal-Bench 2.1 (agentic coding), the Agentic Index (tool use), AA-LCR (long context), IFBench (instruction following), Omniscience (factual recall with hallucinations penalized), GPQA Diamond (scientific reasoning), GDPval-AA (office work products), and MMMU-Pro (multimodal input). The dashboard renders these as tabs that switch the vertical axis of the frontier chart. Capability scores are stored as their latest measured values; only prices are tracked over time. Metrics measured for only a small fraction of models (LiveCodeBench, AIME) are left out.

The Artificial Analysis [Decision Index](https://artificialanalysis.ai/evaluations/artificial-analysis-decision-index) gets a tab of its own. It is not a field of the main payload: its own page lists its own contestants (language models and dedicated decision models, which return a probability for every option in one call) with their own measured cost and time per task. The updater reads that page too, keeps `data/decision-history.json`, and runs the same frontier, record, and advance derivations on it, with costs per 1,000 tasks. Decision models have no published release dates, so every contestant is dated by its first observation (from October 19, 2026). The Decision Index is versioned separately from the Intelligence Index, so its recompositions are declared in `data/decision-eras.json` (v2.0 began on November 7, 2026) and handled like the main eras described below. A failed read of that page does not fail the run: the tab keeps its last good data and the update workflow opens an issue.

Three properties follow from keeping a history instead of a snapshot:

- **Models that leave keep their data.** When Artificial Analysis retires a model from live benchmarking, its observations stay and it is marked retired, so the record only grows.
- **Prices are dated.** An observation is appended whenever a model's cost or index changes, so a price cut is dated to when it was observed instead of being back-dated to the model's release. Frontier snapshots and tier records use the price in effect on each date.
- **Advances are derived, not curated.** A frontier advance is any date on which the Pareto frontier of (higher index, lower cost) changed, whether through a release or a price change. Each one records the index range the model took over, the models it took that range from, any tier cost record it set, and whether it pushed the intelligence ceiling.

Observation began on August 19, 2026. Before that date the only value available is a model's price at first observation, indexed by its release date, except for the price events recorded by hand in `data/price-events.json`. Earlier cuts that are not recorded make older points look cheaper than they were, which understates the collapse and dates it too early.

When the source recomposes the Intelligence Index, as it did on September 5, 2026, scores and measured costs before and after the change are not comparable. Such a change is declared by hand as an era in `data/eras.json`. Observations store the index alongside the cost, so every derivation uses the index in effect on each date: frontiers before a boundary keep the old scores, records reset at the boundary, a model's first re-measurement under a new index is not reported as an advance, and models never re-scored under the current index compete only in the eras they were measured in. Collapse and halving figures pool the within-era declines, so the full history keeps informing them without a cost ever being compared across a boundary. A date on which the measured cost moved by more than 10% for a large share of models at once is treated as a re-measurement of the evaluation suite rather than a wave of price changes, and produces no advances. The updater refuses to merge a fetch whose live set shrank by more than 30% or whose median index shift exceeds 2 points unless an era within a week of the run date has been declared, so the next recomposition stops the pipeline for a human decision instead of merging silently. The Decision Index fetch has the same median-shift guard against `data/decision-eras.json`.

Free and promotional endpoints with a measured cost of zero are excluded, since they distort the cost axis.

## Posting Advances

`llm_cost_frontier.social` announces each new frontier advance on the Overall view to Bluesky and Mastodon: one post per base model per date, reasoning levels together as on the cards, with the card image attached (with alt text) and a link that opens the dashboard with the model highlighted. The update workflow runs it in a `post` job after each deploy that followed a data change, so the card a post attaches is already live.

It is off until credentials exist. With none of the four repository secrets below set, the job exits having done nothing and commits nothing. To switch a network on, add its two secrets under Settings, Secrets and variables, Actions:

| Secret | Value |
|---|---|
| `BLUESKY_HANDLE` | The posting account's handle, such as `llm-frontier.bsky.social` |
| `BLUESKY_APP_PASSWORD` | An app password for that account: in Bluesky, Settings, Privacy and security, App passwords, Add App Password. Do not use the account password |
| `MASTODON_BASE_URL` | The account's server, such as `https://mastodon.social` |
| `MASTODON_ACCESS_TOKEN` | An access token for that account: in Mastodon, Preferences, Development, New application, with only the `write:statuses` and `write:media` scopes checked; the token is on the application's page |

The rules that keep it from flooding a timeline:

- A network's first run with credentials marks every advance already in the data as posted and posts nothing, so switching it on never back-posts history. Posting starts with the next advance.
- Only advances dated within the last three days are ever posted (`--max-age-days`), and at most four per network per run, oldest first (`--max-posts`).
- `data/social-posted.json` records what each network accepted. It is written after each accepted post and committed by the job, and an advance in it is never posted again. Deleting a network's key from the file makes its next run a first run.
- A failure on one network is reported as a warning annotation on the run. It never blocks the other network, fails the run, or opens a pipeline-failure issue, and the refused advance is retried on the next run while it is still inside the three-day window.

A post is kept within Bluesky's 300 graphemes and Mastodon's 500 characters by leaving out whole clauses, least important first, never by cutting a sentence. To mention the lab behind a model, add its handles to `data/social-handles.json`, keyed by the creator name the dashboard shows; the file ships empty, and a lab without an entry is not mentioned:

```json
{"Example Lab": {"bluesky": "example-lab.com", "mastodon": "@examplelab@mastodon.social"}}
```

To read what would be posted without credentials and without writing anything:

```bash
PYTHONPATH=src python -m llm_cost_frontier.social --preview 3   # the posts for the three most recent advances
PYTHONPATH=src python -m llm_cost_frontier.social --dry-run     # what the next run would post, given the state file
```

To turn a network off, delete its secrets; the state file can stay. X and LinkedIn are not covered: posting with an image through the X API needs paid access, and LinkedIn needs an approved developer application.

## Usage

The updater is standard library only, so it needs no dependencies to run:

```bash
PYTHONPATH=src python -m llm_cost_frontier            # fetch, merge, rebuild
PYTHONPATH=src python -m llm_cost_frontier --offline  # rebuild from the stored history
```

Or install it and use the console script:

```bash
pip install git+https://github.com/catalystneuro/llm-frontier
llm-cost-frontier --help
```

Paths and the feed's base URL are options, so the tool can write wherever you want:

```bash
llm-cost-frontier --history data/history.json --out build/llm-frontier.json \
                  --feed build/feed.xml --site https://example.org
```

A run refuses to rewrite the history if it parses fewer than 50 live models, which guards against a change in the source page's layout quietly emptying the dataset.

The test suite runs offline. The unit tests need only pytest and cover the payload parsing, the history merge, the frontier derivations, and the output assembly, checking invariants against the repository's real data so they keep passing as the data grows. A second suite drives the assembled site in a headless browser (`pip install playwright && playwright install chromium`), asserting a clean console, working tab and era switching, and phone-width behavior including rotation; it is skipped when playwright is absent. `.github/workflows/test.yml` runs both on every pull request and push to main.

```bash
pip install pytest
pytest
```

## Advance Cards

Each frontier advance also gets a 1200x630 PNG suitable for social sharing: the scatter of every model as it stood on the advance date, the frontier before and after, and the advancing model highlighted, with the region it took from the previous frontier shaded. Reasoning levels of the same base model that advance on the same date share one card, so a release that lands three variants on the frontier produces one image, not three. The shaded region and the dashed line show the frontier as it would stand without that model's change, so on a date with several advancing models each card credits only its own model. Because the state is reconstructed as of the advance date, a card never changes once rendered, so the renderer skips existing files and only new advances cost anything on the nightly run. This is the one part of the pipeline that needs a dependency:

```bash
pip install matplotlib
PYTHONPATH=src python -m llm_cost_frontier.render           # render new cards
PYTHONPATH=src python -m llm_cost_frontier.render --force   # re-render everything
```

## Schedule

`.github/workflows/update.yml` runs the updater every three hours, at 17 minutes past the hour (GitHub delays scheduled runs most at the top of the hour), commits `data/history.json` and the build artifacts when they change, and then redeploys the site. A failed run opens a `pipeline-failure` issue (or adds to the open one), and the next successful run closes it; while updates are stuck, the dashboard shows a staleness note once its data file was last deployed more than 15 hours ago. The workflow can also be run by hand from the Actions tab.

## Caveats

The Intelligence Index is one aggregate of nine evaluations, so two models with the same score may behave differently on a particular task. Cost per task is measured on a reasoning heavy evaluation suite with long prompts; a chat workload with short prompts would scale differently across models. Prices are what buyers pay, which says nothing about what inference costs the provider.

## License

BSD 3-Clause. Data is derived from [Artificial Analysis](https://artificialanalysis.ai/), whose terms govern its use.

## Citing

The site's [about page](https://llm-frontier.catalystneuro.com/about/) has a suggested citation, and `CITATION.cff` carries the same information for GitHub's "Cite this repository" button. Cite the date you read the site, since the numbers change every few hours, and credit Artificial Analysis as the source of the measurements. Events that changed how the numbers should be read (index recompositions, gaps, corrections) are logged in the site's [data notes](https://llm-frontier.catalystneuro.com/notes/), in `site/notes/index.html`; add an entry there when one happens.
