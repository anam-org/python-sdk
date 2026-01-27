"""Exception classes for the Anam SDK."""

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Error codes for Anam SDK errors."""

    CONFIGURATION_ERROR = "configuration_error"
    AUTHENTICATION_ERROR = "authentication_error"
    VALIDATION_ERROR = "validation_error"
    CONNECTION_ERROR = "connection_error"
    WEBRTC_ERROR = "webrtc_error"
    SIGNALLING_ERROR = "signalling_error"
    SESSION_ERROR = "session_error"
    SERVER_ERROR = "server_error"
    TIMEOUT_ERROR = "timeout_error"
    NO_PLAN_FOUND = "no_plan_found"
    USAGE_LIMIT_REACHED = "usage_limit_reached"
    CONCURRENT_SESSION_LIMIT = "concurrent_session_limit"
    SPEND_CAP_REACHED = "spend_cap_reached"
    SERVICE_BUSY = "service_busy"


class AnamError(Exception):
    """Base exception for all Anam SDK errors."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.SERVER_ERROR,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def __repr__(self) -> str:
        return f"AnamError(message={self.message!r}, code={self.code!r}, status_code={self.status_code!r})"


class ConfigurationError(AnamError):
    """Raised when there's an error in the client configuration."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, ErrorCode.CONFIGURATION_ERROR, 400, details)


class AuthenticationError(AnamError):
    """Raised when authentication fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, ErrorCode.AUTHENTICATION_ERROR, 401, details)


class ConnectionError(AnamError):
    """Raised when connection to Anam services fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, ErrorCode.CONNECTION_ERROR, details=details)


class SessionError(AnamError):
    """Raised when there's an error with the streaming session."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.SESSION_ERROR,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code, status_code, details)
