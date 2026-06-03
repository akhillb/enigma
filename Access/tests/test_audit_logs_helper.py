import datetime

import pytest

from Access.audit_logs_helper import (
    InvalidAuditFilterError,
    get_audit_log_filters,
    get_audit_log_entries,
    _normalize_user_access,
    _normalize_group_access,
    _normalize_membership,
    gen_audit_logs_csv,
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


EMPTY_FILTERS = {
    "user": "",
    "access_tag": "",
    "status": "",
    "record_type": "",
    "date_from": None,
    "date_to": None,
}


def _entry(requested_on, record_type):
    return {"requested_on": requested_on, "record_type": record_type}


def test_get_audit_log_entries_merges_and_sorts_desc(mocker):
    mocker.patch(
        "Access.audit_logs_helper._user_access_entries",
        return_value=[_entry("2026-01-02 10:00:00", "User Access")],
    )
    mocker.patch(
        "Access.audit_logs_helper._group_access_entries",
        return_value=[_entry("2026-01-03 10:00:00", "Group Access")],
    )
    mocker.patch(
        "Access.audit_logs_helper._membership_entries",
        return_value=[_entry("2026-01-01 10:00:00", "Membership")],
    )
    entries = get_audit_log_entries(dict(EMPTY_FILTERS))
    assert [e["record_type"] for e in entries] == [
        "Group Access",
        "User Access",
        "Membership",
    ]


def test_get_audit_log_entries_record_type_restricts_models(mocker):
    user_mock = mocker.patch(
        "Access.audit_logs_helper._user_access_entries", return_value=[]
    )
    group_mock = mocker.patch(
        "Access.audit_logs_helper._group_access_entries", return_value=[]
    )
    membership_mock = mocker.patch(
        "Access.audit_logs_helper._membership_entries",
        return_value=[_entry("2026-01-01 10:00:00", "Membership")],
    )
    filters = dict(EMPTY_FILTERS, record_type="membership")
    entries = get_audit_log_entries(filters)
    assert len(entries) == 1
    membership_mock.assert_called_once_with(filters)
    user_mock.assert_not_called()
    group_mock.assert_not_called()


def test_get_audit_log_entries_access_tag_excludes_membership(mocker):
    mocker.patch(
        "Access.audit_logs_helper._user_access_entries", return_value=[]
    )
    mocker.patch(
        "Access.audit_logs_helper._group_access_entries", return_value=[]
    )
    membership_mock = mocker.patch(
        "Access.audit_logs_helper._membership_entries", return_value=[]
    )
    filters = dict(EMPTY_FILTERS, access_tag="github")
    assert get_audit_log_entries(filters) == []
    membership_mock.assert_not_called()


def _user_access_mapping_mock(mocker):
    mapping = mocker.MagicMock()
    mapping.access.access_tag = "github_access"
    mapping.access.access_label = {"repo": "enigma", "keySecret": "s3cret"}
    mapping.user_identity.user.email = "alice@example.com"
    mapping.status = "Approved"
    mapping.requested_on = datetime.datetime(2026, 1, 2, 10, 0, 0)
    mapping.updated_on = datetime.datetime(2026, 1, 3, 11, 0, 0)
    mapping.approver_1.user.username = "boss1"
    mapping.approver_2 = None
    mapping.revoker = None
    mapping.request_reason = "need repo access"
    mapping.decline_reason = None
    return mapping


def test_normalize_user_access_happy_path(mocker):
    entry = _normalize_user_access(_user_access_mapping_mock(mocker))
    assert entry["record_type"] == "User Access"
    assert entry["user"] == "alice@example.com"
    assert entry["access"] == "github_access (repo-enigma)"
    assert "keySecret" not in entry["access"]
    assert entry["status"] == "Approved"
    assert entry["requested_on"] == "2026-01-02 10:00:00"
    assert entry["updated_on"] == "2026-01-03 11:00:00"
    assert entry["actors"] == "boss1"
    assert entry["reason"] == "need repo access"


def test_normalize_user_access_null_fields(mocker):
    mapping = _user_access_mapping_mock(mocker)
    mapping.user_identity = None
    mapping.approver_1 = None
    mapping.access.access_label = {}
    mapping.decline_reason = "duplicate request"
    entry = _normalize_user_access(mapping)
    assert entry["user"] == ""
    assert entry["access"] == "github_access"
    assert entry["actors"] == ""
    assert entry["reason"] == "duplicate request"


def test_normalize_group_access(mocker):
    mapping = mocker.MagicMock()
    mapping.group.name = "devs"
    mapping.access.access_tag = "aws_access"
    mapping.requested_by.email = "bob@example.com"
    mapping.status = "Pending"
    mapping.requested_on = datetime.datetime(2026, 2, 1, 9, 0, 0)
    mapping.updated_on = datetime.datetime(2026, 2, 1, 9, 0, 0)
    mapping.approver_1 = None
    mapping.approver_2 = None
    mapping.revoker.user.username = "ops1"
    mapping.request_reason = "team onboarding"
    mapping.decline_reason = None
    entry = _normalize_group_access(mapping)
    assert entry["record_type"] == "Group Access"
    assert entry["user"] == "bob@example.com"
    assert entry["access"] == "devs -> aws_access"
    assert entry["actors"] == "ops1"
    assert entry["reason"] == "team onboarding"


def test_normalize_membership(mocker):
    membership = mocker.MagicMock()
    membership.user.email = "carol@example.com"
    membership.group.name = "devs"
    membership.status = "Declined"
    membership.requested_on = datetime.datetime(2026, 3, 1, 8, 0, 0)
    membership.updated_on = datetime.datetime(2026, 3, 2, 8, 0, 0)
    membership.approver.user.username = "owner1"
    membership.reason = "wants in"
    membership.decline_reason = "not on team"
    entry = _normalize_membership(membership)
    assert entry["record_type"] == "Membership"
    assert entry["user"] == "carol@example.com"
    assert entry["access"] == "devs"
    assert entry["actors"] == "owner1"
    assert entry["reason"] == "not on team"


def test_normalize_group_access_null_requested_by(mocker):
    mapping = mocker.MagicMock()
    mapping.group.name = "devs"
    mapping.access.access_tag = "aws_access"
    mapping.requested_by = None
    mapping.status = "Pending"
    mapping.requested_on = datetime.datetime(2026, 2, 1, 9, 0, 0)
    mapping.updated_on = datetime.datetime(2026, 2, 1, 9, 0, 0)
    mapping.approver_1 = None
    mapping.approver_2 = None
    mapping.revoker = None
    mapping.request_reason = "team onboarding"
    mapping.decline_reason = None
    entry = _normalize_group_access(mapping)
    assert entry["user"] == ""
    assert entry["actors"] == ""


SAMPLE_ENTRY = {
    "record_type": "User Access",
    "user": "alice@example.com",
    "access": "github_access (repo-enigma)",
    "status": "Approved",
    "requested_on": "2026-01-02 10:00:00",
    "updated_on": "2026-01-03 11:00:00",
    "actors": "boss1",
    "reason": "need repo access",
}

CSV_HEADER = "RecordType,User,Access,Status,RequestedOn,UpdatedOn,Actors,Reason"


def test_gen_audit_logs_csv_happy_path():
    response = gen_audit_logs_csv([dict(SAMPLE_ENTRY)])
    assert response["Content-Type"] == "text/csv"
    assert 'attachment; filename="AuditLogs-' in response["Content-Disposition"]
    rows = response.content.decode().strip().split("\r\n")
    assert rows[0] == CSV_HEADER
    assert rows[1] == (
        "User Access,alice@example.com,github_access (repo-enigma),"
        "Approved,2026-01-02 10:00:00,2026-01-03 11:00:00,boss1,need repo access"
    )


def test_gen_audit_logs_csv_empty_list_has_header_only():
    response = gen_audit_logs_csv([])
    rows = response.content.decode().strip().split("\r\n")
    assert rows == [CSV_HEADER]
