"""Safe domain errors exposed by the HTTP API."""


class DomainError(Exception):
    """Base class for errors with a stable public API contract."""

    code: str
    message: str
    status_code: int

    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class UnauthenticatedError(DomainError):
    def __init__(self) -> None:
        super().__init__("AUTHENTICATION_REQUIRED", "Authentication required", 401)


class AuthenticationFailedError(DomainError):
    def __init__(self) -> None:
        super().__init__("AUTHENTICATION_FAILED", "Username or password is invalid", 401)


class PasswordChangeRequiredError(DomainError):
    def __init__(self) -> None:
        super().__init__("PASSWORD_CHANGE_REQUIRED", "Password change required", 403)


class PasswordReuseForbiddenError(DomainError):
    def __init__(self) -> None:
        super().__init__("PASSWORD_REUSE_FORBIDDEN", "New password must be different", 422)


class ForbiddenError(DomainError):
    def __init__(self, code: str = "PROJECT_FORBIDDEN", message: str = "You cannot access this project") -> None:
        super().__init__(code, message, 403)


class OwnerRequiredError(ForbiddenError):
    def __init__(self) -> None:
        super().__init__("OWNER_REQUIRED", "Owner access required")


class NotFoundError(DomainError):
    def __init__(self) -> None:
        super().__init__("PROJECT_NOT_FOUND", "Project not found", 404)


class FileNotFoundError(NotFoundError):
    def __init__(self) -> None:
        DomainError.__init__(self, "FILE_NOT_FOUND", "File not found", 404)


class ConflictError(DomainError):
    def __init__(self) -> None:
        super().__init__("PROJECT_NAME_CONFLICT", "A project with this name already exists", 409)


class FileUploadSizeMismatchError(DomainError):
    def __init__(self) -> None:
        super().__init__("FILE_UPLOAD_SIZE_MISMATCH", "Uploaded object size does not match declared size", 409)


class FileProvisioningPendingError(DomainError):
    def __init__(self) -> None:
        super().__init__("FILE_PROVISIONING_PENDING", "File upload provisioning is pending", 503)


class FileCompletionPendingError(DomainError):
    def __init__(self) -> None:
        super().__init__("FILE_COMPLETION_PENDING", "File completion is pending", 503)
