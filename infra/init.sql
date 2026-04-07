-- ForgeChain Postgres bootstrap
-- Stores job audit trail (Redis is the live store; Postgres is append-only audit log).

CREATE TABLE IF NOT EXISTS fc_job_audit (
    id           BIGSERIAL PRIMARY KEY,
    task_id      UUID        NOT NULL,
    state        VARCHAR(20) NOT NULL,
    role         VARCHAR(40),
    reviewer     VARCHAR(100),
    metadata     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fc_job_audit_task ON fc_job_audit(task_id);
CREATE INDEX IF NOT EXISTS idx_fc_job_audit_state ON fc_job_audit(state);
CREATE INDEX IF NOT EXISTS idx_fc_job_audit_created ON fc_job_audit(created_at DESC);

-- Token ledger (every LLM call logged here for cost analysis)
CREATE TABLE IF NOT EXISTS fc_token_ledger (
    entry_id          UUID        PRIMARY KEY,
    task_id           UUID        NOT NULL,
    role              VARCHAR(40),
    tier              VARCHAR(10),   -- junior | mid | senior | cto
    stage             VARCHAR(30),   -- draft | escalation | cto_review | cto_revision
    provider          VARCHAR(20),
    model             VARCHAR(60),
    prompt_tokens     INT,
    completion_tokens INT,
    total_tokens      INT,
    input_cost_usd    NUMERIC(14,8),
    output_cost_usd   NUMERIC(14,8),
    total_cost_usd    NUMERIC(14,8),
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fc_ledger_task    ON fc_token_ledger(task_id);
CREATE INDEX IF NOT EXISTS idx_fc_ledger_tier    ON fc_token_ledger(tier);
CREATE INDEX IF NOT EXISTS idx_fc_ledger_provider ON fc_token_ledger(provider);
CREATE INDEX IF NOT EXISTS idx_fc_ledger_recorded ON fc_token_ledger(recorded_at DESC);

-- Patch storage (for jobs where patches exceed Redis memory budget)
CREATE TABLE IF NOT EXISTS fc_patch (
    task_id    UUID PRIMARY KEY,
    patch      TEXT NOT NULL,
    pr_url     TEXT,
    stored_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
