# otbox journey footage (terminal-control)

A **visual review aid**: MP4 recordings of every otbox simulated-user journey
playing out inside a real PTY, across the supported harnesses (`claude`,
`codex`, `pi`, and the synthetic `echo`). Footage is rendered with
[terminal-control](https://github.com/kitlangton/terminal-control)
(`termctrl`) and collected into a self-contained gallery.

This is **additive** and **not** assertion-grade. The tmux-based
`make capture-refresh` path remains the validated, tested capture lane; this
recorder reuses the exact same box-preparation sequence (it imports
`runner.py`'s private helpers verbatim) but swaps the tmux spawn + expect
loop for `termctrl start/send/show/mark/stop` + `termctrl video`.

## Quick start

```bash
# one scenario against its native agent
make otbox-footage SCENARIO=add-helper-function

# override the harness (record the same journey against a different agent)
make otbox-footage SCENARIO=add-helper-function HARNESS=claude FPS=24

# the synthetic echo journey — needs no real agent, runs in default CI
make otbox-footage SCENARIO=echo-meta

# record every scenario for its native agent, then build the gallery
make otbox-footage-all
```

Equivalent CLI form:

```bash
python -m tests.otbox footage --scenario echo-meta --json
python -m tests.otbox footage --all --fps 20
python -m tests.otbox footage --scenario add-helper-function --harness claude --open
```

## Where the artifacts land

Per scenario × harness, under the captures tree:

```
tests/otbox/captures/<scenario>/footage/<agent>/
  ├── <scenario>.termctrl   # the raw terminal-control recording
  ├── <scenario>.mp4        # the exported video (--footer --hide-cursor)
  ├── markers.json          # navigable markers: ready, turn-N-prompt/response, stop
  ├── result.json           # FootageResult: verdict, turns, durations, paths
  └── scenario.json         # scenario provenance (name/description/digest)
```

The aggregate gallery lands under:

```
tests/otbox/captures/_footage/
  ├── gallery.html          # self-contained grid, one card per scenario × harness
  └── gallery.json          # the same data as structured JSON
```

All of this is **gitignored** (media only — the recorder code is committed).
Regenerate on demand; nothing here is checked in.

## Opening the gallery

```bash
open tests/otbox/captures/_footage/gallery.html      # macOS
# or pass --open to the CLI:
python -m tests.otbox footage --all --open
```

The gallery is a single standalone HTML file. Each card embeds its MP4 with
`<video controls preload="metadata">`, the scenario name + description, an
agent badge, the per-turn list (✓ / ✗ matched), a verdict chip
(PASS / FAIL / SKIP), and the run duration. The MP4 is referenced by a path
relative to the gallery dir, so the file stays portable inside the repo.

## Dependencies

```bash
cargo install terminal-control     # provides `termctrl` (v0.3.0+)
brew install ffmpeg                # terminal-control shells out to ffmpeg for MP4 export
```

## Graceful degradation

The recorder never crashes and never blocks default CI:

| Missing            | Result                                                            |
| ------------------ | ----------------------------------------------------------------- |
| `termctrl`         | `SKIP` ("termctrl not installed; cargo install terminal-control") |
| agent binary       | `SKIP` (binary not found for the scenario's agent)                |
| `ffmpeg`           | `SKIP` (recording is kept; MP4 export is skipped)                 |
| host not onboarded | `SKIP` (no `claude`/`codex`/`pi` login state to copy into the box)|

A turn whose `expect_regex` does not match within its timeout is encoded as
`verdict="FAIL"` + an `error_message` — but the **video is still produced**,
because the whole point of footage is to see what went wrong. `--all` SKIPs
cleanly for every absent binary and only exits non-zero on a genuine FAIL.

## Relationship to capture-refresh

| Path                        | Purpose                          | Output                |
| --------------------------- | -------------------------------- | --------------------- |
| `make capture-refresh`      | assertion-grade capture (tmux)   | box snapshot + audit  |
| `make otbox-footage`        | visual review aid (termctrl)     | MP4 + gallery         |

Both resolve a box the same way and drive the same scenarios; only the
drive + output differ. Use capture-refresh for evidence, footage for review.
