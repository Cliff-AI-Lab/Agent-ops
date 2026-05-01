DROP TABLE IF EXISTS sessions;
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  user_sop TEXT,
  status TEXT DEFAULT 'active',
  state TEXT NOT NULL,
  messages_json TEXT NOT NULL,
  requirement_spec_json TEXT,
  audit_log_json TEXT
);

DROP TABLE IF EXISTS model_profiles;
CREATE TABLE model_profiles (
  model_id TEXT PRIMARY KEY,
  family TEXT,
  context_window INT,
  json_reliability REAL,
  function_calling_ok INT,
  long_context_ok INT,
  chinese_quality REAL,
  needs_schema_reminder INT,
  recommended_temperature REAL,
  max_retry INT,
  last_probed_at TEXT
);

-- Batch X: Marketplace + Asset Hub
DROP TABLE IF EXISTS generated_assets;
CREATE TABLE generated_assets (
  asset_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  product_type TEXT NOT NULL DEFAULT 'app',
  source_session_id TEXT,
  source_sop TEXT,
  generation_id TEXT,
  generated_at TEXT NOT NULL,
  artifact_path TEXT NOT NULL,
  agent_yaml_path TEXT,
  file_count INT DEFAULT 0,
  total_bytes INT DEFAULT 0,
  intent_keywords_json TEXT DEFAULT '[]',
  tags_json TEXT DEFAULT '[]',
  capabilities_used_json TEXT DEFAULT '[]',
  tools_used_json TEXT DEFAULT '[]',
  quality_score REAL,
  usage_count INT DEFAULT 0,
  promoted_by TEXT,
  promoted_at TEXT,
  status TEXT NOT NULL DEFAULT 'draft'
);
CREATE INDEX IF NOT EXISTS idx_assets_status ON generated_assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_session ON generated_assets(source_session_id);
