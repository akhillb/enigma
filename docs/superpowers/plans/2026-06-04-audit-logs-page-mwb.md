# Audit Logs Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, Admin/Ops-only Audit Logs page that aggregates access-lifecycle events from four existing models into one filterable, paginated table with CSV export.

**Architecture:** A new isolated helper module `Access/audit_helper.py` owns all logic: it parses GET filters, pushes them down to per-model ORM queries, normalizes each row to a common dict, merges and sorts them, and renders CSV. A thin `audit_logs` view in `Access/views.py` wires GET params → helper → `Paginator` → template. No schema changes, no new writes.

**Tech Stack:** Django 4.2, Python `csv`, Django `Paginator`, Bootstrap 4 + jQuery templates, `pytest` + `pytest-mock` (no DB fixtures — pure/mocked unit tests, matching repo convention).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `Access/audit_helper.py` | **new** — `KNOWN_STATUSES`, `parse_filters`, source mappers, `build_audit_entries`, `gen_audit_logs_csv`, `PAGE_SIZE` |
| `Access/views.py` | **modify** — add `audit_logs` view |
| `EnigmaAutomation/urls.py` | **modify** — import + register `auditLogs` route |
| `templates/EnigmaOps/auditLogs.html` | **new** — filter form + results table + pagination + CSV button |
| `templates/global_layout.html` | **modify** — sidebar nav link (inside existing MANAGEMENT block) |
| `Access/tests/test_audit_logs_helper.py` | **new** — helper unit tests |
| `Access/tests/test_audit_logs_view.py` | **new** — view unit tests |

**Conventions discovered (follow exactly):**
- Mappers read these attribute paths: `UserAccessMapping` actor = `user_identity.user.email`; `MembershipV2` actor = `user.email`; `GroupV2` actor = `requester.email`; `GroupAccessMapping` actor = `requested_by.email`. Access resource = `access.access_tag`; group resource = `group.name` (or `name` for `GroupV2`).
- Auth decorators: `@login_required` then `@user_admin_or_ops` (from `Access.decorators`), exactly as `pending_failure` in `Access/views.py`.
- CSV pattern mirrors `gen_all_user_access_list_csv` in `Access/views_helper.py:143`.
- Tests use lightweight mock objects + `mocker` (see `Access/tests/test_access_views_helper.py`), no `@pytest.mark.django_db`.

Run tests with: `python -m pytest Access/tests/<file> -v` from repo root (venv active).

---

## Task 1: Helper foundations — `KNOWN_STATUSES` + `parse_filters`

**Files:**
- Create: `Access/audit_helper.py`
- Test: `Access/tests/test_audit_logs_helper.py`

- [ ] **Step 1: Write the failing test**

Create `Access/tests/test_audit_logs_helper.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'Access.audit_helper'`

- [ ] **Step 3: Write minimal implementation**

Create `Access/audit_helper.py`:

```python
"""Read-only audit log aggregation over existing access-lifecycle models.

Aggregates UserAccessMapping, MembershipV2, GroupV2 and GroupAccessMapping
into a unified, filterable, sortable stream. No schema changes, no writes.
"""
import csv
import datetime
import logging

from django.http import HttpResponse

from Access.models import (
    GroupAccessMapping,
    GroupV2,
    MembershipV2,
    UserAccessMapping,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 25

# Union of every status declared on the four source models, sorted + unique.
KNOWN_STATUSES = sorted(
    {
        "Pending", "SecondaryPending", "Processing", "Approved", "GrantFailed",
        "Declined", "Offboarding", "ProcessingRevoke", "RevokeFailed", "Revoked",
        "Deprecated", "Inactive",
    }
)


def _parse_date(value):
    """Return a date for an ISO YYYY-MM-DD string, else None (never raises)."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _validate_status(value):
    """Return value if it is a known status, else None."""
    value = (value or "").strip()
    return value if value in KNOWN_STATUSES else None


def parse_filters(get_params):
    """Normalize raw GET params into a typed filter dict; invalid values drop to None."""
    return {
        "date_from": _parse_date(get_params.get("dateFrom")),
        "date_to": _parse_date(get_params.get("dateTo")),
        "actor": (get_params.get("actor") or "").strip() or None,
        "status": _validate_status(get_params.get("status")),
        "resource": (get_params.get("resource") or "").strip() or None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add Access/audit_helper.py Access/tests/test_audit_logs_helper.py
git commit -m "feat: add audit log filter parsing helper"
```

---

## Task 2: Source mappers + value helpers

**Files:**
- Modify: `Access/audit_helper.py`
- Test: `Access/tests/test_audit_logs_helper.py`

- [ ] **Step 1: Write the failing test**

Append to `Access/tests/test_audit_logs_helper.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: FAIL — `AttributeError: module 'Access.audit_helper' has no attribute 'map_user_access'`

- [ ] **Step 3: Write minimal implementation**

Append to `Access/audit_helper.py` (after `parse_filters`):

```python
def _email(related):
    """Return related.email or '' for a possibly-None related User."""
    return getattr(related, "email", "") or "" if related is not None else ""


def _access_tag(access):
    return getattr(access, "access_tag", "") or "" if access is not None else ""


def _group_name(group):
    return getattr(group, "name", "") or "" if group is not None else ""


def _first_reason(*values):
    """Return the first non-empty reason string."""
    for value in values:
        if value:
            return value
    return ""


def map_user_access(obj):
    identity_user = getattr(obj.user_identity, "user", None) if obj.user_identity else None
    return {
        "timestamp": obj.updated_on,
        "actor": _email(identity_user),
        "action": "User Access",
        "status": obj.status,
        "resource": _access_tag(obj.access),
        "reason": _first_reason(obj.decline_reason, obj.fail_reason, obj.request_reason),
        "approver": _email(obj.approver_1),
        "source_type": "user_access",
    }


def map_membership(obj):
    return {
        "timestamp": obj.updated_on,
        "actor": _email(obj.user),
        "action": "Group Membership",
        "status": obj.status,
        "resource": _group_name(obj.group),
        "reason": _first_reason(obj.decline_reason, obj.reason),
        "approver": _email(obj.approver),
        "source_type": "group_membership",
    }


def map_group(obj):
    # GroupV2's resource is its own `name` field (not a related object).
    return {
        "timestamp": obj.updated_on,
        "actor": _email(obj.requester),
        "action": "Group Lifecycle",
        "status": obj.status,
        "resource": getattr(obj, "name", "") or "",
        "reason": _first_reason(obj.decline_reason),
        "approver": _email(obj.approver),
        "source_type": "group_lifecycle",
    }


def map_group_access(obj):
    group = _group_name(obj.group)
    access = _access_tag(obj.access)
    resource = " / ".join([part for part in (group, access) if part])
    return {
        "timestamp": obj.updated_on,
        "actor": _email(obj.requested_by),
        "action": "Group Access",
        "status": obj.status,
        "resource": resource,
        "reason": _first_reason(obj.decline_reason, obj.request_reason),
        "approver": _email(obj.approver_1),
        "source_type": "group_access",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add Access/audit_helper.py Access/tests/test_audit_logs_helper.py
git commit -m "feat: add per-source audit entry mappers"
```

---

## Task 3: `build_audit_entries` — ORM filter, merge, sort

**Files:**
- Modify: `Access/audit_helper.py`
- Test: `Access/tests/test_audit_logs_helper.py`

- [ ] **Step 1: Write the failing test**

Append to `Access/tests/test_audit_logs_helper.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'build_audit_entries'`

- [ ] **Step 3: Write minimal implementation**

Append to `Access/audit_helper.py`:

```python
# Each source declares the ORM paths used to push filters to the DB.
SOURCES = (
    {
        "model": UserAccessMapping,
        "timestamp_field": "updated_on",
        "actor_path": "user_identity__user__email",
        "resource_path": "access__access_tag",
        "mapper": map_user_access,
    },
    {
        "model": MembershipV2,
        "timestamp_field": "updated_on",
        "actor_path": "user__email",
        "resource_path": "group__name",
        "mapper": map_membership,
    },
    {
        "model": GroupV2,
        "timestamp_field": "updated_on",
        "actor_path": "requester__email",
        "resource_path": "name",
        "mapper": map_group,
    },
    {
        "model": GroupAccessMapping,
        "timestamp_field": "updated_on",
        "actor_path": "requested_by__email",
        "resource_path": "group__name",
        "mapper": map_group_access,
    },
)


def _orm_filters(source, filters):
    orm = {}
    ts = source["timestamp_field"]
    if filters["date_from"]:
        orm[ts + "__date__gte"] = filters["date_from"]
    if filters["date_to"]:
        orm[ts + "__date__lte"] = filters["date_to"]
    if filters["actor"]:
        orm[source["actor_path"] + "__icontains"] = filters["actor"]
    if filters["status"]:
        orm["status"] = filters["status"]
    if filters["resource"]:
        orm[source["resource_path"] + "__icontains"] = filters["resource"]
    return orm


def build_audit_entries(filters):
    """Query every source with pushed-down filters, normalize, merge, sort desc."""
    entries = []
    for source in SOURCES:
        orm = _orm_filters(source, filters)
        for obj in source["model"].objects.filter(**orm):
            entries.append(source["mapper"](obj))
    entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return entries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add Access/audit_helper.py Access/tests/test_audit_logs_helper.py
git commit -m "feat: add unified audit entry builder with pushed-down filters"
```

---

## Task 4: `gen_audit_logs_csv`

**Files:**
- Modify: `Access/audit_helper.py`
- Test: `Access/tests/test_audit_logs_helper.py`

- [ ] **Step 1: Write the failing test**

Append to `Access/tests/test_audit_logs_helper.py`:

```python
def _sample_entry(**overrides):
    base = {
        "timestamp": datetime.datetime(2026, 1, 5, 10, 30, 0),
        "actor": "alice@example.com", "action": "User Access",
        "status": "Approved", "resource": "aws-prod",
        "approver": "boss@example.com", "reason": "need, it",
        "source_type": "user_access",
    }
    base.update(overrides)
    return base


def test_gen_audit_logs_csv_headers_and_type():
    response = audit_helper.gen_audit_logs_csv([_sample_entry()])
    assert response["Content-Type"] == "text/csv"
    assert "attachment; filename=" in response["Content-Disposition"]
    body = response.content.decode("utf-8")
    lines = body.splitlines()
    assert lines[0] == "Timestamp,Actor,Action,Status,Resource,Approver,Reason"


def test_gen_audit_logs_csv_escapes_commas_in_reason():
    response = audit_helper.gen_audit_logs_csv([_sample_entry(reason="need, it")])
    body = response.content.decode("utf-8")
    assert '"need, it"' in body
    assert "2026-01-05 10:30:00" in body


def test_gen_audit_logs_csv_empty_list_is_header_only():
    response = audit_helper.gen_audit_logs_csv([])
    body = response.content.decode("utf-8").strip()
    assert body == "Timestamp,Actor,Action,Status,Resource,Approver,Reason"


def test_gen_audit_logs_csv_handles_missing_timestamp():
    response = audit_helper.gen_audit_logs_csv([_sample_entry(timestamp=None)])
    body = response.content.decode("utf-8").splitlines()
    assert body[1].startswith(",")  # empty timestamp cell
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'gen_audit_logs_csv'`

- [ ] **Step 3: Write minimal implementation**

Append to `Access/audit_helper.py`:

```python
CSV_HEADER = ["Timestamp", "Actor", "Action", "Status", "Resource", "Approver", "Reason"]


def gen_audit_logs_csv(entries):
    """Render the (already filtered) entries as a downloadable CSV response."""
    response = HttpResponse(content_type="text/csv")
    filename = "AuditLogs-" + datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S") + ".csv"
    response["Content-Disposition"] = 'attachment; filename="' + filename + '"'

    writer = csv.writer(response)
    writer.writerow(CSV_HEADER)
    for entry in entries:
        timestamp = entry["timestamp"]
        writer.writerow(
            [
                timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "",
                entry["actor"],
                entry["action"],
                entry["status"],
                entry["resource"],
                entry["approver"],
                entry["reason"],
            ]
        )
    return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add Access/audit_helper.py Access/tests/test_audit_logs_helper.py
git commit -m "feat: add audit logs CSV export helper"
```

---

## Task 5: `audit_logs` view

**Files:**
- Modify: `Access/views.py`
- Test: `Access/tests/test_audit_logs_view.py`

- [ ] **Step 1: Write the failing test**

Create `Access/tests/test_audit_logs_view.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest Access/tests/test_audit_logs_view.py -v`
Expected: FAIL — `AttributeError: module 'Access.views' has no attribute 'audit_logs'` (and/or `audit_helper` not imported)

- [ ] **Step 3: Write minimal implementation**

In `Access/views.py`, add the import near the other `from Access import ...` lines at the top:

```python
from Access import audit_helper
```

Then add the view (place it next to `pending_failure`):

```python
@login_required
@user_admin_or_ops
def audit_logs(request):
    """Read-only, filterable audit log of access-lifecycle events (admin/ops only)."""
    try:
        filters = audit_helper.parse_filters(request.GET)
        entries = audit_helper.build_audit_entries(filters)
    except Exception:
        logger.exception(
            "Error building audit logs: %s" % (traceback.format_exc())
        )
        entries = []

    if request.GET.get("responseType") == "csv":
        return audit_helper.gen_audit_logs_csv(entries)

    paginator_obj = Paginator(entries, audit_helper.PAGE_SIZE)
    try:
        page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    last_page = paginator_obj.num_pages
    page = min(max(page, 1), last_page)
    page_obj = paginator_obj.page(page)

    base_params = request.GET.copy()
    base_params.pop("page", None)
    base_params.pop("responseType", None)

    context = {
        "entries": page_obj.object_list,
        "current_page": page,
        "last_page": last_page,
        "statuses": audit_helper.KNOWN_STATUSES,
        "base_qs": base_params.urlencode(),
    }
    return render(request, "EnigmaOps/auditLogs.html", context)
```

> `Paginator`, `render`, `logger`, `traceback`, `login_required`, and `user_admin_or_ops` are already imported in `Access/views.py` (verify the top of the file; `from Access.decorators import user_admin_or_ops` exists for `pending_failure`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest Access/tests/test_audit_logs_view.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add Access/views.py Access/tests/test_audit_logs_view.py
git commit -m "feat: add audit logs view"
```

---

## Task 6: Register the route

**Files:**
- Modify: `EnigmaAutomation/urls.py`

- [ ] **Step 1: Add `audit_logs` to the `from Access.views import (...)` block**

Add the name `audit_logs,` to the existing parenthesized import (alphabetical-ish, near `all_user_access_list,`).

- [ ] **Step 2: Add the URL pattern**

Find the block of `re_path(...)` entries that reference `access/...` and add:

```python
    re_path(r"^access/auditLogs$", audit_logs, name="auditLogs"),
```

- [ ] **Step 3: Verify Django loads URLs without error**

Run: `python manage.py check`
Expected: `System check identified no issues` (0 silenced).

- [ ] **Step 4: Verify reverse resolves**

Run:
```bash
python -c "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','EnigmaAutomation.settings'); django.setup(); from django.urls import reverse; print(reverse('auditLogs'))"
```
Expected: `/access/auditLogs`

- [ ] **Step 5: Commit**

```bash
git add EnigmaAutomation/urls.py
git commit -m "feat: add audit logs route"
```

---

## Task 7: Page template

**Files:**
- Create: `templates/EnigmaOps/auditLogs.html`

Design rules applied from `modern-web-guidance` (forms + accessibility guides): `<form method="GET">` for idempotent filtering; every input has an associated `<label for>`; `<fieldset>`/`<legend>` group the filters; visible labels (no placeholder-as-label); native `type="date"` inputs; semantic table with `<caption>` + `<th scope="col">`; `<select>` for the 12-status list; an explicit empty-state row. Adapted to the repo's Bootstrap 4 + jQuery (no build step).

- [ ] **Step 1: Create the template**

Create `templates/EnigmaOps/auditLogs.html`:

```html
{% extends 'global_layout.html' %}
{% block content %}
<div class="container-fluid" style="padding:20px;">
  <h1 class="h4" style="margin-bottom:16px;">Audit Logs</h1>

  <form action="{% url 'auditLogs' %}" method="GET" class="card" style="padding:16px;margin-bottom:20px;">
    <fieldset style="border:none;padding:0;margin:0;">
      <legend class="h6">Filters</legend>
      <div class="form-row">
        <div class="form-group col-md-2">
          <label for="dateFrom">Date from</label>
          <input type="date" id="dateFrom" name="dateFrom" class="form-control"
                 value="{{ request.GET.dateFrom }}">
        </div>
        <div class="form-group col-md-2">
          <label for="dateTo">Date to</label>
          <input type="date" id="dateTo" name="dateTo" class="form-control"
                 value="{{ request.GET.dateTo }}">
        </div>
        <div class="form-group col-md-3">
          <label for="actor">Actor (email)</label>
          <input type="text" id="actor" name="actor" class="form-control"
                 autocomplete="off" value="{{ request.GET.actor }}">
        </div>
        <div class="form-group col-md-2">
          <label for="status">Status</label>
          <select id="status" name="status" class="form-control">
            <option value="">All</option>
            {% for status in statuses %}
            <option value="{{ status }}" {% if request.GET.status == status %}selected{% endif %}>{{ status }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="form-group col-md-3">
          <label for="resource">Resource / module</label>
          <input type="text" id="resource" name="resource" class="form-control"
                 autocomplete="off" value="{{ request.GET.resource }}">
        </div>
      </div>
      <div>
        <button type="submit" class="btn btn-primary">Apply filters</button>
        <a href="{% url 'auditLogs' %}" class="btn btn-secondary">Reset</a>
        <a href="?{{ base_qs }}{% if base_qs %}&{% endif %}responseType=csv"
           class="btn btn-success">Export CSV</a>
      </div>
    </fieldset>
  </form>

  <div class="table-responsive" aria-live="polite">
    <table class="table table-striped table-sm">
      <caption class="sr-only">Audit log of access-lifecycle events</caption>
      <thead>
        <tr>
          <th scope="col">Timestamp</th>
          <th scope="col">Actor</th>
          <th scope="col">Action</th>
          <th scope="col">Status</th>
          <th scope="col">Resource</th>
          <th scope="col">Approver</th>
          <th scope="col">Reason</th>
        </tr>
      </thead>
      <tbody>
        {% for entry in entries %}
        <tr>
          <td>{{ entry.timestamp|date:"Y-m-d H:i:s" }}</td>
          <td>{{ entry.actor }}</td>
          <td>{{ entry.action }}</td>
          <td>{{ entry.status }}</td>
          <td>{{ entry.resource }}</td>
          <td>{{ entry.approver }}</td>
          <td>{{ entry.reason }}</td>
        </tr>
        {% empty %}
        <tr>
          <td colspan="7" style="text-align:center;">No audit log entries match the current filters.</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  {% if last_page > 1 %}
  <nav aria-label="Audit log pages">
    <ul class="pagination">
      <li class="page-item {% if current_page <= 1 %}disabled{% endif %}">
        <a class="page-link" href="?{{ base_qs }}{% if base_qs %}&{% endif %}page={{ current_page|add:'-1' }}">Previous</a>
      </li>
      <li class="page-item disabled">
        <span class="page-link">Page {{ current_page }} of {{ last_page }}</span>
      </li>
      <li class="page-item {% if current_page >= last_page %}disabled{% endif %}">
        <a class="page-link" href="?{{ base_qs }}{% if base_qs %}&{% endif %}page={{ current_page|add:'1' }}">Next</a>
      </li>
    </ul>
  </nav>
  {% endif %}
</div>
{% endblock %}
```

> Confirm the block name: open `templates/global_layout.html` and check the `{% block %}` that wraps page content (it is `{% block content %}` based on sibling pages). If sibling EnigmaOps templates use a different block name, match theirs.

- [ ] **Step 2: Verify the page renders for an admin/ops user**

Run the dev server (`python manage.py runserver`) logged in as an admin/ops user and visit `/access/auditLogs`. Confirm: filter form shows, table renders (or empty-state row), no template errors in console. (If no dev data, use `scripts/seed_dev_data.py` if available.)

- [ ] **Step 3: Verify filter round-trip + CSV**

Submit a filter (e.g. a status); confirm the querystring appears in the URL and the form repopulates. Click **Export CSV**; confirm a `text/csv` download with the same filters applied.

- [ ] **Step 4: Commit**

```bash
git add templates/EnigmaOps/auditLogs.html
git commit -m "feat: add audit logs page template"
```

---

## Task 8: Sidebar navigation link

**Files:**
- Modify: `templates/global_layout.html`

- [ ] **Step 1: Add the nav link inside the existing MANAGEMENT block**

In the `{% if request.user.is_superuser or user.user.is_manager or is_ops %}` MANAGEMENT `<ul>` (the one containing `allUserAccessList` and `allUsersList`), add as the last `<li>`:

```html
        <li><a href={% url 'auditLogs' %}><i class="fa fa-history nav-icon"></i>Audit Logs</a></li>
```

- [ ] **Step 2: Verify the link renders and routes**

Reload any page as an admin/ops user; confirm "Audit Logs" appears under MANAGEMENT and links to `/access/auditLogs`. Confirm it is absent for a non-admin/ops user (block is already gated).

- [ ] **Step 3: Commit**

```bash
git add templates/global_layout.html
git commit -m "feat: add audit logs sidebar nav link"
```

---

## Task 9: Full verification

- [ ] **Step 1: Run the full new test suite**

Run: `python -m pytest Access/tests/test_audit_logs_helper.py Access/tests/test_audit_logs_view.py -v`
Expected: all pass (22 tests total).

- [ ] **Step 2: Run Django system check**

Run: `python manage.py check`
Expected: no issues.

- [ ] **Step 3: Run repo lint/pre-commit on changed files**

Run: `pre-commit run --files Access/audit_helper.py Access/views.py EnigmaAutomation/urls.py templates/EnigmaOps/auditLogs.html templates/global_layout.html Access/tests/test_audit_logs_helper.py Access/tests/test_audit_logs_view.py`
Expected: hooks pass (fix any flake8/pylama findings, e.g. unused imports, line length).

- [ ] **Step 4: Confirm no regressions in the Access app**

Run: `python -m pytest Access/tests -q`
Expected: no new failures attributable to this change.

- [ ] **Step 5: Final commit (only if lint produced fixes)**

```bash
git add -A
git commit -m "chore: lint fixes for audit logs page"
```

---

## Self-Review Notes (author)

- **Spec coverage:** data source (Tasks 2-3), admin/ops gate (Task 5 + decorator), all four filters (Task 3 `_orm_filters`), date-range/actor/status/resource semantics (Task 1 `parse_filters` + Task 3), pagination + clamping (Task 5), CSV export (Task 4 + view branch), template form/table/empty-state (Task 7), nav link (Task 8), test matrix happy/empty/error (Tasks 1-5). All spec sections map to a task.
- **Type consistency:** entry dict keys (`timestamp, actor, action, status, resource, reason, approver, source_type`) are identical across mappers, builder sort, CSV writer, and template. `parse_filters` output keys (`date_from, date_to, actor, status, resource`) match `_orm_filters` reads. `PAGE_SIZE = 25` used in view test math (30 → 2 pages).
- **No placeholders:** every code step contains complete code; the `map_group` resource note gives the exact final line to use.
