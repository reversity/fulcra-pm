# fulcra-pm

A rudimentary, agent-friendly project management system backed by the
[Fulcra Life API](https://api.fulcradynamics.com/openapi.json). Designed so
multiple Claude Code sessions (and other agents) authenticated to the same
Fulcra account see a shared, persistent task list.

Storage model: each task is a Fulcra `MomentAnnotation` whose `description`
holds a JSON envelope marked with `"_pm":1`. Fulcra's `POST/PUT/DELETE/
cancel_deletion` on annotations give us a full CRUD + soft-delete lifecycle for
free.

## What it is and isn't

| Is | Isn't |
|---|---|
| Shared across Claude sessions & agents that auth as the same Fulcra user | A real PM system (no SLAs, no users-as-first-class) |
| Persistent across machines (it's server-side) | A replacement for in-session `TodoWrite` |
| Stdlib-only Python wrapper, easy to embed in any agent | Optimized for thousands of tasks (no server-side filtering) |
| CRUD by JSON over HTTPS | A schema-stable API — the underlying endpoint is `v1alpha1` |

## Install

This is a Claude Code plugin. There are two install paths:

### Option A — install as a plugin (recommended, auto-discovers the skill)

1. **Install the Fulcra CLI** (one-time, for auth):
   ```bash
   uv tool install 'git+https://github.com/fulcradynamics/fulcra-api-python.git@add-cli'
   fulcra auth login    # opens browser, OAuth device flow
   ```

2. **Load the plugin in a Claude Code session:**
   ```bash
   claude --plugin-dir /Users/ashkalb/Developer/FulcraCentral/fulcra-pm
   ```
   Or copy the directory into a marketplace and `claude plugin install fulcra-pm`.

3. **Verify the skill loaded.** In Claude Code, ask:
   > "List my tasks."

   The session should pick up the `fulcra-pm` skill from the system reminder
   and invoke the wrapper script.

### Option B — run the wrapper directly (no plugin install)

```bash
python3 /Users/ashkalb/Developer/FulcraCentral/fulcra-pm/skills/fulcra-pm/scripts/pm.py health
```

A convenience symlink at `~/.claude/skills/fulcra-pm/scripts/pm.py` is also set
up so existing tooling that referenced that path keeps working.

## Usage

All commands emit JSON on stdout. Pipe to `jq` or parse in Python.

```bash
# Inside a plugin context: use ${CLAUDE_PLUGIN_ROOT}
PM="python3 ${CLAUDE_PLUGIN_ROOT}/skills/fulcra-pm/scripts/pm.py"
# Outside a plugin context: absolute path
PM="python3 /Users/ashkalb/Developer/FulcraCentral/fulcra-pm/skills/fulcra-pm/scripts/pm.py"

# Create
$PM create "Fix login redirect" --priority p1 --project auth --due 2026-05-15 --agent claude-ash

# List (default: active only)
$PM list                          # all active
$PM list --project auth
$PM list --status doing
$PM list --all                    # include done + cancelled

# Inspect / update (any unique 4+ char id prefix works)
$PM get af74
$PM update af74 --status doing
$PM update af74 --priority p0 --notes "scope grew"
$PM done af74                     # shortcut for --status done

# Comment (appends a timestamped line to notes)
$PM comment af74 "Branch fix/login-redirect ready for review" --agent claude-ash

# Soft delete + restore
$PM delete af74
$PM restore af74
```

## Data model

A task lives inside one Fulcra annotation:

```jsonc
{
  // Fulcra annotation envelope
  "id":             "<uuid>",
  "name":           "Fix login redirect",   // <-- task title
  "annotation_type":"moment",
  "tags":           [],
  "spec":           null,
  "measurement_spec": null,
  "created_at":     "<iso>",
  "updated_at":     "<iso>",
  "deleted_at":     null,                   // soft-delete flag

  // PM payload, encoded as JSON in `description`
  "description": "{\"_pm\":1,\"v\":1,\"status\":\"todo\",\"priority\":\"p1\",\"project\":\"auth\",\"assignee\":\"ash\",\"due\":\"2026-05-15T23:59:59Z\",\"agent\":\"claude-ash\",\"notes\":\"...\",\"created\":\"...\",\"updated\":\"...\"}"
}
```

The `pm.py list` command flattens this so the JSON is task-shaped, not
annotation-shaped:

```json
{
  "id": "af744fbc-…",
  "title": "Fix login redirect",
  "status": "todo",
  "priority": "p1",
  "project": "auth",
  "assignee": "ash",
  "due": "2026-05-15T23:59:59Z",
  "agent": "claude-ash",
  "notes": "...",
  "created": "...",
  "updated": "...",
  "deleted_at": null,
  "fulcra_userid": "..."
}
```

## What the Fulcra API actually exposes for writes

For reference — the script uses 4 of these 9 endpoints:

| Method | Path | Used by | Purpose |
|---|---|---|---|
| POST | `/user/v1alpha1/annotation` | `create` | Create task |
| GET | `/user/v1alpha1/annotation` | `list` | Fetch all annotations |
| GET | `/user/v1alpha1/annotation/{id}` | `get`, `update`, `comment` | Read one |
| PUT | `/user/v1alpha1/annotation/{id}` | `update`, `comment`, `done` | Update |
| DELETE | `/user/v1alpha1/annotation/{id}` | `delete` | Soft delete |
| POST | `/user/v1alpha1/annotation/{id}/cancel_deletion` | `restore` | Restore soft-deleted |
| POST | `/user/v1alpha1/tag` | — | (not used) |
| POST | `/user/v1alpha1/preferences` | — | (not used) |
| POST | `/ingest/v1/record` | — | (not used — for time-series data) |

## Known limits & gotchas

- **Tasks pollute your annotation catalog.** They show up in the Fulcra Context
  app alongside real annotations like "Neck Pain" or "Coffee". The `_pm:1`
  envelope keeps the script from confusing them, but you'll see them in the UI.
- **Multi-agent identity is honor-system.** Anyone with the same Fulcra account
  sees the same tasks; `--agent` and `--assignee` are free-text labels.
- **No server-side filters.** Listing fetches all annotations and filters
  locally. Fine for hundreds, slow at thousands.
- **`v1alpha1`.** Subject to change. The script will surface raw HTTP errors so
  schema drift is visible.
- **Soft-deletes accumulate.** `delete` flags `deleted_at` but the row stays.
  No bulk-purge command yet.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `FULCRA_API_BASE` | `https://api.fulcradynamics.com` | API host override |
| `FULCRA_PM_LOG` | `warn` | `debug` / `info` / `warn` / `error`, logs to stderr |

## Plugin layout

```
fulcra-pm/                          # plugin root
├── .claude-plugin/
│   └── plugin.json                 # plugin manifest
├── README.md                       # this file
└── skills/
    └── fulcra-pm/                  # the (only) skill
        ├── SKILL.md                # agent-facing skill definition
        ├── scripts/
        │   └── pm.py               # CRUD wrapper, stdlib only
        └── references/
            ├── data-model.md       # envelope schema, full Fulcra write surface
            └── limits.md           # failure modes, recovery, env vars
```
