"""One-time migration: create the users table and add user_id FKs to Phase 7 tables.

Usage:
    DATABASE_URL=postgresql://... python utils/migrate_users.py
"""

from __future__ import annotations

import os
import sys

import psycopg2

DDL = """
-- users table
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    google_sub  VARCHAR(255) NOT NULL UNIQUE,
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(255),
    avatar_url  TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    deleted_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_google_sub ON users (google_sub);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- Phase 7 FK additions (no-op if tables don't exist yet)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_portfolio') THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'user_portfolio' AND column_name = 'user_id'
        ) THEN
            ALTER TABLE user_portfolio
                ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
            CREATE INDEX IF NOT EXISTS idx_portfolio_user_id ON user_portfolio (user_id);
        END IF;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'daily_analysis_log') THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'daily_analysis_log' AND column_name = 'user_id'
        ) THEN
            ALTER TABLE daily_analysis_log
                ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
            CREATE INDEX IF NOT EXISTS idx_log_user_id ON daily_analysis_log (user_id);
        END IF;
    END IF;
END $$;
"""


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        print("Migration completed successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
