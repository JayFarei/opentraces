.PHONY: version-check dirty-check clean build-viewer build-schema build-cli build \
       test lint publish-schema publish-cli publish-test-schema publish-test-cli \
       tag release brew-update otbox-slice otbox-journeys otbox-tier1 \
       otbox-matrix otbox-inventory otbox-agent-session otbox-live-hf capture-refresh \
       search-eval search-eval-real search-eval-xl search-eval-slope \
       search-eval-profile search-eval-test

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

build-viewer:
	cd web/viewer && npm install && npm run build

build-schema:
	cd $(SCHEMA_DIR) && python3 -m build

build-cli:
	python3 -m build

build: clean build-schema build-cli

# ---------- Test ----------

test:
	python3 -m pytest tests/ -v

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

otbox-journeys:
	$(OTBOX_PY) -m pytest tests/otbox/test_otbox_slice.py -v

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

# Plan 071 — capture-refresh against a simulated-user scenario. The
# default-CI safe value is `echo-meta` (uses the in-tree echo binary).
# Real scenarios (add-helper-function etc.) require the named agent
# binary on PATH; otherwise the command SKIPs cleanly.
SCENARIO ?= echo-meta
capture-refresh:
	$(OTBOX_PY) -m tests.otbox capture-refresh --scenario $(SCENARIO) --json

# ---------- search-eval harness (plan 088) ----------
# Runs the progressive-discovery loop over a deterministic, real-bucket-sized
# planted corpus and emits perf + outcome metrics to
# tests/search_eval/SEARCH-EVAL.md. `search-eval` is the fast inner-loop (dev
# tier ~150 traces); `search-eval-real` is the real-scale tier (~profile size).
search-eval:
	$(OTBOX_PY) -m tests.search_eval.runner --tier dev --seed 1

search-eval-real:
	$(OTBOX_PY) -m tests.search_eval.runner --tier real-scale --seed 1

# The xl (~10k trace) tier + scaling-slope gate (U7): run real-scale then xl,
# then `search-eval-slope` proves bounded-query p95 stays ~flat as the corpus
# grows (the qmd invariant at scale). xl is heavy — intended for a nightly lane.
search-eval-xl:
	$(OTBOX_PY) -m tests.search_eval.runner --tier xl --seed 1

search-eval-slope:
	$(OTBOX_PY) -m tests.search_eval.slope

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
