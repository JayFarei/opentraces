# standup — consumer prototype

A daily standup, reconstructed from OpenTraces evidence in the local trace
bucket. Not a dashboard: it produces the prose an engineer would *say* in
standup, project by project — what was attempted, what landed, what is ready for
review, and what is still open.

This is a public example of a prototype, not yet wired into the CLI. It follows the
workflow→consumer pattern (a deterministic producer that ingests bucket traces,
then a renderer), the same seam as `trail blame pr`.

## Task

Turn sanitized OpenTraces evidence into a narrative daily standup article. The
prompt accepts session summaries, trace capsules, and pull request reports as
inputs, then writes a shareable standup with a short read-aloud block.

## Inputs

- Sanitized narrative session summaries from bucket traces.
- Optional shareable trace capsules for concrete bugs, reproductions, or lessons.
- Optional `opentraces trail blame pr render` reports for branch or PR status.
- `sample-evidence-packet.md` and `sample-standup.md` - synthetic public
  fixtures showing the intended prompt input and output shape.

## Pipeline

```
bucket traces ──▶ extract_narrative.py ──▶ STANDUP-PROMPT.md ──▶ standup-<date>.md ──▶ render_html.py ──▶ .html
                  "just enough" signal      session/PR/capsule prose           readable article
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
   It deliberately skips full step bodies, tool output and diffs. A fuller wired
   consumer can add trace capsules and `trail blame pr render` output to the same
   evidence packet.

2. **`STANDUP-PROMPT.md`** — a shareable generation prompt. It tells the writer
   how to use narrative summaries, trace capsules, and PR reports as evidence,
   then produce project paragraphs plus a four-line standup-ready block. In a
   wired consumer this runs through `core.llm_provider.detect_provider()`; in the
   prototype the model writes `standup-<date>.md` directly.

3. **`render_html.py`** — renders the prose as a readable HTML *article* (headings
   + paragraphs) in the opentraces "Capsule" editorial style. Not a stat grid.

## Evidence Types

- **Session summaries** explain intent, pivots, agent wrap-up, and files touched.
- **Trace capsules** explain concrete bugs, reproductions, dependency unblocks,
  workflow lessons, or follow-up tasks that are worth sharing.
- **Pull request reports** explain what actually shipped, what is ready for
  review, and which trace evidence backs the branch.

Metrics can support the packet, but they are not the standup. The prompt should
turn evidence into accountable prose: attempted, landed, ready for review, open.

## Run

Inspect the committed public fixture:

```bash
sed -n '1,120p' examples/standup/sample-evidence-packet.md
sed -n '1,120p' examples/standup/sample-standup.md
python examples/standup/render_html.py --md examples/standup/sample-standup.md --out /tmp/sample-standup.html
```

Run against the local private bucket:

```bash
source .venv/bin/activate
python examples/standup/extract_narrative.py             # writes narrative-source-<yesterday>.md
# optionally append trace capsules and `opentraces trail blame pr render` output
# feed STANDUP-PROMPT.md + the evidence packet to a model -> standup-<date>.md
python examples/standup/render_html.py --md examples/standup/standup-<date>.md
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
