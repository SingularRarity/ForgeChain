# Database Engineer Skills
## Applied to role: db_eng

---

## Stack

- PostgreSQL 16
- SQLAlchemy 2.0 (async, `AsyncSession`)
- Alembic for migrations
- asyncpg as the async driver

---

## Alembic migration template

```python
"""add_fc_token_ledger_table

Revision ID: abc123
Revises: def456
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    op.create_table(
        "fc_token_ledger",
        sa.Column("entry_id",          sa.UUID(),          primary_key=True),
        sa.Column("task_id",           sa.UUID(),          nullable=False),
        sa.Column("role",              sa.String(40)),
        sa.Column("tier",              sa.String(10)),
        sa.Column("total_cost_usd",    sa.Numeric(14, 8)),
        sa.Column("recorded_at",       sa.TIMESTAMPTZ(),   server_default=sa.func.now()),
    )
    op.create_index("idx_fc_ledger_task",     "fc_token_ledger", ["task_id"])
    op.create_index("idx_fc_ledger_recorded", "fc_token_ledger", ["recorded_at"])

def downgrade() -> None:
    op.drop_index("idx_fc_ledger_recorded", table_name="fc_token_ledger")
    op.drop_index("idx_fc_ledger_task",     table_name="fc_token_ledger")
    op.drop_table("fc_token_ledger")
```

---

## SQLAlchemy 2.0 async session pattern

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
```

---

## Query patterns

```python
# Always parameterised — never f-strings
result = await session.execute(
    select(FCJobAudit)
    .where(FCJobAudit.task_id == task_id)
    .order_by(FCJobAudit.created_at.desc())
    .limit(50)
)

# Bulk insert with returning
stmt = insert(FCTokenLedger).values(rows).returning(FCTokenLedger.entry_id)
result = await session.execute(stmt)
await session.commit()
```

---

## Index strategy

- Index foreign keys always
- Index columns used in WHERE or ORDER BY with >10k rows
- Partial indexes for soft-delete patterns: `WHERE deleted_at IS NULL`
- Use `EXPLAIN ANALYZE` before declaring an index complete

---

## What to avoid

- `TEXT` for fixed-length identifiers — use `VARCHAR(n)` or `UUID`
- Storing JSON blobs as `TEXT` — use `JSONB` (queryable, indexed)
- `TIMESTAMP` without timezone — always `TIMESTAMPTZ`
- N+1 queries — use `joinedload` or `selectinload` in SQLAlchemy
- Migrations without a `downgrade()` — always provide rollback
- Plaintext PII in any column — encrypt at application layer
