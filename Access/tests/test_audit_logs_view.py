import json

import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse

from Access.audit_logs_helper import InvalidAuditFilterError
from Access.views import audit_logs


def _ops_request(mocker, get_params):
    request = mocker.MagicMock()
    request.GET = get_params
    request.user.user.isAdminOrOps.return_value = True
    return request


def test_audit_logs_denies_non_ops_user(mocker):
    request = mocker.MagicMock()
    request.GET = {}
    request.user.user.isAdminOrOps.return_value = False
    with pytest.raises(PermissionDenied):
        audit_logs(request)


def test_audit_logs_ui_renders_template(mocker):
    render_mock = mocker.patch(
        "Access.views.render", return_value=HttpResponse("ok")
    )
    request = _ops_request(mocker, {})
    response = audit_logs(request)
    assert response.status_code == 200
    template_name = render_mock.call_args[0][1]
    assert template_name == "EnigmaOps/auditLogs.html"
    context = render_mock.call_args[0][2]
    assert "statuses" in context
    assert "record_types" in context


def test_audit_logs_json_paginates_and_clamps_page(mocker):
    entries = [{"record_type": "User Access", "index": i} for i in range(25)]
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_filters", return_value={}
    )
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_entries",
        return_value=entries,
    )
    request = _ops_request(mocker, {"responseType": "json", "page": "99"})
    response = audit_logs(request)
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["current_page"] == 3
    assert payload["last_page"] == 3
    assert payload["total_count"] == 25
    assert len(payload["dataList"]) == 5


def test_audit_logs_json_empty_results(mocker):
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_filters", return_value={}
    )
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_entries", return_value=[]
    )
    request = _ops_request(mocker, {"responseType": "json"})
    payload = json.loads(audit_logs(request).content)
    assert payload["dataList"] == []
    assert payload["current_page"] == 1
    assert payload["last_page"] == 1
    assert payload["total_count"] == 0


def test_audit_logs_invalid_filter_returns_400(mocker):
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_filters",
        side_effect=InvalidAuditFilterError("Invalid date for 'dateFrom'."),
    )
    request = _ops_request(mocker, {"responseType": "json", "dateFrom": "bad"})
    response = audit_logs(request)
    assert response.status_code == 400
    payload = json.loads(response.content)
    assert "dateFrom" in payload["error"]


def test_audit_logs_entry_fetch_failure_returns_500(mocker):
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_filters", return_value={}
    )
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_entries",
        side_effect=Exception("DB down"),
    )
    request = _ops_request(mocker, {"responseType": "json"})
    response = audit_logs(request)
    assert response.status_code == 500
    payload = json.loads(response.content)
    assert payload["error"] == "Failed to fetch audit log entries."


def test_audit_logs_unknown_response_type_returns_400(mocker):
    request = _ops_request(mocker, {"responseType": "xml"})
    response = audit_logs(request)
    assert response.status_code == 400
    payload = json.loads(response.content)
    assert "responseType" in payload["error"]


def test_audit_logs_csv_delegates_to_helper(mocker):
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_filters", return_value={}
    )
    mocker.patch(
        "Access.views.audit_logs_helper.get_audit_log_entries",
        return_value=[{"record_type": "Membership"}],
    )
    csv_response = HttpResponse(content_type="text/csv")
    csv_mock = mocker.patch(
        "Access.views.audit_logs_helper.gen_audit_logs_csv",
        return_value=csv_response,
    )
    request = _ops_request(mocker, {"responseType": "csv"})
    response = audit_logs(request)
    assert response is csv_response
    csv_mock.assert_called_once_with(data_list=[{"record_type": "Membership"}])
