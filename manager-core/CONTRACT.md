# Retained Manager Core

Base: Draculabo/AntigravityManager v0.20.0, commit
23696a2fe276b79009cb0f637f3af030b25c4b20. upstream/ is generated original source.
Do not replace account lease, routing, retry, quota, OAuth, database or protocol
implementations wholesale. Original Electron main runtime retained; no renderer,
tray, desktop updater or GUI main.ts startup. Production must not be started or
real account stores read during development. Synthetic isolated tests only.

## Ownership

- Parent: package/build/preparation, docs, integration verification and deployment.
- Runtime worker: runtime/*.ts and test/runtime* only.
- Patch worker: original source edits under upstream/src/server/main.ts,
  upstream/src/modules/proxy-gateway/**; test/patch*; emit patches/ fixes as unified
  patches against original local reference, or tell parent exact edits to export.
- Web worker: manager_control.py, UI integration in cc_relay.py and ui.html,
  tests/test_manager_control.py. Preserve all existing dirty changes.

## Runtime

runtime/main.ts waits for Electron app ready then dynamically imports runtime
services only AFTER validating explicit AGM_CORE_DATA_DIR and AGM_CORE_USER_DATA
absolute paths. Never default to existing Manager data. Existing original
getAgentDir gets a narrow env override via a parent build adapter/patch. Set
app userData before any account/security import. Require AGM_CORE_API_KEY.
AGM_CORE_PORT default 8402. AGM_CORE_CONTROL_PORT default 18446. Loopback only.
AGM_CORE_CONTROL_TOKEN required; no plaintext Google credentials or exports.
AGM_CORE_CONTROL_FILE optional absolute manifest path; manifest {url,token,pid}
is ignored local-only run data written restricted and removed on orderly quit.
No runtime credential migration outside explicitly selected data directories.

Preserve ConfigManager, CloudAccountRepo, AccountLeaseService, ProxyModule,
bootstrapNestServer, stopNestServer. Keep config proxy fields incl balancing,
preferred accounts, model mappings and quota controls; override listening port
and local API key only. No initDatabase of the official IDE database.

## Management API

Separate loopback HTTP control service, authenticated Bearer token for ALL
routes. Reject browser Origin entirely (Python bridge is only caller). Bound
body <=16KiB and reject unknown fields/actions. Safe errors, no upstream/token
objects. GET /status and POST /action with {action:string, confirm?:boolean}.
Actions: next-account, refresh-current, stop. stop requires confirm:true, sends
response before shutdown. next-account matches original tray setActive, does
NOT clear sticky sessions or silently alter preferred account scheduling.
refresh-current uses original quota/token-refresh services; return safe state.

GET /status response:
{available:true, running:true, current_account:null|{id,email,models:[
 {name,percentage,reset_time:null|string}]}, accounts_count:number,
 gateway:{running:boolean,port:number,active_accounts:number},
 scheduling_mode:string, header_profile:string, body_limit_bytes:33554432}.
Unavailable Python bridge response has available:false,running:false,error
with safe static message. No fake quota0 for unknown data.

## Python/Web

GET /api/manager/status; POST /api/manager/action passes only whitelisted actions.
Python reads manifest from config manager_core_control_file or fixed
BASE/manager-core/run/control.json, no arbitrary URL from client requests.
Validate manifest URL loopback http, allowed path /, no userinfo/query/fragment;
disable proxies/redirects, short timeout, bounded replies; never expose token.
New mutating routes require same-origin Host/Origin validation and JSON; reject
cross-site fetch metadata. Existing UI same-origin requests work. Tests must
not contact production or existing credentials.
UI retains existing design, Manager tab/panel with current account, quota rows,
gateway and pool count/mode; icon refresh/next account, stop confirmation. Show
loading, no account, offline and errors. No credential inputs or token content.

## Patch Boundaries

32MiB Fastify JSON body cap and loopback bind. Header profile official|manager
(default official); captured UA antigravity/hub/2.15.0 (aidev_client;
os_type=windows; arch=amd64; cl=983516863); omit x-goog-user-project for official
only, preserve body envelope. Correct cached usage input=prompt-cache. Keep
per-turn embedded system messages chronological, not global prefix. Preserve
exact tool signatures; NEVER latest/heuristic signature for a different call.
Existing root cc-relay custom_modifier session/token/signature fixes stay active.

Need original multi-account tests, original protocol tests and local control/UI
tests. No production cutover or real store migration before explicit approval.
