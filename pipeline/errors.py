"""Custom exceptions surfaced to the CLI with clear, actionable messages."""


class PipelineError(Exception):
    """Base class for all errors raised by the pipeline."""


class AuthError(PipelineError):
    """OAuth credentials are missing, expired, or have been revoked."""


class NetworkError(PipelineError):
    """No internet connection or the Drive API is unreachable."""


class QuotaError(PipelineError):
    """Drive API rate limit or quota exceeded."""


class DuplicateProjectError(PipelineError):
    """A project with the requested name already exists."""


class ProjectNotFoundError(PipelineError):
    """A requested project does not exist under Content Creation/Projects."""
