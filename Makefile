.PHONY: version-check dirty-check clean build-viewer build-schema build-cli build \
       test lint publish-schema publish-cli publish-test-schema publish-test-cli \
       tag release brew-update

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

build-schema: clean
	cd $(SCHEMA_DIR) && python3 -m build

build-cli: build-viewer
	python3 -m build

build: build-schema build-cli

# ---------- Test ----------

test:
	python3 -m pytest tests/ -v

lint:
	python3 -m ruff check src/ packages/ tests/

# ---------- Publish ----------

publish-schema: build-schema
	python3 -m twine upload $(SCHEMA_DIR)/dist/*

publish-cli: build-cli
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
