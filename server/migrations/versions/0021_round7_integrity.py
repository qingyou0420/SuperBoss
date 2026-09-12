"""Close unrecorded old-0018 confirmation upgrades without guessing.

Revision ID: 0021_round7_integrity
Revises: 0020_round6_integrity

0019 still isolates duplicate-points OK rows that lack reviewed_at / confirm
events. That is intentional: unknown historical OK values cannot be batch-opened.
This revision does not re-run that isolation. It:

1. Records a candidate list of unrecorded duplicate-points revisions together
   with explicit processing steps.
2. Applies only operator-supplied rows in knowledge_legacy_confirmation_map.
3. Leaves every unmapped candidate isolated so OWNER can confirm through the
   current points-review API (which writes audit).

Pre-upgrade path for databases still on old 0018: apply the same schema-only
review-audit DDL as 0020, then OWNER confirm, then alembic upgrade head. The
0019 guard will keep those recorded confirmations.
"""

from collections.abc import Sequence

from alembic import op

revision = "0021_round7_integrity"
down_revision = "0020_round6_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CANDIDATE_REASON = "UNRECORDED_DUPLICATE_POINTS"
_CANDIDATE_STEPS = (
    "Missing per-revision confirmation evidence; keep isolated. "
    "Steps: (1) Before 0019, apply 0020 schema-only review-audit DDL then OWNER "
    "points-review confirm. (2) If a trusted source lists this revision, INSERT "
    "it into knowledge_legacy_confirmation_map and run apply_legacy_confirmation_map. "
    "(3) After upgrade, OWNER re-confirms via current points-review to persist "
    "audit. Do not batch-update this list back to OK."
)

APPLY_LEGACY_CONFIRMATION_SQL = """
WITH candidates AS (
    SELECT
        r.id,
        r.doc_id,
        r.version,
        COALESCE(r.points_review, 'OK') AS previous_status,
        m.confirmed_by,
        d.project_id
    FROM knowledge_legacy_confirmation_map m
    JOIN knowledge_revisions r ON r.id = m.revision_id
    JOIN knowledge_docs d ON d.id = r.doc_id
    WHERE r.points_review = 'NEEDS_REVIEW'
      AND r.points_reviewed_at IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM knowledge_revision_review_events e
          WHERE e.revision_id = r.id
            AND e.action = 'confirm'
      )
),
updated AS (
    UPDATE knowledge_revisions r
    SET points_review = 'OK',
        points_reviewed_by = c.confirmed_by,
        points_reviewed_at = now()
    FROM candidates c
    WHERE r.id = c.id
    RETURNING r.id
),
ins_events AS (
    INSERT INTO knowledge_revision_review_events (
        id, revision_id, doc_id, action, outcome, actor_id,
        previous_status, new_status, original_points_json
    )
    SELECT
        gen_random_uuid(),
        c.id,
        c.doc_id,
        'confirm',
        'CONFIRMED',
        c.confirmed_by,
        c.previous_status,
        'OK',
        '[]'::jsonb
    FROM candidates c
    RETURNING revision_id
)
INSERT INTO audit_logs (
    id, actor_kind, actor_id, action, object_type, object_id, project_id,
    outcome, metadata_json, created_at
)
SELECT
    gen_random_uuid(),
    'user',
    c.confirmed_by,
    'knowledge.revision.points_review.confirm',
    'knowledge_revision',
    c.id,
    c.project_id,
    'CONFIRMED',
    jsonb_build_object(
        'doc_id', c.doc_id::text,
        'revision_id', c.id::text,
        'version', c.version,
        'action', 'confirm',
        'previous_status', c.previous_status,
        'new_status', 'OK',
        'repeat', false,
        'legacy_map', true
    ),
    now()
FROM candidates c
"""

_UNRECORDED_DUPLICATE_SQL = """
    r.points_reviewed_at IS NULL
    AND COALESCE(r.points_review, 'OK') IN ('OK', 'NEEDS_REVIEW')
    AND NOT EXISTS (
        SELECT 1
        FROM knowledge_revision_review_events e
        WHERE e.revision_id = r.id
          AND e.action = 'confirm'
    )
    AND r.id IS DISTINCT FROM d.draft_revision_id
    AND jsonb_typeof(COALESCE(r.points_json, '[]'::jsonb)) = 'array'
    AND jsonb_array_length(COALESCE(r.points_json, '[]'::jsonb)) > 0
    AND EXISTS (
        SELECT 1
        FROM knowledge_revisions other
        WHERE other.doc_id = r.doc_id
          AND other.id <> r.id
          AND other.points_json = r.points_json
    )
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_unrecorded_ok_candidates (
            revision_id UUID PRIMARY KEY
                REFERENCES knowledge_revisions(id) ON DELETE CASCADE,
            doc_id UUID NOT NULL
                REFERENCES knowledge_docs(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            points_review VARCHAR(16) NOT NULL,
            reason TEXT NOT NULL,
            recommended_action TEXT NOT NULL,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_legacy_confirmation_map (
            revision_id UUID PRIMARY KEY
                REFERENCES knowledge_revisions(id) ON DELETE CASCADE,
            confirmed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            note TEXT NOT NULL DEFAULT '',
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO knowledge_unrecorded_ok_candidates (
            revision_id, doc_id, version, points_review, reason, recommended_action
        )
        SELECT
            r.id,
            r.doc_id,
            r.version,
            COALESCE(r.points_review, 'OK'),
            '{_CANDIDATE_REASON}',
            '{_CANDIDATE_STEPS}'
        FROM knowledge_revisions r
        JOIN knowledge_docs d ON d.id = r.doc_id
        WHERE {_UNRECORDED_DUPLICATE_SQL}
        ON CONFLICT (revision_id) DO NOTHING
        """
    )
    op.execute(APPLY_LEGACY_CONFIRMATION_SQL)


def downgrade() -> None:
    return
