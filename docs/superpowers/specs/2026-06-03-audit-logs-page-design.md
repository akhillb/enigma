# Audit Logs Page — Design

**Date:** 2026-06-03
**Branch:** `feat/audit-logs-page`
**Status:** Approved by user (brainstorming session)

## Goal

Add an admin/ops-only "Audit Logs" page that presents a unified, filterable audit
trail built from existing request/approval data. Results render after an explicit
filter submit, are paginated server-side, and can be exported as CSV.

## Scope

- **In:** unified read-only view over `UserAccessMapping`, `GroupAccessMapping`,
  and `MembershipV2`; five filters; pagination; CSV export; sidebar nav link.
- **Out:** new `AuditLog` model or event instrumentation (rejected — option B in
  brainstorm); editing or acting on records from this page; DRF API.

## Access Control

- `@login_required` + `@user_admin_or_ops` (existing decorator,
  `Access/decorators.py`).
- Nav link appears in the MANAGEMENT sidebar section of
  `templates/global_layout.html`, gated the same way as sibling ops links.

## Data Layer

New helpers in `Access/views_helper.py`:

### `get_audit_log_filters(request)`

Parses GET params into per-model ORM filter dicts. Params:

| Param | Type | Filter behavior |
|---|---|---|
| `user` | text | icontains on username/email of the acting user |
| `accessTag` | text | icontains on access tag (User/Group Access only) |
| `status` | choice | exact status match |
| `recordType` | choice | `userAccess` / `groupAccess` / `membership` — restricts to one model |
| `dateFrom` | date `YYYY-MM-DD` | `requested_on__date__gte` |
| `dateTo` | date `YYYY-MM-DD` | `requested_on__date__lte` |

Malformed dates raise a validation error surfaced as JSON 400.

### `get_audit_log_entries(filters)`

Queries each applicable model with `select_related` on user/access/group
relations, normalizes rows to a common dict:

| Field | UserAccessMapping | GroupAccessMapping | MembershipV2 |
|---|---|---|---|
| `record_type` | "User Access" | "Group Access" | "Membership" |
| `user` | `user_identity.user` | `requested_by` | `user` |
| `access` | access tag + label | group name + access tag | group name |
| `status` | `status` | `status` | `status` |
| `requested_on` | direct | direct | direct |
| `updated_on` | direct | direct | direct |
| `actors` | approver_1/2, revoker | approver_1/2, revoker | approver |
| `reason` | request/decline reason | request reason | reason |

Merged list sorted by `requested_on` descending. When `recordType` is set only
that model is queried.

### `gen_audit_logs_csv(data_list)`

Mirrors `gen_all_user_access_list_csv`. Columns: RecordType, User, Access,
Status, RequestedOn, UpdatedOn, Actors, Reason. Filename
`AuditLogs-{YYYY-MM-DD_HH:MM:SS}.csv`. Exports **all filtered rows**, not just
the current page.

## View + URL

`audit_logs(request)` in `Access/views.py`:

- Default → render `EnigmaOps/auditLogs.html` (filters only, no data fetched).
- `responseType=json` → `{dataList, current_page, last_page, total_count}`;
  paginated via Django `Paginator`, 10 rows/page; `EmptyPage` clamps to last
  page.
- `responseType=csv` → CSV download of full filtered set.

URL: `re_path(r"^access/auditLogs$", views.audit_logs, name="auditLogs")` in
`EnigmaAutomation/urls.py`.

## Template + UX

`templates/EnigmaOps/auditLogs.html`, extends `global_layout.html`
(Bootstrap 4.1.3 + jQuery, same as sibling list pages):

- Filter bar: user (text), access tag (text), status (select), record type
  (select), date from/to (date inputs), **Submit** button.
- Table is empty until Submit; empty-state message shown.
- Submit → AJAX GET with `responseType=json`; render rows + Bootstrap
  pagination; page clicks re-fetch with current filters.
- **Export CSV** button (disabled until first search) navigates to the current
  filter querystring with `responseType=csv`.
- Empty result set → "No audit records match your filters."

## Error Handling

- Malformed `dateFrom`/`dateTo` → JSON 400 `{error}` rendered inline above the
  table.
- Out-of-range / non-numeric `page` → clamped to valid range.
- Unauthorized users → existing decorator behavior (redirect / 401).

## Testing (pytest)

- **Permissions:** anonymous → redirect; logged-in non-ops → denied; ops → 200.
- **Happy path:** each filter narrows results; merged cross-model sort order by
  `requested_on` desc; pagination metadata correct.
- **Empty/null:** no matching rows → empty `dataList`; CSV with header row only;
  blank filters return all records.
- **Errors:** malformed date → 400; out-of-range page → clamped; non-ops CSV
  request denied.
- **CSV:** `Content-Disposition` attachment header; row content matches applied
  filters.
