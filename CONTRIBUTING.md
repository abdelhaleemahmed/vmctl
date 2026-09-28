# Contributing to vmctl

Thanks for your interest in improving vmctl! It's a small, focused tool that
manages virtual machines with config-as-code — contributions that keep it that
way are very welcome.

## Development setup

```bash
git clone https://github.com/abdelhaleemahmed/vmctl.git
cd vmctl
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1   |  Linux/macOS: source .venv/bin/activate
pip install -e ".[test]"
```

To actually drive VirtualBox you also need **VBoxManage** on your `PATH`
(installed with VirtualBox) — it is *not* needed for the unit tests.

## Running the tests

```bash
pytest
```

The unit suite is **fast and hypervisor-free** — it covers the deterministic
logic (the CLI parser, config round-trips through dict/YAML, and the validator).
Please add tests for any new logic, and keep VBoxManage/real-hypervisor calls
out of the unit tests (those paths are verified manually / by integration).

## Building the docs

```bash
cd docs && python -m sphinx -b html source _build/html
```

Docs are reStructuredText under `docs/source/`. Keep the build **warning-free**.

## Guidelines

- **Config-as-code first.** A VM is data; the tool exports, validates, and
  re-creates that data. State-changing commands stay **dry-run by default**.
- **Keep the provider seam thin.** Hypervisor-specific code lives under
  `vmctl/providers/<name>/`, behind the interface in `vmctl/providers/base.py`.
  The core, serializers, and validators stay provider-agnostic.
- Match the surrounding style (naming, docstrings, comment density).
- Update the docs and `CHANGELOG.md` when you change behavior.

## Areas of interest

- [ ] libvirt/KVM provider
- [ ] QEMU provider
- [ ] Snapshot management
- [ ] VM cloning with disk copy
- [ ] Network configuration templates

## Pull requests

1. Branch from `main`.
2. Make sure `pytest` passes and the docs build cleanly.
3. Describe what changed and why. Small, focused PRs are easiest to review.

## Reporting bugs

Open an issue with your OS, Python version, VirtualBox version
(`VBoxManage --version`), the exact command, and the output.
