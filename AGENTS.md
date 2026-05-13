# AGENTS.md — fulcra-pm

**Purpose:** This file is a self-contained instruction set for any AI agent
(Claude Code, OpenCode, Cursor, Aider, custom harness) that needs to
read/write tasks stored in Fulcra. It assumes nothing about prior context —
read this top-to-bottom and act on it.

## What this is

A shared task tracker backed by the [Fulcra Life API](https://api.fulcradynamics.com).
Tasks live server-side as `MomentAnnotation` records, so every agent that
authenticates to the same Fulcra user account sees the same list.

**Use it for:** persistent tasks across sessions, cross-agent handoffs,
"remember this for later", project tracking.
**Don't use it for:** ephemeral within-session checklists (use the harness's
local todo tool — e.g. Claude Code's `TodoWrite`).

## Setup (one-time per machine)

```bash
# 1. Install the uv package manager if not present
command -v uv >/dev/null || brew install uv         # macOS
# or: curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install the Fulcra CLI (provides the OAuth token)
uv tool install 'git+https://github.com/fulcradynamics/fulcra-api-python.git@add-cli'

# 3. Clone this plugin somewhere stable
git clone https://github.com/reversity/fulcra-pm.git ~/fulcra-pm

# 4. Authenticate (opens a browser; OAuth device flow)
fulcra auth login

# 5. Smoke test
python3 ~/fulcra-pm/skills/fulcra-pm/scripts/pm.py health
# Expected: {"ok": true, "status": 200, "api_base": "https://api.fulcradynamics.com"}
```

For Claude Code specifically (auto-discovery + autoload):
```bash
claude plugin marketplace add reversity/fulcra-pm
claude plugin install fulcra-pm@fulcra-pm-marketplace
```
Then `${CLAUDE_PLUGIN_ROOT}/skills/fulcra-pm/scripts/pm.py` is the canonical
script path inside any Claude Code session.

## Invocation contract

The script is stdlib-only Python. All commands emit JSON on **stdout**.
Errors emit JSON on **stderr** and exit non-zero. Pipe to `jq` or parse with
`python3 -c "import sys, json; ..."`.

```bash
PM="python3 /path/to/fulcra-pm/skills/fulcra-pm/scripts/pm.py"
```

### Always run health first if anything looks off

```bash
$PM health
# → {"ok": true, "status": 200, "api_base": "https://api.fulcradynamics.com"}
# If status != 200, re-auth: `fulcra auth login`
```

### Create

```bash
$PM create "Fix the login redirect bug" \
  --status todo \
  --priority p1 \
  --project auth-refresh \
  --assignee ash \
  --due 2026-05-15 \
  --agent <stable-handle-for-this-agent> \
  --notes "Reproduces on Safari 18"
```

Returns the created task as JSON. Capture `id` if the task may be referenced
later.

**Field semantics:**

| Field | Type | Values |
|---|---|---|
| `--status` | enum | `todo` (default) / `doing` / `blocked` / `done` / `cancelled` |
| `--priority` | enum | `p0` / `p1` / `p2` / `p3` |
| `--project` | string | Free-text label. Reuse stable names. |
| `--assignee` | string | Who should DO the task. |
| `--agent` | string | Who CREATED/last-updated the task. Different from assignee. Use a stable handle for your agent identity. |
| `--due` | string | `YYYY-MM-DD` or ISO datetime. |
| `--notes` | string | Free text. |

### List

```bash
$PM list                              # active only (hides done/cancelled)
$PM list --project auth-refresh
$PM list --status doing
$PM list --assignee ash
$PM list --agent <handle>
$PM list --all                        # include done/cancelled
$PM list --include-deleted            # include soft-deleted
```

Returns a JSON array. Sorted: doing → blocked → todo → done → cancelled, then
by priority, then by due date.

### Read / update / mark done

IDs accept any unique 4+ character prefix.

```bash
$PM get af744fbc
$PM update af744fbc --status doing
$PM update af744fbc --priority p0 --notes "scope grew"
$PM done af744fbc                     # shortcut for --status done
```

### Comment (timestamped append to `notes`)

```bash
$PM comment af744fbc "Branch fix/login-redirect ready for review" \
  --agent <stable-handle>
```

Appends a line `[<iso-timestamp>] <agent>: <text>` to the task's `notes`.

### Soft delete / restore

```bash
$PM delete af744fbc                   # sets deleted_at, hidden from list
$PM restore af744fbc                  # clears deleted_at
```

## Output shape

`list`, `get`, `create`, `update`, `done`, `comment` all return objects of
this shape (or arrays of them, for `list`):

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
  "notes":         "string (may include timestamped comment lines)",
  "created":       "ISO datetime",
  "updated":       "ISO datetime",
  "deleted_at":    "ISO datetime|null",
  "fulcra_userid": "<uuid>"
}
```

`delete` and `restore` return `{"id": "...", "deleted": true}` /
`{"id": "...", "restored": true}`.

## Agent etiquette

1. **Identify yourself.** Pass a stable `--agent` handle when creating or
   commenting (e.g. `claude-code-<user>`, `opencode-<machine>`,
   `aider-<repo>`). Used for trace/audit; not security.
2. **Don't touch non-PM annotations.** The script enforces this via a
   `"_pm":1` marker in the task description. If you get
   `"annotation <id> is not a PM task"`, the annotation is real user data —
   don't operate on it, even if it looks like a task.
3. **Don't bypass the wrapper.** Direct calls to the Fulcra annotation
   endpoints can corrupt the envelope schema. Use `pm.py` commands.
4. **Don't impersonate other agents.** Use your own handle in `--agent`.
   `--assignee` is the right field to "delegate to" another agent.
5. **Hand off explicitly.** When ending a session with unfinished work,
   create or update tasks with `--status todo --assignee <next handler>`
   and tell the user the task IDs.

## Triggers — when an agent should reach for this

Look for user intents like:
- "add a task to / remember to / track that ..."
- "what am I working on / what's <agent> working on"
- "mark <id or title> as done / blocked / doing"
- "list my tasks / list project X / what's blocking us"
- "hand this off to the next session / agent"
- "assign this to <name>"

Don't reach for it when the user wants a within-conversation checklist —
use the harness's local todo tool for that.

## Failure modes & recovery

| Symptom | Cause | Fix |
|---|---|---|
| `fulcra CLI not on PATH` | uv tool not installed or PATH issue | `uv tool install 'git+https://github.com/fulcradynamics/fulcra-api-python.git@add-cli'` then ensure `~/.local/bin` is on PATH |
| `pm health` returns non-200, or HTTP 401 on any command | Token expired/missing | Run `fulcra auth login` (interactive — surface URL to user) |
| `id prefix 'af' is ambiguous (N matches)` | Too-short prefix | Ask user for more characters |
| `annotation <id> is not a PM task` | The id is a real Fulcra annotation, not a task | Refuse the operation; tell user it's not a PM task |
| `WARNING: FULCRA_API_BASE is set to '<host>', not fulcradynamics.com` | Hostile/test env var redirected the API base | Check `FULCRA_API_BASE` env; unset unless intentional |

## Critical constraints — read before doing anything cross-account

1. **Each user/machine must `fulcra auth login` separately.** Tokens are
   per-installation.
2. **Different Fulcra users = different task lists.** The list endpoint
   returns only annotations owned by the authenticated `fulcra_userid`.
   "Same task list" only happens when everyone authenticates as the **same
   Fulcra user account**.
3. **`v1alpha1` API.** The underlying Fulcra annotation endpoints are
   `/user/v1alpha1/annotation`. Subject to change. If you see schema errors,
   check `https://api.fulcradynamics.com/openapi.json`.
4. **Tasks share the Fulcra annotation catalog.** They show up in the Fulcra
   Context iOS/web app alongside real annotations like "Neck Pain" or
   "Coffee". The `"_pm":1` marker keeps the script from confusing them, but
   nothing prevents the Context UI from showing them.
5. **No server-side filtering.** `list` fetches all annotations and filters
   client-side. Fine for hundreds; slow at thousands.

## Quick worked examples

### Hand off work at end of session

```bash
$PM create "Continue the auth refactor in branch feat/auth-v2" \
  --status todo --priority p1 \
  --assignee "next-claude-session" \
  --agent "claude-code-ash" \
  --notes "Stopped after wiring the OAuth callback. TODO: update tests in tests/auth/."
```

Surface the returned id to the user.

### Resume work in a new session

```bash
$PM list --assignee "next-claude-session" --status todo
# Pick one
$PM update <id> --status doing --assignee me --agent "claude-code-ash-2026-05-13"
$PM comment <id> "Picked this up; reviewing prior notes." --agent "claude-code-ash-2026-05-13"
```

### Surface blockers across all projects

```bash
$PM list --status blocked
```

### What changed today

```bash
$PM list --all | python3 -c "
import sys, json, datetime as d
items = json.load(sys.stdin)
today = d.date.today().isoformat()
for t in items:
    if (t.get('updated') or '').startswith(today):
        print(t['status'], '-', t['title'])
"
```

## Source of truth

- **Repo:** https://github.com/reversity/fulcra-pm
- **Script:** `skills/fulcra-pm/scripts/pm.py` (stdlib-only Python)
- **Skill prompt:** `skills/fulcra-pm/SKILL.md` (Claude Code skill format)
- **Detailed data model:** `skills/fulcra-pm/references/data-model.md`
- **Limits & recovery:** `skills/fulcra-pm/references/limits.md`
- **Fulcra OpenAPI:** https://api.fulcradynamics.com/openapi.json
