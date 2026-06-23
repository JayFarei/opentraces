# Google Search Console setup (Phase 1)

`gsc-report.mjs` is the first credentialed monitor signal: per priority page index verdict + canonical drift (URL Inspection API) and clicks/impressions/CTR/position + top queries (Search Analytics API). It no-ops gracefully until the service-account secret exists, so it can ship in the scheduled monitor today and light up the moment you finish these steps. Auth is a service-account JWT signed with Node stdlib — no npm deps.

This is the **usual week-1 blocker** because the property must explicitly grant the service account; the key existing is not enough. Do it in this order.

## 1. Google Cloud project + APIs

```bash
# (or reuse a project) enable the APIs the pull needs
gcloud services enable searchconsole.googleapis.com bigquery.googleapis.com --project <PROJECT_ID>
```

## 2. Service account + key

```bash
gcloud iam service-accounts create opentraces-gsc --project <PROJECT_ID> \
  --display-name "opentraces GSC monitor"
gcloud iam service-accounts keys create gsc-sa.json \
  --iam-account opentraces-gsc@<PROJECT_ID>.iam.gserviceaccount.com
# note the client_email; you grant it on the property next. Keep gsc-sa.json OUT of git.
```

## 3. Grant the service account on the GSC property (the blocker)

Search Console → **Settings → Users and permissions → Add user** → paste the service account's `client_email` (`opentraces-gsc@<PROJECT_ID>.iam.gserviceaccount.com`) → permission **Full** (or Restricted). The API scope used is `webmasters.readonly`. *Without this grant the API returns 403 even though the key is valid.*

## 4. Store the secret + property

- Repo secret **`GSC_SERVICE_ACCOUNT_JSON`** = the entire contents of `gsc-sa.json` (inline JSON):
  ```bash
  gh secret set GSC_SERVICE_ACCOUNT_JSON < gsc-sa.json
  ```
- Repo variable **`GSC_SITE_URL`** = the property as registered. Domain property → `sc-domain:opentraces.ai` (the default). URL-prefix property → `https://opentraces.ai/`.
  ```bash
  gh variable set GSC_SITE_URL --body "sc-domain:opentraces.ai"
  ```

The `seo-monitor.yml` workflow already passes both into the GSC step; it runs every ~2 days and is a no-op until the secret is present.

## 5. Verify end-to-end

```bash
# offline logic check (no creds needed):
node scripts/seo/gsc-report.mjs --self-test

# one real pull once creds are set (writes nothing unless --write):
GSC_SERVICE_ACCOUNT_JSON="$(cat gsc-sa.json)" GSC_SITE_URL="sc-domain:opentraces.ai" \
  node scripts/seo/gsc-report.mjs --json | head -40
```
A successful run prints each priority page's `verdict / coverageState` (and a `⚠ canonical drift` flag if Google's selected canonical ≠ the declared one) plus the 28-day analytics summary.

## 6. (recommended) GSC → BigQuery bulk export — turn on day one

The Search Analytics API is sampled and retains 16 months; the bulk export is the unsampled substrate the §4 CausalImpact verdict reads, and it is **not retroactive** — it only accumulates from the day you enable it. Operator action (not scripted here):

1. A **billing-enabled BigQuery project + a dataset**.
2. Grant the **GSC service agent** (a Google-managed `search-console-...@gcp-sa-...` account, distinct from your service account) **BigQuery Job User + Data Editor** on that project.
3. Search Console → **Settings → Bulk data export** → point at the project/dataset.

See `../../SEO-AEO-LOOP.md` §5 for the rationale and §2 for what each signal feeds.
