CREATE TABLE IF NOT EXISTS sdk_runtime_state (
 conversation_id BIGINT PRIMARY KEY REFERENCES conversations(id),
 fingerprint TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', files_json TEXT NOT NULL DEFAULT '{}',
 sdk_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sdk_session_entries (
 conversation_id BIGINT NOT NULL REFERENCES conversations(id), project_key TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL,
 subpath TEXT NOT NULL, seq BIGINT NOT NULL, entry_uid TEXT NOT NULL, entry_json TEXT NOT NULL,
 PRIMARY KEY(conversation_id,project_key,session_id,subpath,seq), UNIQUE(conversation_id,project_key,session_id,subpath,entry_uid)
);
ALTER TABLE sdk_runtime_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE sdk_session_entries ENABLE ROW LEVEL SECURITY;
