# standup — consumer prototype

A daily standup, reconstructed from the previous day's agent sessions in the
local trace bucket. Not a dashboard: it produces the prose an engineer would
*say* in standup, project by project — what was attempted, what landed, what is
still open.

This is a `runs/` prototype, not yet wired into the CLI. It follows the
workflow→consumer pattern (a deterministic producer that ingests bucket traces,
then a renderer), the same seam as `trail blame pr`.

## Pipeline

```
bucket traces ──▶ extract_narrative.py ──▶ (LLM writes) standup-<date>.md ──▶ render_html.py ──▶ .html
                  "just enough" signal      narrative prose                    readable article
```

1. **`extract_narrative.py`** — ingests *just enough* per session to narrate, not
   the whole trace. Collapses resume/supersede chains to one logical effort
   (group by `session_id`, keep the terminal head, union files across the chain),
   then pulls three signals:
   - **what was asked** — the opening task plus the human's follow-up turns (the
     pivots in those turns are the story);
   - **what the agent reported** — its closing summary message (the landing signal
     in the agent's own words);
   - **what touched the tree** — the edited files.
   It deliberately skips full step bodies, tool output and diffs.

2. **`STANDUP-PROMPT.md`** — the generation prompt. Tells the writer to work
   project by project (attempted / landed / still-open), separate build from
   research from personal errands, and be honest about uncertainty (e.g. "fixed in
   the working tree, not yet committed"). Ends with a four-line standup-ready
   block. In a wired consumer this runs through `core.llm_provider.detect_provider()`;
   in the prototype the model writes `standup-<date>.md` directly.

3. **`render_html.py`** — renders the prose as a readable HTML *article* (headings
   + paragraphs) in the opentraces "Capsule" editorial style. Not a stat grid.

## Why narrative, not metrics

The first cut ingested cheap metrics (trace ids, step counts, file-category
badges) and rendered a dashboard. It was misleading: the day's longest session
looked like "plan 089, 1439 steps", but its user turns showed it had *pivoted*
into debugging a trace-enrollment config gap and a Codex sub-agent parser bug.
Metrics can't see a pivot; the asks and the wrap-up can.

## Run

```bash
source .venv/bin/activate
python runs/standup-demo/extract_narrative.py            # writes narrative-source-<yesterday>.md
# feed STANDUP-PROMPT.md + the narrative source to a model -> standup-<date>.md
python runs/standup-demo/render_html.py --md runs/standup-demo/standup-<date>.md
```

## Privacy

Generated outputs (`narrative-source-*`, `packet-*`, `standup-*`) embed real
content from the bucket, including personal sessions (addresses, contacts, etc.),
so they are **gitignored** and stay local. Only the code and the prompt are
committed. A wired consumer would run `security.sanitize_record` over sessions
before they reach the writer.

## Next

Wire as `opentraces trace standup [--date] [--html]`: a `daily-standup-v1`
workflow package whose `build_rows.py` is `extract_narrative.py`, plus a renderer
under the existing consumer surface, with the prompt driven through
`detect_provider()`.
