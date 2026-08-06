PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    project_root TEXT NOT NULL UNIQUE CHECK (length(trim(project_root)) > 0),
    current_revision INTEGER NOT NULL CHECK (current_revision >= 0),
    current_digest TEXT NOT NULL CHECK (length(current_digest) = 71),
    focus_json TEXT NOT NULL CHECK (json_valid(focus_json)),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id, current_revision)
        REFERENCES revisions(project_id, revision)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS revisions (
    project_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    parent_revision INTEGER,
    digest TEXT NOT NULL CHECK (length(digest) = 71),
    focus_json TEXT NOT NULL CHECK (json_valid(focus_json)),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    patch_json TEXT NOT NULL CHECK (json_valid(patch_json)),
    session_id TEXT,
    actor_id TEXT NOT NULL CHECK (length(trim(actor_id)) > 0),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, revision),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, parent_revision)
        REFERENCES revisions(project_id, revision)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        (revision = 0 AND parent_revision IS NULL)
        OR (revision > 0 AND parent_revision = revision - 1)
    )
);

CREATE TABLE IF NOT EXISTS entities (
    project_id TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK (length(trim(entity_type)) > 0),
    entity_id TEXT NOT NULL CHECK (length(trim(entity_id)) > 0),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, entity_type, entity_id),
    FOREIGN KEY (project_id, revision)
        REFERENCES revisions(project_id, revision)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    agent_id TEXT NOT NULL CHECK (length(trim(agent_id)) > 0),
    bound_revision INTEGER NOT NULL CHECK (bound_revision >= 0),
    bound_digest TEXT NOT NULL CHECK (length(bound_digest) = 71),
    focus_json TEXT NOT NULL CHECK (json_valid(focus_json)),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    activated_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (session_id, project_id),
    FOREIGN KEY (project_id, bound_revision)
        REFERENCES revisions(project_id, revision)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS approvals (
    project_id TEXT NOT NULL,
    approval_id TEXT NOT NULL CHECK (length(trim(approval_id)) > 0),
    target_type TEXT NOT NULL CHECK (length(trim(target_type)) > 0),
    target_id TEXT NOT NULL CHECK (length(trim(target_id)) > 0),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'approved', 'changes_requested', 'revoked')
    ),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, approval_id),
    FOREIGN KEY (project_id, revision)
        REFERENCES revisions(project_id, revision)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    digest TEXT NOT NULL CHECK (length(digest) = 71),
    capsule_json TEXT NOT NULL CHECK (json_valid(capsule_json)),
    relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, relative_path),
    FOREIGN KEY (session_id, project_id)
        REFERENCES sessions(session_id, project_id),
    FOREIGN KEY (project_id, revision)
        REFERENCES revisions(project_id, revision)
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session_id TEXT,
    actor_id TEXT NOT NULL CHECK (length(trim(actor_id)) > 0),
    event_type TEXT NOT NULL CHECK (length(trim(event_type)) > 0),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id, revision)
        REFERENCES revisions(project_id, revision),
    FOREIGN KEY (session_id, project_id)
        REFERENCES sessions(session_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_revisions_project_created
    ON revisions(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project_active
    ON sessions(project_id, active, updated_at);
CREATE INDEX IF NOT EXISTS idx_handoffs_project_revision
    ON handoffs(project_id, revision);
CREATE INDEX IF NOT EXISTS idx_approvals_project_status
    ON approvals(project_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_project_event
    ON audit_events(project_id, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_metadata_code_unique
    ON projects(lower(json_extract(manifest_json, '$.metadata.code')))
    WHERE json_type(manifest_json, '$.metadata.code') = 'text';

PRAGMA user_version = 1;
