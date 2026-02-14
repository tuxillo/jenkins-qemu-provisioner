CREATE TABLE IF NOT EXISTS hosts (
  host_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  bootstrap_token_hash TEXT,
  session_token_hash TEXT,
  session_expires_at TEXT,
  cpu_total INTEGER NOT NULL DEFAULT 0,
  cpu_free INTEGER NOT NULL DEFAULT 0,
  ram_total_mb INTEGER NOT NULL DEFAULT 0,
  ram_free_mb INTEGER NOT NULL DEFAULT 0,
  io_pressure REAL NOT NULL DEFAULT 0,
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS leases (
  lease_id TEXT PRIMARY KEY,
  vm_id TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  jenkins_node TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL,
  host_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  connect_deadline TEXT NOT NULL,
  ttl_deadline TEXT NOT NULL,
  last_heartbeat TEXT,
  last_error TEXT,
  FOREIGN KEY(host_id) REFERENCES hosts(host_id)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  lease_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(lease_id) REFERENCES leases(lease_id)
);

CREATE INDEX IF NOT EXISTS idx_leases_label_state ON leases(label, state);
CREATE INDEX IF NOT EXISTS idx_leases_host_state ON leases(host_id, state);
CREATE INDEX IF NOT EXISTS idx_hosts_last_seen ON hosts(last_seen);
