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

### Security

- A VM name is interpolated into file paths and, for libvirt, into an XML
  document. A name containing `/` or `..` redirected where the domain definition
  was written. Names are now checked against a per-provider pattern, in the
  emitters themselves rather than only in the validator — a provider's
  `create_vm` is reachable directly, so a check the validator alone performs does
  not protect the files it writes. Found by the conformance suite.

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

- **A VM can describe the machine underneath the guest**: `arch`, `machine`
  (chipset), and a CPU with `sockets`/`cores`/`threads` and a `model` (`host`,
  `host-model`, or a named CPU). libvirt needs all of it; VirtualBox has none of it
  and says so rather than ignoring it.
- **Network cards are named by what the guest sees** -- `virtio`, `e1000`,
  `e1000e`, `rtl8139`, `pcnet`, `ne2k`, `vmxnet3` -- instead of by VirtualBox's
  chipset id. A model a hypervisor does not have is reported, and the exact Intel
  variant a VirtualBox VM had is preserved, so re-creating it gives the guest the
  same card.
- `vmctl validate` now catches a CPU topology that does not multiply out to the CPU
  count, which libvirt refuses outright.
- Fixed: warnings mentioned VirtualBox by name even when talking to libvirt.
- **A VM says which OS its guest runs in neutral terms**: `guest_os: ubuntu22.04`
  rather than a hypervisor's own identifier. The ids are libosinfo's, the ones
  virt-install and GNOME Boxes use, and each hypervisor translates -- VirtualBox
  creates `Ubuntu22_LTS_64`, libvirt records the guest OS in the domain's metadata,
  where it now survives a round trip for the first time. `ostype:` and raw
  hypervisor strings keep working.
- Fixed: exporting a VM whose guest OS was outside a forty-entry list produced a
  config that could not be imported -- VirtualBox reports a description
  ("Debian 12 Bookworm (64-bit)") and accepts only an id ("Debian12_64"). vmctl now
  knows all 227 types the product lists.
- `vmctl validate` now points out a misspelled guest OS, which it could not do
  before.
- **Storage is described as devices on a bus**, so a disk, a CD-ROM and a floppy
  drive are one list (`storage:`) rather than a list of "disks" that had to
  pretend. A device says what `kind` it is, which `bus` it hangs off and which
  `controller` it attaches to -- previously one field meant "bus" and another
  meant "controller", which is why `"SATA"` used to be a magic value.
- A controller now has both a portable `id` that devices reference and the
  `native_name` its own hypervisor uses, so the same file describes the same
  layout on another hypervisor.
- New device settings: `readonly`, `discard` (pass the guest's TRIM through),
  `hotpluggable`, and `provider_options` for a native detail with no neutral
  equivalent yet. Where a bus cannot carry one -- VirtualBox accepts hot-plug on
  SATA and USB only, and libvirt has no per-device flag at all -- vmctl reports
  it instead of leaving the setting looking applied.
- **Leaving `format` out now means "whichever this provider creates natively"**,
  so one file makes a VDI on VirtualBox and a qcow2 on libvirt. Settings that are
  not set are left out of an export rather than written as `null`.
- Fixed: a VM exported and re-imported had `--bootable off` on its storage
  controllers, so it could fail to boot. VirtualBox reports that setting and vmctl
  was not reading it.
- Fixed: `vmctl delete` on libvirt reported success but left every disk image on
  disk, because `virsh undefine --remove-all-storage` only removes volumes inside
  a storage pool. Images vmctl created are now removed; images attached from
  elsewhere are left alone.
- Configuration files and code written for 1.1.x keep working: `disks:`, `type:`,
  `variant:`, `controller_name:`, `port:`, `device:`, `DiskConfig` and
  `StorageControllerConfig` are all still accepted, and saving a file writes the
  current names.
- **A disk's kind, its bus, its format and its allocation are now four separate
  things.** `hdd` and `ssd` were never two kinds of device -- solid state is a
  flag on a disk (`nonrotational`), which is how both VirtualBox and libvirt
  model it. Configuration files written for 1.1.x keep loading: `type: ssd`
  becomes a disk with the flag set, and the old names stay importable.
- Solid-state disks are now actually created as such, and honestly: VirtualBox
  marks the attachment, libvirt sets a rotation rate -- and on a bus that cannot
  say it, such as virtio-blk, vmctl reports the setting as dropped instead of
  handing back a spinning disk.
- **virtio-blk** is now a bus vmctl knows. A libvirt domain using it used to be
  read back as virtio-scsi and re-created on the wrong bus, which moved the
  guest's `/dev/vda` to `/dev/sda`.
- Providers now state **where they keep disk images** through one contract method
  instead of each naming it differently, including whether each VM gets its own
  subdirectory and which path separator the target host uses.
- **A conformance suite every provider must pass** (`tests/conformance/`). One
  parameterised set of rules covering the provider contract, the capability
  declaration's internal consistency, deterministic emission, and that a VM name
  cannot break out of the commands or documents it is embedded in. It runs without
  a hypervisor, so a provider can be checked on a machine that has none.
- **A second hypervisor: libvirt / QEMU-KVM.** `vmctl -p libvirt …` creates,
  reads, edits, starts, stops and deletes domains. The same config file works
  against either provider, and anything that does not translate is reported
  rather than dropped. Verified end to end on libvirt 11.10.0 / QEMU 10.1.0,
  including booting a domain.
- **`vmctl migrate <vm> --to PROVIDER`** recreates a VM on a different
  hypervisor. Verified end to end: a VM on VirtualBox (EFI64, 2 vCPU, an IDE disk,
  a NAT adapter) was recreated on libvirt with all eight checked settings intact,
  its IDE disk moved to virtio-scsi and both changes reported. Dry-run by default,
  and it refuses unless you accept the changes with `--policy`, listing all of
  them at once.
  `--with-disks` converts and attaches the real images; when they are not readable
  from this machine vmctl names them instead of quietly making an empty VM.
- **`vmctl convert <source> <target>`** converts a disk image between formats,
  using whatever the selected provider converts with — `qemu-img` for libvirt,
  `VBoxManage clonemedium` for VirtualBox. Only formats that provider can write
  are offered. Dry-run by default.
- **`--policy strict|nearest|convert`** decides what happens when a
  configuration asks for something the hypervisor cannot do. `strict` (the
  default) refuses and names **everything** that would have to change, in one
  error rather than one per run; `nearest` substitutes the closest supported value;
  `convert` also converts disk images. Every
  substitution, drop and conversion is reported before anything runs — the point
  being that a VM never comes out quietly different from the one asked for.
- **Automatic device placement.** A configuration no longer has to say which
  port and device number each disk uses; vmctl assigns the lowest free position
  on the controller, deterministically, respecting IDE's master/slave pairing.
  Positions you do state are kept exactly. `port` and `device` now default to
  "unset" rather than `0`, which had made two devices that did not care where
  they went collide.
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
