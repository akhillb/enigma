# Audit Logs Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin/ops-only Audit Logs page: unified audit trail over `UserAccessMapping`, `GroupAccessMapping`, `MembershipV2` with five filters, submit-triggered AJAX results, server-side pagination (10/page), and CSV export of the full filtered set.

**Architecture:** New focused helper module `Access/audit_logs_helper.py` (follows the repo's `*_helper.py` convention — note: spec said `views_helper.py`; a dedicated module keeps the file focused and testable). One view `audit_logs` in `Access/views.py` with three response modes (`ui` render / `json` / `csv`), mirroring `all_user_access_list`. Template `templates/EnigmaOps/auditLogs.html` follows the AJAX pattern of `allUserAccessList.html` but builds DOM via jQuery `.text()` (no string-concat HTML — avoids the XSS class CodeQL flagged before).

**Tech Stack:** Django 4 (function views, `Paginator`), Bootstrap 4.1.3 + jQuery templates, pytest + pytest-mock (mock-based unit tests, no DB fixtures — matches existing suite).

**Spec:** `docs/superpowers/specs/2026-06-03-audit-logs-page-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `Access/audit_logs_helper.py` | Create | Filter parsing, per-model query+normalize, merge/sort, CSV generation |
| `Access/tests/test_audit_logs_helper.py` | Create | Unit tests for the helper module |
| `Access/views.py` | Modify (append) | `audit_logs` view (3 response modes) |
| `Access/tests/test_audit_logs_view.py` | Create | Unit tests for the view (mocked helpers) |
| `EnigmaAutomation/urls.py` | Modify | Route `^access/auditLogs$` → `auditLogs` |
| `templates/EnigmaOps/auditLogs.html` | Create | Filter form, AJAX table, pagination, CSV button |
| `templates/global_layout.html` | Modify | Sidebar nav link (MANAGEMENT section) |

Conventions used throughout:
- Run tests with: `.venv/bin/python -m pytest <path> -v` from repo root (`pytest.ini` sets `DJANGO_SETTINGS_MODULE`).
- All normalized entries share one dict shape: `record_type`, `user`, `access`, `status`, `requested_on`, `updated_on`, `actors`, `reason`. Timestamps are `str(dt)[:19]` strings — ISO-like, so lexicographic sort == chronological sort.

---

### Task 1: Filter parsing — `get_audit_log_filters`

**Files:**
- Create: `Access/audit_logs_helper.py`
- Test: `Access/tests/test_audit_logs_helper.py`

- [ ] **Step 1: Write the failing tests**

Create `Access/tests/test_audit_logs_helper.py`:

```python
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
    ],
)
def test_get_audit_log_filters_invalid_input(mocker, params, message_fragment):
    with pytest.raises(InvalidAuditFilterError) as excinfo:
        get_audit_log_filters(_request_with(mocker, params))
    assert message_fragment in str(excinfo.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'Access.audit_logs_helper'`

- [ ] **Step 3: Write minimal implementation**

Create `Access/audit_logs_helper.py`:

```python
import csv
import datetime

from django.db.models import Q
from django.http import HttpResponse

from Access.models import GroupAccessMapping, MembershipV2, UserAccessMapping

RECORD_TYPE_USER_ACCESS = "userAccess"
RECORD_TYPE_GROUP_ACCESS = "groupAccess"
RECORD_TYPE_MEMBERSHIP = "membership"

RECORD_TYPE_CHOICES = (
    (RECORD_TYPE_USER_ACCESS, "User Access"),
    (RECORD_TYPE_GROUP_ACCESS, "Group Access"),
    (RECORD_TYPE_MEMBERSHIP, "Membership"),
)

ALL_STATUSES = sorted(
    {choice[0] for choice in UserAccessMapping.STATUS_CHOICES}
    | {choice[0] for choice in GroupAccessMapping.STATUS_CHOICES}
    | {choice[0] for choice in MembershipV2.STATUS}
)


class InvalidAuditFilterError(Exception):
    """Raised when audit log filter input is malformed."""


def _parse_date(value, param_name):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise InvalidAuditFilterError(
            "Invalid date for '%s'. Expected YYYY-MM-DD." % param_name
        )


def get_audit_log_filters(request):
    record_type = request.GET.get("recordType", "").strip()
    valid_record_types = {choice[0] for choice in RECORD_TYPE_CHOICES} | {""}
    if record_type not in valid_record_types:
        raise InvalidAuditFilterError("Invalid recordType '%s'." % record_type)

    date_from = _parse_date(request.GET.get("dateFrom", "").strip(), "dateFrom")
    date_to = _parse_date(request.GET.get("dateTo", "").strip(), "dateTo")
    if date_from and date_to and date_from > date_to:
        raise InvalidAuditFilterError("dateFrom must not be after dateTo.")

    return {
        "user": request.GET.get("user", "").strip(),
        "access_tag": request.GET.get("accessTag", "").strip(),
        "status": request.GET.get("status", "").strip(),
        "record_type": record_type,
        "date_from": date_from,
        "date_to": date_to,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add Access/audit_logs_helper.py Access/tests/test_audit_logs_helper.py
git commit -m "feat: add audit log filter parsing helper"
```

---

### Task 2: Normalization + merged entries — `get_audit_log_entries`

**Files:**
- Modify: `Access/audit_logs_helper.py`
- Test: `Access/tests/test_audit_logs_helper.py`

- [ ] **Step 1: Write the failing tests**

Append to `Access/tests/test_audit_logs_helper.py` (extend the existing import to include the new names):

```python
from Access.audit_logs_helper import (  # noqa: F811 — replaces earlier import
    InvalidAuditFilterError,
    get_audit_log_filters,
    get_audit_log_entries,
    _normalize_user_access,
    _normalize_group_access,
    _normalize_membership,
)

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
    membership_mock.assert_called_once()
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
```

Also add `import datetime` at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: new tests FAIL — `ImportError: cannot import name 'get_audit_log_entries'`

- [ ] **Step 3: Write the implementation**

Append to `Access/audit_logs_helper.py`:

```python
def _format_timestamp(value):
    return str(value)[:19] if value else ""


def _format_label(access_label):
    if not access_label:
        return ""
    return ", ".join(
        key + "-" + str(val).strip("[]")
        for key, val in access_label.items()
        if key != "keySecret"
    )


def _username(access_user):
    return access_user.user.username if access_user else ""


def _format_actors(*access_users):
    return ", ".join(filter(None, (_username(u) for u in access_users)))


def _normalize_user_access(mapping):
    label = _format_label(mapping.access.access_label)
    access = mapping.access.access_tag + (" (" + label + ")" if label else "")
    return {
        "record_type": "User Access",
        "user": mapping.user_identity.user.email if mapping.user_identity else "",
        "access": access,
        "status": mapping.status,
        "requested_on": _format_timestamp(mapping.requested_on),
        "updated_on": _format_timestamp(mapping.updated_on),
        "actors": _format_actors(
            mapping.approver_1, mapping.approver_2, mapping.revoker
        ),
        "reason": mapping.decline_reason or mapping.request_reason or "",
    }


def _normalize_group_access(mapping):
    return {
        "record_type": "Group Access",
        "user": mapping.requested_by.email if mapping.requested_by else "",
        "access": mapping.group.name + " -> " + mapping.access.access_tag,
        "status": mapping.status,
        "requested_on": _format_timestamp(mapping.requested_on),
        "updated_on": _format_timestamp(mapping.updated_on),
        "actors": _format_actors(
            mapping.approver_1, mapping.approver_2, mapping.revoker
        ),
        "reason": mapping.decline_reason or mapping.request_reason or "",
    }


def _normalize_membership(membership):
    return {
        "record_type": "Membership",
        "user": membership.user.email if membership.user else "",
        "access": membership.group.name,
        "status": membership.status,
        "requested_on": _format_timestamp(membership.requested_on),
        "updated_on": _format_timestamp(membership.updated_on),
        "actors": _format_actors(membership.approver),
        "reason": membership.decline_reason or membership.reason or "",
    }


def _apply_date_and_status_filters(queryset, filters):
    if filters["status"]:
        queryset = queryset.filter(status=filters["status"])
    if filters["date_from"]:
        queryset = queryset.filter(requested_on__date__gte=filters["date_from"])
    if filters["date_to"]:
        queryset = queryset.filter(requested_on__date__lte=filters["date_to"])
    return queryset


def _user_access_entries(filters):
    queryset = UserAccessMapping.objects.select_related(
        "user_identity__user__user",
        "access",
        "approver_1__user",
        "approver_2__user",
        "revoker__user",
    )
    if filters["user"]:
        queryset = queryset.filter(
            Q(user_identity__user__user__username__icontains=filters["user"])
            | Q(user_identity__user__email__icontains=filters["user"])
        )
    if filters["access_tag"]:
        queryset = queryset.filter(
            access__access_tag__icontains=filters["access_tag"]
        )
    queryset = _apply_date_and_status_filters(queryset, filters)
    return [_normalize_user_access(mapping) for mapping in queryset]


def _group_access_entries(filters):
    queryset = GroupAccessMapping.objects.select_related(
        "group",
        "access",
        "requested_by",
        "approver_1__user",
        "approver_2__user",
        "revoker__user",
    )
    if filters["user"]:
        queryset = queryset.filter(
            Q(requested_by__user__username__icontains=filters["user"])
            | Q(requested_by__email__icontains=filters["user"])
        )
    if filters["access_tag"]:
        queryset = queryset.filter(
            access__access_tag__icontains=filters["access_tag"]
        )
    queryset = _apply_date_and_status_filters(queryset, filters)
    return [_normalize_group_access(mapping) for mapping in queryset]


def _membership_entries(filters):
    queryset = MembershipV2.objects.select_related(
        "user", "group", "approver__user"
    )
    if filters["user"]:
        queryset = queryset.filter(
            Q(user__user__username__icontains=filters["user"])
            | Q(user__email__icontains=filters["user"])
        )
    queryset = _apply_date_and_status_filters(queryset, filters)
    return [_normalize_membership(membership) for membership in queryset]


def get_audit_log_entries(filters):
    record_type = filters["record_type"]
    entries = []
    if record_type in ("", RECORD_TYPE_USER_ACCESS):
        entries.extend(_user_access_entries(filters))
    if record_type in ("", RECORD_TYPE_GROUP_ACCESS):
        entries.extend(_group_access_entries(filters))
    # Memberships carry no access tag, so an accessTag filter excludes them.
    if record_type in ("", RECORD_TYPE_MEMBERSHIP) and not filters["access_tag"]:
        entries.extend(_membership_entries(filters))
    # Timestamps are ISO-like strings: lexicographic sort == chronological.
    entries.sort(key=lambda entry: entry["requested_on"], reverse=True)
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add Access/audit_logs_helper.py Access/tests/test_audit_logs_helper.py
git commit -m "feat: add unified audit log entry builder"
```

---

### Task 3: CSV export — `gen_audit_logs_csv`

**Files:**
- Modify: `Access/audit_logs_helper.py`
- Test: `Access/tests/test_audit_logs_helper.py`

- [ ] **Step 1: Write the failing tests**

Append to `Access/tests/test_audit_logs_helper.py` (add `gen_audit_logs_csv` to the imports from `Access.audit_logs_helper`):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: new tests FAIL — `ImportError: cannot import name 'gen_audit_logs_csv'`

- [ ] **Step 3: Write the implementation**

Append to `Access/audit_logs_helper.py`:

```python
AUDIT_CSV_COLUMNS = (
    "record_type",
    "user",
    "access",
    "status",
    "requested_on",
    "updated_on",
    "actors",
    "reason",
)


def gen_audit_logs_csv(data_list):
    response = HttpResponse(content_type="text/csv")
    filename = (
        "AuditLogs-"
        + datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
        + ".csv"
    )
    response["Content-Disposition"] = 'attachment; filename="' + filename + '"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "RecordType",
            "User",
            "Access",
            "Status",
            "RequestedOn",
            "UpdatedOn",
            "Actors",
            "Reason",
        ]
    )
    for entry in data_list:
        writer.writerow([entry[column] for column in AUDIT_CSV_COLUMNS])
    return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add Access/audit_logs_helper.py Access/tests/test_audit_logs_helper.py
git commit -m "feat: add audit logs CSV export helper"
```

---

### Task 4: View + URL

**Files:**
- Modify: `Access/views.py` (append view; extend imports)
- Modify: `EnigmaAutomation/urls.py:20-46` (import) and `urlpatterns`
- Test: `Access/tests/test_audit_logs_view.py`

- [ ] **Step 1: Write the failing tests**

Create `Access/tests/test_audit_logs_view.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_view.py -v`
Expected: FAIL — `ImportError: cannot import name 'audit_logs' from 'Access.views'`

- [ ] **Step 3: Write the view**

In `Access/views.py`:

1. Ensure these imports exist near the top (most already do — add only what's missing):

```python
from Access import audit_logs_helper
```

(`Paginator`, `JsonResponse`, `render`, `login_required`, and `user_admin_or_ops` are already imported for existing views — verify with `grep -n "user_admin_or_ops\|Paginator\|JsonResponse" Access/views.py` and add any that are missing using the same import style as the surrounding lines.)

2. Append the view at the end of the file:

```python
@login_required
@user_admin_or_ops
def audit_logs(request):
    """Unified audit trail over user access, group access and membership
    requests. Supports ui (default), json and csv response types.
    """
    response_type = request.GET.get("responseType", "ui")

    if response_type == "ui":
        return render(
            request,
            "EnigmaOps/auditLogs.html",
            {
                "statuses": audit_logs_helper.ALL_STATUSES,
                "record_types": audit_logs_helper.RECORD_TYPE_CHOICES,
            },
        )

    try:
        filters = audit_logs_helper.get_audit_log_filters(request)
    except audit_logs_helper.InvalidAuditFilterError as ex:
        return JsonResponse({"error": str(ex)}, status=400)

    entries = audit_logs_helper.get_audit_log_entries(filters)

    if response_type == "csv":
        return audit_logs_helper.gen_audit_logs_csv(data_list=entries)

    try:
        page = int(request.GET.get("page", 1))
    except ValueError:
        page = 1
    paginator = Paginator(entries, 10)
    page = max(1, min(page, paginator.num_pages))
    page_obj = paginator.page(page)

    return JsonResponse(
        {
            "dataList": list(page_obj),
            "current_page": page,
            "last_page": paginator.num_pages,
            "total_count": paginator.count,
        }
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest Access/tests/test_audit_logs_view.py -v`
Expected: all PASS

- [ ] **Step 5: Add the URL route**

In `EnigmaAutomation/urls.py`, add `audit_logs` to the existing `from Access.views import (...)` block (alphabetical position is not enforced — append after `resolve_bulk,`):

```python
    audit_logs,
```

Add to `urlpatterns` (after the `allUsersList` entry, `EnigmaAutomation/urls.py:64`):

```python
    re_path(r"^access/auditLogs$", audit_logs, name="auditLogs"),
```

- [ ] **Step 6: Verify Django config**

Run: `.venv/bin/python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/python -m pytest Access/ bootprocess/ -v`
Expected: all PASS (no regressions)

- [ ] **Step 8: Commit**

```bash
git add Access/views.py EnigmaAutomation/urls.py Access/tests/test_audit_logs_view.py
git commit -m "feat: add audit logs view and route"
```

---

### Task 5: Template + sidebar nav

**Files:**
- Create: `templates/EnigmaOps/auditLogs.html`
- Modify: `templates/global_layout.html:130-136` (MANAGEMENT nav section)

- [ ] **Step 1: Create the template**

Create `templates/EnigmaOps/auditLogs.html`:

```html
{% extends 'global_layout.html' %}
{% load static %}

{% block content_body %}
<style>
  .audit-filter-label {
    color: white;
    margin-bottom: 2px;
    display: block;
  }
  .audit-filter-field {
    height: 32px;
    width: 100%;
  }
</style>

<div class="page-header" style="margin-bottom:0px; padding: 10px;">
  <div class="container-fluid">
    <h2 class="h5 no-margin-bottom">Audit Logs
      <button id="export-csv-button" class="btn btn-primary" style="float: right;"
              onclick="exportCSV()" disabled>Export current selection as CSV</button>
    </h2>
  </div>
</div>

<div class="container-fluid" style="padding: 10px;">
  <form id="audit-filter-form" onsubmit="return submitFilters();">
    <div class="form-row">
      <div class="col-md-2">
        <label class="audit-filter-label" for="user">User</label>
        <input class="audit-filter-field" id="user" type="text" placeholder="Username or email">
      </div>
      <div class="col-md-2">
        <label class="audit-filter-label" for="accessTag">Access Tag</label>
        <input class="audit-filter-field" id="accessTag" type="text" placeholder="e.g. github">
      </div>
      <div class="col-md-2">
        <label class="audit-filter-label" for="status">Status</label>
        <select class="audit-filter-field" id="status">
          <option value="">All</option>
          {% for status in statuses %}
          <option value="{{ status }}">{{ status }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="col-md-2">
        <label class="audit-filter-label" for="recordType">Record Type</label>
        <select class="audit-filter-field" id="recordType">
          <option value="">All</option>
          {% for value, label in record_types %}
          <option value="{{ value }}">{{ label }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="col-md-2">
        <label class="audit-filter-label" for="dateFrom">From</label>
        <input class="audit-filter-field" id="dateFrom" type="date">
      </div>
      <div class="col-md-2">
        <label class="audit-filter-label" for="dateTo">To</label>
        <input class="audit-filter-field" id="dateTo" type="date">
      </div>
    </div>
    <div class="form-row" style="margin-top: 10px;">
      <div class="col-md-2">
        <button type="submit" class="btn btn-primary">Submit</button>
      </div>
    </div>
  </form>
  <div id="audit-error" class="alert alert-danger" style="display: none; margin-top: 10px;"></div>
</div>

<div class="wrapper" style="overflow-x: auto; width: 100%;">
  <table class="table table-bordered table-striped">
    <thead class="thead-dark">
      <tr style="text-align: center;">
        <th>Record Type</th>
        <th>User</th>
        <th>Access</th>
        <th>Status</th>
        <th>Requested On</th>
        <th>Last Updated</th>
        <th>Actors</th>
        <th>Reason</th>
      </tr>
    </thead>
    <tfoot>
      <tr>
        <th colspan="8" style="padding:1%; text-align: center;">
          <button onclick="decreasePagenum()" type="button" class="btn btn-primary"
                  style="padding: 0px 30px;">&laquo; Prev</button>
          <span id="pagedisplay">Submit filters to view audit logs</span>
          <button onclick="increasePagenum()" type="button" class="btn btn-primary"
                  style="padding: 0px 30px;">Next &raquo;</button>
        </th>
      </tr>
    </tfoot>
    <tbody id="tableData">
      <tr>
        <td colspan="8" class="text-center">No results yet. Set filters and press Submit.</td>
      </tr>
    </tbody>
  </table>
</div>

<script>
var page_number = 1;
var last_page = 1;
var has_searched = false;

var FILTER_FIELD_IDS = ["user", "accessTag", "status", "recordType", "dateFrom", "dateTo"];
var ENTRY_COLUMNS = ["record_type", "user", "access", "status",
                     "requested_on", "updated_on", "actors", "reason"];

function buildUrlFromParams(responseType) {
  var params = [];
  for (var i = 0; i < FILTER_FIELD_IDS.length; i++) {
    var value = document.getElementById(FILTER_FIELD_IDS[i]).value;
    if (value && value.trim().length) {
      params.push(FILTER_FIELD_IDS[i] + "=" + encodeURIComponent(value.trim()));
    }
  }
  params.push("responseType=" + responseType);
  params.push("page=" + String(page_number));
  return "auditLogs?" + params.join("&");
}

function renderRows(dataList) {
  var tbody = $("#tableData");
  tbody.empty();
  if (!dataList.length) {
    tbody.append(
      $("<tr>").append(
        $("<td>", {colspan: 8, "class": "text-center"})
          .text("No audit records match your filters.")
      )
    );
    return;
  }
  for (var i = 0; i < dataList.length; i++) {
    var row = $("<tr>");
    for (var j = 0; j < ENTRY_COLUMNS.length; j++) {
      // .text() treats values as text, not HTML — keeps user data XSS-safe.
      row.append($("<td>").text(dataList[i][ENTRY_COLUMNS[j]] || ""));
    }
    tbody.append(row);
  }
}

function updateTable() {
  $("#audit-error").hide();
  $.ajax({
    url: buildUrlFromParams("json"),
    success: function(result) {
      renderRows(result["dataList"]);
      page_number = result["current_page"];
      last_page = result["last_page"];
      has_searched = true;
      document.getElementById("export-csv-button").disabled = false;
      $("#pagedisplay").text(
        "Page " + String(result["current_page"]) + " of "
        + String(result["last_page"]) + " (" + String(result["total_count"]) + " records)"
      );
    },
    error: function(xhr) {
      var message = "Error occurred while fetching audit logs.";
      if (xhr.responseJSON && xhr.responseJSON.error) {
        message = xhr.responseJSON.error;
      }
      $("#audit-error").text(message).show();
    }
  });
}

function submitFilters() {
  page_number = 1;
  updateTable();
  return false;
}

function decreasePagenum() {
  if (!has_searched || page_number <= 1) {
    return;
  }
  page_number -= 1;
  updateTable();
}

function increasePagenum() {
  if (!has_searched || page_number >= last_page) {
    return;
  }
  page_number += 1;
  updateTable();
}

function exportCSV() {
  if (!has_searched) {
    return;
  }
  location.replace(buildUrlFromParams("csv"));
}
</script>
{% endblock %}
```

- [ ] **Step 2: Add the sidebar nav link**

In `templates/global_layout.html`, inside the MANAGEMENT `<ul>` (after the `allUsersList` line, currently line 134), add:

```html
        {% if request.user.is_superuser or is_ops %}
        <li><a href={% url 'auditLogs' %}><i class="fa fa-history nav-icon"></i>Audit Logs</a></li>
        {% endif %}
```

(The MANAGEMENT section is also visible to managers; the inner `{% if %}` hides the link from managers who would hit `PermissionDenied` on the page itself.)

- [ ] **Step 3: Verify template syntax**

Run: `.venv/bin/python manage.py check && .venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'EnigmaAutomation.settings')
django.setup()
from django.template.loader import get_template
get_template('EnigmaOps/auditLogs.html')
print('template OK')
"`
Expected: `System check identified no issues` and `template OK`

- [ ] **Step 4: Commit**

```bash
git add templates/EnigmaOps/auditLogs.html templates/global_layout.html
git commit -m "feat: add audit logs page template and nav link"
```

---

### Task 6: Final verification

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest Access/ bootprocess/ -v`
Expected: all PASS

- [ ] **Step 2: Lint changed files**

Run: `.venv/bin/python -m pylama Access/audit_logs_helper.py Access/tests/test_audit_logs_helper.py Access/tests/test_audit_logs_view.py`
Expected: no new errors (pylama config is in `pylama.ini`). Fix anything reported.

- [ ] **Step 3: Manual smoke test (optional but recommended)**

If a dev environment is available (`docker-compose up` or `make`; `scripts/seed_dev_data.py` can seed data):
1. Log in as an ops/admin user → "Audit Logs" appears in the MANAGEMENT sidebar.
2. Open `/access/auditLogs` → filters visible, table shows "No results yet".
3. Submit with no filters → rows render, pager shows counts.
4. Apply each filter; verify narrowing. Enter a bad date via URL (`?responseType=json&dateFrom=bad`) → inline error.
5. Export CSV → file downloads with filtered rows.
6. Log in as non-ops → no nav link; direct URL → 403.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: address lint and review findings for audit logs"
```

(Skip if nothing changed.)
