from pipeline import errors


def test_all_custom_errors_are_pipeline_errors():
    custom = [
        errors.AuthError, errors.NetworkError, errors.QuotaError,
        errors.DuplicateProjectError, errors.ProjectNotFoundError,
        errors.SessionNotFoundError, errors.MasterAlreadyExistsError,
        errors.ReconstructionValidationError, errors.FFmpegNotFoundError,
        errors.FFmpegError, errors.MasterNotFoundError, errors.ProxyAlreadyExistsError,
        errors.ProxyValidationError, errors.ResolveNotAvailableError,
        errors.ResolveConnectionError, errors.SessionNotReadyError,
        errors.LocalSyncNotFoundError,
    ]
    for cls in custom:
        assert issubclass(cls, errors.PipelineError)


def test_missing_chunks_error_carries_missing_and_duplicates():
    exc = errors.MissingChunksError("bad session", missing=[3, 7], duplicates=[5])
    assert exc.missing == [3, 7]
    assert exc.duplicates == [5]
    assert str(exc) == "bad session"


def test_pipeline_error_is_catchable_as_exception():
    try:
        raise errors.AuthError("token expired")
    except errors.PipelineError as exc:
        assert "token expired" in str(exc)
    else:
        raise AssertionError("AuthError should have been caught as PipelineError")
