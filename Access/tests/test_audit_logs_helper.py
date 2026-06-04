import datetime

from Access import audit_helper


def test_parse_filters_happy_path():
    params = {
        "dateFrom": "2026-01-01",
        "dateTo": "2026-02-01",
        "actor": "  alice@example.com ",
        "status": "Approved",
        "resource": "  aws-prod ",
    }
    result = audit_helper.parse_filters(params)
    assert result == {
        "date_from": datetime.date(2026, 1, 1),
        "date_to": datetime.date(2026, 2, 1),
        "actor": "alice@example.com",
        "status": "Approved",
        "resource": "aws-prod",
    }


def test_parse_filters_empty_params_yield_none():
    result = audit_helper.parse_filters({})
    assert result == {
        "date_from": None,
        "date_to": None,
        "actor": None,
        "status": None,
        "resource": None,
    }


def test_parse_filters_invalid_date_dropped():
    result = audit_helper.parse_filters({"dateFrom": "not-a-date", "dateTo": "13/2026"})
    assert result["date_from"] is None
    assert result["date_to"] is None


def test_parse_filters_unknown_status_dropped():
    result = audit_helper.parse_filters({"status": "Bogus"})
    assert result["status"] is None


def test_known_statuses_is_sorted_unique():
    assert audit_helper.KNOWN_STATUSES == sorted(set(audit_helper.KNOWN_STATUSES))
    assert "Approved" in audit_helper.KNOWN_STATUSES
    assert "GrantFailed" in audit_helper.KNOWN_STATUSES


import types


def _ns(**kwargs):
    """Tiny nested-attribute stub."""
    return types.SimpleNamespace(**kwargs)


def test_map_user_access_happy_path():
    ts = datetime.datetime(2026, 1, 5, 10, 0, 0)
    obj = _ns(
        updated_on=ts,
        user_identity=_ns(user=_ns(email="alice@example.com")),
        access=_ns(access_tag="aws-prod"),
        status="Approved",
        request_reason="need it",
        decline_reason=None,
        fail_reason=None,
        approver_1=_ns(email="boss@example.com"),
    )
    entry = audit_helper.map_user_access(obj)
    assert entry == {
        "timestamp": ts,
        "actor": "alice@example.com",
        "action": "User Access",
        "status": "Approved",
        "resource": "aws-prod",
        "reason": "need it",
        "approver": "boss@example.com",
        "source_type": "user_access",
    }


def test_map_user_access_null_relations_yield_empty_strings():
    ts = datetime.datetime(2026, 1, 5, 10, 0, 0)
    obj = _ns(
        updated_on=ts, user_identity=None, access=None, status="Pending",
        request_reason=None, decline_reason=None, fail_reason=None, approver_1=None,
    )
    entry = audit_helper.map_user_access(obj)
    assert entry["actor"] == ""
    assert entry["resource"] == ""
    assert entry["approver"] == ""
    assert entry["reason"] == ""


def test_map_membership_uses_decline_reason_when_present():
    ts = datetime.datetime(2026, 1, 6, 9, 0, 0)
    obj = _ns(
        updated_on=ts, user=_ns(email="bob@example.com"),
        group=_ns(name="dev-team"), status="Declined",
        reason="please", decline_reason="no", approver=_ns(email="lead@example.com"),
    )
    entry = audit_helper.map_membership(obj)
    assert entry["action"] == "Group Membership"
    assert entry["actor"] == "bob@example.com"
    assert entry["resource"] == "dev-team"
    assert entry["reason"] == "no"
    assert entry["source_type"] == "group_membership"


def test_map_group_lifecycle():
    ts = datetime.datetime(2026, 1, 7, 8, 0, 0)
    obj = _ns(
        updated_on=ts, requester=_ns(email="carol@example.com"),
        name="new-group", status="Approved", decline_reason=None,
        approver=_ns(email="lead@example.com"),
    )
    entry = audit_helper.map_group(obj)
    assert entry["action"] == "Group Lifecycle"
    assert entry["actor"] == "carol@example.com"
    assert entry["resource"] == "new-group"
    assert entry["source_type"] == "group_lifecycle"


def test_map_group_access():
    ts = datetime.datetime(2026, 1, 8, 7, 0, 0)
    obj = _ns(
        updated_on=ts, requested_by=_ns(email="dan@example.com"),
        group=_ns(name="ops"), access=_ns(access_tag="gcp-read"),
        status="Revoked", request_reason="audit", decline_reason=None,
        approver_1=_ns(email="lead@example.com"),
    )
    entry = audit_helper.map_group_access(obj)
    assert entry["action"] == "Group Access"
    assert entry["actor"] == "dan@example.com"
    assert entry["resource"] == "ops / gcp-read"
    assert entry["source_type"] == "group_access"


def _patch_all_sources(mocker, ua=None, mem=None, grp=None, ga=None):
    mocker.patch.object(audit_helper.UserAccessMapping.objects, "filter",
                        return_value=ua or [])
    mocker.patch.object(audit_helper.MembershipV2.objects, "filter",
                        return_value=mem or [])
    mocker.patch.object(audit_helper.GroupV2.objects, "filter",
                        return_value=grp or [])
    mocker.patch.object(audit_helper.GroupAccessMapping.objects, "filter",
                        return_value=ga or [])


def test_build_audit_entries_merges_and_sorts_desc(mocker):
    ua = _ns(updated_on=datetime.datetime(2026, 1, 1), user_identity=None,
             access=None, status="Approved", request_reason="", decline_reason=None,
             fail_reason=None, approver_1=None)
    mem = _ns(updated_on=datetime.datetime(2026, 3, 1), user=None, group=None,
              status="Approved", reason="", decline_reason=None, approver=None)
    _patch_all_sources(mocker, ua=[ua], mem=[mem])

    entries = audit_helper.build_audit_entries(audit_helper.parse_filters({}))

    assert [e["source_type"] for e in entries] == ["group_membership", "user_access"]
    assert entries[0]["timestamp"] > entries[1]["timestamp"]


def test_build_audit_entries_no_match_returns_empty(mocker):
    _patch_all_sources(mocker)
    assert audit_helper.build_audit_entries(audit_helper.parse_filters({})) == []


def test_build_audit_entries_pushes_filters_to_each_source(mocker):
    ua_filter = mocker.patch.object(audit_helper.UserAccessMapping.objects, "filter",
                                    return_value=[])
    mocker.patch.object(audit_helper.MembershipV2.objects, "filter", return_value=[])
    mocker.patch.object(audit_helper.GroupV2.objects, "filter", return_value=[])
    mocker.patch.object(audit_helper.GroupAccessMapping.objects, "filter",
                        return_value=[])

    filters = audit_helper.parse_filters(
        {"dateFrom": "2026-01-01", "actor": "alice", "status": "Approved",
         "resource": "aws"}
    )
    audit_helper.build_audit_entries(filters)

    ua_filter.assert_called_once_with(
        updated_on__date__gte=datetime.date(2026, 1, 1),
        user_identity__user__email__icontains="alice",
        status="Approved",
        access__access_tag__icontains="aws",
    )
