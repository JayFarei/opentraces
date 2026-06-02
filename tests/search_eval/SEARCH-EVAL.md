# Search Evaluation (plan 088)

Performance **and** outcome metrics for Trace Spotlight's progressive-discovery loop (`trace query -> map -> slice -> get --card -> get -> discover`), measured over a deterministic, real-bucket-sized planted corpus. Regenerate with `make search-eval` (dev tier) or `make search-eval-real` (real-scale).

> The harness is the executable spec: seed rows S1-S8 plus the discovery archetype document the progressive-discovery capabilities and checked boundedness. `OK` checks that observed RED/GREEN matches each row's documented expectation.

## Tier: `dev` (seed 1)

- corpus: **150** traces, **25** query rows (25 green / 0 red)
- snapshot key: `sha256:9c5866ad912240b2722b2cf8a1849268f50c0f39ea4b22d04b798a6f2988e15d`
- outcome digest (deterministic): `sha256:f0d7287c2bc1370b38193a6d9f4ca9d75b2a1ffaa750bae5f810014373b0cdef`
- profile digest: `sha256:754dc8669c68dbc82b5f9cf39f31ab2c7dd4fd9efce933f33d87f0fb3800111e`
- discovery-loop smoke: ok (stages: query, map, slice, card, get, discover)
- trace discover grouping: ok (3 day groups, 6 cards)
- summary_non_boilerplate_rate: **1.00**
- repeated/parallel query reliability: ok (20 sequential + 8 parallel, sync_count=0, elapsed=4030.86ms)
- **invariants_ok: yes**

### Seed Evaluation Dataset (S1–S8)

| Seed | Query | Total | Outcome | p95 ms | Status | Expected | OK |
|------|-------|------:|---------|-------:|--------|----------|----|
| S1 | `--lex do we know how to slice a trajectory per intent` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 196.40 | GREEN | green | ok |
| S1 | `--lex trajectory intent slices` | 10 | recall@10=1.00, mrr=0.11, rank=9 | 198.22 | GREEN | green | ok |
| S2 | `--semantic break a trajectory into per-intent slices` | 48 | recall@5=1.00, mrr=1.00, rank=1 | 211.67 | GREEN | green | ok |
| S3 | `--lex nico100` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 204.34 | GREEN | green | ok |
| S4 | `--lex chronotopic100` | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 200.72 | GREEN | green | ok |
| S5 | `--semantic latest work on the recencytopic100 stack` | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 221.99 | GREEN | green | ok |
| S6 | `--lex does our cli allow fast search over previous traces` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 195.61 | GREEN | green | ok |
| S7 | `--lex how many lines of code in this project` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 191.36 | GREEN | green | ok |
| S8 | `--files *src/core/intent-1-00.py` | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 208.04 | GREEN | green | ok |

### By archetype

| Archetype | n | mean recall | mean p95 ms | green | red |
|-----------|--:|------------:|------------:|------:|----:|
| boundedness_cliff | 1 | 1.00 | 203.72 | 1 | 0 |
| chronological | 3 | 1.00 | 198.39 | 3 | 0 |
| descriptive | 4 | 1.00 | 196.20 | 4 | 0 |
| discovery | 1 | 1.00 | 214.64 | 1 | 0 |
| facet | 2 | 1.00 | 205.99 | 2 | 0 |
| recency | 3 | 1.00 | 220.40 | 3 | 0 |
| reference_bare | 3 | 1.00 | 197.60 | 3 | 0 |
| reference_id | 3 | 1.00 | 201.61 | 3 | 0 |
| semantic_precedent | 3 | 1.00 | 209.13 | 3 | 0 |
| superseded | 2 | 1.00 | 200.46 | 2 | 0 |

### Boundedness (qmd invariant, R3)

The qmd invariant (R3): a query may scan ~its matches, not the whole corpus. `rows_scanned ~ corpus` with few matches = the O(corpus) cliff (U6 target). Deterministic, so it gates at any tier regardless of ms.

| Row | Path | matched | rows_scanned | corpus | bounded | expected | OK |
|-----|------|--------:|-------------:|-------:|---------|----------|----|
| chrono-00 | index_fts | 4 | 4 | 150 | bounded | bounded | ok |
| chrono-01 | index_fts | 4 | 4 | 150 | bounded | bounded | ok |
| chrono-02 | index_fts | 4 | 4 | 150 | bounded | bounded | ok |
| recency-00 | projection_fts | 112 | 112 | 18645 | bounded | bounded | ok |
| recency-01 | projection_fts | 112 | 112 | 18645 | bounded | bounded | ok |
| recency-02 | projection_fts | 112 | 112 | 18645 | bounded | bounded | ok |
| refbare-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refbare-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refbare-02 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refid-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refid-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refid-02 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| facet-00 | index_scan | 4 | 150 | 150 | O(corpus) | O(corpus) | ok |
| facet-01 | index_scan | 4 | 150 | 150 | O(corpus) | O(corpus) | ok |
| desc-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| desc-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| desc-02 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| desc-03 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| discover-00 | index_fts | 6 | 6 | 150 | bounded | bounded | ok |
| supersede-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| supersede-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| precedent-sem-00 | projection_fts | 48 | 48 | 18645 | bounded | bounded | ok |
| precedent-lex-00 | index_fts | 10 | 10 | 150 | bounded | bounded | ok |
| precedent-sem-01 | projection_fts | 48 | 48 | 18645 | bounded | bounded | ok |
| cliff-00 | projection_concept | 12 | 12 | 18645 | bounded | bounded | ok |

### All query rows

| Row | Archetype | Mode | Total | Outcome | p95 | Status | Bounded | OK |
|-----|-----------|------|------:|---------|----:|--------|---------|----|
| chrono-00 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 200.72 | GREEN/green | bounded (4/150) | ok |
| chrono-01 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 199.42 | GREEN/green | bounded (4/150) | ok |
| chrono-02 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 195.03 | GREEN/green | bounded (4/150) | ok |
| recency-00 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 221.99 | GREEN/green | bounded (112/18645) | ok |
| recency-01 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 219.82 | GREEN/green | bounded (112/18645) | ok |
| recency-02 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 219.40 | GREEN/green | bounded (112/18645) | ok |
| refbare-00 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 194.02 | GREEN/green | bounded (1/150) | ok |
| refbare-01 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 201.73 | GREEN/green | bounded (1/150) | ok |
| refbare-02 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 197.05 | GREEN/green | bounded (1/150) | ok |
| refid-00 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 204.34 | GREEN/green | bounded (1/150) | ok |
| refid-01 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 201.68 | GREEN/green | bounded (1/150) | ok |
| refid-02 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 198.80 | GREEN/green | bounded (1/150) | ok |
| facet-00 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 208.04 | GREEN/green | O(corpus) (150/150) | ok |
| facet-01 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 203.93 | GREEN/green | O(corpus) (150/150) | ok |
| desc-00 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 191.36 | GREEN/green | bounded (1/150) | ok |
| desc-01 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 195.61 | GREEN/green | bounded (1/150) | ok |
| desc-02 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 196.40 | GREEN/green | bounded (1/150) | ok |
| desc-03 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 201.44 | GREEN/green | bounded (1/150) | ok |
| discover-00 | discovery | lex | 6 | recall@10=1.00, mrr=1.00, rank=1 | 214.64 | GREEN/green | bounded (6/150) | ok |
| supersede-00 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 199.47 | GREEN/green | bounded (1/150) | ok |
| supersede-01 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 201.45 | GREEN/green | bounded (1/150) | ok |
| precedent-sem-00 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=1.00, rank=1 | 211.67 | GREEN/green | bounded (48/18645) | ok |
| precedent-lex-00 | semantic_precedent | lex | 10 | recall@10=1.00, mrr=0.11, rank=9 | 198.22 | GREEN/green | bounded (10/150) | ok |
| precedent-sem-01 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=0.50, rank=2 | 217.51 | GREEN/green | bounded (48/18645) | ok |
| cliff-00 | boundedness_cliff | semantic | 12 | recall@10=1.00, mrr=1.00, rank=1 | 203.72 | GREEN/green | bounded (12/18645) | ok |

## Tier: `real-scale` (seed 1)

- corpus: **1084** traces, **24** query rows (24 green / 0 red)
- snapshot key: `sha256:65854ca6217a3128505c587f0037f97f6edf34c943039c9913fe11537f8bfc46`
- outcome digest (deterministic): `sha256:ed84949a6e10ff806001d86aa5263cff392eaff8e6fb9a2cf8626ba4af69b317`
- profile digest: `sha256:754dc8669c68dbc82b5f9cf39f31ab2c7dd4fd9efce933f33d87f0fb3800111e`
- discovery-loop smoke: ok (stages: query, map, slice, get)
- summary_non_boilerplate_rate: **1.00**
- **invariants_ok: yes**

### Seed Evaluation Dataset (S1–S8)

| Seed | Query | Total | Outcome | p95 ms | Status | Expected | OK |
|------|-------|------:|---------|-------:|--------|----------|----|
| S1 | `--lex do we know how to slice a trajectory per intent` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 224.45 | GREEN | green | ok |
| S1 | `--lex trajectory intent slices` | 10 | recall@10=1.00, mrr=0.11, rank=9 | 209.95 | GREEN | green | ok |
| S2 | `--semantic break a trajectory into per-intent slices` | 48 | recall@5=1.00, mrr=1.00, rank=1 | 232.04 | GREEN | green | ok |
| S3 | `--lex nico100` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 216.05 | GREEN | green | ok |
| S4 | `--lex chronotopic100` | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 211.72 | GREEN | green | ok |
| S5 | `--semantic latest work on the recencytopic100 stack` | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 230.15 | GREEN | green | ok |
| S6 | `--lex does our cli allow fast search over previous traces` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 213.43 | GREEN | green | ok |
| S7 | `--lex how many lines of code in this project` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.99 | GREEN | green | ok |
| S8 | `--files *src/core/intent-1-00.py` | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 297.79 | GREEN | green | ok |

### By archetype

| Archetype | n | mean recall | mean p95 ms | green | red |
|-----------|--:|------------:|------------:|------:|----:|
| boundedness_cliff | 1 | 1.00 | 221.04 | 1 | 0 |
| chronological | 3 | 1.00 | 209.28 | 3 | 0 |
| descriptive | 4 | 1.00 | 214.24 | 4 | 0 |
| facet | 2 | 1.00 | 297.53 | 2 | 0 |
| recency | 3 | 1.00 | 234.80 | 3 | 0 |
| reference_bare | 3 | 1.00 | 207.64 | 3 | 0 |
| reference_id | 3 | 1.00 | 215.54 | 3 | 0 |
| semantic_precedent | 3 | 1.00 | 225.32 | 3 | 0 |
| superseded | 2 | 1.00 | 209.07 | 2 | 0 |

### Boundedness (qmd invariant, R3)

The qmd invariant (R3): a query may scan ~its matches, not the whole corpus. `rows_scanned ~ corpus` with few matches = the O(corpus) cliff (U6 target). Deterministic, so it gates at any tier regardless of ms.

| Row | Path | matched | rows_scanned | corpus | bounded | expected | OK |
|-----|------|--------:|-------------:|-------:|---------|----------|----|
| chrono-00 | index_fts | 4 | 4 | 1084 | bounded | bounded | ok |
| chrono-01 | index_fts | 4 | 4 | 1084 | bounded | bounded | ok |
| chrono-02 | index_fts | 4 | 4 | 1084 | bounded | bounded | ok |
| recency-00 | projection_fts | 112 | 112 | 216206 | bounded | bounded | ok |
| recency-01 | projection_fts | 112 | 112 | 216206 | bounded | bounded | ok |
| recency-02 | projection_fts | 112 | 112 | 216206 | bounded | bounded | ok |
| refbare-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refbare-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refbare-02 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refid-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refid-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refid-02 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| facet-00 | index_scan | 4 | 1084 | 1084 | O(corpus) | O(corpus) | ok |
| facet-01 | index_scan | 4 | 1084 | 1084 | O(corpus) | O(corpus) | ok |
| desc-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| desc-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| desc-02 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| desc-03 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| supersede-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| supersede-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| precedent-sem-00 | projection_fts | 48 | 48 | 216206 | bounded | bounded | ok |
| precedent-lex-00 | index_fts | 10 | 10 | 1084 | bounded | bounded | ok |
| precedent-sem-01 | projection_fts | 48 | 48 | 216206 | bounded | bounded | ok |
| cliff-00 | projection_concept | 12 | 12 | 216206 | bounded | bounded | ok |

### All query rows

| Row | Archetype | Mode | Total | Outcome | p95 | Status | Bounded | OK |
|-----|-----------|------|------:|---------|----:|--------|---------|----|
| chrono-00 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 211.72 | GREEN/green | bounded (4/1084) | ok |
| chrono-01 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 207.86 | GREEN/green | bounded (4/1084) | ok |
| chrono-02 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 208.27 | GREEN/green | bounded (4/1084) | ok |
| recency-00 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 230.15 | GREEN/green | bounded (112/216206) | ok |
| recency-01 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 233.09 | GREEN/green | bounded (112/216206) | ok |
| recency-02 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 241.16 | GREEN/green | bounded (112/216206) | ok |
| refbare-00 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 207.08 | GREEN/green | bounded (1/1084) | ok |
| refbare-01 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.21 | GREEN/green | bounded (1/1084) | ok |
| refbare-02 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 206.62 | GREEN/green | bounded (1/1084) | ok |
| refid-00 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 216.05 | GREEN/green | bounded (1/1084) | ok |
| refid-01 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.36 | GREEN/green | bounded (1/1084) | ok |
| refid-02 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 221.21 | GREEN/green | bounded (1/1084) | ok |
| facet-00 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 297.79 | GREEN/green | O(corpus) (1084/1084) | ok |
| facet-01 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 297.27 | GREEN/green | O(corpus) (1084/1084) | ok |
| desc-00 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.99 | GREEN/green | bounded (1/1084) | ok |
| desc-01 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 213.43 | GREEN/green | bounded (1/1084) | ok |
| desc-02 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 224.45 | GREEN/green | bounded (1/1084) | ok |
| desc-03 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.09 | GREEN/green | bounded (1/1084) | ok |
| supersede-00 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.90 | GREEN/green | bounded (1/1084) | ok |
| supersede-01 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 208.24 | GREEN/green | bounded (1/1084) | ok |
| precedent-sem-00 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=1.00, rank=1 | 232.04 | GREEN/green | bounded (48/216206) | ok |
| precedent-lex-00 | semantic_precedent | lex | 10 | recall@10=1.00, mrr=0.11, rank=9 | 209.95 | GREEN/green | bounded (10/1084) | ok |
| precedent-sem-01 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=0.50, rank=2 | 233.98 | GREEN/green | bounded (48/216206) | ok |
| cliff-00 | boundedness_cliff | semantic | 12 | recall@10=1.00, mrr=1.00, rank=1 | 221.04 | GREEN/green | bounded (12/216206) | ok |

## Scaling slope: `dev` → `real-scale` (150 → 1084 traces, 7.23× growth)

The qmd invariant (R2): for a fixed result size, bounded-query p95 stays ~flat as the corpus grows (ratio ≤ 3.0×). O(corpus) rows (the --files facet scan) are exempt and track corpus growth.

- **bounded queries within slope: yes**

| Row | Archetype | bounded | small p95 | large p95 | ratio | OK |
|-----|-----------|---------|----------:|----------:|------:|----|
| chrono-00 | chronological | bounded | 200.72 | 211.72 | 1.05 | ok |
| chrono-01 | chronological | bounded | 199.42 | 207.86 | 1.04 | ok |
| chrono-02 | chronological | bounded | 195.03 | 208.27 | 1.07 | ok |
| cliff-00 | boundedness_cliff | bounded | 203.72 | 221.04 | 1.09 | ok |
| desc-00 | descriptive | bounded | 191.36 | 209.99 | 1.1 | ok |
| desc-01 | descriptive | bounded | 195.61 | 213.43 | 1.09 | ok |
| desc-02 | descriptive | bounded | 196.4 | 224.45 | 1.14 | ok |
| desc-03 | descriptive | bounded | 201.44 | 209.09 | 1.04 | ok |
| precedent-lex-00 | semantic_precedent | bounded | 198.22 | 209.95 | 1.06 | ok |
| precedent-sem-00 | semantic_precedent | bounded | 211.67 | 232.04 | 1.1 | ok |
| precedent-sem-01 | semantic_precedent | bounded | 217.51 | 233.98 | 1.08 | ok |
| recency-00 | recency | bounded | 221.99 | 230.15 | 1.04 | ok |
| recency-01 | recency | bounded | 219.82 | 233.09 | 1.06 | ok |
| recency-02 | recency | bounded | 219.4 | 241.16 | 1.1 | ok |
| refbare-00 | reference_bare | bounded | 194.02 | 207.08 | 1.07 | ok |
| refbare-01 | reference_bare | bounded | 201.73 | 209.21 | 1.04 | ok |
| refbare-02 | reference_bare | bounded | 197.05 | 206.62 | 1.05 | ok |
| refid-00 | reference_id | bounded | 204.34 | 216.05 | 1.06 | ok |
| refid-01 | reference_id | bounded | 201.68 | 209.36 | 1.04 | ok |
| refid-02 | reference_id | bounded | 198.8 | 221.21 | 1.11 | ok |
| supersede-00 | superseded | bounded | 199.47 | 209.9 | 1.05 | ok |
| supersede-01 | superseded | bounded | 201.45 | 208.24 | 1.03 | ok |
| facet-00 | facet | O(corpus) | 208.04 | 297.79 | 1.43 | ok |
| facet-01 | facet | O(corpus) | 203.93 | 297.27 | 1.46 | ok |

---
_Outcome metrics are deterministic (recall/MRR/NDCG/tau/recency-hit over distinct traces); perf metrics are wall-clock (gate on scaling slope + counters, not absolute ms — see budgets). Generated by `tests/search_eval/report.py`._
