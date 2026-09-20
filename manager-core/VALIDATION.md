# Validation

## Current Result

The final local core test gate passed:

```text
Test Files  23 passed (23)
Tests       162 passed (162)
```

The gate covers the retained account-lease, retry, model-routing, rate-limit, internal-SSE, Claude request/response, manager-core patch, and control-service tests selected by `vitest.config.mjs`.

The Windows ephemeral-port test harness previously encountered a fetch forbidden-port collision. It now uses Node's HTTP client for local control requests. The complete rerun passed. Python regression tests also passed: 77 tests, including the new bridge and same-origin controls.

Fresh-source preparation and forward-patch application passed from a newly archived pinned source tree.

The isolated runtime smoke also passed all eight checks:

- Original gateway started with an isolated empty store.
- Authenticated control manifest was written.
- Control authentication and browser-Origin rejection worked.
- A request larger than 1 MiB reached the original service.
- The 32 MiB body limit returned `413` for an oversized request.
- Confirmed stop shut the isolated core down cleanly.
- A copied synthetic encrypted key and Local State survive two starts with the same userData.
- A corrupted imported key is rejected without changing the key or database.

The smoke run uses temporary `AGM_CORE_DATA_DIR` and `AGM_CORE_USER_DATA` directories, random local credentials, loopback ports, and an empty account store. It does not read, migrate, or export real account data.

The migration bootstrap preflight fails closed when an existing `.mk` cannot be decrypted, before real repositories are opened. Electron `userData` and `sessionData` are set synchronously before any await so secure storage is initialized against the intended isolated directory; this prevents development-mode key rotation during migration setup.

Synthetic UI checks passed at desktop `1440` and mobile `390` viewports without overflow. The checked flows were next-account, refresh with an `83%` quota display, offline confirmed stop, and relay return behavior. These are synthetic UI results, not live-provider verification.

## Reproduce

```powershell
npm install --legacy-peer-deps
npm run prepare:native
npm test
npm run build
npm run test:runtime
npm run verify:core
```

Run `prepare:source` only for a fresh generated `upstream/` directory with the pinned source checkout. Do not use it to overwrite a working source tree.

`tools/deploy-local.py` is intentionally excluded from the validation commands above. It is a parent-operated backup/cutover helper that reads local Manager data, creates an ACL-restricted local backup, and starts an isolated runtime only under the approved procedure. It retains the original `keytar` dependency for legacy encrypted records. Do not treat its existence as evidence that a production cutover occurred.

## Scope Limits

Synthetic tests do not establish an improved real-workload cache hit rate, long-running production stability, or live multi-account distribution. The existing account store contains one account, so multi-account behavior is covered by original-core tests, not by this local live sample.

## Approved Local Cutover

On 2026-09-20, after explicit user approval, the deployment helper backed up configuration, a SQLite-consistent account database and encrypted key material into an ACL-restricted, Git-ignored directory. Original installed Manager files and its account database were not replaced. A final SQLite snapshot was taken after stopping its writer.

Initial copied-store startup exposed an Electron readiness ordering bug: asynchronous directory creation allowed secure-storage initialization before the intended userData path was set. The isolated copied key was restored from the untouched backup. The launcher now configures userData/sessionData synchronously before any await and validates an existing key before original development-mode recovery code can run. The original `keytar` dependency was also restored.

The corrected copied store loaded successfully with one configured account and original `balance` scheduling. Minimal synthetic-text inference returned HTTP 200 before and after cutover, each with 89 uncached input tokens and one output token. These short requests are connectivity checks, not cache benchmarks.

The new gateway now owns loopback port 8045. One updated relay process owns 8400 and 8610, replacing the prior split processes. The integrated production web status reports `available: true`, `balance`, `official`, a 33,554,432-byte body limit and the expected account count. The control service binds only loopback port 18446. Production browser inspection also found no horizontal overflow at mobile width. Management actions were tested against synthetic accounts, not against the live account pool.
