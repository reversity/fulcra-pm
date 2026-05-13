# fulcra-pm limits, failure modes, and recovery

## Limits

- **No server-side filtering.** `pm list` fetches all annotations and filters
  client-side. Acceptable up to a few hundred tasks; degrades at thousands.
- **No per-agent identity.** Every agent OAuths as the same Fulcra user.
  `--agent` and `--assignee` are honor-system string labels, not security
  boundaries.
- **Tasks pollute the Fulcra annotation catalog.** They appear in the Fulcra
  Context iOS/web app alongside real annotations like "Neck Pain". The
  `_pm:1` envelope marker prevents `pm.py` from confusing them with real
  annotations; nothing prevents the Context UI from showing them.
- **No comment threads.** `pm comment` appends timestamped lines to a single
  `notes` blob. No per-comment IDs, edits, or deletes.
- **Soft deletes accumulate.** `pm delete` flags `deleted_at` but the row
  stays. There is no bulk-purge command.
- **`v1alpha1` API.** Subject to change without notice.

## Failure modes

### `fulcra` CLI not on PATH

```json
{"error": "fulcra CLI not on PATH. Install via 'uv tool install …'"}
```

Recovery: install via `uv tool install 'git+https://github.com/fulcradynamics/fulcra-api-python.git@add-cli'`.

### Stale or missing token

`pm health` returns non-200, or any command returns HTTP 401.

Recovery: run `fulcra auth login` — it opens a browser for OAuth device flow.
Surface the URL to the user; the flow times out in 2 minutes.

### Ambiguous id prefix

```json
{"error": "id prefix 'af' is ambiguous (3 matches)"}
```

Recovery: ask for at least 4–8 characters of the UUID.

### Annotation not a PM task

```json
{"error": "annotation <id> is not a PM task"}
```

Cause: the annotation's `description` does not contain `"_pm":1`. The skill
refuses to operate on non-PM annotations to avoid clobbering the user's real
Fulcra data.

### HTTP 5xx from Fulcra

`pm.py` surfaces the raw error body. Retry once. If it persists, check
[Fulcra status](https://support.fulcradynamics.com/).

## Recovery procedures

### Re-authenticate

```bash
fulcra auth login
python3 ~/.claude/skills/fulcra-pm/scripts/pm.py health
```

### Sanity-check the annotation catalog

```bash
# Count Fulcra annotations vs. PM tasks
TOKEN=$(fulcra auth print-access-token)
curl -sS -H "Authorization: Bearer $TOKEN" \
  https://api.fulcradynamics.com/user/v1alpha1/annotation \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
pm = sum(1 for a in data if '\"_pm\"' in (a.get('description') or ''))
print(f'{len(data)} total annotations, {pm} are PM tasks')"
```

### Manually inspect a task at the API level

```bash
TOKEN=$(fulcra auth print-access-token)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://api.fulcradynamics.com/user/v1alpha1/annotation/<full-uuid>" \
  | python3 -m json.tool
```

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `FULCRA_API_BASE` | `https://api.fulcradynamics.com` | Override API host |
| `FULCRA_PM_LOG` | `warn` | `debug` / `info` / `warn` / `error` (stderr) |
