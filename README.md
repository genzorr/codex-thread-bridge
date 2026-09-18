# codex-thread-bridge

An independent MCP server that lets an agent create and message Codex sessions
through the **running App Server on the same host**.

Built for this workflow: an agent in an SSH-backed Desktop task creates another
session on the remote machine, and you open and continue that session in Desktop
on your local computer. The transport and Desktop continuation were demonstrated
with Codex 0.153.4 on Linux. This package provides a reusable MCP interface to that
transport; it does not restore or impersonate OpenAI's native Desktop tools.

## Install

Requires Python 3.11+ (and Git for isolated launches), [uv](https://docs.astral.sh/uv/),
and a running, authenticated Codex App Server with a Unix WebSocket control socket.
Run the bridge as the same
user, on the same host as that App Server. Linux is the validated host platform;
the Unix transport is not a Windows-native transport.

From a clone of this repository:

```sh
uv sync --frozen --no-dev
.venv/bin/codex-thread-bridge --help
codex app-server daemon version
```

Register the server **on the remote Codex host**, replacing the absolute path:

```sh
codex mcp add codex-thread-bridge -- /absolute/path/codex-thread-bridge/.venv/bin/codex-thread-bridge
```

Equivalent entry in that host's `~/.codex/config.toml`:

```toml
[mcp_servers.codex-thread-bridge]
command = "/absolute/path/codex-thread-bridge/.venv/bin/codex-thread-bridge"
tool_timeout_sec = 60
args = [
  "--default-permissions", "your-local-profile",
  "--default-approval-policy", "on-request",
  "--default-approvals-reviewer", "auto_review",
]
```

The three default flags are optional installation policy. The generic bridge does not assume a personal profile name. Without them, omitted creation arguments retain the conservative legacy behavior: read-only sandbox and approval policy `never`. An `on-request` default requires `auto_review`; the bridge never answers approval requests itself.

The same defaults can be supplied through the stdio MCP server environment. Command-line flags take precedence over these variables:

```toml
[mcp_servers.codex-thread-bridge.env]
CODEX_THREAD_BRIDGE_DEFAULT_PERMISSIONS = "your-local-profile"
CODEX_THREAD_BRIDGE_DEFAULT_APPROVAL_POLICY = "on-request"
CODEX_THREAD_BRIDGE_DEFAULT_APPROVALS_REVIEWER = "auto_review"
```

After bridge code or MCP configuration changes, request a reload from this checkout with `uv run --locked codex-thread-bridge-reload`. The command displays the target App Server socket and asks for `y/N` confirmation before sending anything; pass `--socket /absolute/path/to/socket` if you use a nondefault socket. An accepted response means App Server queued a refresh for loaded tasks, not that every current task has already received the new tool schema. Start a fresh SSH-backed task and check its MCP tool inventory before a live test. The command does not restart the App Server.

The default socket is `$CODEX_HOME/app-server-control/app-server-control.sock`,
with `CODEX_HOME` defaulting to `~/.codex`. Override it with `--socket /path/to.sock`.
This socket uses a WebSocket handshake, not newline-delimited JSON. No additional
API key is needed; the existing App Server owns its authentication and model usage.

## Tools

| Tool | Behavior |
| --- | --- |
| `get_capabilities` | Connect and report server identity and bridge limitations |
| `create_thread` | Create one durable session in an existing directory; optionally name it and send its initial prompt |
| `create_worktree_thread` | Create a locked, retained bridge-managed Git worktree at an explicit commit and start a task; no Desktop-managed lifecycle |
| `update_thread_permissions` | Apply one named permission profile or explicit legacy sandbox policy to an idle task |
| `send_message_to_thread` | Resume an explicitly selected idle thread without configuration overrides, then send one message |
| `steer_thread` | Append a message to one active turn with an exact turn ID precondition; does not resume or change settings |
| `list_threads` | Read a page of unarchived backend thread summaries |
| `read_thread` | Read metadata and a paginated history without resuming |
| `wait_thread` | Wait up to 50 seconds for the supplied recent turn ID |
| `get_goal` | Read persistent Goal state |
| `get_operation` | Recover a mutation receipt after a lost response or client restart |

Text limits apply to display content such as messages, previews, and summaries.
Pagination cursors, IDs, paths, and other protocol fields are returned unchanged.
For the first page, omit `cursor`. For later pages, pass `nextCursor` as the exact
string returned, even when it looks like JSON. The MCP cursor argument accepts a
string, not `null`; an empty string also selects the first page. Restart the MCP
server and rediscover its schemas after upgrading to this cursor handling.

Client-rendered tool names include the configured MCP server namespace. Tool
arguments and receipts are this bridge's API, not a drop-in copy of native Desktop
schemas. Clients should discover the tools and use their declared input schemas.

Example tool arguments (these are MCP calls, not shell commands):

```json
{
  "request_id": "demo-create-001",
  "cwd": "/absolute/path/to/project",
  "title": "Bridge validation",
  "prompt": "Do not use tools or edit files. Reply exactly: BRIDGE_READY"
}
```

Pass that object to `create_thread` when the bridge installation configures a named permission profile. To select a profile per call, add `"permissions": "profile-name"`. Keep the returned `threadId` and `turnId`;
use them with `wait_thread`. After checking the session in Desktop, use
`send_message_to_thread` with a **new** request ID for the intentional follow-up:

```json
{
  "request_id": "demo-message-001",
  "thread_id": "<returned threadId>",
  "message": "Do not use tools or edit files. Reply exactly: BRIDGE_FOLLOWUP_OK"
}
```

To steer a task while its turn is active, obtain that task's current turn ID from `read_thread` or an earlier creation/message receipt and call `steer_thread` with a new request ID:

```json
{
  "request_id": "demo-steer-001",
  "thread_id": "<target threadId>",
  "expected_turn_id": "<active turnId>",
  "message": "Focus on the failing test before continuing."
}
```

The App Server rejects the request if the turn has finished or another turn is active. An accepted receipt confirms dispatch to that turn, not that the agent has processed the message. Reuse the same request ID and inspect `get_operation` if the outcome is uncertain; do not send a new request ID to retry blindly.

Creation uses installation defaults when configured and otherwise defaults to `read-only` with approval policy `never`; explicit execution settings are documented below. Ordinary task creation should omit legacy `sandbox` arguments and use the configured named profile. Explicit `workspace-write`, `read-only`, and `danger-full-access` remain available for deliberately restricted or legacy-compatible tasks; obtain authorization for the chosen environment before calling. Omitted model/reasoning use the App Server's configured defaults. Initial dispatch is withheld if the returned cwd, permission profile, sandbox kind, or approval settings differ from the request. Compare the full returned permission receipt before sending further instructions.

## Delivery and recovery

All mutation tools require a stable `request_id`. The bridge records its intent
before calling the App Server. Repeating that ID with identical arguments returns
the retained receipt; using it with different arguments fails before any action.
Creation fingerprints the supplied directory path before filesystem resolution.
Replaying a retained request therefore works after that directory is removed or
its symlink target changes. The existing-directory requirement applies to new
creations; an intentional new action needs its own request ID.

| Receipt status | Meaning |
| --- | --- |
| `accepted` | Requested API steps returned successfully; a turn may still be running |
| `failed` | A known Git/API rejection or environment mismatch; inspect retained artifacts and IDs |
| `outcome_unknown` | Transport/client failure; some or all effects may have happened |
| `in_progress_or_unknown` | Operation is running, or the process stopped before recording its outcome |

`retrySafe: false` means **do not issue a new request ID to repeat the action**.
Reusing the same ID is safe while the ledger is retained. The bridge never retries
a sent mutation or automatically continues a partially completed create. If the
server created a thread but its response was lost, even its ID may be unknown.
This is conservative deduplication, not an exactly-once guarantee across the
server and the local ledger.

Receipts persist in `$XDG_STATE_HOME/codex-thread-bridge` (default
`~/.local/state/codex-thread-bridge`), in an endpoint-scoped SQLite database. Use
`--state-dir` to select a stable alternative. Keep this directory across restarts;
deleting it discards deduplication history. Newly created state files are private
to the current user. Receipts may contain conversation metadata/content; the
bridge adds no telemetry and does not publish them.

Socket paths are canonicalized, so symlink aliases to the same socket share a
ledger. When upgrading from a version that hashed the unresolved socket path,
stop older bridge processes and first start the updated bridge with each
previously configured socket spelling and the same state directory. This imports
that spelling's legacy ledger into the canonical ledger without deleting it.
Only then switch to another socket alias. Unknown historical spellings cannot
be recovered from hashed filenames. Conflicting receipts stop startup for manual
inspection rather than choosing one or dispatching again.

Earlier creation fingerprints used a resolved directory path. Legacy receipts
also accept the matching resolved path, so an unchanged symlink continues to
work. If that old symlink has already been removed or retargeted, the original
input spelling cannot be reconstructed and replay may report an argument
conflict. Use `get_operation` with the original request ID to inspect it; do not
create a new ID to retry delivery. New receipts use strict supplied-argument
matching and do not apply this legacy fallback.

Reading, listing, waiting, and Goal inspection never resume or modify a thread.
Messaging explicitly calls `thread/resume` without configuration overrides before `turn/start`. It refuses an active thread or a client-side approval policy; `on-request` with App Server Auto-review is supported. Concurrent external clients can still change a thread between those steps; the App Server remains authoritative. Active-turn steering calls `turn/steer` with an explicit turn ID and does not resume, interrupt, or override model, effort, cwd, or permissions. Unsupported client-side tool/approval requests receive an explicit error; continue those tasks in Desktop.

## Desktop compatibility

The backend and Desktop project registries can differ. In the observed SSH setup,
the backend returned no projects and `projectId: null` for threads that Desktop
correctly placed in its saved project. Supply `app_server_project_id` only when
that ID actually exists in `project/read`; the bridge never imports projects or
guesses Desktop project identities.

Verify the actual Desktop listing and UI for each launch. An empty session may
not appear until it receives an initial prompt. Existing-checkout visibility has
been demonstrated. `create_worktree_thread` implements **bridge-managed Git
worktrees**, with explicit ownership and manual cleanup. Desktop-managed worktree
creation is not supported. Bridge-managed isolated creation, follow-up messaging,
Desktop project listing, manual Desktop continuation and preservation passed
live checks on 0.153.4. Retention evidence covers the observed run.
No archive/delete, general shell-execution or Goal-setting tool is exposed.

The bridge targets the public/experimental API shape observed in 0.153.4. It
uses experimental paginated reads and fails with the actual API error on
incompatible servers. Tool discovery and Desktop visibility require a separate
installed-MCP acceptance test. The inspected App Server protocol exposes creation
in an existing directory, but no worktree-creation method; isolated launches use
local Git preparation followed by `thread/start`.

## Isolated launches

Use `create_worktree_thread` only after approval of the bridge-managed retention
contract, repository, commit, placement, permissions and exact prompt. Git creates
a detached, locked worktree; the bridge never archives, prunes or removes it.
There is no implicit dirty-file carryover, setup script, fetch or Goal creation.
It creates no branch, runs no stash/reset, and does not initialize submodules.
Git hooks, filesystem-monitor programs and checkout filters are disabled during
preparation, so LFS files remain pointers. The worktree shares the repository's
Git object store; the task sandbox does not restrict the bridge's Git preparation.

```json
{
  "request_id": "isolated-readiness-001",
  "source_repository": "/absolute/path/to/project",
  "starting_revision": "FULL_COMMIT_OBJECT_ID",
  "destination": "/absolute/path/to/retained-checkout",
  "worktree_mode": "bridge-managed-retained",
  "sandbox": "read-only",
  "expected_sandbox_policy": {"type": "readOnly", "networkAccess": false}
}
```

Replace the revision placeholder with a full lowercase commit ID (for example,
from `git rev-parse HEAD`), not a branch or abbreviated SHA. The commit must already
be available locally. The source must be an existing non-bare checkout root. Both
paths must be canonical and absolute; the destination must be absent with an
existing parent, outside repositories and Git metadata. This example creates a
readiness-only task without a model turn.
Optional `prompt`, `title`, `model` and `reasoning_effort` are exact overrides;
omitted model/reasoning preserve configuration defaults. For other sandboxes,
supply the complete expected policy, including all roots and network/temp flags.
The bridge verifies the response rather than silently accepting different settings.

Receipts contain `worktree`, `threadId`, `creation`, `permissionReceipt`,
`initialPrompt`, `phase`, and `recoveryRequired`. Partial receipts may lack task or
turn IDs. Reusing the same request ID replays its historical receipt without
recreating the worktree, starting a second task, or sending another message.
An environment or placement mismatch withholds the prompt and retains artifacts.
The caller owns further readiness checks and any later Goal handoff. Verify actual
Desktop project membership separately; backend project IDs do not establish it.

Failed or interrupted launches may leave a reservation directory, incomplete
checkout, Git metadata, task or dispatched turn. Inspect `get_operation`,
`git worktree list --porcelain` and the actual task listing before manual recovery;
never use a new request ID to blindly retry. The caller owns retention and cleanup.
A worktree lock protects against ordinary pruning, not external deletion, and a
replayed receipt is historical evidence rather than proof of current existence.

## Development

```sh
uv sync --frozen --group dev
uv run pytest
uv run ty check src
uv run ruff check .
uv run ruff format --check .
uv build
```

To check the real connection without creating a session:

```sh
uv run python scripts/check_connection.py
```

Tests use a fake App Server over a real Unix WebSocket and an MCP stdio client.
They do not start models or touch your Codex sessions. Implementation details and
contribution guidance are in [CONTRIBUTING.md](CONTRIBUTING.md).
If the system temporary directory is inside a Git checkout, pass pytest a
`--basetemp` outside that repository for isolated-worktree tests.

## References

- [Codex App Server protocol](https://learn.chatgpt.com/docs/app-server)
- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
- [Official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Missing SSH-hosted Desktop tools report](https://github.com/openai/codex/issues/40865)

Independent project, not affiliated with or endorsed by OpenAI. MIT licensed.

## Explicit execution permissions

`create_thread` accepts a named App Server permission profile in `permissions`. Before creating a task, the bridge calls `permissionProfile/list` with the resolved target `cwd` and rejects a missing or managed-disallowed profile. It sends `permissions` instead of legacy `sandbox`, records the requested profile and effective `activePermissionProfile`, and withholds the initial prompt if the returned profile or approval settings differ. Named profiles are a beta App Server field and require the bridge's existing `experimentalApi` opt-in.

Do not combine explicit `permissions` with `sandbox` or `sandbox_policy`. When an installation has a default named profile, an explicit legacy `sandbox` deliberately overrides that default for the one creation. `create_worktree_thread` retains its existing explicit legacy sandbox contract.

`update_thread_permissions` applies either one validated named `permissions` profile or one user-authorized, complete legacy `sandbox_policy` through `thread/settings/update`; the two forms cannot be combined. Supply the exact current `expected_identity` fields (`thread_id`, `cwd`, `model`, `reasoning_effort`), plus `approval_policy` and `approvals_reviewer`. Named profiles are validated through `permissionProfile/list` for the task cwd before the update, and the bridge verifies the returned `activePermissionProfile.id`. The task must be idle. The bridge records requested and before/after settings, verifies permissions and identity/workspace-root preservation, and never starts a turn as part of an update. A concurrent external client can race the final idle check; coordinate task ownership while updating. There is no atomic App Server idle compare-and-set.

Existing-directory `create_thread` accepts `sandbox_policy` with its matching `sandbox` kind and verifies the complete effective policy before the initial prompt. Defaults remain read-only and approval `never`. Explicit `on-request` requires `approvals_reviewer="auto_review"`, so the App Server owns escalation review. Optional `model` and `reasoning_effort` select the creation profile; explicit values are checked against the returned settings before any initial prompt is sent. A mismatch retains the task ID in the failed receipt without dispatching work. Omitted fields preserve configured defaults. Messaging carries no settings overrides and accepts `never` or `on-request` with Auto-review; human/client-side approvals remain unsupported and are never silently approved. This does not promise approval of every requested action.

Updates require a stable `request_id`, including failures. A replay returns the original receipt without dispatching again or continuing a partial operation. On unknown outcomes inspect `get_operation` and effective task settings before deciding on a separately authorized action. Profile changes are task-scoped; no global permissions or other tasks are modified. Existing bridge processes must reload the MCP to expose the new schema; an already-running model turn is not interrupted by editing the installed files.

Workspace writable roots must be directories. Socket/device files are not supported workspace roots in the Linux sandbox; they can break every shell launch. Keep directory roots scoped to the approved checkouts and caches, enable network explicitly, and use App Server Auto-review for authorized operations requiring escalation. The bridge does not grant approval on behalf of Auto-review or change other tasks.
