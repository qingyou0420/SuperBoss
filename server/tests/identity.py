"""Complete local-identity fixtures shared by non-authentication tests."""

from datetime import UTC, datetime

from superboss.modules.auth.passwords import hash_password
from superboss.modules.users.models import Role, User, UserStatus

LOCAL_TEST_PASSWORD = "synthetic local test password"
LOCAL_TEST_PASSWORD_HASH = hash_password(LOCAL_TEST_PASSWORD)


def local_user(
    username: str,
    *,
    display_name: str = "",
    role: Role = Role.STAFF,
    status: UserStatus = UserStatus.ACTIVE,
    must_change_password: bool = False,
) -> User:
    """Create a complete synthetic User without persisting plaintext credentials."""
    return User(
        username=username,
        display_name=display_name,
        password_hash=LOCAL_TEST_PASSWORD_HASH,
        password_changed_at=datetime.now(UTC),
        must_change_password=must_change_password,
        role=role,
        status=status,
    )
