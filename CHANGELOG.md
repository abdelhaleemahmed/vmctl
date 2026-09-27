# Changelog

All notable changes to vmctl are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file is the single source: the Sphinx documentation includes it rather than
restating it.

## [Unreleased]

Work from the remediation plan in [`PLAN.md`](https://github.com/ahmedhal/vmctl/blob/main/PLAN.md). Finding ids (F-nn, H-nn,
L-nn) refer to that document. Everything below was verified against a real
VirtualBox 7.1.18 host as well as by the test suite.

### Fixed — the export/import round trip

The tool's premise is that a VM exported to YAML can be recreated. Several
things prevented that:

- **Hand-written configs could not be imported at all.** Nothing created the
  storage controller a disk referenced, so `storageattach` failed with
  *"Could not find a controller named 'SATA'"* — including for the config in
  this project's own README. Controllers are now synthesised for any bus a disk
  uses (F-01).
- **EFI VMs were exported as BIOS** and recreated unbootable: the firmware value
  was compared case-sensitively against lowercase `efi`, while VirtualBox reports
  `BIOS`, `EFI`, `EFI32` and `EFI64` (F-02).
- **Every disk was reported as dynamically allocated.** VirtualBox 7.x prints
  `Format variant:`, not `Variant:`, so a fixed-size medium was recreated as a
  growing one (F-20).
- **Host-only adapters lost their interface.** The name was read from
  `hostonlyif<n>`; VirtualBox emits `hostonlyadapter<n>` (F-19).
- **VMs with an ISO attached could not be recreated.** A blank image was created
  for the optical drive and attached as a DVD, which VirtualBox rejects.
  Removable media are now attached, never created (F-04, F-18).
- **Windows disk paths were parsed with doubled separators**, because
  machine-readable output escapes backslashes (F-16).
- **Ten configuration fields were exported and then silently dropped** on create:
  `pae`, `nested_virt`, `hotplug`, `execution_cap`, `page_fusion`, `hpet`,
  `clipboard_mode`, `draganddrop`, and TPM (F-05).
- **Hyphenated settings were invisible**, so `nested-hw-virt` never reached the
  model (F-03).
- **`--secureboot` is not a VBoxManage option.** Requesting secure boot made the
  create fail partway, leaving a registered VM with no disks. Secure boot now
  goes through `modifynvram enrollmssignatures` and requires EFI firmware (F-17).
- **Audio was reported as enabled for every modern VM**, because `audio="default"`
  names the driver rather than switching sound on (F-21).
- **An unrecognised storage controller silently became SATA**, which let a floppy
  controller absorb disk attachments (F-15).
- **Output was not deterministic.** Controller order came from set iteration, so
  the same config could attach the system disk to a different controller between
  runs (F-14).
- New media are created in VirtualBox's configured machine folder instead of a
  hardcoded `~/VirtualBox VMs` (F-13).

### Fixed — validation and error reporting

- `vmctl validate` reports the field at fault instead of raising a `TypeError`.
  An empty file, an unknown key, a bad enum value, a wrong type and a missing
  required field each produce one sentence, the accepted values, and a
  "did you mean" suggestion (F-06).
- Loading a config no longer empties the mapping it was given (F-07).
- The validator is pure and reads the provider's capability limits instead of
  hardcoded numbers. It no longer silently modifies the configuration, its
  warnings actually fire, and it catches slot clashes, ports beyond a
  controller's port count, IDE master/slave limits, secure boot with BIOS
  firmware, and unsupported buses — before anything runs (F-08).
- Warnings go to stderr, so `vmctl import … | sh` stays usable.

### Fixed — CLI behaviour

- `vmctl edit` applies changes. It emits only the settings that differ, is
  dry-run by default with `--execute`, refuses a running VM, and reports changes
  it cannot make in place instead of ignoring them (F-10).
- `vmctl completion` prints a working script. It used to print an instruction
  containing `__VMCTL_COMPLETE` — one underscore too many (F-09).
- `vmctl batch create` resolves and checks the whole file first: names are
  required and must be unique, and existing VMs are detected before anything is
  created. It reports what was created, what failed and what was not attempted,
  with `--continue-on-error` to keep going (F-12).
- A declined `vmctl delete` exits non-zero instead of looking like success (L-01).
- `vmctl stop --wait SECONDS` waits for the guest to actually stop (L-06).
- `vmctl import`/`create --execute` check the name is free first (L-07).
- `BaseProvider.edit_vm` no longer defaults to deleting and recreating the VM,
  which destroyed its disks (F-11).

### Added

- **A second hypervisor: libvirt / QEMU-KVM.** `vmctl -p libvirt …` creates,
  reads, edits, starts, stops and deletes domains. The same config file works
  against either provider, and anything that does not translate is reported
  rather than dropped. Verified end to end on libvirt 11.10.0 / QEMU 10.1.0,
  including booting a domain.
- **Provider selection**: `--provider/-p`, `$VMCTL_PROVIDER`, or detection of
  whatever is installed. `vmctl providers` lists what is usable here and which
  one is the default. Providers register lazily, so a hypervisor whose tooling is
  absent costs nothing, and third-party providers can register through the
  `vmctl.providers` entry-point group.

- `DiskType.FLOPPY` and `StorageControllerType` values for NVMe, floppy, USB and
  virtio-scsi, so every bus and device kind VirtualBox supports can be
  expressed. Each bus/chipset pair and its port-count rules were verified on a
  live host.
- A test suite: 218 tests running in about two seconds with no hypervisor
  installed, driven by captured `VBoxManage` output.
- `scripts/capture-fixtures.sh` to record that output from a real host.
- `examples/` with the documented configurations, validated by CI.
- Continuous integration: tests on Python 3.8–3.12, black, flake8, mypy, both
  documentation builds, a packaging check, and the examples.
- A declared minimum VirtualBox version (7.0), reported with the version found
  when the installed one is too old (H-07).

### Changed

- `DiskConfig.controller_name` defaults to `None` rather than the literal
  `"SATA"`, which claimed a specific controller even for a disk on another bus.
  Configs that name `"SATA"` still work.
- The emitter uses `createmedium disk` rather than the deprecated `createhd`.
- `vmctl/core/exceptions.py` lost seventeen unused classes and three unused
  helpers, two of which shadowed Python builtins (H-08).
- The package version is single-sourced from `vmctl/__init__.py` (H-03).
- `.gitignore` no longer ignores all YAML everywhere, which had been hiding
  `tests/fixtures` and `.github/workflows` — the reason this project had no CI
  (H-01). Sphinx build output is no longer committed (H-02).

## [1.1.9] - 2026

- Fixed network adapter name read/restore for all connection types.
- Added Arabic Sphinx documentation, with RTL layout fixes.

## [1.1.8] - 2025

- **Fix**: VirtualBox CD-ROM drives backed by ISO images are correctly
  identified as `DVD` type instead of `HDD`.
- **Feature**: CLI migrated from argparse to Click with shell tab completion for
  bash, zsh and fish. VM names are completed live from VirtualBox.
- **Docs**: added `docs/features.md`, `docs/USER_GUIDE.md`, and the GitHub Pages
  landing page.
- **Docs**: added Sphinx developer documentation.
- **Docstrings**: Google-style docstrings across public classes and methods.

## 1.1.7 and earlier

See the [GitHub releases page](https://github.com/ahmedhal/vmctl/releases).
