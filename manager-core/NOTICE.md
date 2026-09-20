# Source and License

This build preserves backend modules from AntigravityManager by Draculabo:
https://github.com/Draculabo/AntigravityManager

Pinned release: v0.20.0. Commit: 23696a2fe276b79009cb0f637f3af030b25c4b20.
The original license is CC-BY-NC-SA-4.0, reproduced in LICENSE. Local runtime,
patches and integrations are provided under the same license. Source patches
must remain attributable and separately reviewable; this is not an upstream
official build or an attestation of the installed Manager binary.

Generated upstream/ contains the pinned source, not user account data. Only the
headless entrypoint and its reachable original backend modules are built.
Account scheduling and failover policies are intentionally not reimplemented.
