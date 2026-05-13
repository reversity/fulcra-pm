# fulcra-pm data model

Each task is stored as one Fulcra `MomentAnnotation` via the
`/user/v1alpha1/annotation` endpoint family. The annotation's `description`
field carries a JSON envelope marked with `"_pm":1`.

## Annotation shape (as Fulcra stores it)

```jsonc
{
  "id":             "<uuid>",
  "name":           "Fix login redirect",        // task title
  "annotation_type":"moment",
  "tags":           [],
  "spec":           null,
  "measurement_spec": null,
  "fulcra_userid":  "<uuid>",
  "created_at":     "<iso>",
  "updated_at":     "<iso>",
  "deleted_at":     null,                        // soft-delete marker
  "fulcra_source_id": "com.fulcradynamics.annotation.<uuid>",

  // PM payload, JSON-encoded inside a string field:
  "description": "{\"_pm\":1,\"v\":1,\"status\":\"todo\",\"priority\":\"p1\",\"project\":\"auth\",\"assignee\":\"ash\",\"due\":\"2026-05-15T23:59:59Z\",\"agent\":\"claude-ash\",\"notes\":\"...\",\"created\":\"...\",\"updated\":\"...\"}"
}
```

## Flattened task shape (what `pm.py` returns)

```json
{
  "id": "af744fbc-…",
  "title": "Fix login redirect",
  "status": "todo|doing|blocked|done|cancelled",
  "priority": "p0|p1|p2|p3|null",
  "project": "string|null",
  "assignee": "string|null",
  "due": "ISO datetime|null",
  "agent": "string|null",
  "notes": "string (may contain timestamped comment lines)",
  "created": "ISO datetime",
  "updated": "ISO datetime",
  "deleted_at": "ISO datetime|null",
  "fulcra_userid": "<uuid>"
}
```

## Field semantics

- `status` — lifecycle state. Default `todo`.
- `priority` — `p0` (drop everything) through `p3` (someday). Optional.
- `project` — free-text grouping label. Pick stable names and reuse them.
- `assignee` — human or agent expected to DO the task.
- `agent` — handle of whoever CREATED or last-updated the task (different from `assignee`).
- `due` — ISO datetime. `pm.py` accepts `YYYY-MM-DD` and normalizes to `T23:59:59Z`.
- `notes` — free text. `pm comment` appends `[<iso>] <author>: <text>` lines.
- `created`, `updated` — set by `pm.py` on every mutation.

## Fulcra Life API write surface (full)

`pm.py` uses 4 of these 9 endpoints. The rest are documented for reference.

| Method | Path | Used by `pm.py` | Purpose |
|---|---|---|---|
| POST | `/user/v1alpha1/annotation` | `create` | Create task |
| GET | `/user/v1alpha1/annotation` | `list` | Fetch all annotations |
| GET | `/user/v1alpha1/annotation/{id}` | `get`, `update`, `comment` | Read one |
| PUT | `/user/v1alpha1/annotation/{id}` | `update`, `comment`, `done` | Update task |
| DELETE | `/user/v1alpha1/annotation/{id}` | `delete` | Soft delete |
| POST | `/user/v1alpha1/annotation/{id}/cancel_deletion` | `restore` | Restore soft-deleted |
| POST | `/user/v1alpha1/tag` | — | Create a Fulcra tag (not used) |
| DELETE | `/user/v1alpha1/tag/id/{tag_id}` | — | Delete a Fulcra tag (not used) |
| POST | `/user/v1alpha1/preferences` | — | Set user preferences (not used) |
| POST | `/ingest/v1/record` | — | Push raw data records (not used) |
| POST | `/ingest/v1/record/batch` | — | Bulk ingest (JSONL) (not used) |

Authoritative spec: `https://api.fulcradynamics.com/openapi.json`

## Quirks worth knowing

- **POST/PUT return `303` with a `Location` header**, not the created/updated
  body. `pm.py` parses the trailing UUID from `Location` and follows up with a
  GET to return the resource.
- **DELETE is soft.** The row remains; `deleted_at` is populated. `restore`
  clears `deleted_at` via the `cancel_deletion` endpoint.
- **The list endpoint has no query parameters.** All filtering happens client
  side in `pm.py`.
- **`v1alpha1`**. The API may change. `pm.py` surfaces raw HTTP error bodies so
  schema drift is obvious.
