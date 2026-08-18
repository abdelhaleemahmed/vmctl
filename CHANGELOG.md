# Changelog

All notable changes to **vmctl** are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-08-19

Initial public release.

### Features
- **Config-as-Code** — export VM configurations to YAML or JSON and re-create
  them reproducibly.
- **Multi-provider architecture** — full VirtualBox support; libvirt/KVM and
  QEMU planned. A thin provider interface isolates the hypervisor.
- **Full lifecycle** — `list`, `read`, `start`, `stop` (graceful or `--force`),
  `status`, `edit`, `create`, `export`, `import`, `delete`, `validate`, and
  `batch` operations.
- **Dry-run by default** — `create`, `import`, and `batch create` print the exact
  provider commands they *would* run; add **`--apply`** to actually run them.
- **Validation** — configs are checked against the provider's capabilities
  before anything is built.
- Extensible providers (`vmctl.providers`), serializers (`vmctl.serializers`),
  and validators (`vmctl.validators`). Console entry point: `vmctl`.

### Changed (vs. earlier internal 1.x builds)
- **BREAKING:** the "actually do it" flag is now **`--apply`** (was `--execute`)
  on `create` / `import` / `batch create`. The internal provider kwarg was
  likewise renamed `execute` → `apply`. `--execute` is no longer accepted.
- `--version` now prints the author and contact, matching the sibling `slimv`
  project: `vmctl 2.0.0 By:Ahmed Abdelhaleem Email: ahmedhal@gmail.com`.

[2.0.0]: https://github.com/abdelhaleemahmed/vmctl/releases/tag/v2.0.0
