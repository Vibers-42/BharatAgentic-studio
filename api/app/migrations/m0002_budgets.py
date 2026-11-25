"""Spend limits (PRD FR-38 / FR-42): an optional monthly limit for a project and an optional limit per conversation,
both in rupees. NULL means no limit, so existing projects behave exactly as before."""


def upgrade(conn, upload_dir: str) -> None:
    conn.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS budget_month_inr NUMERIC(12, 2)")
    conn.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS budget_conversation_inr NUMERIC(12, 2)")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_project_created_idx ON usage (project_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_conversation_idx ON usage (conversation_id)")
