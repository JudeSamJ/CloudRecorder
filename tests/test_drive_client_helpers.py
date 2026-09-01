"""Only the pure helper functions in drive_client.py — DriveClient itself needs
a real OAuth token and live network, which this sandbox has neither of."""

from pipeline import drive_client


def test_escape_handles_single_quote():
    assert drive_client._escape("O'Brien") == "O\\'Brien"


def test_escape_handles_backslash():
    assert drive_client._escape("a\\b") == "a\\\\b"


def test_escape_handles_both():
    assert drive_client._escape("back\\slash's here") == "back\\\\slash\\'s here"


def test_escape_leaves_plain_string_untouched():
    assert drive_client._escape("YouTube_003") == "YouTube_003"


class _FakeErrorDetail(dict):
    pass


class _FakeHttpError(Exception):
    def __init__(self, error_details):
        self.error_details = error_details


def test_extract_reason_returns_first_reason():
    exc = _FakeHttpError([{"reason": "rateLimitExceeded"}])
    assert drive_client._extract_reason(exc) == "rateLimitExceeded"


def test_extract_reason_returns_none_when_no_details():
    exc = _FakeHttpError([])
    assert drive_client._extract_reason(exc) is None


def test_extract_reason_returns_none_when_details_missing_attribute():
    class NoDetails(Exception):
        pass

    assert drive_client._extract_reason(NoDetails()) is None


def test_extract_reason_returns_none_when_not_a_list():
    exc = _FakeHttpError("not a list")
    assert drive_client._extract_reason(exc) is None
