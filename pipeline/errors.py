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
