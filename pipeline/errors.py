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


class SessionNotFoundError(PipelineError):
    """No chunks were found in Drive for the requested session ID."""


class MissingChunksError(PipelineError):
    """A session's chunks have gaps or duplicate sequence numbers."""

    def __init__(self, message: str, missing: list[int], duplicates: list[int]):
        super().__init__(message)
        self.missing = missing
        self.duplicates = duplicates


class MasterAlreadyExistsError(PipelineError):
    """A master file for this session already exists in Drive."""


class ReconstructionValidationError(PipelineError):
    """The reconstructed master failed duration or decode-integrity validation."""


class FFmpegNotFoundError(PipelineError):
    """ffmpeg/ffprobe are not installed or not on PATH."""


class FFmpegError(PipelineError):
    """A low-level ffmpeg/ffprobe subprocess call failed."""


class MasterNotFoundError(PipelineError):
    """No reconstructed master file exists in Drive for the requested session."""


class ProxyAlreadyExistsError(PipelineError):
    """A proxy file for this session already exists in Drive."""


class ProxyValidationError(PipelineError):
    """The generated proxy failed duration or decode-integrity validation."""


class ResolveNotAvailableError(PipelineError):
    """The DaVinci Resolve scripting module couldn't be imported — env vars
    (RESOLVE_SCRIPT_API/LIB, PYTHONPATH) missing, or Resolve isn't installed."""


class ResolveConnectionError(PipelineError):
    """Could not connect to (or launch and then connect to) a running Resolve."""


class SessionNotReadyError(PipelineError):
    """The requested session isn't at the READY stage yet (no validated proxy)."""


class LocalSyncNotFoundError(PipelineError):
    """The session's original or proxy file isn't present yet at its expected
    local Drive-for-desktop-synced path."""


class QualityMismatchError(PipelineError):
    """A new session's resolution/fps doesn't match the project's existing master,
    so it can't be stream-copy-appended without re-encoding (not attempted)."""


class SessionAlreadyMergedError(PipelineError):
    """This session's chunks have already been merged into the project's master."""
