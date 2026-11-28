# Local engine integration v1

Status: specified before implementation, 2026-11-24. The product decision is
`docs/decisions/headless-engine.md`. This is an engine data/control contract;
all presentation belongs to an independent cockpit. Examples use placeholders,
never real capabilities. Unknown major versions fail closed.

## Entry points and lifecycle

- `mentat run --repo PATH` and `mentat resume --repo PATH --session ID` run
  without a server or rendered report. Existing session limits and checkpoints
  apply; a completed invocation is not a whole-application proof.
- Add `--integration-file PRIVATE_DIRECTORY/connection.json` to either command
  to publish one optional loopback endpoint while that invocation runs.
- `mentat integration serve --repo PATH --session ID --connection-file
  PRIVATE_DIRECTORY/connection.json` serves saved state independently. It can
  queue feedback for an eligible noninvalidated session, but has no controller
  stop capability. Its lifetime does not own a controller.
- `mentat status --repo PATH --session ID --json` remains a bounded machine
  snapshot. Trusted local CLI evidence export/compare provides separately
  authorized raw evidence; the HTTP capability never grants arbitrary artifact,
  filesystem, command, process, network, policy or credential access.

The engine owns its existing authoritative SQLite state and cancellation.
Clients never own the controller or leases. Closing a connection, dropping a
client, server shutdown, slow polling or an expired snapshot cannot cancel
autonomous work. A requested stop is distinct from a confirmed checkpoint;
existing cancellation cleanup and unknown-remote reconciliation remain required.
Paused means the recorded invocation checkpoint has pending work; blocked means
unresolved work/gaps. Stopped and completed are recorded states, not claims of
proof. A DB marked running without an attached live controller is reported as
controller unavailable/unknown, never live execution confirmed. Resume is a
trusted CLI operation and creates a fresh controller/server generation without
resetting attempts, findings, authority or ambiguous remote dispatch.

## Local security and resource limits

Bind only IPv4 `127.0.0.1`, with an ephemeral port by default; reject foreign or
ambiguous Host headers. Generate at least 256 random capability bits per server
generation and require `Authorization: Bearer <capability>` on all data/control
routes. Use constant-time comparison. Tokens never appear in URLs, CLI output,
environment, server errors or request logs. Do not log requests. Bind a live
adapter to one session/controller generation; an inspector never gains live
control by reading a running status from disk.

Create the rendezvous file exclusively, no-follow, mode 0600, one link, owned by
the current user inside an owner-only mode-0700 directory. Open and verify its
directory without following symlink components, retain the directory descriptor,
and use dirfd-relative creation/cleanup; verify directory identity against
replacement. Permit only trusted nonwritable ancestors (the system temporary
directory may be a sticky ancestor, never the final private directory). Do not overwrite an
existing file, follow links, or write in a repository. It contains protocol major,
loopback base URL, bearer capability, ephemeral server ID and selected session
when known. Fsync it and keep its file descriptor open until shutdown to prevent
inode reuse if the path is replaced. Shutdown checks that the path still names
the published inode before removing it. Publish only after the listener and file
are ready. If secure creation or fsync fails, shut down the adapter without
disclosing a capability. A crash may leave a stale file. Clients treat failed
connectivity as unavailable and use a fresh trusted launch or file. A failed
connection is only an availability result. The trust boundary includes root and
same-user processes with access to the private files.

Reject every supplied Origin by default. An operator may explicitly allow exact
loopback HTTP origins for an independently hosted cockpit; reject `null`, wildcard,
credentialed origins and malformed origins. Requests without Origin support
native local clients. The operator selects at most 8 exact origins using repeated
`--allow-origin http://127.0.0.1:PORT` (or `http://localhost:PORT`) on the trusted
launch command; origin permission is never supplied by a client request.
Exact allowed-origin preflight may advertise only required methods/headers;
never wildcard or reflect unapproved origins. Clients do not send a bearer on
unauthenticated preflight. Authenticate
before expensive state or freshness work. No cookie authentication.

HTTP uses closed connections, a 3-second socket inactivity timeout and 10-second
request lifetime, a fixed maximum
of 8 concurrent connections, bounded header/body/response sizes, and a bounded
snapshot builder/cache. Reject chunked/ambiguous framing, duplicate sensitive
headers, unexpected Content-Type, extra JSON keys and nonfinite JSON. Limits are
discoverable and enforced: request body 16 KiB, feedback UTF-8 8 KiB, key 128
characters matching `[A-Za-z0-9._~-]{1,128}`, page 1..100 items, response 256 KiB, materialized snapshot 8 MiB,
at most 2 cached snapshots/16 MiB total, TTL 30 seconds, one concurrent build.
Bound the raw SQLite capture before loading it (32 MiB total serialized data,
20,000 events and 10,000 jobs/records); over-capacity returns an explicit error,
not a truncated or passing assurance result. Individual public strings and
collections are bounded with explicit omission metadata; no silent loss of
assurance qualifications. Identity/status/freshness, assumptions and proof scope
cannot be silently shortened; a necessary qualification that cannot fit makes
the record explicitly incomplete/omitted or fails capacity. Encoded resource items and page arrays each have a further 240,000-byte ceiling.
A single record that cannot fit yields a deterministic
capacity error or an explicitly incomplete omitted record, never endless paging.
Limit JSON nesting to 16, decoded collection items to 2,000, headers to 16 KiB,
and admit at most 30 requests/second and 2 snapshot builds/second per server.
Apply a 10-second operation budget, a killable trusted projection child with the
remaining budget, and a 2-second SQLite busy/read deadline. Parent serialization
and parsing are bounded to 32 MiB input/8 MiB output and checked against that
budget; trusted parent Python CPU is not forcibly preemptible. An overdue result
never returns as successful assurance. This is bounded operation/cleanup behavior,
not a universal hard real-time theorem.
Release read transactions before external freshness work. Admission failure
must not hold a controller write lock. Further narrower implementation limits must be
documented and exposed before acceptance. Slow/disconnected clients must not
block engine work, exhaust threads, grow state without bound or transfer control.
The raw-capture ceiling is a v1 availability limit: a very large saved session
may be permanently unsnapshotable through v1. Retain its authoritative state and
use separately trusted local inspection; retry/paging does not bypass the cap.
The incremental changes query has its own bounds and does not collect a full
snapshot or inherit the 20,000-event total-history ceiling.

## Discovery, snapshots and updates

All successful responses are JSON objects with `api_version: 1`, `server_id`,
durable opaque `store_id` and selected `session_id` (null while starting).
Identifiers are data, not bearer secrets. Routes outside `/v1` return an error;
there is no page, static asset, report or filesystem-serving route.

`GET /v1/capabilities` returns protocol/version, supported resources, configured
limits, exact optional controls, controller availability and restart semantics.
For example:

```json
{"api_version":1,"server_id":"SERVER","store_id":"STORE","session_id":"SESSION","capabilities":{"snapshots":true,"changes":true,"feedback":true,"stop":false,"raw_artifacts":false},"controller":{"availability":"unavailable","generation":null},"limits":{"page_items":100,"snapshot_ttl_seconds":30}}
```

`GET /v1/snapshot` creates a bounded immutable public materialization from one
SQLite read transaction and returns snapshot ID, expiry, event high-water,
change cursor, observation time, session/lifecycle/revision summary, freshness
observation and each resource count. The initial capture and journal watermark
are consistent. Git worktree, policy and tool observations are external and
cannot be atomically locked with SQLite: check before/after projection and mark
changed/unavailable identities stale/unknown or refuse/retry. Never claim a
cross-system atomic snapshot. Expose `observed_at` and a recommended refresh
interval of at most 5 seconds; clients must periodically refresh even when no
journal event occurs. A page is an as-observed historical snapshot, never an
unlimited lease on current assurance.
Within the freshness window, an unchanged datastore and controller can reuse a
published snapshot. Reuse preserves its original observation time and expiry.
Freshness reads use trusted bounded Git invocation with sanitized environment
and configuration, disabled hooks/fsmonitor/external diff/textconv and prompts,
no networking and strict subprocess deadlines. Repository configuration must not
execute helpers merely because a client requests status; unavailable inspection
produces unknown/stale, never current assurance.

`GET /v1/snapshots/SNAPSHOT/RESOURCE?offset=0&limit=100` pages that exact
materialization with stable order, offset, total, next offset and omission data.
Resources include requirements, claims, jobs, findings, decisions, evidence and
inventory. Inventory lists committed paths with a status and reason. A path is
mapped when a current reviewed requirement cites that exact path and base.
Expired/evicted/foreign snapshot returns explicit reset-required. A client
reacquires a snapshot instead of mixing pages. New capture failures do not alter
existing engine results.

`GET /v1/changes?cursor=CURSOR&limit=100` returns bounded durable event notices
in increasing global SQLite sequence order, a high-water, next cursor and
`has_more`. Sequence gaps between sessions are valid. Cursor binds durable store,
session, ephemeral server generation and scanned sequence; reject malformed,
foreign, ahead-of-head or previous-server cursors with reset-required. Notices
are typed invalidations with sequence/kind and allowlisted opaque related IDs,
never raw journal payloads. Advance over all scanned rows even when an event is
not disclosable; no last-1000 window or silent omission. Reconnect within the same
server uses the cursor; after restart reauthenticate from the private rendezvous
and reacquire a snapshot. Snapshot cursor starts at the selected session's
greatest committed sequence in that SQLite capture (zero if none), including
undisclosed events. Each changes call fixes its session-specific high-water once;
scan only rows after the cursor up to that mark, advance through all scanned rows,
and set `has_more` if further selected-session rows remain at that mark. An empty
public notice list may still advance its cursor. V1 is bounded polling, not
streaming/SSE or a push lease.

### V1 message schema and examples

These examples describe supported shapes, not results from an executed session.
`SERVER`, `STORE`, `SESSION`, `SNAPSHOT`, `CURSOR` and evidence/claim IDs are
placeholders. Timestamps are finite UTC Unix seconds. Required common success
fields are `api_version` (integer 1), `server_id` (opaque string), `store_id`
(durable opaque string) and `session_id` (opaque string, nullable only while
starting). Missing engine data is absent with `unavailable_fields`, never a
fabricated value. Empty collections are arrays, not null. Clients reject unknown
major versions and tolerate additional v1 response fields; requests reject extra
fields. Status, freshness and assurance method are separate fields.

| Message | Required payload fields | Semantics |
| --- | --- | --- |
| Snapshot | snapshot_id:string, expires_at:number, observed_at:number, high_water:integer>=0, cursor:string, session:object, freshness:object, counts:object | Session includes actual id/status/base/head and explicit controller availability; never raw repo/policy. Counts name all seven resources. |
| Resource page | snapshot_id:string, resource:string, offset:integer>=0, total:integer>=0, next_offset:integer or null, items:array | Omitted fields/items carry explicit `omissions`; next_offset null means this materialization's end, not complete assurance. |
| Resource item | id:string, status:string, freshness:string, unavailable_fields:array | Other fields are narrowly allowlisted supported engine fields; absent proof/scope is not proof. Requirement/specification source paths and exact safe links are optional. |
| Changes | high_water:integer>=0, cursor:string, has_more:boolean, events:array | Each notice requires seq:integer>0 and kind:string; optional related_ids is a bounded array of opaque identifiers. No raw payload. |
| Receipt | receipt:object | Receipt requires request_id/action/status/session_id/event_seq/controller_generation; controller_generation may be null for saved feedback. No feedback body or capability. |
| Error | api_version:1, error:object | Error requires code:string, message:string, retryable:boolean, reset_required:boolean. Unauthenticated errors omit session/store identities. |

```json
{"api_version":1,"server_id":"SERVER","store_id":"STORE","session_id":"SESSION","snapshot_id":"SNAPSHOT","expires_at":1806080430,"observed_at":1806080400,"high_water":1204,"cursor":"CURSOR","session":{"id":"SESSION","status":"paused","base":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","head":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","controller":{"availability":"unavailable","generation":null}},"freshness":{"status":"unknown","observed_at":1806080400},"counts":{"requirements":1,"claims":1,"jobs":4,"findings":0,"decisions":2,"evidence":1}}
```

A claim page may include this item only when the engine actually derives the
conditional source result and its identity. The abbreviated example assumes its
full qualification fits; an implementation must preserve actual complete scope
and assumptions rather than replacing them with this example text:

```json
{"id":"CLAIM","status":"established-within-scope","freshness":"current","unavailable_fields":[],"evidence_ids":["EVIDENCE"],"formal_contract":{"path":"app.py","function":"format_survived","inputs":{"value":"int"},"requires":"value == 0 or value == 1","ensures":"result == ('Yes' if value == 1 else 'No')"},"proof_scope":{"method":"source-proof","revision":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","assumptions":["Actual recorded translator, solver and runtime assumptions appear here in full."],"claim_assumptions":[],"qualification":"Conditional on the reviewed contract, exact source identity, and stated translator, runtime and solver assumptions."}}
```

```json
{"api_version":1,"server_id":"SERVER","store_id":"STORE","session_id":"SESSION","high_water":1208,"cursor":"NEXT-CURSOR","has_more":false,"events":[{"seq":1208,"kind":"session_checkpoint"}]}
```

```json
{"api_version":1,"error":{"code":"reset_required","message":"Reacquire a snapshot for this server generation.","retryable":false,"reset_required":true}}
```

## Interpretable assurance and authorized detail

Allowlisted data includes stable requirement/claim IDs, area/provenance, bounded
statements and assumptions, supported formal contract and scope, safe repository
relative specification/source links at immutable revisions, job role/loop/state/
attempt and bounded errors, independent decision/finding associations, and
evidence method/status/input/contract/source/check/tool/policy digests where
supported by actual engine records. Preserve historical status and current
freshness separately. Do not invent missing links, assurance or agent activity;
report unavailable fields and explicit gaps.

Distinguish formal model proof, conditional source proof, tests, negative-control
counterexample/replay, established-within-scope, evidenced violation, unattempted,
unsupported, inconclusive and stale. A test pass or accepted review is never
proof. Keep preconditions, assumptions, unsupported language/implementation
boundaries and evidence revisions with every proof result. Invalidated/stale
records remain historical and cannot become current because a client refreshes
or copies a prior snapshot. A stale snapshot cannot authorize an engine effect.

Safe immutable GitHub source links derive only from trusted canonical repository
metadata and validated path/revision, never arbitrary supplied hrefs. Do not
serve source bytes or raw files. Drop raw prompts, stdout/stderr, commands,
environment, credentials, lease tokens, raw policy bodies, raw remote URLs,
host-path fields and raw feedback fields by construction. The capability
deliberately authorizes bounded project/agent-authored prose and relative paths;
these may themselves contain sensitive project information. Redaction of known
tokens, feedback, path-like strings and credential patterns is conservative best
effort, not a guarantee to identify every secret embedded in arbitrary prose.
Omit uncertain private content with an explicit indication; never echo bearer or
request bodies in errors/logs. No credential-store or raw-prompt source is
projected. Clients must protect authorized project data. Clients may obtain the same
allowlisted record by paging its resource; v1 has no raw-artifact/detail access.
Operators needing private details use separately authorized local evidence/state
tools and must review them before sharing. The capability is not authority to
read a named filesystem path. Explicit elision is an availability gap, not proof.

## Optional controls, validation and idempotency

`POST /v1/controls` accepts exactly one of:

```json
{"request_id":"UNIQUE-KEY","action":"feedback","payload":{"message":"Please clarify the intended input domain."}}
```

```json
{"request_id":"UNIQUE-KEY","action":"stop","payload":{}}
```

Control admission uses an immutable durable receipt keyed by session/request ID,
bound to action and canonical payload digest. Persist feedback event + receipt,
or stop-request event + receipt, atomically under `BEGIN IMMEDIATE`. Repeating
the same request returns the original receipt; conflicting action/payload returns
409. Lookup exact prior receipts before lifecycle/capacity admission so a retry
still succeeds after checkpoint or at the request limit. Bound requests per session (1,000), validate key/message/body, and never
delete receipts to reopen duplicate effects. Return 202 and `requested` or
`queued` as appropriate; neither means a loop transition, feedback consumption,
cancellation completion or successful merge. Feedback is optional and may be
consumed by a later eligible iteration; invalidated sessions reject new feedback.
For completed/paused/stopped/blocked saved sessions, queued means retained for a
possible trusted resume, never guaranteed consumption. Feedback is untrusted task
data, not instructions granting engine authority. It cannot override system or
review constraints, credentials, tools, policy, proof predicates or gates.
Clients must preserve request IDs across retries after an uncertain response.

Stop requires the same attached live session/controller generation, not already
stopping/terminal. Persist request first, then signal that exact coordinator;
no DB flag alone claims cancellation. A crash between receipt and signal leaves
a historical requested control with delivery unknown. It never replays into a
later resume/controller generation. Retries return its old receipt only. A saved
inspector rejects stop as controller unavailable. No API pause/resume, shell,
arbitrary job injection, approval, proof-state mutation, policy edit, attempt
reset, publication or merge control exists. Existing trusted CLI lifecycle and
engine qualification checks retain their authority.
Synchronize stop admission/signal with the adapter's lifecycle teardown, and
never replace its coordinator or generation during that lifetime.

Errors are bounded JSON with `api_version`, stable `error.code`, safe message and
retry/reset guidance; never exception strings containing paths/tokens. Use 400
invalid request, 401 unauthorized, 403 origin/host forbidden, 404 unknown route or
resource, 409 conflict/reset/ unavailable control, 413 request/output capacity,
429 concurrency/storage admission, and 503 temporarily unavailable. Authentication
failure does not reveal session existence. A control receipt acknowledges only
durable admission; timeout/shutdown may leave delivery unknown. Delivery or
completion requires separate positive engine observation. Rejected/capacity
errors are not assurance verdicts.

## Evidence bundle format

New machine-only manifests carry top-level strict integer `bundle_format: 2`.
Absence means legacy format 1 and requires the recorded `report.md`; never infer
the new format from a missing report. Any present value other than integer 2,
including booleans/null/1/unknown, fails closed. The marker is inside exact
manifest bytes already bound by the local receipt and acceptance manifest digest.
This is the existing local-state integrity model, not a signature against an
actor able to rewrite both manifest and receipt. Preserve every original check,
identity, qualification, proof, assumption and artifact hash in the manifest.
Project record `schema_version: 2` continues to describe assurance semantics;
bundle format independently describes storage representation for every run kind.
New writes reject reserved marker/artifact-map/manifest/report collisions, do not
generate report.md, and preserve all existing qualification and acceptance gates.
Legacy valid manifests/files/receipts are never rewritten. Integrity/format
validation occurs centrally before returning any saved run; compare/export expose
actual manifest references and never invent a report path. Test marker tampering,
missing/extra/modified files, invalid versions, both acceptance bindings and all
project/showcase kinds without regenerating immutable historical fixtures.

## Migration and acceptance evidence

Removed surfaces: `ui`, `--ui/--no-ui`, `evidence render`, `evidence compare --out`,
`evidence pr-summary`, all executable HTML/CSS/JS rendering and new Markdown
verification reports. No compatibility renderer is retained. Historical bundles
and reports stay immutable and valid under their original format. Replace UI
launches with an explicit private integration rendezvous and implement presentation
in the separate cockpit. CLI scripts using `report` paths or raw `status --json`
must adopt machine references/public fields. Old approval/verifier identities do
not silently carry to the changed engine; initialize/approve fresh authority as
required, retaining old state as history. Do not replace controller code in a
running session.

Acceptance requires independent architecture/security/data review, a separate
verifier, and real loopback E2E tests for the lifecycle, disclosure, ordering,
consistency, bounds, rejection, reconnect/restart, feedback and stop semantics
above. Full required gates and the actual qualifying self-run bind the final
reviewed identities. UI absence and successful checks do not imply whole-app proof.
