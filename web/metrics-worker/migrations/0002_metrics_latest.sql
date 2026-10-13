CREATE TABLE IF NOT EXISTS metrics_latest (
  project_id TEXT NOT NULL,
  source TEXT NOT NULL,
  metric TEXT NOT NULL,
  bucket TEXT NOT NULL,
  granularity TEXT NOT NULL,
  unit TEXT NOT NULL,
  value INTEGER NOT NULL,
  as_of TEXT NOT NULL,
  meta_json TEXT,
  PRIMARY KEY(project_id, source, metric)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_metrics_snapshot_retention
  ON metrics_snapshots(granularity, as_of);

-- The only full-history ranking is this one-time backfill, before deployment.
-- Ties retain the old lookup index's granularity/bucket order, including PyPI
-- daily points whose as_of timestamps are identical within one collection.
INSERT INTO metrics_latest (
  project_id, source, metric, bucket, granularity, unit, value, as_of, meta_json
)
SELECT project_id, source, metric, bucket, granularity, unit, value, as_of, meta_json
FROM (
  SELECT project_id, source, metric, bucket, granularity, unit, value, as_of, meta_json,
    ROW_NUMBER() OVER (
      PARTITION BY project_id, source, metric
      ORDER BY as_of DESC, granularity ASC, bucket ASC
    ) AS rank
  FROM metrics_snapshots
)
WHERE rank = 1;
