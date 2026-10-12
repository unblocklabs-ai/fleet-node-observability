# npm release: 0.1.0

Published publicly as `@unblocklabs/fleet-node@0.1.0` on
`2026-09-28T06:21:26.070Z`; npm `latest` points to `0.1.0`.

- Package: https://www.npmjs.com/package/@unblocklabs/fleet-node/v/0.1.0
- Exact existing Bill-tested artifact published; no rebuild or repack.
- SHA-256: `be9e44d4fe76036f3d135d133f0debfbc0c8200dc545f50df94b63c9829a4d51`.
- Registry SHA-512 integrity matches the local artifact. An unauthenticated public
  tarball download also matches that SHA-256 exactly.
- npm scope ownership and public access verified. Browser login and separate publish
  authorization completed; no credentials were copied into the package or repository.
- Initial public metadata 404s cleared after propagation; no second publish attempted.
- Fresh registry installation into disposable prefix
  `/tmp/fleet-npm-release-smoke.iBR23A/prefix` succeeded; CLI `--version` returned
  `0.1.0` and `--help` succeeded with explicit Node 24.21.0.
- No fleet state or service changes, Git commit, push or GitHub release performed.

For production installation/update/rollback, use the explicit runtime and dedicated
user-owned prefix commands in `../README.md`, substituting the exact registry version
`@unblocklabs/fleet-node@0.1.0` for the local tarball. Existing installations must close
the maintenance gate with `fleet-node stop` before npm replacement, then run
`prepare`, `validate`, and `start`.
