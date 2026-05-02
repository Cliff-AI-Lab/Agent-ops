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

-- ─────────────────────────────────────────────────────────────────────────
-- Phase 2 (V2.0.0 → V2.0.2): Factory Session state machine
-- ─────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS factory_sessions;
CREATE TABLE factory_sessions (
  session_id TEXT PRIMARY KEY,
  nl TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  final_run_id TEXT,
  final_artifact_path TEXT,
  industry_code TEXT,
  scenario TEXT,
  -- Phase 5 multi-mode: 'design' / 'variant' / 'production'
  -- design: all 6 gates require human (default; first-time agent class)
  -- variant: gates 1-3 auto-pass; gates 4-6 (test/ui/deploy) require human
  -- production: gates 1-5 auto-pass; only test gate (4) halts on eval fail
  mode TEXT NOT NULL DEFAULT 'design'
);
CREATE INDEX IF NOT EXISTS idx_factory_sessions_state ON factory_sessions(state);

DROP TABLE IF EXISTS factory_artifacts;
CREATE TABLE factory_artifacts (
  artifact_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  run_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES factory_sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_factory_artifacts_session ON factory_artifacts(session_id, stage);

DROP TABLE IF EXISTS factory_gate_decisions;
CREATE TABLE factory_gate_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  gate_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  payload_json TEXT,
  decided_by TEXT,
  decided_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES factory_sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_factory_decisions_session ON factory_gate_decisions(session_id);
