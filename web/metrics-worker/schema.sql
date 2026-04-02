CREATE TABLE IF NOT EXISTS metrics_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  source TEXT NOT NULL,
  metric TEXT NOT NULL,
  bucket TEXT NOT NULL,
  granularity TEXT NOT NULL,
  unit TEXT NOT NULL,
  value INTEGER NOT NULL,
  as_of TEXT NOT NULL,
  meta_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(project_id, source, metric, bucket)
);

CREATE INDEX IF NOT EXISTS idx_metrics_lookup
  ON metrics_snapshots(project_id, source, metric, granularity, bucket, as_of);
