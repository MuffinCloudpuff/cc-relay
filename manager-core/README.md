# Manager Core

`manager-core` is a headless wrapper around the retained Antigravity Manager v0.20.0 backend. It starts the original configuration, account persistence, account-lease, quota, retry, model-routing, and proxy modules from `upstream/`; it does not replace them with a single-account relay.

The retained source is pinned to `23696a2fe276b79009cb0f637f3af030b25c4b20`. The runtime keeps Electron only where the original secure storage and account services require it. It does not start the renderer, tray UI, updater, or desktop main entrypoint.

## Behavior

The gateway binds to loopback with a 32 MiB Fastify JSON body limit. The separate loopback control service uses a required Bearer token and exposes safe status plus `next-account`, `refresh-current`, and confirmed `stop` actions.

`next-account` preserves the original tray meaning: it selects the next account through `CloudAccountRepo.setActive`. It does not clear sticky sessions, alter the scheduler, or change preferred-account settings. Account selection, quota, retry, model mappings, and pool behavior remain owned by the original Manager modules.

The internal Gemini HTTP header profile defaults to `official`; set `AGM_CORE_HEADER_PROFILE=manager` only to use the Manager profile. This selection changes HTTP headers, not request-body identity, account selection, or model routing.

Original secure-storage compatibility retains both `keytar` and `@napi-rs/keyring`, as declared by the retained upstream package. They serve different original code paths: `keytar` remains the legacy master-key fallback when a safeStorage primary key cannot authenticate existing ciphertext. The native `keytar` package must be installed and packaged for the Electron runtime; it is external to the headless bundle. `prepare:native` currently prepares `better-sqlite3` only.

The headless bootstrap must set Electron `userData` and `sessionData` paths synchronously before any awaited work or access that can initialize Electron secure storage. This preserves the copied key material's original OS-crypt context and avoids the original development-mode recovery behavior that can rotate an undecryptable `.mk` file.

## Source And License

`upstream/` is generated from the pinned upstream revision. To create it in a clean tree with no existing `upstream/`, provide a checkout at that exact revision:

```powershell
git clone --branch v0.20.0 --single-branch https://github.com/Draculabo/AntigravityManager.git D:\path\to\manager-core-v0.20.0
git -C D:\path\to\manager-core-v0.20.0 checkout 23696a2fe276b79009cb0f637f3af030b25c4b20
npm run prepare:source -- D:\path\to\manager-core-v0.20.0
```

The preparation step archives the upstream `src`, package metadata, TypeScript configuration, and `LICENSE`, then applies reviewed patches from `patches/*.patch`. Use `npm run verify:core` to confirm the protected retained-core files still match `preserved-core.json`.

The retained upstream source is licensed under CC BY-NC-SA 4.0. Keep its `upstream/LICENSE`, preserve attribution and license notices, mark modifications, and comply with the non-commercial and share-alike terms when redistributing adaptations.

## Local Commands

```powershell
npm install --legacy-peer-deps
npm run prepare:native
npm test
npm run build
npm run test:runtime
```

`prepare:native` prepares `better-sqlite3` for the installed Electron version. `build` bundles the headless runtime and rejects a desktop entrypoint in the output.

## Deployment Helper

`tools/deploy-local.py` is a parent-operated local migration helper, not a smoke test. Its `prepare` action creates an ACL-restricted timestamped backup, uses SQLite backup for committed account data, and copies the existing encrypted master-key material into an isolated runtime directory. `start`, `status`, and `stop` operate through a loopback control manifest.

The helper necessarily reads local Manager configuration and account data when run. Do not use it for synthetic validation, do not export its generated backup or manifest, and do not run it without an approved backup/cutover procedure. `activate` takes a final database snapshot, switches the original gateway port, verifies inference, and restarts the relay/web process. Generated state, encrypted stores, keys and logs are private and ignored by Git.

## Installed Local Version

The approved local cutover completed on 2026-09-20. The gateway remains on `127.0.0.1:8045`, the relay on `127.0.0.1:8400`, and the integrated UI at `http://127.0.0.1:8610/#manager`. The original installed Manager and its data remain untouched for rollback. The new runtime owns a separate encrypted copy under `run/`, protected by a Windows ACL.

The original store currently contains one configured account; this is not a single-account implementation restriction. Original multi-account scheduling remains intact. The existing relay's session isolation, dynamic-token-hint normalization and signature recovery remain in the forwarding chain. The Manager panel represents the original tray, not the full account-import/settings desktop application.

To restart the new core after a confirmed UI stop, run from the repository root:

```powershell
python manager-core/tools/deploy-local.py start --port 8045
```

Do not run the old Manager concurrently on the same port. Before rebuilding, stop the core; do not overwrite the live bundle. Installation of the new core does not modify Windows login/startup settings.

### Rollback

Stop the new core first, then start the original installed application. The relay already targets the same port, so its provider configuration needs no change:

```powershell
python manager-core/tools/deploy-local.py stop
Start-Process -FilePath "$env:LOCALAPPDATA\antigravity_manager\app-0.20.0\antigravity-manager.exe" -WindowStyle Hidden
```

The original application's account database has not been replaced. Subsequent new-core account/quota updates belong to the separate `run/data` copy; do not overwrite either store to reconcile them without a fresh backup. The new web Manager panel is offline while the original desktop application is serving requests.
