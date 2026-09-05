"""Single-schema baseline matching current SQLAlchemy models.

Revision ID: 0001_baseline
Revises:
"""

from collections.abc import Sequence

from alembic import op

from superboss.core.db import Base
from superboss.modules.agent import models as agent_models  # noqa: F401
from superboss.modules.audit import models as audit_models  # noqa: F401
from superboss.modules.auth import models as auth_models  # noqa: F401
from superboss.modules.files import models as file_models  # noqa: F401
from superboss.modules.finance import models as finance_models  # noqa: F401
from superboss.modules.knowledge import models as knowledge_models  # noqa: F401
from superboss.modules.projects import models as project_models  # noqa: F401
from superboss.modules.users import models as user_models  # noqa: F401

revision = "0001_baseline"
down_revision = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
