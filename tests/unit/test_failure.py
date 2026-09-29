import pytest

from gcreelmap.domain.failure import MAX_ATTEMPTS, ErrorCode, describe, is_transient

_TRANSIENT_TRUTH = {
    ErrorCode.DOWNLOAD_FAILED: True,
    ErrorCode.UNSUPPORTED: False,
    ErrorCode.DURATION_CAP: False,
    ErrorCode.EXTRACTION_FAILED: True,
    ErrorCode.NO_PLACES: False,
    ErrorCode.TIMEOUT: True,
    ErrorCode.INTERNAL: True,
    ErrorCode.DUPLICATE: False,
}


@pytest.mark.parametrize("code,expected", list(_TRANSIENT_TRUTH.items()))
def test_is_transient_truth_table(code: ErrorCode, expected: bool) -> None:
    assert is_transient(code) is expected


@pytest.mark.parametrize("code", list(ErrorCode))
def test_describe_is_non_empty_for_every_code(code: ErrorCode) -> None:
    assert describe(code)
    assert isinstance(describe(code), str)


def test_max_attempts() -> None:
    assert MAX_ATTEMPTS == 3
