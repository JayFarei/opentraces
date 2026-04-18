# Runbook

This is the operator path for a `gnhf`-driven optimization campaign.

## 1. Preconditions

- Work from a clean git tree.
- Use the committed perf harness and baseline already in this repo.
- Run from the repo root.

## 2. Install And Configure `gnhf`

Install:

```bash
npm install -g gnhf
```

Optional `~/.gnhf/config.yml` for a Codex-first run:

```yaml
agent: codex
agentArgsOverride:
  codex:
    - -m
    - gpt-5.4
    - -c
    - model_reasoning_effort="xhigh"
maxConsecutiveFailures: 3
preventSleep: true
```

Adjust the model or arguments if your local Codex setup differs.

## 3. Reconfirm The Baseline

Before any optimization run:

```bash
./.venv/bin/pytest -q tests/perf --perf-lane smoke
OT_TUI_SMOKE=1 OT_WEB_SMOKE=1 ./.venv/bin/pytest -q tests/e2e/test_tui_tmux_smoke.py tests/e2e/test_web_agent_browser_smoke.py
python3 tests/perf/render_baseline_report.py
```

If these are not green, fix that first. Do not start the `gnhf` run from a drifting baseline.

## 4. Prove The Loop On A Short Run

Run a short validation pass before committing to all 30 iterations:

```bash
cat perf-optimization/gnhf-objective.md | gnhf \
  --agent codex \
  --max-iterations 3 \
  --stop-when "The weighted latency score is at least 40 percent better, the weighted memory score is at least 25 percent better, and perf smoke plus user smokes are green"
```

Review:

- the commits `gnhf` created
- `.gnhf/runs/<run-id>/notes.md`
- `.gnhf/runs/<run-id>/gnhf.log`
- measured before/after numbers against the rubric

## 5. Full 30-Iteration Run

When the short run looks sane:

```bash
cat perf-optimization/gnhf-objective.md | gnhf \
  --agent codex \
  --max-iterations 30 \
  --stop-when "The weighted latency score is at least 40 percent better, the weighted memory score is at least 25 percent better, and perf smoke plus user smokes are green"
```

Notes:

- `gnhf` expects a clean tree at start.
- Successful iterations are committed individually.
- Failed iterations are rolled back by `gnhf`.
- Run metadata is stored under `.gnhf/runs/`; treat it as local operator state, not product output.

## 6. Optional Worktree Mode

Use worktrees only after the single-agent path is stable. If you split work, do it by surface so experiments do not conflict:

- TUI + viewer lane
- watcher + web + core lane

Example:

```bash
cat perf-optimization/gnhf-objective.md | gnhf --agent codex --worktree --max-iterations 15
```

## 7. Post-Run Validation

After the run:

```bash
./.venv/bin/pytest -q tests/perf --perf-lane smoke
OT_TUI_SMOKE=1 OT_WEB_SMOKE=1 ./.venv/bin/pytest -q tests/e2e/test_tui_tmux_smoke.py tests/e2e/test_web_agent_browser_smoke.py
python3 tests/perf/render_baseline_report.py
git log --oneline --decorate -30
```

Then compare the new `tests/perf/BASELINE.md` with the prior version and fill in the iteration review template.

## 8. Review Discipline

- Keep only iterations that satisfy the rubric.
- Revert or drop changes that move numbers in the wrong direction.
- Do not merge a run just because it produced many commits; merge only the subset with defensible wins.
