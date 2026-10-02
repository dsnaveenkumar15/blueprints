CREATE TABLE IF NOT EXISTS blueprints (
  id TEXT NOT NULL,
  version INTEGER NOT NULL,
  status TEXT NOT NULL,            -- draft | approved | rejected | retired
  body TEXT NOT NULL,
  lint TEXT NOT NULL,
  source TEXT NOT NULL,            -- seed | generated
  reason TEXT NOT NULL DEFAULT 'new',
  cause_event_id INTEGER,
  parent_version INTEGER,
  provenance TEXT NOT NULL DEFAULT '{}',   -- model, prompt versions, review id: what produced and approved it
  created_at REAL NOT NULL,
  decided_at REAL,
  PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS blueprint_refresh (
  id TEXT NOT NULL,
  reason TEXT NOT NULL,
  cause_event_id INTEGER NOT NULL,
  created_at REAL NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (id, cause_event_id)
);
