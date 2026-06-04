import datetime
import types

import pytest
from django.core.exceptions import PermissionDenied
from django.http import QueryDict

from Access import views


def _request(query="", is_authenticated=True, is_admin_or_ops=True):
    user = types.SimpleNamespace(
        is_authenticated=is_authenticated,
        user=types.SimpleNamespace(isAdminOrOps=lambda: is_admin_or_ops),
    )
    request = types.SimpleNamespace(user=user, GET=QueryDict(query))
    return request


def _entry(ts):
    return {
        "timestamp": ts, "actor": "a@e.com", "action": "User Access",
        "status": "Approved", "resource": "aws", "approver": "", "reason": "",
        "source_type": "user_access",
    }


def test_audit_logs_admin_renders_with_paginated_context(mocker):
    entries = [_entry(datetime.datetime(2026, 1, i + 1)) for i in range(30)]
    mocker.patch.object(views.audit_helper, "build_audit_entries", return_value=entries)
    render = mocker.patch.object(views, "render", return_value="RENDERED")

    result = views.audit_logs(_request("page=2"))

    assert result == "RENDERED"
    context = render.call_args.args[2]
    assert context["current_page"] == 2
    assert context["last_page"] == 2  # 30 entries / PAGE_SIZE(25) = 2 pages
    assert len(context["entries"]) == 5
    assert "Approved" in context["statuses"]


def test_audit_logs_non_admin_raises_permission_denied(mocker):
    mocker.patch.object(views.audit_helper, "build_audit_entries", return_value=[])
    with pytest.raises(PermissionDenied):
        views.audit_logs(_request(is_admin_or_ops=False))


def test_audit_logs_csv_response(mocker):
    mocker.patch.object(views.audit_helper, "build_audit_entries",
                        return_value=[_entry(datetime.datetime(2026, 1, 1))])
    response = views.audit_logs(_request("responseType=csv"))
    assert response["Content-Type"] == "text/csv"


def test_audit_logs_out_of_range_page_clamps(mocker):
    mocker.patch.object(views.audit_helper, "build_audit_entries",
                        return_value=[_entry(datetime.datetime(2026, 1, 1))])
    render = mocker.patch.object(views, "render", return_value="RENDERED")
    views.audit_logs(_request("page=999"))
    assert render.call_args.args[2]["current_page"] == 1


def test_audit_logs_builder_error_renders_empty(mocker):
    mocker.patch.object(views.audit_helper, "build_audit_entries",
                        side_effect=Exception("boom"))
    render = mocker.patch.object(views, "render", return_value="RENDERED")
    views.audit_logs(_request())
    assert list(render.call_args.args[2]["entries"]) == []
