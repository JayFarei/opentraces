.PHONY: version-check dirty-check clean build-schema build-cli build \
       test test-premerge test-premerge-shard test-premerge-timing test-integration-shard \
       lint publish-schema publish-cli publish-test-schema publish-test-cli \
       tag release brew-update otbox-slice otbox-journeys otbox-tier1 \
       otbox-matrix otbox-inventory otbox-gc otbox-agent-session otbox-live-hf otbox-scale release-gate \
       slicer-soft-evidence \
       capture-refresh \
       capture-refresh-check capture-refresh-all \
       otbox-acceptance \
       otbox-footage otbox-footage-all \
       search-eval search-eval-real search-eval-xl search-eval-slope \
       search-eval-cache search-eval-live search-eval-profile search-eval-test

SCHEMA_DIR := packages/opentraces-schema
VERSION := $(shell python3 -c "import re; m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('src/opentraces/__init__.py').read()); print(m.group(1))")
SCHEMA_VERSION := $(shell python3 -c "import re; m=re.search(r'SCHEMA_VERSION\s*=\s*\"([^\"]+)\"', open('$(SCHEMA_DIR)/src/opentraces_schema/version.py').read()); print(m.group(1))")

# ---------- Guards ----------

version-check:
	@echo "CLI version:    $(VERSION)"
	@echo "Schema version: $(SCHEMA_VERSION)"
	@python3 -c "import re; v='$(VERSION)'; assert re.match(r'^\d+\.\d+\.\d+$$', v), f'Bad CLI version: {v}'"
	@python3 -c "import re; v='$(SCHEMA_VERSION)'; assert re.match(r'^\d+\.\d+\.\d+$$', v), f'Bad schema version: {v}'"

dirty-check:
	@test -z "$$(git status --porcelain)" || (echo "ERROR: Working tree is dirty. Commit or stash first." && exit 1)

# ---------- Clean ----------

clean:
	rm -rf dist/ build/ $(SCHEMA_DIR)/dist/ $(SCHEMA_DIR)/build/

# ---------- Build ----------

build-schema:
	cd $(SCHEMA_DIR) && python3 -m build

build-cli:
	python3 -m build

build: clean build-schema build-cli

# ---------- Test ----------

test:
	python3 -m pytest tests/ -v

PYTEST_CI_MARKS := not perf and not real_repl and not trail_real_repl and not user_smoke
PYTEST_XDIST ?= auto
SHARD_INDEX ?= 0
SHARD_TOTAL ?= 1
PYTEST_CI_DESELECTS := \
	--deselect "tests/integration/test_bucket_dataset_remote_flow_uat.py::test_restored_private_bucket_feeds_dataset_publish_without_leaking_bucket" \
	--deselect "tests/integration/test_bucket_remote_uat.py::test_installed_runtime_syncs_bucket_to_fake_remote_and_restores" \
	--deselect "tests/integration/test_trace_trails_corpus.py::test_trace_trails_corpus_fixture_is_current" \
	--deselect "tests/integration/test_probe_a9_track_survival_speed.py::test_probe_a9_track_survival_speed" \
	--deselect "tests/integration/test_probe_a10_no_glob_guard.py::test_probe_a10_no_glob_guard" \
	--deselect "tests/integration/test_probe_b1_creation_bijection.py::test_probe_b1_creation_bijection" \
	--deselect "tests/integration/test_probe_b2_manifest_events_count.py::test_probe_b2_manifest_events_count" \
	--deselect "tests/integration/test_probe_b3_per_patch_attribution.py::test_probe_b3_per_patch_attribution" \
	--deselect "tests/integration/test_probe_b4_lineage_surface_agreement.py::test_probe_b4_lineage_surface_agreement"

test-premerge: test-premerge-shard test-premerge-timing

test-premerge-shard:
	@files="$$(python3 scripts/ci_test_files.py --lane premerge --shard-index $(SHARD_INDEX) --shard-total $(SHARD_TOTAL))"; \
	if [ -z "$$files" ]; then echo "No pre-merge test files for shard $(SHARD_INDEX)/$(SHARD_TOTAL)."; exit 0; fi; \
	$(OTBOX_PY) -m pytest $$files -q -n $(PYTEST_XDIST) --dist loadfile \
		-m "$(PYTEST_CI_MARKS) and not timing_sensitive"

test-premerge-timing:
	@files="$$(python3 scripts/ci_test_files.py --lane premerge)"; \
	if [ -z "$$files" ]; then echo "No pre-merge timing files selected."; exit 0; fi; \
	$(OTBOX_PY) -m pytest $$files -q -m "$(PYTEST_CI_MARKS) and timing_sensitive"

test-integration-shard:
	@files="$$(python3 scripts/ci_test_files.py --lane integration --shard-index $(SHARD_INDEX) --shard-total $(SHARD_TOTAL))"; \
	if [ -z "$$files" ]; then echo "No integration/e2e test files for shard $(SHARD_INDEX)/$(SHARD_TOTAL)."; exit 0; fi; \
	$(OTBOX_PY) -m pytest $$files -q -m "$(PYTEST_CI_MARKS)" $(PYTEST_CI_DESELECTS)

lint:
	python3 -m ruff check src/ packages/ tests/

# ---------- otbox test environment ----------
# otbox is the snapshottable full test environment (kb/plans/060).
# `otbox-slice` proves the thin vertical slice; `otbox-journeys` sweeps
# every Tier 0 catalogue journey. Both run offline against the `local`
# driver. See tests/otbox/README.md.
#
# Prefer the repo venv so these work without an activated shell (the
# autonomous-delivery-contract verifier may not have one).
OTBOX_PY := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

otbox-slice:
	$(OTBOX_PY) -m pytest tests/otbox/test_otbox_slice.py::test_vertical_slice -v

# Trace Slicer Library (issue #141) soft-evidence — advisory conformance +
# 3-persona utility over real bucket traces. NOT a CI gate. Add LLM=1 for the
# optional per-persona scoring pass (needs a detect_provider() backend).
slicer-soft-evidence:
	$(OTBOX_PY) examples/trace-slicer/soft_evidence.py --sample $(or $(SAMPLE),40) $(if $(LLM),--llm,)

otbox-journeys:
	$(OTBOX_PY) -m pytest tests/otbox/test_otbox_slice.py -v -ra

# Tier 1 (plan 061). Opt-in: OT_OTBOX_TIER1=1. With OT_OTBOX_SSH_TARGET
# set, runs against the operator's tailnet target; without it, spins up
# a local sshd fixture (no system Remote Login change needed).
otbox-tier1:
	OT_OTBOX_TIER1=1 $(OTBOX_PY) -m pytest tests/otbox/test_tailscale_slice.py -v

# Plan 062. Matrix runs every (journey, base-checkpoint) pair, sharing
# checkpoints across journeys via snapshot fork. `otbox-inventory`
# rebuilds the Click registry × journey-ownership map.
otbox-matrix:
	./otbox matrix

otbox-inventory:
	./otbox matrix --inventory --strict

# Issue #53. Sweep killed-run box residue (dead-pid boxes, meta-less
# stubs) and aged _capture-refresh-* snapshots. Never touches the
# current box, live runs, or the _checkpoint-* snapshot cache.
otbox-gc:
	./otbox gc --json

# Release gate (U0/U11): jtbd inventory strict, the claims-ledger gate,
# envelope budgets, catalogue lint, then the rollup verdict. Pass
# OTBOX_LEDGER=path/to/run-ledger.json to fold the latest local run
# ledger's red rows + per-claim derived status into the verdict.
release-gate: otbox-inventory
	$(OTBOX_PY) -m pytest tests/otbox/test_claims_ledger.py \
		tests/otbox/test_envelope_budgets.py tests/otbox/test_catalogue_lint.py -q
	$(OTBOX_PY) -m tests.otbox.release_gate $(if $(OTBOX_LEDGER),--ledger $(OTBOX_LEDGER),)

# Plan 064 vertical slice — prove the consumer-API surfaces return real
# evidence on a REAL captured agent session (not the empty-state
# envelope). Spec § Verify: this is the single make target the
# autonomous-delivery contract uses to gate the slice.
otbox-agent-session:
	$(OTBOX_PY) -m pytest tests/otbox/test_fake_harness.py tests/otbox/test_agent_session_slice.py tests/otbox/test_real_agent_optin.py -v

# Opt-in LIVE HuggingFace lane: runs the live_hf journeys end-to-end against
# REAL private HF dataset repos (ephemeral, keep-on-failure, under the token
# owner's namespace). Requires OT_OTBOX_LIVE_HF=1 plus a token — either
# OPENTRACES_LIVE_HF_TOKEN / HF_TOKEN, or a cached `hf auth login`. SKIPs (never
# fails) without the gate, so it is excluded from default CI. See
# tests/otbox/README.md "Live HuggingFace lane".
otbox-live-hf:
	OT_OTBOX_LIVE_HF=1 $(OTBOX_PY) -m pytest tests/otbox/test_live_hf_slice.py -v

# Issue #213 (seal-family W5) — the nightly `scale` lane. Cold-builds the
# ~600-trace / ~50K-event `c-mature-bucket` world (5-12 min / 1.5-4 GB) and
# runs the `mature-bucket-perf` perf recurrence guard: four seal-family hot
# commands under catastrophic-regression duration + RSS ceilings. OFF the
# per-PR gate and off default `pytest tests/otbox/`; gated behind
# OT_OTBOX_SCALE=1. Runs in the nightly workflow only.
otbox-scale:
	OT_OTBOX_SCALE=1 $(OTBOX_PY) -m pytest tests/otbox/test_scale_lane.py -v -ra

# Plan 071 — capture-refresh against a simulated-user scenario. The
# default-CI safe value is `echo-meta` (uses the in-tree echo binary).
# Real scenarios (add-helper-function etc.) require the named agent
# binary on PATH; otherwise the command SKIPs cleanly.
SCENARIO ?= echo-meta
capture-refresh:
	$(OTBOX_PY) -m tests.otbox capture-refresh --scenario $(SCENARIO) --json

# Plan B0 — harness-version staleness report: compares installed agent
# binary versions against the capture manifest's binary_version rows.
# Pure report (no boxes, no agents driven); CI-safe.
capture-refresh-check:
	$(OTBOX_PY) -m tests.otbox capture-refresh --check-versions

# Plan B0 — regenerate the whole scenario batch for one harness after a
# version bump (e.g. `make capture-refresh-all AGENT=claude`). Requires
# the agent binary on PATH; scenarios SKIP cleanly when it is absent.
AGENT ?= claude
capture-refresh-all:
	$(OTBOX_PY) -m tests.otbox capture-refresh --all --agent $(AGENT) --json

# Issue #61 (plan 095 U9) — B0 acceptance ritual. Drives the 5 acceptance
# scenarios (J1/J6/J7/J10/J13) through the real agent, scores each arc, and
# writes the schema-versioned report to tests/otbox/captures/_acceptance/
# report.json. Machine-gated: needs a real agent binary on PATH; SKIPs
# cleanly otherwise. The default-CI safe path is `--echo` (synthetic
# harness, no real agent, no network). Single-scenario override (its own var
# so the capture-refresh `SCENARIO ?= echo-meta` default never leaks in):
# `make otbox-acceptance AGENT=claude ACCEPTANCE_SCENARIO=acceptance-j1-onboarding`.
ACCEPTANCE_SCENARIO ?=
otbox-acceptance:
	$(OTBOX_PY) -m tests.otbox acceptance --agent $(AGENT) \
		$(if $(ACCEPTANCE_SCENARIO),--scenario $(ACCEPTANCE_SCENARIO),) --json

# Journey footage (terminal-control). ADDITIVE visual-review aid: records an
# MP4 of a simulated-user journey via `termctrl` and builds a gallery. Needs
# `termctrl` (cargo install terminal-control) + ffmpeg; absent → SKIP cleanly.
# The default-CI safe value is `echo-meta` (uses the in-tree echo binary).
# `make otbox-footage SCENARIO=add-helper-function HARNESS=claude FPS=24`.
# See tests/otbox/FOOTAGE.md.
HARNESS ?=
FPS ?= 20
otbox-footage:
	$(OTBOX_PY) -m tests.otbox footage --scenario $(SCENARIO) \
		$(if $(HARNESS),--harness $(HARNESS),) --fps $(FPS) --json

otbox-footage-all:
	$(OTBOX_PY) -m tests.otbox footage --all $(if $(HARNESS),--harness $(HARNESS),) --fps $(FPS) --json

# ---------- search-eval harness (plan 088) ----------
# Runs the progressive-discovery loop over a deterministic, real-bucket-sized
# planted corpus and emits perf + outcome metrics to
# tests/search_eval/SEARCH-EVAL.md. `search-eval` is the fast inner-loop (dev
# tier ~150 traces); `search-eval-real` is the real-scale tier (~profile size).
search-eval:
	$(OTBOX_PY) -m tests.search_eval.runner --tier dev --seed 1

search-eval-real:
	$(OTBOX_PY) -m tests.search_eval.runner --tier real-scale --seed 1 --cache

# The opt-in snapshot-cache lane (U9): build the corpus once into a content-
# addressed cache, then prove a restore-and-measure run is byte-identical + green.
search-eval-cache:
	OT_SEARCH_EVAL_CACHE=1 $(OTBOX_PY) -m pytest \
		tests/search_eval/test_search_eval.py::test_snapshot_cache_restore_and_measure -v

# The xl (~10k trace) tier + scaling-slope gate (U7): run real-scale then xl,
# then `search-eval-slope` proves bounded-query p95 stays ~flat as the corpus
# grows (the qmd invariant at scale). xl is heavy — intended for a nightly lane.
search-eval-xl:
	$(OTBOX_PY) -m tests.search_eval.runner --tier xl --seed 1

search-eval-slope:
	$(OTBOX_PY) -m tests.search_eval.slope

# Ungated --live mode (U8): run the real Seed Evaluation Dataset queries against
# the operator's actual ~/.opentraces bucket -> tests/search_eval/LIVE-EVAL.md.
search-eval-live:
	$(OTBOX_PY) -m tests.search_eval.live

# Refresh the committed real-bucket size profile from ~/.opentraces (U0).
search-eval-profile:
	$(OTBOX_PY) tests/search_eval/profiler.py --out tests/search_eval/real-bucket-profile.json

# The CI gate: scorer units, determinism, and the dev-tier invariant checks.
search-eval-test:
	$(OTBOX_PY) -m pytest tests/search_eval/test_search_eval.py -v

# ---------- Publish ----------

publish-schema:
	python3 -m twine upload $(SCHEMA_DIR)/dist/*

publish-cli:
	python3 -m twine upload dist/*

publish-test-schema: build-schema
	python3 -m twine upload --repository testpypi $(SCHEMA_DIR)/dist/*

publish-test-cli: build-cli
	python3 -m twine upload --repository testpypi dist/*

# ---------- Tag ----------

tag: dirty-check
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	git push origin "v$(VERSION)"

# ---------- Full Release ----------

release: dirty-check version-check test lint build publish-schema publish-cli tag
	@echo ""
	@echo "Released opentraces v$(VERSION) (schema v$(SCHEMA_VERSION))"
	@echo "  PyPI: https://pypi.org/project/opentraces/$(VERSION)/"
	@echo "  PyPI: https://pypi.org/project/opentraces-schema/$(SCHEMA_VERSION)/"
	@echo ""
	@echo "Next: update Homebrew formula with 'make brew-update'"

# ---------- Homebrew ----------

brew-update:
	@echo "Fetching SHA256 for opentraces $(VERSION) from PyPI..."
	@curl -sL "https://pypi.org/pypi/opentraces/$(VERSION)/json" | python3 -c \
		"import sys,json; d=json.load(sys.stdin); urls=[u for u in d['urls'] if u['packagetype']=='sdist']; print(urls[0]['digests']['sha256'] if urls else 'NOT FOUND')"
	@echo ""
	@echo "Update Formula/opentraces.rb with the new URL and SHA256."
