from klinescan.core.errors import (
    BusError,
    ConfigurationError,
    MalformedFrameError,
    NegativeResponseError,
    ScanTimeout,
    UnsupportedOperationError,
    classify_exception,
)


def test_classify_known_error():
    assert classify_exception(BusError("bus")).error_class.value == "bus_fault"
    assert classify_exception(ScanTimeout("timeout")).error_class.value == "timeout"
    assert classify_exception(NegativeResponseError(0x22, 0x31)).error_class.value == "negative_response"
    assert classify_exception(MalformedFrameError("bad")).error_class.value == "malformed_frame"
    assert classify_exception(UnsupportedOperationError("no")).error_class.value == "unsupported"


def test_configuration_error_is_classified():
    assert classify_exception(ConfigurationError("bad config")).error_class.value == "configuration"
