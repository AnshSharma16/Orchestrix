"""Application exception hierarchy (Orchestrix V2).

No framework, database or service imports: this module sits at the bottom
of the dependency graph. HTTP status mapping belongs in the API layer.

    AppException
    ├── ResourceNotFoundError
    ├── AuthorizationError
    ├── ValidationError
    │   └── SnapshotValidationError      publish-time graph/snapshot problems
    ├── DuplicateEmailError
    ├── LeaseLostError                   worker no longer owns the job
    └── WorkflowExecutionError
        ├── SnapshotIntegrityError       stored snapshot failed hash check
        ├── TransientError               node failure, retry allowed
        └── PermanentError               node failure, never retry
            └── UncertainDeliveryError   side effect may already have happened
"""


class AppException(Exception):
    """Base exception for application-level errors."""


class ResourceNotFoundError(AppException):
    """Raised when a requested resource does not exist."""


class AuthorizationError(AppException):
    """Raised when the user is not allowed to perform an action."""


class ValidationError(AppException):
    """Raised when business-level validation fails."""


class WorkflowExecutionError(AppException):
    """Raised when workflow execution fails."""


class DuplicateEmailError(AppException):
    """Raised when a user attempts to register an existing email."""

    def __init__(self, email: str):
        self.email = email
        super().__init__("Email already registered")


# --- V2: workflow snapshots -------------------------------------------------


class SnapshotValidationError(ValidationError):
    """Raised when a workflow graph fails validation while building a snapshot.

    Carries every problem found, so callers can report them all at once.
    """

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class SnapshotIntegrityError(WorkflowExecutionError):
    """Raised when a stored snapshot does not match its recorded hash.

    Permanent: the execution fails without retry. The message should include
    the expected and the computed hash.
    """


# --- V2: durable queue ------------------------------------------------------


class LeaseLostError(AppException):
    """Raised when a worker's fenced write finds it no longer owns the job.

    A control-flow signal, not a failure: the job now belongs to another
    worker or the reaper. The holder must stop and write nothing more.
    Deliberately NOT a WorkflowExecutionError, so execution-failure
    handling can never swallow it.
    """


# --- V2: node execution outcomes --------------------------------------------


class TransientError(WorkflowExecutionError):
    """Node failure that may succeed on retry (timeouts, connection errors,
    5xx, 429).

    retry_after_seconds carries a server-requested delay (e.g. Retry-After),
    which the retry policy should honor.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class PermanentError(WorkflowExecutionError):
    """Node failure that retrying cannot fix (4xx, bad config, bad template)."""


class UncertainDeliveryError(PermanentError):
    """Raised when a side effect may already have happened and the node is
    configured not to risk repeating it (retry_on_ambiguous=false).

    Permanent by design: fails the execution for operator decision instead
    of silently re-sending.
    """