"""Domain-specific exceptions for MyFreeFarm."""


class MFFException(Exception):
    """Base exception for all MyFreeFarm errors."""


class AuthenticationError(MFFException):
    """Raised when login or token extraction fails."""


class CaptchaRequiredError(AuthenticationError):
    """Raised when upstream server requires captcha verification / bot detection triggered."""


class SessionExpiredError(MFFException):
    """Raised when RID is invalid or session has expired."""


class UpstreamMaintenanceError(MFFException):
    """Raised when upstream server is down for maintenance (HTTP 503 or maintenance HTML)."""


class UpstreamAPIError(MFFException):
    """Raised when game server returns an error code or invalid datablock."""


class NetworkTimeoutError(MFFException):
    """Raised when upstream request times out."""


class InsufficientStockError(MFFException):
    """Raised when products cannot be grasped or are unavailable."""


class CircuitBreakerOpenError(MFFException):
    """Raised when circuit breaker is active and prevents upstream requests."""
