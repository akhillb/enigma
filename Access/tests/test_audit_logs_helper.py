import pytest

from Access.audit_logs_helper import (
    InvalidAuditFilterError,
    get_audit_log_filters,
)


def _request_with(mocker, params):
    request = mocker.MagicMock()
    request.GET = params
    return request


def test_get_audit_log_filters_happy_path(mocker):
    request = _request_with(
        mocker,
        {
            "user": " alice ",
            "accessTag": "github",
            "status": "Approved",
            "recordType": "userAccess",
            "dateFrom": "2026-01-01",
            "dateTo": "2026-01-31",
        },
    )
    filters = get_audit_log_filters(request)
    assert filters["user"] == "alice"
    assert filters["access_tag"] == "github"
    assert filters["status"] == "Approved"
    assert filters["record_type"] == "userAccess"
    assert str(filters["date_from"]) == "2026-01-01"
    assert str(filters["date_to"]) == "2026-01-31"


def test_get_audit_log_filters_empty_params(mocker):
    filters = get_audit_log_filters(_request_with(mocker, {}))
    assert filters == {
        "user": "",
        "access_tag": "",
        "status": "",
        "record_type": "",
        "date_from": None,
        "date_to": None,
    }


@pytest.mark.parametrize(
    "params, message_fragment",
    [
        ({"dateFrom": "01-31-2026"}, "dateFrom"),
        ({"dateTo": "not-a-date"}, "dateTo"),
        ({"recordType": "bogus"}, "recordType"),
        (
            {"dateFrom": "2026-02-01", "dateTo": "2026-01-01"},
            "must not be after",
        ),
        ({"status": "HACKED"}, "Invalid status"),
    ],
)
def test_get_audit_log_filters_invalid_input(mocker, params, message_fragment):
    with pytest.raises(InvalidAuditFilterError) as excinfo:
        get_audit_log_filters(_request_with(mocker, params))
    assert message_fragment in str(excinfo.value)


def test_get_audit_log_filters_whitespace_only_dates(mocker):
    filters = get_audit_log_filters(
        _request_with(mocker, {"dateFrom": "   ", "dateTo": " "})
    )
    assert filters["date_from"] is None
    assert filters["date_to"] is None
