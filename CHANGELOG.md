# Changelog

All notable changes to vmctl are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file is the single source: the Sphinx documentation includes it rather than
restating it.

## [Unreleased]

### Added

- The landing page gained two sections carried over from the page it replaced:
  **"What the file looks like"**, a complete annotated config, and **"Real-world
  scenarios"** -- disaster-recovery rehearsal, team dev environments, and
  infrastructure-as-code with drift. Both were rewritten rather than transcribed:
  the old ones used the 1.1.x spelling (`ostype:`, `disks:`, uppercase enums) and
  described a VirtualBox-only tool.

  The config shown is checked against all four providers, which is how it was found
  that the first version of it could not be: it named `qcow2` and put a disk on
  `virtio-blk`, and **no image format is creatable by all four** -- VirtualBox
  creates seven, libvirt and QEMU two, VMware one, and the intersection is empty. So
  the example leaves the format out, which each hypervisor then fills with its own,
  and the page says why instead of implying any file is portable everywhere.

### Removed

- `docs/index.html` and `docs/index_2.html`, the GitHub Pages landing pages from
  v1.1.8 (March). Neither had been served since the site moved to `landing/` plus
  Sphinx, nothing referenced them, and both still announced "vmctl - Config-as-Code
  CLI for VirtualBox" and told the reader to `pip install vmctl`. The two sections
  worth keeping are above; the rest is in git history.

## [4.0.1] - 2026-09-29

### Fixed

Four things in shared code that still assumed VirtualBox was the only provider. All
four are things a user meets before anything else, and all four were found by driving
the command line rather than by the suite -- one of them only by driving the real
shell-completion protocol, because the first, plausible-looking fix was still wrong.

Re-verified afterwards on all four hypervisors, since the changes were in shared code
rather than in any one provider: 21 real VMs, 336 checks, 0 failed.

- **`--disk-format` offered every provider VirtualBox's formats.** The list was built
  once when the module loaded, so `vmctl -p vmware import f.yaml --disk-format vdi`
  was advertised by `--help` and by tab completion, and then refused by validation --
  VMware creates one format, libvirt and QEMU two, VirtualBox seven. Completion now
  offers what the selected hypervisor can create, the help names `vmctl capabilities`
  rather than reciting one provider's answer, and a misspelling is caught against the
  vocabulary while "can this hypervisor create it" stays with the validator, which has
  a measured reason and honours `--policy`. Same for `convert --to`.
- **Tab completion offered VirtualBox's VM names whatever `-p` said.** It ran
  `VBoxManage list vms` itself, so on a machine with only libvirt it offered nothing
  and on one with both it offered the wrong list. It asks the provider now. Fixing
  that exposed a second half: Click parses options but does not call callbacks while
  completing, so `ctx.obj` is empty there and the first fix still answered for the
  default provider -- `-p` is now found whether the callback has run or not. Verified
  by driving the real bash-completion protocol with one VM on each of two
  hypervisors.
- **`export -o -` created a file named `-`** in the working directory and reported
  success, so a pipeline got nothing and a stray file appeared. `-` means stdout now,
  as it does everywhere else, and nothing else is printed there -- the document is the
  output.
- **Six help strings described a VirtualBox-only tool**, including the first line of
  `vmctl --help`: "Manage VirtualBox VMs with config-as-code support". Also the
  extension-to-format lookup in `convert` consulted VirtualBox's table for every
  provider, which is invisible only while the extension happens to be spelled the
  same.

## [4.0.0] - 2026-09-28

**Five defects in the path a user takes first, found by using the tool instead of
testing it.**

The 3.0.0 artifacts were verified by 1194 passing tests and `vmctl selftest` on two
real hypervisors, and `vmctl export vm -o vm.json` still wrote YAML into the file.
Both of those checks are written against vmctl's own idea of itself, so when that
idea is wrong they agree and pass. This release comes from driving the command line
the way a person does -- 21 real VMs across all four hypervisors, every image format
each one can create on every bus that can carry a disk, reading the files that land
on disk rather than asserting about them. `TEST-REPORT.md` is the record, with an
asciinema cast per hypervisor.

Three of the five are round-trip breaks: export a VM, import it back. That is the
thing vmctl exists to do.

### Why 4.0.0 and not 3.0.1

`requires-python` moved from 3.8 to 3.13, which stops vmctl installing on five
interpreter versions it used to support. A patch number would say "bugfix only"
while silently refusing to install on the Python someone already has.


### Added

- **The command line can say "like this file, but".** `import` accepted `--new-name`
  and `--disk-format` and nothing else, so the two things anyone actually wants to
  change from a file -- how much RAM and how many CPUs -- could not be changed at
  all without editing it first. Four flags now do it, on `import` and `create`:

  ```
  vmctl import vm.yaml --set memory.mb=4096 --set cpu.count=8
  vmctl import vm.yaml --add-disk size_mb=40960,bus=virtio-blk,format=qcow2
  vmctl import vm.yaml --add-nic network_type=bridged,model=virtio
  vmctl import vm.yaml --patch bigger.yaml
  ```

  They are *merges over the mapping*, applied before it becomes a `VMConfig` -- the
  same thing `extends:` already does, using the same `merge()` walk. So the model
  validates an overridden field as it validates a written one, the validator checks
  it against the provider's measured capabilities, the translator applies `--policy`
  to it, and the emitters place it. `--add-disk format=vdi` against VMware is
  refused under `strict` and substituted under `nearest` with no provider code
  involved, and there is no path that reaches a provider bypassing the checks.

  `--disk-format` stops being special: it is now the "every disk" layer of one
  precedence order (file, `--patch`, new devices, `--disk-format`, each device's own
  fields, `--set`), so `--disk-format vdi --add-disk format=qcow2` has an answer --
  the narrower flag wins -- and the run *says so* rather than resolving it silently:

  ```
  note: storage[1].format is 'qcow2' from --add-disk, which outranks --disk-format ('vdi')
  ```

  Field names are the schema's own (`size_mb`, not `size`), so there is no second
  vocabulary to keep in step. Overrides are applied to the configuration as the model
  sees it, so they reach a disk written the 1.1.x way (`disks:`) and one the file
  never spelled out.

### Fixed

- **A libvirt VM could not be exported and recreated while the original existed.**
  An export carries the domain's UUID -- which is what makes redefining *that* domain
  work -- so importing it under a new name asked libvirt to define a second domain
  with the first one's identity, and libvirt refuses: `domain 'x' is already defined
  with uuid ...`. That is the tool's central promise failing on a whole provider.
  Creating a domain now never claims an identity read from another one; the rename
  path in `emit_modify_vm` had already worked this out, and this is the other half.
- **A QEMU VM with two disks on `virtio-scsi` or `usb` could not be exported and
  re-imported.** The `.0` after those controllers is the controller's own bus, shared
  by every device on it, so unlike SATA it cannot carry a device's address -- and the
  emitter left the address out entirely. QEMU assigned LUNs itself and the command
  line, which for this provider *is* the VM, did not record which disk was which.
  Both read back at port 0, and the export then failed validation for colliding
  slots. Now written as `scsi-id=` and `port=` and read back from them. Measured
  against qemu-kvm 10.1.0 before changing: `scsi-hd` takes `scsi-id`/`lun`,
  `usb-storage` takes `port`, both from 0.

- **libvirt warned that the I/O APIC was off, which it cannot be.** Any libvirt VM
  with more than one CPU and no `boot.ioapic` in its file -- which is every VM
  re-imported from its own export -- printed "an x86 guest needs an I/O APIC to use
  them, and libvirt will not give it one". Measured before changing anything: `info
  qtree` on a q35 machine started with `-smp 2` and nothing asking for an interrupt
  controller reports `dev: ioapic`, and each CPU carries a `/lapic (apic)`; the same
  on `pc`. Neither device can be removed -- libvirt's `<ioapic>` only selects which
  component emulates one -- so the warning named a knob that does not exist.
  `ioapic_optional=False` now, as QEMU and VMware already had it. VirtualBox still
  warns, because `--ioapic off` there is a real setting with a real effect.

  All three were found by driving the CLI on real hypervisors, not by the test
  suite -- see `TEST-REPORT.md`.

### Changed

- **vmctl requires Python 3.13 or later** (was 3.8). This is a breaking change for
  anyone on an older interpreter, and wants a major version when it is released.

  The reason is the support window, which is what matters for a tool people
  install once and keep: 3.13 has security support until October 2029. Everything
  below it was either end-of-life already (3.8 since October 2024, 3.9 since
  October 2025) or within a month of it (3.10 on 2026-10-31). CPython has no LTS
  releases -- every version gets about two years of bugfixes and three more of
  security fixes -- so "the long-support one" means the newest, and CI now runs
  **3.13 and 3.14** so that a break on the newer one is a failure here rather than
  a bug report after somebody upgrades.

  Three of the six defects found while publishing 3.0.0 existed only because 3.8
  was still declared supported. With the floor raised, the workarounds they needed
  are gone rather than carried:

  - `ET.indent` is called directly again; the hand-written fallback is deleted.
  - `license = "MIT"`, the PEP 639 form, is back -- so the metadata now carries
    `License-Expression: MIT` instead of a deprecated table that setuptools stops
    accepting in February 2027.
  - `entry_points()` has one code path instead of a modern one and a 3.9 dict
    fallback beside it.

  `black` stays pinned below the next style year: the 3.10 floor it wanted is no
  longer the obstacle, but the 15 files of reformatting still are, and that belongs
  in its own commit.

### Fixed

Publishing 3.0.0 ran the suite on a machine that was not the one it was developed
on, which found four things that only held here.

- **Planning a VM no longer needs that hypervisor installed.** `vmctl -p qemu
  import f.yaml --out plan.sh` and `vmctl -p libvirt validate f.yaml` both failed
  on a host without QEMU or `virsh` -- but a host that cannot run a VM is the one
  most likely to be *writing* a config for one that can (F-13, A-09). QEMU now
  emits the conventional binary name when there is no local QEMU to name, and
  libvirt's probes fall back to the declared tables when `virsh` is absent rather
  than raising. Running a VM still requires the tool, and still says so.
- **The golden files recorded a home directory.** They held `/home/vagrant/...`,
  so they could only pass for a user named `vagrant`. They now name an explicit
  folder, and the one `emit()` helper the goldens are written and compared with
  lives in one place instead of three.
- **`black` is pinned below the next style year** (`>=25,<26`). black 26 wanted 15
  files reformatted and requires Python 3.10, which the 3.9 this project supports
  cannot install -- so an open bound meant a release nobody made could both fail
  CI and be impossible to satisfy locally.
- **`pip install` worked on every Python except the oldest one supported.**
  `license = "MIT"` is the PEP 639 form and needs setuptools 77+, but the newest
  setuptools that supports Python 3.8 predates it -- so on 3.8 the install failed
  while resolving the build backend, before any vmctl code ran. Back to the table
  form, which current setuptools accepts until 2027-02-18. Python 3.8 has been
  end-of-life since October 2024 and this is the second thing it has cost; when
  `requires-python` rises to 3.9, the string form goes back in.
- **The libvirt provider did not work on Python 3.8 at all.** Every domain
  document goes through `ET.indent`, which arrived in 3.9 -- so on the oldest
  supported Python, creating, editing, exporting or diffing a libvirt VM raised
  `AttributeError`. It now falls back to the same algorithm, verified to produce
  byte-identical XML on every example config, because a domain document is
  compared by `diff` and read by people. A sweep found no other 3.9-or-later API
  in the package, and no syntax newer than 3.8.
- Smaller: `docs/sphinx/_static` is tracked, so a fresh clone does not warn (and
  with `-W`, fail) when building the documentation; the stderr-separation test
  works on Click 8.1 and 8.2+, which removed `mix_stderr`; `regenerate_golden.py`
  runs again now that the repository root has a `conftest.py` of its own; and one
  `importlib.metadata` fallback is typed the way current mypy wants.

### Changed

- The install instructions name the 3.0.0 wheel on the releases page instead of
  `pip install vmctl`, which cannot work: vmctl is not on PyPI. Both Sphinx trees
  also still opened by calling vmctl "a command-line tool for managing VirtualBox
  VMs", which has not been true for three providers.

## [3.0.0] - 2026-09-28

**vmctl manages VMs on four hypervisors now, and the round trip works.**

The premise -- export a VM to YAML, recreate it anywhere -- did not hold: the example
config in the project's own README could not be imported. This release is the work from
[`PLAN.md`](https://github.com/abdelhaleemahmed/vmctl/blob/main/PLAN.md), which fixed
that and then generalised it. Finding ids (F-nn, H-nn, L-nn, M-nn, A-nn, P-nn, E-nn)
refer to that document, where each is written up with what it cost and how it was found.

The four providers are **VirtualBox**, **libvirt/QEMU-KVM**, **plain QEMU** and **VMware
Workstation**, and a VM can be migrated between them. Every capability table here was
*measured against the running product* -- not read from documentation -- and each
provider states where its numbers came from, because a measured limit and a remembered
one look identical in a table. Fifty-four findings were fixed along the way; the ones
that cost the most were all of one kind: a hypervisor accepting a setting and not
keeping it.

### Why 3.0.0 and not 2.1.0

The public 2.0.0 (19 August) was an *earlier* snapshot of this project, published as an
initial release: an argparse CLI with one provider. This release comes from the line of
development that had already moved to Click, and it changes that published interface --
so the major number moves. The numbering is not a claim that 2.0.0 came first in
development; it is the ordering users see.

### Breaking, relative to the published 2.0.0

- **`--apply` is now `--execute`.** The flag that turns a dry run into a real one is
  `--execute` on every command that changes a VM, and there is no alias: `--apply` would
  collide with `vmctl apply`, which is a command in its own right now, and one word
  cannot mean both "run this plan" and "converge this VM".
- **The CLI is Click, not argparse.** Same commands, plus fourteen more, and tab
  completion for bash, zsh and fish -- including live VM names.
- **The Python API changed.** `DiskConfig` is now `StorageDevice`, storage is one list of
  devices rather than a list of "disks", plans are `Plan`/`Step` objects rather than
  `List[List[str]]`, and providers are reached through a registry. `DiskConfig`,
  `StorageControllerConfig` and `Plan.as_argv_lists()` remain as aliases.
- **vmctl is no longer VirtualBox-only**, so it chooses a provider: `$VMCTL_PROVIDER`,
  then whichever hypervisor is installed. `-p` says which. On a machine with only
  VirtualBox, nothing changes.
- **A VM created now is not identical to one created by an earlier version**, because
  the earlier one emitted several settings wrongly or not at all. That is the fix, and
  it is worth knowing before re-creating a VM you rely on.

### Not breaking

- **Configuration files load unchanged, and always will.** `disks:`, `ostype:`,
  `adapter_type: "82540EM"`, `type: HDD`, `variant:`, `controller:` and the rest are
  accepted indefinitely -- there are tests that load config files produced by the older
  code and fail if they ever stop working. `schema_version:` is *optional*: a file
  without it is read as the current format. The plan had pencilled in dropping these
  names at a major version; keeping them costs one mapping table and breaks nobody.

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

- **A worked example per hypervisor** in `examples/`, each written in that provider's
  dialect and explaining in comments why it looks that way: VDI on SATA with named
  port-forward rules for VirtualBox, qcow2 on virtio-scsi with a CPU topology for
  libvirt, virtio-blk with `hostfwd` for plain QEMU, VMDK on LSI Logic with vmxnet3 for
  VMware. Plus `examples/lab/`, which shows `extends:`. Each is validated and planned
  against *its own* provider by CI, and each was created, started, read back and deleted
  on real hardware.
- **Both Sphinx trees** gained a Hypervisors page (the Arabic one in Arabic) and a
  generated reference, and their guides now cover `port_forwards`, `schema_version` and
  `extends`; installation asks for whichever hypervisor you use rather than VirtualBox
  specifically. Fixed there: nine `core` modules and every provider's tables and
  converters were missing from the API pages, and both trees kept their own hardcoded
  version number -- one said 1.1.8 while the package said 1.1.9. Tests now fail when a
  module is undocumented or a tree hardcodes a version.
- **The user guide and both documentation trees** now describe a four-hypervisor tool:
  choosing a provider, migrating between them, snapshots, port forwards, `extends:`,
  `apply`, `diff`, `doctor`, `selftest`, JSON output, and a page per hypervisor
  explaining what differs. The "write a config from scratch" section teaches today's
  field names instead of the 1.1.x ones.
- Fixed, all six found by writing and running those examples:
  - a libvirt config naming a network that does not exist passed validation and failed
    when the domain started -- vmctl could not tell "this host has none" from "vmctl
    could not ask";
  - `cpu.model: host` on a host without KVM produced a command line QEMU refuses to
    start; the nearest model it can use is substituted and reported;
  - `description` and `boot.ioapic` were dropped silently on QEMU, and `boot.ioapic` on
    VMware;
  - a VM read back could differ from the file that made it with nothing said, in the
    handful of fields a hypervisor decides for itself (libvirt resolves `machine: q35`
    to a versioned type and `host-model` to a concrete CPU, and always adds a USB
    controller) or cannot report (VirtualBox's TPM and secure boot; neither it nor
    VMware has a per-disk boot flag). Those are now declared, so `apply` and `selftest`
    stop reporting them as drift;
  - on VirtualBox, `audio_enabled: true` produced a sound device with both streams
    switched off -- audio that was neither on for the guest nor read back as on;
  - a VMware host-only adapter given a named vmnet would not start at all; the name is
    reported as not applied instead.
- Fixed: the batch file example in the user guide could not be run -- an inline `base_vm`
  was required to have a `name` that every instance then overwrites.
- **`vmctl selftest`** creates a throwaway VM (128 MB, one small empty disk), reads it
  back and compares it to what was asked for, snapshots it, starts it, stops it and
  deletes it -- asserting each step. The VM is deleted even when a step fails, what a
  provider says it cannot do is skipped rather than failed, and it exits 1 if anything
  failed so it can gate a CI runner. All four providers pass it on real hardware.
- Fixed: on VirtualBox, a configuration asking to boot "disk, dvd, nothing, nothing"
  produced a VM with `disk` in the third slot as well -- vmctl skipped the empty slots
  and VirtualBox keeps its own default there. A VM then disagreed with the file that
  made it for ever. Found by `vmctl selftest` in its first run.
- Fixed: on libvirt, `usb_enabled: false` cannot be honoured -- libvirt gives every
  domain a USB controller -- and vmctl now reports that instead of quietly reading the
  VM back with a different value than the file asked for.
- **`schema_version:`** in a config file. A file that omits it is read as the current
  format, which is what it almost always is; a file written by a *newer* vmctl is now
  refused with a sentence saying so instead of failing on whichever field it reaches
  first.
- **Generated documentation**: `docs/features.md` (every config field),
  `docs/providers.md` (what each hypervisor supports) and `docs/commands.md` are now
  produced from the code by `scripts/generate-docs.py`, and CI checks the committed
  copies are current. `docs/features.md` had drifted badly while it was written by hand
  -- it still described `ostype` as a VirtualBox identifier and had no `storage`. Every
  field of the model is now documented in one place, which improved the JSON Schema an
  editor loads at the same time.
- **The provider contract is public and checked**: `docs/writing-a-provider.md`, with a
  worked example in `examples/vmctl-null/` -- a provider in its own package that
  `vmctl providers` lists and the conformance suite covers, with no change to vmctl.
- Fixed: one conformance rule read a provider's *source text* and found the words it was
  looking for inside the base class's docstring, so it failed any provider that
  correctly declines to edit VMs in place. Found by running the suite against a
  third-party provider.
- **`extends:` in a config file**, so a lab does not repeat itself: a file can be built
  on one or more base files, which may themselves extend others. A mapping merges and
  the child wins; a list is replaced, because writing `storage:` means stating all of
  it. Paths are relative to the file that names them, a YAML config may extend a JSON
  base, and `vmctl validate` prints what a file is built on. A missing base or a cycle
  is reported with the file that caused it.
- Fixed: a config file containing nothing but whitespace was reported as missing a
  field rather than as empty.
- **NAT port forwarding** (`port_forwards:` on a network adapter), which the model had
  no word for -- so a lab VM's `ssh to localhost:2222` was lost on every round trip.
  VirtualBox and QEMU do it natively; libvirt needs its passt backend, and vmctl checks
  whether passt is installed rather than emitting a domain libvirt would reject; VMware
  has no per-VM setting at all (its NAT forwards are host-wide) and says so. `vmctl
  validate` catches a port outside 1-65535, an unknown protocol, and two rules claiming
  the same host port -- one message instead of four different ones from four
  hypervisors.
- Fixed: a config file that listed `storage:` or `networks:` reset every field of an
  existing disk or adapter that the file did not mention -- `bootable`, `discard`,
  `promiscuous_mode` and the rest -- back to defaults when applied. Only what a file
  states is applied, which was already true of settings and is now true of list
  entries too.
- **Snapshots, on all four hypervisors**: `vmctl snapshot take|list|restore|delete`.
  Taking one is immediate; restoring and deleting ask first, because each throws
  something away. What a listing can show depends on the hypervisor and
  `vmctl capabilities` now says so -- VirtualBox and libvirt keep a description,
  libvirt and QEMU a timestamp, `vmrun` reports names only -- and a description given
  to a provider that cannot store one is reported rather than quietly lost. On libvirt
  and plain QEMU the snapshot lives inside the qcow2, so a raw disk cannot be
  snapshotted at all: vmctl refuses up front, naming the disk, instead of letting the
  hypervisor fail half way through.
- Fixed: on libvirt, a VM that had snapshots could not be deleted -- libvirt refuses
  to undefine a domain whose snapshot metadata is still there, so `vmctl delete` failed
  with no reason given. Found while cleaning up after the feature above.
- **Machine-readable output**: `--format json` on `list`, `status`, `validate` and
  `diff`, and a new `doctor`. Sorted keys, so two runs produce the same bytes. An
  invalid file reports the error as JSON on stdout and still exits 1, because prose on
  stderr and nothing on stdout is what forces a pipeline to write `|| true`.
- **`vmctl -v`** prints each command on stderr as it runs, so a plan that fails
  part-way through can be traced to the step it failed on -- the last line printed is
  the command that failed. **`vmctl -q`** suppresses warnings; errors still print and
  still exit non-zero.
- **`vmctl doctor`** checks whether this machine can run VMs at all: memory, CPUs,
  hardware virtualisation and bridges on the host side; the hypervisor's tool and
  version, where images go and how much room is there on the other -- plus whatever
  that provider knows about itself, such as libvirt's connection, QEMU's accelerator,
  VirtualBox's kernel module or VMware's tools directory. It exits 1 when something
  will stop vmctl working, so it can gate a pipeline. A working-but-slow setup (no
  hardware virtualisation, so guests are emulated) is reported, not failed.
- Fixed: on a machine without VirtualBox, vmctl raised a `TypeError` from inside
  itself instead of saying "install VirtualBox and make sure VBoxManage is on your
  PATH". Found by the first run of `doctor`; every provider is now required to report
  a missing tool as a dependency error, which found the same shape in the QEMU
  provider.
- Fixed: VMware was reported as "not installed" on machines where it works, because
  Workstation's installer does not put `vmrun` on PATH. `vmctl providers` said
  unavailable, and vmctl would never pick VMware automatically.
- Fixed: four of the libosinfo guest-OS ids vmctl writes into a libvirt domain are not
  in libosinfo's database at all, including the generic Linux one, so they resolved to
  nothing in tools that read them. Measured against `osinfo-query` and corrected, with
  the database's ids committed as a fixture so the claim stays checked. A guest OS
  named by family without a version -- `ubuntu` rather than `ubuntu22.04`, which is the
  default -- is now recorded as the closest id libosinfo does have and reported as a
  substitution, instead of being dropped and reported as a lost setting on every VM.
- **`vmctl apply <file>`** makes the hypervisor match a configuration file, and is
  meant to be run repeatedly: it creates the VM when there is none, changes only what
  drifted when there is, and says "already matches the file" when there is nothing to
  do. Dry-run by default. Only what the file states is applied — a file that mentions
  the CPU count changes only that — and anything the file cannot know is kept, such as
  where an image lives on this host, the MAC the hypervisor generated, or a libvirt
  domain's UUID. Disks on an existing VM are never created, resized or removed; what
  cannot be converged is reported with the reason. With `--execute` the VM is read
  back afterwards and anything that did not converge is listed, which exits 1.
- On VirtualBox, `apply` and `edit` now change the guest OS type and the network
  adapters too — including adding one and removing one — because measuring
  `VBoxManage` rather than assuming showed that `--ostype`, `--nicN` and `--nictypeN`
  all work on a stopped VM. They used to be reported as changes vmctl could not make.
- **Fixed, and the most serious bug in this release: editing a VM destroyed its
  disks.** On QEMU, libvirt and VMware, changing a setting rewrote the VM's definition
  using the same code that creates a VM — which also *creates the disks*. So
  `vmctl edit my-vm --memory 512 --execute` ran `qemu-img create` over the VM's own
  image and the guest's filesystem was gone (verified by writing a pattern into an
  image and watching it not survive). A disk the VM already has is now attached, never
  re-created, and the provider conformance suite has a rule that fails any provider
  whose edit plan would make a disk again.
- Fixed: on VirtualBox, a VM that does not exist was reported as a failure to talk to
  VirtualBox rather than as a missing VM, so `apply` refused to create it.
- Fixed: a config file with no `storage:` section was read as asking for a VM with no
  disks, so `vmctl diff` reported every disk as drift and `apply` reported drift it
  would never converge. An absent section is not a request, the same rule that already
  applied to individual settings.
- Fixed: every warning from `vmctl edit` was printed twice.
- **`--clone-disks` on `import` and `create`** copies a VM's disk contents, not just
  its shape. Opt-in, because it is the slow and space-hungry part, and it says how
  many images and roughly how much data before it starts. An image this machine
  cannot read is reported and that disk is created blank, as it would have been
  anyway. It is the same code path as `migrate --with-disks`, so both convert the
  formats the target cannot write, and both refuse rather than leaving an empty disk
  behind. A config file cannot say where a VM's data was -- that describes a host, so
  it is left out of an export -- so on a file, name the image in `source:`, which is
  then *copied* rather than shared between two VMs.
- Fixed: cloning with `--disk-format` produced a file whose contents did not match its
  name -- `--disk-format raw` on a qcow2 image wrote the qcow2 container byte for byte
  into a `.raw` file and attached it as raw, so the guest would have found a qcow2
  header where its partition table should be. vmctl reported success.
- **vmctl now asks the host what it has**, instead of trusting a table for everything:
  which bridges exist, which machine types this QEMU build offers, which guest OS ids
  this VirtualBox knows. So `vmctl validate` catches `adapter_name: eth0` on a machine
  that has no `eth0`, rather than the create failing halfway through. A host that
  cannot be asked keeps working exactly as before.
- **`vmctl export --all -d <dir>`** exports every VM as its own file plus a manifest,
  and nothing in the output changes between runs -- so the directory is something you
  can commit and review. The documented "lab snapshot" needed a shell loop before.
- **`vmctl schema`** emits a JSON Schema for config files, generated from vmctl's own
  model. Point an editor at it for autocompletion and in-place validation; it accepts
  the 1.1.x field names and covers batch files too.
- **`vmctl diff <vm> <file>`** shows how a VM differs from a config file, field by
  field. Read-only, and it exits 1 when they differ so a pipeline can watch for drift.
  Only what the file actually states is compared -- a default is not a request -- and
  devices are matched by where they are rather than by a name most hypervisors cannot
  store.
- Fixed: a VM's boot order had three slots in a fresh config and four when read back
  from a hypervisor, so a VM differed from the file it was created from in a field
  neither had mentioned.
- **`vmctl capabilities`** prints what a hypervisor can actually do: formats, buses,
  which device kinds attach to which bus, limits — and where each figure was
  measured. `--format json` for scripts.
- **`--out PATH`** on `import`, `create`, `edit` and `migrate` writes the plan out
  instead of only printing it: a runnable shell script, or a directory to get the
  hypervisor's own artifact (the libvirt domain XML, the `.vmx`, the QEMU run script)
  beside it.
- Fixed, all found by actually migrating VMs between hypervisors: a `.vmx`'s disks
  could not be found from outside its own directory (so they came back as 20 GB
  blanks); `migrate --with-disks` failed because the conversion ran before anything
  had made the target directory; a converted image was named after the format it used
  to be; QEMU and libvirt offered three network cards this QEMU build does not have,
  which libvirt accepts and then cannot start.
- **A fourth hypervisor: VMware Workstation** (`vmctl -p vmware`). A VM is a
  directory with a `.vmx` in it; vmctl writes the file, makes the disks with
  `vmware-vdiskmanager` and drives the lifecycle with `vmrun`. VMDK is the only
  format VMware can attach, so a VirtualBox VM migrates across directly while one
  from libvirt or QEMU is refused with the reason rather than created unbootable.
- **A third hypervisor: plain QEMU** (`vmctl -p qemu`). No daemon and no registry --
  each VM is a directory holding its disks and a runnable command line, which is
  also what vmctl reads back when you export it. Create, list, read, edit, start,
  stop and delete all work, and a VM can be migrated to it from either of the other
  two.
- Fixed: on libvirt, vmctl offered disk formats that this QEMU build cannot write.
  The domain used to define successfully and then fail to start with "Driver 'vmdk'
  can only be used for read-only devices"; now it is refused up front, with the
  policy flag that converts instead.
- Fixed: reading a VM while it was running reported its disks as 20 GB, because
  `qemu-img` cannot open an image the running VM has open.
- Translation reports no longer list settings that are at their default value, so
  what is left in them is what actually did not carry over.
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

See the [GitHub releases page](https://github.com/abdelhaleemahmed/vmctl/releases).
