# Audit Logs Page — Design Spec

- **Date:** 2026-06-04
- **Branch:** `feat/audit-logs-page-mwb`
- **Status:** Approved (design), pending implementation plan

## Goal

Add a read-only **Audit Logs** page to Enigma that surfaces a unified,
filterable, paginated stream of access-lifecycle events drawn from existing
models, with CSV export. No schema changes and no new event writes.

## Decisions (locked)

| Question | Decision |
| --- | --- |
| Data source | **Aggregate existing records** — read-only view over existing models. No new table, no signals. |
| Access control | **Admin/Ops only** — `@login_required` + `@user_admin_or_ops`. |
| Filters | Date range, Actor/user, Action/status, Resource/module (all four). |
| Aggregation strategy | **Approach A** — per-source ORM filtering, Python normalize + merge + sort, `Paginator`. |

## Source models

Aggregated from `Access/models.py`. Each row is normalized to a common entry
shape. Timestamp used for ordering is `updated_on` (the most recent state
change), falling back to `requested_on` where relevant.

| Source (`source_type`) | Model | Actor | Resource | Status field | Reason fields |
| --- | --- | --- | --- | --- | --- |
| `user_access` | `UserAccessMapping` | `user_identity.user.email` | `access.access_tag` | `status` (10 states incl. GrantFailed, Revoked) | `request_reason`, `decline_reason`, `fail_reason` |
| `group_membership` | `MembershipV2` | `user.email` | `group.name` | `status` (Pending/Approved/Declined/Revoked) | `reason`, `decline_reason` |
| `group_lifecycle` | `GroupV2` | `requester.email` | `name` | `status` (Pending/Approved/Declined/Deprecated) | `decline_reason` |
| `group_access` | `GroupAccessMapping` | `requested_by.email` | `group.name` + `access.access_tag` | `status` (Pending/Approved/Declined/Revoked/Inactive) | `request_reason`, `decline_reason` |

Nullable FKs (`approver`, `requester`, `requested_by`, `user_identity`) are
guarded; missing relations render as empty strings, never raise.

## Normalized entry shape

```python
{
    "timestamp":   datetime,   # updated_on (ordering key)
    "actor":       str,        # email of the subject/requester
    "action":      str,        # human label, e.g. "User Access", "Group Membership"
    "status":      str,        # source status verbatim
    "resource":    str,        # access tag / group name
    "reason":      str,        # most relevant reason for current status
    "source_type": str,        # machine key from table above
    "approver":    str,        # approver email, "" if none
}
```

## Components

### 1. Route — `EnigmaAutomation/urls.py`
```python
re_path(r"^access/auditLogs$", audit_logs, name="auditLogs"),
```

### 2. View — `Access/views.py :: audit_logs(request)`
- Decorated `@login_required` + `@user_admin_or_ops` (mirrors `pending_failure`).
- Reads GET params: `dateFrom`, `dateTo`, `actor`, `status`, `resource`,
  `page`, `responseType`.
- Calls `audit_helper.build_audit_entries(filters)`.
- `responseType == "csv"` → `audit_helper.gen_audit_logs_csv(entries)`.
- Otherwise → `Paginator(entries, PAGE_SIZE)` with `EmptyPage`/out-of-range
  clamping (mirrors `all_user_access_list` lines 522-528), render template.
- Wrapped in defensive try/except with `logger.exception`, consistent with
  existing views; on error renders the page with an empty result set rather
  than 500-ing.

### 3. Helper — new module `Access/audit_helper.py`
Isolated so `views.py` stays thin and merge logic is unit-testable alone.

- `AUDIT_SOURCES` — registry; one entry per source declaring `model`,
  `timestamp_field`, and a `map_entry(obj) -> dict` mapper.
- `parse_filters(request) -> dict` — extracts/validates GET params. Invalid
  dates are dropped (treated as "no constraint"), never raise. Status is
  validated against the known status set; unknown → dropped.
- `build_audit_entries(filters) -> list[dict]`:
  1. For each source, build an ORM `.filter(**source_orm_filters)` pushing
     date range / actor (`icontains` on email) / status (exact) / resource
     (`icontains`) down to the DB.
  2. Map each row via its `map_entry`.
  3. Concatenate all sources, sort by `timestamp` descending.
- `gen_audit_logs_csv(entries) -> HttpResponse` — mirrors
  `gen_all_user_access_list_csv` (`text/csv`, timestamped filename,
  `csv.writer`). Columns: `Timestamp, Actor, Action, Status, Resource,
  Approver, Reason`.

### 4. Template — `templates/EnigmaOps/auditLogs.html`
Extends `global_layout.html`, consistent with sibling EnigmaOps pages.
- **Filter form** (method `GET`): `dateFrom`/`dateTo` (native `date` inputs),
  `actor` (text), `status` (select, options = union of source statuses),
  `resource` (text). Submit reloads with query params.
- **Results table**: columns matching the entry shape; uses the existing
  tablesorter styling. Empty state row when no results.
- **Pagination**: prev/next + page numbers that **preserve all active filter
  query params** (append `&page=N` to current querystring).
- **Export CSV** button: resubmits the same filter querystring with
  `responseType=csv`.
- Frontend markup informed by `modern-web-guidance` (accessible filter form +
  results table) at build time, adapted to Bootstrap 4 + jQuery (no build step).

### 5. Navigation — `templates/global_layout.html`
Add a sidebar `<li>` link to `{% url 'auditLogs' %}` near the existing
`allUserAccessList` entry (line ~133), gated to admin/ops if a template flag is
available; otherwise the view's own decorator enforces access.

## Filter semantics

| Filter | Param | ORM application |
| --- | --- | --- |
| Date from | `dateFrom` | `<timestamp_field>__date__gte` |
| Date to | `dateTo` | `<timestamp_field>__date__lte` |
| Actor | `actor` | `<actor_email_path>__icontains` |
| Status | `status` | `status` exact (validated) |
| Resource | `resource` | resource path `__icontains` |

Empty/absent param = no constraint. Invalid date or unknown status = dropped
(no constraint), never an error.

## Error handling

- Invalid date string → filter dropped, page renders.
- `page` out of range / non-integer → clamp to valid page (existing pattern).
- Missing nullable FK on a row → empty string in that field.
- Unexpected exception in view → logged, page renders with empty results.

## Pagination

- `PAGE_SIZE = 25`.
- Django `Paginator` over the merged list (only when `responseType != "csv"`).
- CSV export ignores pagination — exports the full filtered set.

## Testing (test-first; happy / empty-null / error per edge-case mandate)

`Access/tests/test_audit_logs_helper.py`:
- Per-source `map_entry` happy path (one fixture row each → correct entry).
- `build_audit_entries` merges + sorts across all four sources by timestamp.
- Each filter: date range, actor, status, resource — **including no-match → []**.
- `parse_filters`: invalid date dropped; unknown status dropped; empty params.
- Nullable FK row → empty-string fields, no exception.
- `gen_audit_logs_csv`: content-type, attachment header, header row, value
  escaping (commas/quotes in reason), empty list → header-only CSV.

`Access/tests/test_audit_logs_view.py`:
- Admin/Ops user → 200, context has paginated entries.
- Non-admin user → 403 (`PermissionDenied`).
- Unauthenticated → redirect to login.
- `responseType=csv` → `text/csv` with attachment header.
- Pagination boundary: page 1, last page, out-of-range page clamps.
- Empty result set → page renders with empty-state.

## Out of scope (YAGNI)

- No `AuditLog` table, signals, or middleware.
- No event capture for actions not already persisted by the four models.
- No live updates / websockets; page is request-response.
- No `QuerySet.union()` (Approach B) — revisit only if data volume explodes;
  isolated in `audit_helper.py` so the swap touches one module.

## Files touched

| File | Change |
| --- | --- |
| `Access/audit_helper.py` | **new** — registry, parse_filters, build_audit_entries, CSV |
| `Access/views.py` | **new** `audit_logs` view |
| `EnigmaAutomation/urls.py` | **new** route |
| `templates/EnigmaOps/auditLogs.html` | **new** template |
| `templates/global_layout.html` | nav link |
| `Access/tests/test_audit_logs_helper.py` | **new** tests |
| `Access/tests/test_audit_logs_view.py` | **new** tests |
