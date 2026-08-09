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


class ForbiddenError(DomainError):
    def __init__(self, code: str = "PROJECT_FORBIDDEN", message: str = "You cannot access this project") -> None:
        super().__init__(code, message, 403)


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


class FileStorageFailureError(DomainError):
    def __init__(self) -> None:
        super().__init__("FILE_STORAGE_FAILURE", "File storage operation failed", 502)
