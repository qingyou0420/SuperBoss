"""Protect history: payments, knowledge revisions, directory sources, workflow.

Revision ID: 0016_round1_integrity
Revises: 0015_placeholder_data
"""

from collections.abc import Sequence

from alembic import op

revision = "0016_round1_integrity"
down_revision = "0015_placeholder_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TEMPLATE_ID = "a0000000-0000-4000-8000-000000000001"
DEFAULT_VERSION_ID = "a0000000-0000-4000-8000-000000000002"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_payments (
            id UUID PRIMARY KEY,
            entry_id UUID NOT NULL REFERENCES finance_entries(id) ON DELETE CASCADE,
            paid_on DATE NOT NULL,
            amount_cents BIGINT NOT NULL,
            idempotency_key VARCHAR(64) NOT NULL DEFAULT '',
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_finance_payments_amount CHECK (
                amount_cents BETWEEN 1 AND 1000000000000
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finance_payments_entry "
        "ON finance_payments (entry_id, paid_on)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_finance_payments_idempotency
        ON finance_payments (entry_id, idempotency_key)
        WHERE idempotency_key <> ''
        """
    )
    op.execute(
        """
        INSERT INTO finance_payments (
            id, entry_id, paid_on, amount_cents, idempotency_key, created_by, created_at
        )
        SELECT gen_random_uuid(), id, COALESCE(paid_on, occurred_on),
               COALESCE(paid_cents, amount_cents), 'migrated-paid',
               created_by, created_at
        FROM finance_entries
        WHERE paid_on IS NOT NULL OR paid_cents IS NOT NULL
        """
    )
    op.execute(
        "ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS voucher VARCHAR(255) "
        "NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE finance_entries ADD COLUMN IF NOT EXISTS voucher_file_id UUID "
        "REFERENCES files(id) ON DELETE SET NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_import_batches (
            batch_key VARCHAR(64) PRIMARY KEY,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_filename VARCHAR(255) NOT NULL DEFAULT ''
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_import_rows (
            id UUID PRIMARY KEY,
            batch_key VARCHAR(64) NOT NULL
                REFERENCES finance_import_batches(batch_key) ON DELETE CASCADE,
            row_index INTEGER NOT NULL,
            status VARCHAR(16) NOT NULL,
            reason VARCHAR(64) NOT NULL DEFAULT '',
            fingerprint VARCHAR(512) NOT NULL DEFAULT '',
            entry_id UUID REFERENCES finance_entries(id) ON DELETE SET NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT ck_finance_import_rows_status CHECK (
                status IN ('INSERTED','SKIPPED','DUPLICATE','UNRESOLVED','LINKED')
            ),
            CONSTRAINT uq_finance_import_rows_batch_row UNIQUE (batch_key, row_index)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_revisions (
            id UUID PRIMARY KEY,
            doc_id UUID NOT NULL REFERENCES knowledge_docs(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            body_md TEXT NOT NULL DEFAULT '',
            change_reason TEXT NOT NULL DEFAULT '',
            stage_title VARCHAR(255) NOT NULL DEFAULT '',
            is_canonical BOOLEAN NOT NULL DEFAULT false,
            source_file_id UUID,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_revisions_doc_version UNIQUE (doc_id, version)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_revisions_doc "
        "ON knowledge_revisions (doc_id, version)"
    )
    op.execute("ALTER TABLE knowledge_docs ADD COLUMN IF NOT EXISTS published_revision_id UUID")
    op.execute("ALTER TABLE knowledge_docs ADD COLUMN IF NOT EXISTS draft_revision_id UUID")
    op.execute(
        """
        INSERT INTO knowledge_revisions (
            id, doc_id, version, body_md, change_reason, stage_title,
            is_canonical, source_file_id, created_by, created_at
        )
        SELECT gen_random_uuid(), id, 1, body_md, change_reason, stage_title,
               is_canonical, source_file_id, created_by, updated_at
        FROM knowledge_docs
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_revisions r WHERE r.doc_id = knowledge_docs.id
        )
        """
    )
    op.execute(
        """
        UPDATE knowledge_docs d
        SET draft_revision_id = r.id,
            published_revision_id = CASE WHEN d.status = 'PUBLISHED' THEN r.id ELSE NULL END
        FROM knowledge_revisions r
        WHERE r.doc_id = d.id AND r.version = 1
          AND d.draft_revision_id IS NULL
        """
    )

    op.execute(
        "ALTER TABLE directory_entries ADD COLUMN IF NOT EXISTS identity_key VARCHAR(512) "
        "NOT NULL DEFAULT ''"
    )
    op.execute(
        """
        UPDATE directory_entries
        SET identity_key = lower(regexp_replace(
            name || '|' || district || '|' || street || '|' || community,
            '\\s+', '', 'g'))
        WHERE identity_key = ''
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_directory_entries_identity "
        "ON directory_entries (identity_key)"
    )
    op.execute(
        "ALTER TABLE directory_entries DROP CONSTRAINT IF EXISTS uq_directory_entries_source"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_import_batches (
            id UUID PRIMARY KEY,
            filename VARCHAR(255) NOT NULL,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_source_rows (
            id UUID PRIMARY KEY,
            batch_id UUID NOT NULL
                REFERENCES directory_import_batches(id) ON DELETE CASCADE,
            filename VARCHAR(255) NOT NULL,
            sheet VARCHAR(64) NOT NULL DEFAULT 'Sheet1',
            row_number INTEGER NOT NULL,
            identity_key VARCHAR(512) NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            entry_id UUID REFERENCES directory_entries(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_directory_source_rows_batch "
        "ON directory_source_rows (batch_id, row_number)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_conflicts (
            id UUID PRIMARY KEY,
            batch_id UUID NOT NULL
                REFERENCES directory_import_batches(id) ON DELETE CASCADE,
            source_row_id UUID REFERENCES directory_source_rows(id) ON DELETE SET NULL,
            existing_entry_id UUID REFERENCES directory_entries(id) ON DELETE SET NULL,
            reason VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_project_links (
            id UUID PRIMARY KEY,
            entry_id UUID NOT NULL
                REFERENCES directory_entries(id) ON DELETE CASCADE,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            is_current BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_directory_project_links UNIQUE (entry_id, project_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO directory_project_links (id, entry_id, project_id, is_current, created_at)
        SELECT gen_random_uuid(), id, project_id, true, created_at
        FROM directory_entries
        WHERE project_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM directory_project_links l
              WHERE l.entry_id = directory_entries.id
                AND l.project_id = directory_entries.project_id
          )
        """
    )

    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS contract_due_on DATE")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS service_completed_on DATE")
    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS service_completed_by UUID "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS template_version_id UUID")
    op.execute("UPDATE projects SET contract_due_on = due_on WHERE contract_due_on IS NULL")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_lead_history (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            previous_lead_id UUID REFERENCES users(id) ON DELETE SET NULL,
            new_lead_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "ALTER TABLE project_nodes ADD COLUMN IF NOT EXISTS required_materials "
        "JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE project_nodes ADD COLUMN IF NOT EXISTS duration_days INTEGER "
        "NOT NULL DEFAULT 1"
    )
    op.execute("ALTER TABLE project_nodes DROP CONSTRAINT IF EXISTS ck_project_nodes_status")
    op.execute(
        """
        ALTER TABLE project_nodes ADD CONSTRAINT ck_project_nodes_status
        CHECK (status IN ('OPEN','DONE','SKIPPED'))
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_templates (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            is_default BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_template_versions (
            id UUID PRIMARY KEY,
            template_id UUID NOT NULL
                REFERENCES workflow_templates(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            published BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_workflow_template_versions UNIQUE (template_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_template_nodes (
            id UUID PRIMARY KEY,
            version_id UUID NOT NULL
                REFERENCES workflow_template_versions(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL,
            title VARCHAR(255) NOT NULL,
            duration_days INTEGER NOT NULL DEFAULT 1,
            photo_required BOOLEAN NOT NULL DEFAULT false,
            document_name VARCHAR(255) NOT NULL DEFAULT '',
            preparation JSONB NOT NULL DEFAULT '[]'::jsonb,
            required_materials JSONB NOT NULL DEFAULT '[]'::jsonb
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO workflow_templates (id, name, is_default)
        VALUES ('{DEFAULT_TEMPLATE_ID}', '业主大会默认流程', true)
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO workflow_template_versions (id, template_id, version, published)
        VALUES ('{DEFAULT_VERSION_ID}', '{DEFAULT_TEMPLATE_ID}', 1, true)
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO workflow_template_nodes (
            id, version_id, sort_order, title, duration_days, photo_required,
            document_name, preparation, required_materials
        )
        SELECT gen_random_uuid(), '{DEFAULT_VERSION_ID}', sort_order, title,
               duration_days, photo_required, document_name, preparation::jsonb,
               required_materials::jsonb
        FROM (VALUES
            (0, '筹备与资料确认', 3, false, '筹备工作安排.pdf',
             '["核对本小区议题和委托资料","确认本项目联系人及沟通方式"]',
             '["document"]'),
            (1, '候选人报名', 5, false, '候选人报名通知.pdf',
             '["准备报名表与候选人资料清单","核对报名截止时间及受理方式"]',
             '["document"]'),
            (2, '候选人名单公示', 3, true, '候选人名单公示.pdf',
             '["使用经确认的候选人名单","提前确认公示位置与照片留存方式"]',
             '["document","photo"]'),
            (3, '业主大会公告', 3, true, '业主大会召开公告.pdf',
             '["核对公告中的议题、时间与地点","确认投票材料与现场准备事项"]',
             '["document","photo"]'),
            (4, '投票与现场执行', 2, true, '投票执行安排.pdf',
             '["按最新日程准备投票材料","确认现场物料与资料保管方式"]',
             '["document","photo"]'),
            (5, '开箱与结果统计', 2, false, '开箱及结果统计表.pdf',
             '["准备经确认的统计表及记录材料","核对结果材料与原始记录"]',
             '["document"]'),
            (6, '结果公示与备案', 5, true, '表决结果公示.pdf',
             '["核对结果公示定稿与相关材料","按本项目要求整理备案资料"]',
             '["document","photo"]')
        ) AS t(sort_order, title, duration_days, photo_required, document_name,
               preparation, required_materials)
        WHERE NOT EXISTS (
            SELECT 1 FROM workflow_template_nodes n
            WHERE n.version_id = '{DEFAULT_VERSION_ID}'
        )
        """
    )
    op.execute(
        """
        UPDATE project_nodes SET required_materials =
            CASE WHEN photo_required
                 THEN '["document","photo"]'::jsonb
                 ELSE '["document"]'::jsonb
            END
        WHERE required_materials = '[]'::jsonb
        """
    )
    op.execute(
        """
        UPDATE project_nodes
        SET duration_days = GREATEST(1, (planned_end - planned_start) + 1)
        WHERE planned_start IS NOT NULL AND planned_end IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_template_nodes")
    op.execute("DROP TABLE IF EXISTS workflow_template_versions")
    op.execute("DROP TABLE IF EXISTS workflow_templates")
    op.execute("DROP TABLE IF EXISTS project_lead_history")
    op.execute("ALTER TABLE project_nodes DROP COLUMN IF EXISTS duration_days")
    op.execute("ALTER TABLE project_nodes DROP COLUMN IF EXISTS required_materials")
    op.execute("ALTER TABLE project_nodes DROP CONSTRAINT IF EXISTS ck_project_nodes_status")
    op.execute(
        """
        ALTER TABLE project_nodes ADD CONSTRAINT ck_project_nodes_status
        CHECK (status IN ('OPEN','DONE'))
        """
    )
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS template_version_id")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS service_completed_by")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS service_completed_on")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS contract_due_on")
    op.execute("DROP TABLE IF EXISTS directory_project_links")
    op.execute("DROP TABLE IF EXISTS directory_conflicts")
    op.execute("DROP TABLE IF EXISTS directory_source_rows")
    op.execute("DROP TABLE IF EXISTS directory_import_batches")
    op.execute(
        """
        ALTER TABLE directory_entries ADD CONSTRAINT uq_directory_entries_source
        UNIQUE (source_filename, source_sheet, source_row)
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_directory_entries_identity")
    op.execute("ALTER TABLE directory_entries DROP COLUMN IF EXISTS identity_key")
    op.execute("ALTER TABLE knowledge_docs DROP COLUMN IF EXISTS draft_revision_id")
    op.execute("ALTER TABLE knowledge_docs DROP COLUMN IF EXISTS published_revision_id")
    op.execute("DROP TABLE IF EXISTS knowledge_revisions")
    op.execute("DROP TABLE IF EXISTS finance_import_rows")
    op.execute("DROP TABLE IF EXISTS finance_import_batches")
    op.execute("ALTER TABLE finance_entries DROP COLUMN IF EXISTS voucher_file_id")
    op.execute("ALTER TABLE finance_entries DROP COLUMN IF EXISTS voucher")
    op.execute("DROP TABLE IF EXISTS finance_payments")
