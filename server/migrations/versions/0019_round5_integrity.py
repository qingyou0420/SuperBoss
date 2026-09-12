"""Repair schema if an earlier 0018 already ran, and retry unique-safe data fixes.

Revision ID: 0019_round5_integrity
Revises: 0018_round3_integrity
"""

from collections.abc import Sequence

from alembic import op

revision = "0019_round5_integrity"
down_revision = "0018_round3_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULTS = (
    ("directory_conflicts", "payload", "'{}'::jsonb"),
    ("directory_entries", "identity_key", "''::character varying"),
    ("directory_project_links", "is_current", "true"),
    ("directory_source_rows", "identity_key", "''::character varying"),
    ("directory_source_rows", "payload", "'{}'::jsonb"),
    ("directory_source_rows", "sheet", "'Sheet1'::character varying"),
    ("finance_entries", "voucher", "''::character varying"),
    ("finance_import_batches", "source_filename", "''::character varying"),
    ("finance_import_rows", "fingerprint", "''::character varying"),
    ("finance_import_rows", "payload", "'{}'::jsonb"),
    ("finance_import_rows", "reason", "''::character varying"),
    ("finance_payments", "idempotency_key", "''::character varying"),
    ("knowledge_revisions", "body_md", "''::text"),
    ("knowledge_revisions", "change_reason", "''::text"),
    ("knowledge_revisions", "is_canonical", "false"),
    ("knowledge_revisions", "points_json", "'[]'::jsonb"),
    ("knowledge_revisions", "points_review", "'OK'::character varying"),
    ("knowledge_revisions", "stage_title", "''::character varying"),
    ("project_nodes", "duration_days", "1"),
    ("project_nodes", "required_materials", "'[]'::jsonb"),
    ("workflow_template_nodes", "document_name", "''::character varying"),
    ("workflow_template_nodes", "duration_days", "1"),
    ("workflow_template_nodes", "photo_required", "false"),
    ("workflow_template_nodes", "preparation", "'[]'::jsonb"),
    ("workflow_template_nodes", "required_materials", "'[]'::jsonb"),
    ("workflow_template_versions", "published", "true"),
    ("workflow_templates", "is_default", "false"),
)

_SOURCE_ROW_SQL = """
CASE
  WHEN jsonb_typeof(payload->'source_row') IN ('number', 'string')
       AND (payload->>'source_row') ~ '^[1-9][0-9]*$'
    THEN (payload->>'source_row')::int
  ELSE NULL
END
"""

_SOURCE_SHEET_SQL = """
COALESCE(NULLIF(btrim(COALESCE(payload->>'source_sheet', '')), ''), 'Sheet1')
"""

_NEEDS_REVIEW_CONFIRMED_GUARD = """
  AND r.points_reviewed_at IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM knowledge_revision_review_events e
      WHERE e.revision_id = r.id
        AND e.action = 'confirm'
  )
"""


def _ensure_points_review_audit_schema() -> None:
    op.execute(
        """
        ALTER TABLE knowledge_revisions
            ADD COLUMN IF NOT EXISTS points_reviewed_by
            UUID REFERENCES users(id) ON DELETE SET NULL
        """
    )
    op.execute(
        "ALTER TABLE knowledge_revisions ADD COLUMN IF NOT EXISTS points_reviewed_at TIMESTAMPTZ"
    )
    op.execute(
        """
        ALTER TABLE knowledge_revision_pollution
            ADD COLUMN IF NOT EXISTS reviewed_by
            UUID REFERENCES users(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_revision_review_events (
            id UUID PRIMARY KEY,
            revision_id UUID NOT NULL
                REFERENCES knowledge_revisions(id) ON DELETE CASCADE,
            doc_id UUID NOT NULL
                REFERENCES knowledge_docs(id) ON DELETE CASCADE,
            action VARCHAR(16) NOT NULL,
            outcome VARCHAR(32) NOT NULL,
            actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
            previous_status VARCHAR(16) NOT NULL,
            new_status VARCHAR(16) NOT NULL,
            original_points_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_revision_review_events_revision
            ON knowledge_revision_review_events (revision_id, created_at)
        """
    )


def _relocate_unique_finance_indexes() -> None:
    op.execute(
        f"""
        UPDATE finance_import_rows AS r
        SET payload = COALESCE(r.payload, '{{}}'::jsonb)
            || jsonb_build_object('source_identity_conflict', true)
        FROM (
            SELECT id,
                   COUNT(*) OVER (PARTITION BY batch_key, source_sheet, source_row) AS sheet_cnt,
                   COUNT(*) OVER (PARTITION BY batch_key, source_row) AS target_cnt
            FROM (
                SELECT id,
                       batch_key,
                       {_SOURCE_ROW_SQL} AS source_row,
                       {_SOURCE_SHEET_SQL} AS source_sheet
                FROM finance_import_rows
            ) parsed
            WHERE source_row IS NOT NULL
        ) AS src
        WHERE r.id = src.id
          AND (src.sheet_cnt > 1 OR src.target_cnt > 1)
        """
    )
    op.execute(
        f"""
        UPDATE finance_import_rows AS r
        SET row_index = -1 - r.row_index
        FROM (
            SELECT id,
                   source_row,
                   COUNT(*) OVER (PARTITION BY batch_key, source_sheet, source_row) AS sheet_cnt,
                   COUNT(*) OVER (PARTITION BY batch_key, source_row) AS target_cnt
            FROM (
                SELECT id,
                       batch_key,
                       {_SOURCE_ROW_SQL} AS source_row,
                       {_SOURCE_SHEET_SQL} AS source_sheet
                FROM finance_import_rows
            ) parsed
            WHERE source_row IS NOT NULL
        ) AS src
        WHERE r.id = src.id
          AND src.source_row IS NOT NULL
          AND src.source_row > 0
          AND src.sheet_cnt = 1
          AND src.target_cnt = 1
          AND r.row_index IS DISTINCT FROM src.source_row
          AND NOT EXISTS (
              SELECT 1 FROM finance_import_rows other
              WHERE other.batch_key = r.batch_key
                AND other.row_index = src.source_row
                AND other.id <> r.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM finance_import_rows other
              WHERE other.batch_key = r.batch_key
                AND other.row_index = -1 - r.row_index
                AND other.id <> r.id
          )
        """
    )
    op.execute(
        f"""
        UPDATE finance_import_rows AS r
        SET row_index = src.source_row
        FROM (
            SELECT id,
                   source_row,
                   COUNT(*) OVER (PARTITION BY batch_key, source_row) AS target_cnt
            FROM (
                SELECT id,
                       batch_key,
                       {_SOURCE_ROW_SQL} AS source_row
                FROM finance_import_rows
            ) parsed
            WHERE source_row IS NOT NULL
        ) AS src
        WHERE r.id = src.id
          AND r.row_index < 0
          AND src.source_row IS NOT NULL
          AND src.target_cnt = 1
          AND NOT EXISTS (
              SELECT 1 FROM finance_import_rows other
              WHERE other.batch_key = r.batch_key
                AND other.row_index = src.source_row
                AND other.id <> r.id
          )
        """
    )
    op.execute(
        """
        UPDATE finance_import_rows AS r
        SET row_index = -1 - r.row_index
        WHERE r.row_index < 0
          AND NOT EXISTS (
              SELECT 1 FROM finance_import_rows other
              WHERE other.batch_key = r.batch_key
                AND other.row_index = -1 - r.row_index
                AND other.id <> r.id
          )
        """
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_revisions ADD COLUMN IF NOT EXISTS released "
        "BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE knowledge_revisions ADD COLUMN IF NOT EXISTS points_review "
        "VARCHAR(16) NOT NULL DEFAULT 'OK'"
    )
    op.execute(
        """
        ALTER TABLE knowledge_revisions
            DROP CONSTRAINT IF EXISTS ck_knowledge_revisions_points_review
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge_revisions
            ADD CONSTRAINT ck_knowledge_revisions_points_review
            CHECK (points_review IN ('OK','NEEDS_REVIEW','CLEARED'))
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_revision_pollution (
            revision_id UUID PRIMARY KEY
                REFERENCES knowledge_revisions(id) ON DELETE CASCADE,
            reason TEXT NOT NULL DEFAULT 'EXPLICIT_POLLUTION',
            original_points_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'knowledge_revision_pollution_staging'
            ) THEN
                INSERT INTO knowledge_revision_pollution (
                    revision_id, original_points_json
                )
                SELECT s.revision_id, COALESCE(r.points_json, '[]'::jsonb)
                FROM knowledge_revision_pollution_staging s
                JOIN knowledge_revisions r ON r.id = s.revision_id
                ON CONFLICT (revision_id) DO NOTHING;
            END IF;
        END $$;
        """
    )
    _ensure_points_review_audit_schema()
    op.execute(
        f"""
        UPDATE knowledge_revisions r
        SET points_review = 'NEEDS_REVIEW'
        WHERE r.points_review = 'OK'
          {_NEEDS_REVIEW_CONFIRMED_GUARD}
          AND r.id IN (
            SELECT copy.id
            FROM knowledge_revisions copy
            JOIN knowledge_docs d ON d.id = copy.doc_id
            WHERE copy.id IS DISTINCT FROM d.draft_revision_id
              AND jsonb_typeof(COALESCE(copy.points_json, '[]'::jsonb)) = 'array'
              AND jsonb_array_length(COALESCE(copy.points_json, '[]'::jsonb)) > 0
              AND EXISTS (
                  SELECT 1
                  FROM knowledge_revisions other
                  WHERE other.doc_id = copy.doc_id
                    AND other.id <> copy.id
                    AND other.points_json = copy.points_json
              )
          )
        """
    )
    op.execute(
        """
        UPDATE knowledge_revision_pollution p
        SET original_points_json = r.points_json
        FROM knowledge_revisions r
        WHERE p.revision_id = r.id
          AND jsonb_typeof(COALESCE(r.points_json, '[]'::jsonb)) = 'array'
          AND jsonb_array_length(COALESCE(r.points_json, '[]'::jsonb)) > 0
          AND (
              jsonb_typeof(COALESCE(p.original_points_json, '[]'::jsonb)) <> 'array'
              OR jsonb_array_length(COALESCE(p.original_points_json, '[]'::jsonb)) = 0
          )
        """
    )
    op.execute(
        """
        UPDATE knowledge_revisions r
        SET points_json = '[]'::jsonb,
            points_review = 'CLEARED'
        FROM knowledge_revision_pollution p
        WHERE r.id = p.revision_id
          AND r.points_review <> 'CLEARED'
        """
    )
    op.execute(
        "ALTER TABLE directory_conflicts ADD COLUMN IF NOT EXISTS status "
        "VARCHAR(16) NOT NULL DEFAULT 'OPEN'"
    )
    op.execute(
        "ALTER TABLE directory_conflicts ADD COLUMN IF NOT EXISTS resolved_entry_id "
        "UUID REFERENCES directory_entries(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE directory_conflicts ADD COLUMN IF NOT EXISTS resolved_by "
        "UUID REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE directory_conflicts ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE directory_conflicts DROP CONSTRAINT IF EXISTS ck_directory_conflicts_status"
    )
    op.execute(
        """
        ALTER TABLE directory_conflicts
            ADD CONSTRAINT ck_directory_conflicts_status
            CHECK (status IN ('OPEN','LINKED','SPLIT','SUPERSEDED'))
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_directory_project_links_entry "
        "ON directory_project_links (entry_id, is_current)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finance_import_rows_batch "
        "ON finance_import_rows (batch_key, row_index)"
    )
    _relocate_unique_finance_indexes()
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_template_versions_template "
        "ON workflow_template_versions (template_id, version)"
    )
    for table, column, default in _DEFAULTS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}")


def downgrade() -> None:
    return
