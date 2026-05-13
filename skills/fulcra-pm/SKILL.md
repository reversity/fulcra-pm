---
name: fulcra-pm
description: >-
  This skill should be used when the user asks to "add a task", "create a
  task", "track this", "list my tasks", "what am I working on", "what's the
  team doing", "what's <agent> working on", "mark this done", "assign this
  to <agent or person>", "hand this off to the next session", or any other
  request to record, query, update, or delegate work that needs to persist
  across Claude Code sessions and agents. Backed by the Fulcra Life API,
  tasks are stored server-side as MomentAnnotation records so every Claude
  session (and other agent) authenticated to the same Fulcra account sees
  the same list. Do not use this skill for ephemeral within-session
  checklists — use TodoWrite for those.
---

# fulcra-pm

A shared task tracker for Claude agents, backed by the Fulcra Life API. Tasks
persist server-side so multiple sessions, subagents, and external agents that
auth to the same Fulcra account see the same list.

## When to use this skill vs. TodoWrite

Use this skill when work needs to outlive the current session or be visible to
another agent. Use `TodoWrite` for ephemeral within-session checklists.

| Signal | This skill | TodoWrite |
|---|---|---|
| "Add a task for me to do tomorrow" | yes | no |
| "What's <other agent> working on?" | yes | no |
| "Hand this off to the next session" | yes | no |
| "Mark the auth bug as done" | yes | no |
| Track this conversation's internal steps | no | yes |

## Prerequisites

Confirm the `fulcra` CLI is installed and authenticated before invoking any
command:

```bash
fulcra auth print-access-token > /dev/null && echo ok
```

If this fails, run `fulcra auth login`. Surface the URL it prints to the user;
the OAuth device flow times out in two minutes.

The wrapper script `scripts/pm.py` is stdlib-only Python — no extra deps.

## Invocation

All commands live behind one script bundled with this skill:

```bash
PM="python3 ${CLAUDE_PLUGIN_ROOT}/skills/fulcra-pm/scripts/pm.py"
```

`${CLAUDE_PLUGIN_ROOT}` resolves to the plugin's install root at runtime. When
the plugin is not installed (running the skill files directly), substitute the
absolute path to `scripts/pm.py`.

Every command emits JSON on stdout. Errors emit JSON on stderr and exit
non-zero. Pipe to `jq` or parse with `python3 -c "import sys, json; …"`.

Always run `$PM health` first if any command returns unexpected output.

### Create a task

```bash
$PM create "Fix login redirect bug" \
  --status todo --priority p1 \
  --project auth-refresh \
  --assignee ash \
  --due 2026-05-15 \
  --agent claude-code-ash \
  --notes "Reproduces on Safari 18; passes on Chrome"
```

Field guidance:

- `--status` — `todo` (default) / `doing` / `blocked` / `done` / `cancelled`.
- `--priority` — `p0` / `p1` / `p2` / `p3`.
- `--project` — free-text grouping label. Reuse stable names.
- `--assignee` — who should DO the task (human or agent).
- `--agent` — who CREATED or last updated the task. Use a stable handle for
  the current session.
- `--due` — `YYYY-MM-DD` or ISO datetime.
- `--notes` — free text.

Capture the returned `id` if the task may be referenced later.

### List tasks

```bash
$PM list                              # default: active only, sorted by status+priority
$PM list --project auth-refresh
$PM list --status doing
$PM list --assignee ash
$PM list --agent claude-code-ash
$PM list --all                        # include done/cancelled
$PM list --include-deleted            # include soft-deleted
```

### Read / update / mark done

IDs accept any unique 4+ character prefix.

```bash
$PM get af744fbc
$PM update af744fbc --status doing
$PM update af744fbc --priority p0 --notes "scope grew"
$PM done af744fbc
```

### Comment (timestamped append to `notes`)

```bash
$PM comment af744fbc "Branch fix/login-redirect ready for review" \
  --agent claude-code-ash
```

### Soft delete / restore

```bash
$PM delete af744fbc
$PM restore af744fbc
```

## Workflow recipes

### Hand off work to a future session

1. `$PM create "<what to do>" --status todo --agent <me> --assignee <next handler> --notes "<context>"`
2. Tell the user the returned task id so they can reference it.

### Inherit work from a prior session

```bash
$PM list --assignee <me> --status todo
```

For each, run `$PM update <id> --status doing --agent <me>` when starting.

### Surface blockers

```bash
$PM list --status blocked
```

### Daily review

```bash
$PM list --all | python3 -c "
import sys, json, datetime as d
items = json.load(sys.stdin)
today = d.date.today().isoformat()
for t in items:
    if (t.get('updated') or '').startswith(today):
        print(t['status'], t['title'])
"
```

## Output contract

`list`, `get`, `create`, `update`, `done`, `comment` all return a task object
of this shape:

```json
{
  "id":            "<uuid>",
  "title":         "string",
  "status":        "todo|doing|blocked|done|cancelled",
  "priority":      "p0|p1|p2|p3|null",
  "project":       "string|null",
  "assignee":      "string|null",
  "due":           "ISO datetime|null",
  "agent":         "string|null",
  "notes":         "string",
  "created":       "ISO datetime",
  "updated":       "ISO datetime",
  "deleted_at":    "ISO datetime|null",
  "fulcra_userid": "<uuid>"
}
```

`list` returns an array of these. `delete` and `restore` return
`{"id": "...", "deleted": true}` or `{"id": "...", "restored": true}`.

## Boundaries

- Do not operate on annotations that are not PM tasks. The script enforces this
  via the `_pm:1` envelope marker — respect the error rather than working around
  it.
- Do not call Fulcra annotation endpoints directly when the script covers the
  operation. Direct calls bypass the envelope schema and may corrupt the task.
- Do not use `--agent` to impersonate a different agent. Use the current
  session's stable handle.

## Additional resources

For deeper context, consult the bundled references on demand:

- **`references/data-model.md`** — exact annotation/envelope schema, full
  Fulcra Life API write surface, the `303 + Location` response quirk.
- **`references/limits.md`** — failure modes, recovery procedures, environment
  variables, manual API debugging recipes.

The wrapper script itself: **`scripts/pm.py`**. Read it when behavior is
unclear or when extending the skill.
