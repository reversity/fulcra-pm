#!/usr/bin/env python3
"""Fulcra-backed PM: store tasks as MomentAnnotations.

Designed for use as a Claude Code agent skill. Stdlib only.

Task storage shape:
    annotation.name        -> task title
    annotation.description -> JSON envelope: {"_pm":1, "v":1, status, priority,
                              project, assignee, due, agent, notes,
                              created, updated}
    annotation.id          -> task id
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

API_BASE = os.environ.get("FULCRA_API_BASE", "https://api.fulcradynamics.com")
LOG_LEVEL = os.environ.get("FULCRA_PM_LOG", "warn").lower()
SCHEMA_VERSION = 1

VALID_STATUSES = ("todo", "doing", "blocked", "done", "cancelled")
VALID_PRIORITIES = ("p0", "p1", "p2", "p3")

log = logging.getLogger("fulcra-pm")


def _setup_logging() -> None:
    level = {"debug": logging.DEBUG, "info": logging.INFO,
             "warn": logging.WARNING, "error": logging.ERROR}.get(
        LOG_LEVEL, logging.WARNING)
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Warn loudly if API base was redirected away from fulcradynamics.com — a
    # hostile env could otherwise siphon bearer tokens to an attacker host.
    if "fulcradynamics.com" not in API_BASE:
        print(
            f"WARNING: FULCRA_API_BASE is set to {API_BASE!r}, not "
            f"fulcradynamics.com. Access tokens will be sent to this host.",
            file=sys.stderr,
        )


class PMError(Exception):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def get_token() -> str:
    if not shutil.which("fulcra"):
        raise PMError(
            "fulcra CLI not on PATH. Install via "
            "'uv tool install git+https://github.com/fulcradynamics/"
            "fulcra-api-python.git@add-cli'"
        )
    try:
        r = subprocess.run(
            ["fulcra", "auth", "print-access-token"],
            check=True, capture_output=True, text=True, timeout=20,
        )
    except subprocess.CalledProcessError as e:
        raise PMError(
            f"fulcra auth failed (run 'fulcra auth login'): "
            f"{e.stderr.strip() or e.stdout.strip()}"
        ) from e
    tok = r.stdout.strip()
    if not tok:
        raise PMError("empty access token; run 'fulcra auth login'")
    log.debug("acquired token len=%d", len(tok))
    return tok


def _request(method: str, path: str, token: str, body: Any = None,
             allow_redirect: bool = False) -> tuple[int, dict, bytes]:
    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    log.debug("%s %s body_bytes=%s", method, url,
              len(data) if data else 0)
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):  # noqa: D401
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        resp = opener.open(req, timeout=30)
        status = resp.status
        hdrs = dict(resp.headers.items())
        body_bytes = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        hdrs = dict(e.headers.items()) if e.headers else {}
        body_bytes = e.read() if hasattr(e, "read") else b""
        if status >= 400:
            text = body_bytes.decode("utf-8", "replace")
            log.error("HTTP %d on %s %s: %s", status, method, url, text)
            raise PMError(
                f"HTTP {status} {method} {path}: "
                f"{text[:500] if text else e.reason}") from e
    log.debug("-> status=%d hdrs=%s", status,
              {k: v for k, v in hdrs.items()
               if k.lower() in ("location", "content-type")})
    return status, hdrs, body_bytes


def _extract_id_from_location(hdrs: dict) -> str:
    loc = hdrs.get("Location") or hdrs.get("location") or ""
    if not loc:
        raise PMError("no Location header on response")
    return loc.rstrip("/").split("/")[-1]


# ---------- Task envelope helpers ----------

def _envelope(status: str, priority: str | None, project: str | None,
              assignee: str | None, due: str | None, agent: str | None,
              notes: str | None, created: str, updated: str) -> str:
    payload = {
        "_pm": 1,
        "v": SCHEMA_VERSION,
        "status": status,
        "priority": priority,
        "project": project,
        "assignee": assignee,
        "due": due,
        "agent": agent,
        "notes": notes or "",
        "created": created,
        "updated": updated,
    }
    return json.dumps(payload, separators=(",", ":"))


def _parse_envelope(description: str) -> dict | None:
    if not description:
        return None
    try:
        d = json.loads(description)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or d.get("_pm") != 1:
        return None
    return d


def _build_annotation_body(title: str, env: str) -> dict:
    return {
        "annotation_type": "moment",
        "name": title,
        "description": env,
        "tags": [],
        "spec": None,
        "measurement_spec": None,
    }


def _flatten(annotation: dict) -> dict | None:
    """Turn a Fulcra annotation into a flat task dict, or None if not a PM task."""
    env = _parse_envelope(annotation.get("description", ""))
    if env is None:
        return None
    return {
        "id": annotation.get("id"),
        "title": annotation.get("name"),
        "status": env.get("status"),
        "priority": env.get("priority"),
        "project": env.get("project"),
        "assignee": env.get("assignee"),
        "due": env.get("due"),
        "agent": env.get("agent"),
        "notes": env.get("notes", ""),
        "created": env.get("created"),
        "updated": env.get("updated"),
        "deleted_at": annotation.get("deleted_at"),
        "fulcra_userid": annotation.get("fulcra_userid"),
    }


# ---------- API operations ----------

def list_annotations(token: str) -> list[dict]:
    status, _, body = _request("GET", "/user/v1alpha1/annotation", token)
    return json.loads(body)


def get_annotation(token: str, ann_id: str) -> dict:
    status, _, body = _request(
        "GET", f"/user/v1alpha1/annotation/{ann_id}", token)
    return json.loads(body)


def create_annotation(token: str, payload: dict) -> str:
    status, hdrs, _ = _request(
        "POST", "/user/v1alpha1/annotation", token, body=payload)
    if status not in (200, 201, 303):
        raise PMError(f"unexpected create status: {status}")
    return _extract_id_from_location(hdrs)


def update_annotation(token: str, ann_id: str, payload: dict) -> None:
    status, _, _ = _request(
        "PUT", f"/user/v1alpha1/annotation/{ann_id}", token, body=payload)
    if status not in (200, 204, 303):
        raise PMError(f"unexpected update status: {status}")


def delete_annotation(token: str, ann_id: str) -> None:
    status, _, _ = _request(
        "DELETE", f"/user/v1alpha1/annotation/{ann_id}", token)
    if status not in (200, 204):
        raise PMError(f"unexpected delete status: {status}")


def restore_annotation(token: str, ann_id: str) -> None:
    status, _, _ = _request(
        "POST",
        f"/user/v1alpha1/annotation/{ann_id}/cancel_deletion", token)
    if status not in (200, 204, 303):
        raise PMError(f"unexpected restore status: {status}")


# ---------- ID resolution ----------

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I)


def resolve_id(token: str, given: str) -> str:
    """Accept full UUID or any unique prefix (>=4 chars)."""
    if UUID_RE.match(given):
        return given
    if len(given) < 4:
        raise PMError("id prefix must be at least 4 chars")
    matches = []
    for ann in list_annotations(token):
        if _parse_envelope(ann.get("description", "")) is None:
            continue
        if ann["id"].lower().startswith(given.lower()):
            matches.append(ann["id"])
    if not matches:
        raise PMError(f"no task matches id prefix {given!r}")
    if len(matches) > 1:
        raise PMError(
            f"id prefix {given!r} is ambiguous ({len(matches)} matches)")
    return matches[0]


# ---------- Validation ----------

def _normalize_due(s: str | None) -> str | None:
    if not s:
        return None
    # Accept YYYY-MM-DD or ISO datetime; pass through anything that parses.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return f"{s}T23:59:59Z"
    try:
        datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except ValueError as e:
        raise PMError(f"invalid --due {s!r}: {e}") from e


def _validate_status(s: str | None) -> str:
    if s is None:
        return "todo"
    if s not in VALID_STATUSES:
        raise PMError(
            f"invalid status {s!r}; allowed: {','.join(VALID_STATUSES)}")
    return s


def _validate_priority(p: str | None) -> str | None:
    if p is None:
        return None
    if p not in VALID_PRIORITIES:
        raise PMError(
            f"invalid priority {p!r}; allowed: "
            f"{','.join(VALID_PRIORITIES)}")
    return p


# ---------- Commands ----------

def cmd_whoami(args) -> int:
    token = get_token()
    status, _, body = _request("GET", "/user/v1alpha1/info", token)
    print(body.decode("utf-8"))
    return 0


def cmd_create(args) -> int:
    token = get_token()
    status = _validate_status(args.status)
    priority = _validate_priority(args.priority)
    due = _normalize_due(args.due)
    now = _now()
    env = _envelope(
        status=status,
        priority=priority,
        project=args.project,
        assignee=args.assignee,
        due=due,
        agent=args.agent,
        notes=args.notes,
        created=now,
        updated=now,
    )
    payload = _build_annotation_body(args.title, env)
    new_id = create_annotation(token, payload)
    log.info("created task %s", new_id)
    ann = get_annotation(token, new_id)
    print(json.dumps(_flatten(ann), indent=2 if args.pretty else None))
    return 0


def cmd_list(args) -> int:
    token = get_token()
    out = []
    for ann in list_annotations(token):
        flat = _flatten(ann)
        if flat is None:
            continue
        if ann.get("deleted_at") and not args.include_deleted:
            continue
        if not args.all and flat["status"] in ("done", "cancelled"):
            if not args.status:
                continue
        if args.status and flat["status"] != args.status:
            continue
        if args.project and flat["project"] != args.project:
            continue
        if args.assignee and flat["assignee"] != args.assignee:
            continue
        if args.agent and flat["agent"] != args.agent:
            continue
        out.append(flat)
    out.sort(key=lambda t: (
        # status order: doing > blocked > todo > done > cancelled
        {"doing": 0, "blocked": 1, "todo": 2,
         "done": 3, "cancelled": 4}.get(t["status"], 5),
        t.get("priority") or "p9",
        t.get("due") or "9999-12-31",
        t.get("created") or "",
    ))
    print(json.dumps(out, indent=2 if args.pretty else None))
    return 0


def cmd_get(args) -> int:
    token = get_token()
    ann_id = resolve_id(token, args.id)
    ann = get_annotation(token, ann_id)
    flat = _flatten(ann)
    if flat is None:
        raise PMError(f"annotation {ann_id} is not a PM task")
    print(json.dumps(flat, indent=2 if args.pretty else None))
    return 0


def cmd_update(args) -> int:
    token = get_token()
    ann_id = resolve_id(token, args.id)
    ann = get_annotation(token, ann_id)
    env = _parse_envelope(ann.get("description", ""))
    if env is None:
        raise PMError(f"annotation {ann_id} is not a PM task")
    title = args.title if args.title is not None else ann.get("name")
    if args.status is not None:
        env["status"] = _validate_status(args.status)
    if args.priority is not None:
        env["priority"] = _validate_priority(args.priority)
    if args.project is not None:
        env["project"] = args.project or None
    if args.assignee is not None:
        env["assignee"] = args.assignee or None
    if args.due is not None:
        env["due"] = _normalize_due(args.due) if args.due else None
    if args.agent is not None:
        env["agent"] = args.agent or None
    if args.notes is not None:
        env["notes"] = args.notes
    env["updated"] = _now()
    payload = _build_annotation_body(
        title, json.dumps(env, separators=(",", ":")))
    update_annotation(token, ann_id, payload)
    print(json.dumps(_flatten(get_annotation(token, ann_id)),
                     indent=2 if args.pretty else None))
    return 0


def cmd_done(args) -> int:
    args.status = "done"
    args.title = None
    args.priority = None
    args.project = None
    args.assignee = None
    args.due = None
    args.agent = None
    args.notes = None
    return cmd_update(args)


def cmd_delete(args) -> int:
    token = get_token()
    ann_id = resolve_id(token, args.id)
    delete_annotation(token, ann_id)
    print(json.dumps({"id": ann_id, "deleted": True}))
    return 0


def cmd_restore(args) -> int:
    token = get_token()
    ann_id = resolve_id(token, args.id)
    restore_annotation(token, ann_id)
    print(json.dumps({"id": ann_id, "restored": True}))
    return 0


def cmd_comment(args) -> int:
    token = get_token()
    ann_id = resolve_id(token, args.id)
    ann = get_annotation(token, ann_id)
    env = _parse_envelope(ann.get("description", ""))
    if env is None:
        raise PMError(f"annotation {ann_id} is not a PM task")
    ts = _now()
    author = args.agent or "anon"
    line = f"[{ts}] {author}: {args.text}"
    existing = env.get("notes", "")
    env["notes"] = (existing + "\n" + line) if existing else line
    env["updated"] = ts
    payload = _build_annotation_body(
        ann.get("name"), json.dumps(env, separators=(",", ":")))
    update_annotation(token, ann_id, payload)
    print(json.dumps(_flatten(get_annotation(token, ann_id)),
                     indent=2 if args.pretty else None))
    return 0


def cmd_health(args) -> int:
    token = get_token()
    status, _, _ = _request("GET", "/user/v1alpha1/info", token)
    print(json.dumps({"ok": True, "status": status, "api_base": API_BASE}))
    return 0


# ---------- argparse ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fulcra-pm",
        description="Fulcra-backed task management for Claude agents.",
    )
    p.add_argument("--pretty", action="store_true",
                   help="pretty-print JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_health = sub.add_parser("health", help="check auth and connectivity")
    p_health.set_defaults(func=cmd_health)

    p_who = sub.add_parser("whoami", help="show authenticated user info")
    p_who.set_defaults(func=cmd_whoami)

    p_create = sub.add_parser("create", help="create a new task")
    p_create.add_argument("title")
    p_create.add_argument("--status", choices=VALID_STATUSES)
    p_create.add_argument("--priority", choices=VALID_PRIORITIES)
    p_create.add_argument("--project")
    p_create.add_argument("--assignee")
    p_create.add_argument("--due", help="ISO datetime or YYYY-MM-DD")
    p_create.add_argument("--agent", help="agent/session id that created this")
    p_create.add_argument("--notes")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="list tasks (default: hide done/cancelled)")
    p_list.add_argument("--status", choices=VALID_STATUSES)
    p_list.add_argument("--project")
    p_list.add_argument("--assignee")
    p_list.add_argument("--agent")
    p_list.add_argument("--all", action="store_true",
                        help="include done/cancelled")
    p_list.add_argument("--include-deleted", action="store_true",
                        help="include soft-deleted tasks")
    p_list.set_defaults(func=cmd_list)

    p_get = sub.add_parser("get", help="show one task")
    p_get.add_argument("id")
    p_get.set_defaults(func=cmd_get)

    p_update = sub.add_parser("update", help="update a task field")
    p_update.add_argument("id")
    p_update.add_argument("--title")
    p_update.add_argument("--status", choices=VALID_STATUSES)
    p_update.add_argument("--priority", choices=VALID_PRIORITIES)
    p_update.add_argument("--project")
    p_update.add_argument("--assignee")
    p_update.add_argument("--due")
    p_update.add_argument("--agent")
    p_update.add_argument("--notes")
    p_update.set_defaults(func=cmd_update)

    p_done = sub.add_parser("done", help="mark task done")
    p_done.add_argument("id")
    p_done.set_defaults(func=cmd_done)

    p_del = sub.add_parser("delete", help="soft-delete a task")
    p_del.add_argument("id")
    p_del.set_defaults(func=cmd_delete)

    p_rest = sub.add_parser("restore", help="restore a soft-deleted task")
    p_rest.add_argument("id")
    p_rest.set_defaults(func=cmd_restore)

    p_cmt = sub.add_parser("comment", help="append a timestamped note")
    p_cmt.add_argument("id")
    p_cmt.add_argument("text")
    p_cmt.add_argument("--agent", help="author handle")
    p_cmt.set_defaults(func=cmd_comment)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    p = build_parser()
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except PMError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # last-resort surface
        log.exception("unexpected error")
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}),
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
