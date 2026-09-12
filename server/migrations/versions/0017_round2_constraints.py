"""Align unique constraints and snapshot knowledge points on revisions.

Revision ID: 0017_round2_constraints
Revises: 0016_round1_integrity
"""

from collections.abc import Sequence

from alembic import op

revision = "0017_round2_constraints"
down_revision = "0016_round1_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_unique(table: str, name: str, columns: str) -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({columns});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
            WHEN duplicate_table THEN NULL;
        END $$;
        """
    )


def upgrade() -> None:
    _add_unique("finance_import_rows", "uq_finance_import_rows_batch_row", "batch_key, row_index")
    _add_unique("knowledge_revisions", "uq_knowledge_revisions_doc_version", "doc_id, version")
    _add_unique("directory_project_links", "uq_directory_project_links", "entry_id, project_id")
    _add_unique(
        "workflow_template_versions", "uq_workflow_template_versions", "template_id, version"
    )
    op.execute(
        "ALTER TABLE knowledge_revisions ADD COLUMN IF NOT EXISTS points_json "
        "JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        """
        UPDATE knowledge_revisions r
        SET points_json = COALESCE((
            SELECT jsonb_agg(
                jsonb_build_object(
                    'id', p.id,
                    'title', p.title,
                    'body_md', p.body_md,
                    'source_file_id', p.source_file_id,
                    'sort_order', p.sort_order
                )
                ORDER BY p.sort_order
            )
            FROM knowledge_points p
            WHERE p.doc_id = r.doc_id
        ), '[]'::jsonb)
        FROM knowledge_docs d
        WHERE r.id = d.draft_revision_id
          AND COALESCE(jsonb_typeof(r.points_json), 'null') = 'array'
          AND jsonb_array_length(r.points_json) = 0
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_revisions DROP COLUMN IF EXISTS points_json")
    op.execute(
        "ALTER TABLE finance_import_rows DROP CONSTRAINT IF EXISTS uq_finance_import_rows_batch_row"
    )
    op.execute(
        "ALTER TABLE knowledge_revisions DROP CONSTRAINT IF EXISTS "
        "uq_knowledge_revisions_doc_version"
    )
    op.execute(
        "ALTER TABLE directory_project_links DROP CONSTRAINT IF EXISTS uq_directory_project_links"
    )
    op.execute(
        "ALTER TABLE workflow_template_versions DROP CONSTRAINT IF EXISTS "
        "uq_workflow_template_versions"
    )
