# vmctl — Remediation & Enhancement Plan

Companion to `audit.txt` (audit of v1.1.9, 2026-09-26). Every fix task below
carries an `F-nn` / `H-nn` id that maps back to an audit finding; enhancements
are `E-nn` and are new proposals.

---

## 0. Ground rules

The shipped version works for its main path — exporting a real VirtualBox VM
and re-importing that export. That path is the regression baseline and must not
change behaviour without a deliberate, noted decision.

1. **Capture before you change.** No logic change in Phase 1+ lands before the
   fixture-based tests in Phase 0 pin the current output.
2. **Backward compatibility for config files.** Any YAML/JSON that `vmctl
   export` produced in 1.1.x must keep loading in every later version. New
   fields get defaults; no field is renamed without an alias.
3. **Fail loud, never silently wrong.** Today several paths guess (BIOS for an
   EFI VM, `SATA` for a missing controller) and produce a plausible-looking but
   wrong VM. Prefer a clear error over a wrong VM.
4. **Dry-run stays the default** on every mutating command.
5. **One behaviour change per commit**, each with the test that proves it.
6. **The core stays hypervisor-neutral.** Anything that reasons about the
   canonical model lives in `core/`; anything vendor-specific lives in a
   provider's four files (Phase 5). A vendor string in `core/`, or an
   `if provider == ...` outside the registry, is a bug — not a shortcut.

---

## 1. Where the code stands

**Sound and worth keeping as-is:** the `VMConfig` dataclass tree as the single
canonical model; the parser / emitter / capabilities split inside the provider;
argv-list subprocess calls (no shell injection surface); dry-run-by-default;
the Click CLI structure and docstrings.

**The gap:** the export→import round trip — the product's whole premise — is
broken for hand-written configs, EFI VMs, VMs with an ISO attached, and ten
individual config fields. There are no tests, so none of it was caught.

---

## Phase 0 — Safety net (do this first)

> **Status: complete.** 115 passing tests + 31 strict-xfail tests pinning every
> known finding, running in ~2s with no hypervisor installed. Two new findings
> (`F-14`, `F-15`) were discovered *by* these tests — see Phase 1. `T-02`
> remains outstanding: the fixtures are synthetic until re-captured on a
> VirtualBox host (`tests/fixtures/README.md`).

No behaviour changes in this phase, with one deliberate exception (`F-14`, a
non-determinism that made golden testing impossible). Goal: make every later
phase verifiable.

### T-01 — Test scaffolding · S
- Add `tests/` with `pytest.ini`-equivalent config already in `pyproject.toml`
  (`[tool.pytest.ini_options]`, `testpaths = ["tests"]`).
- `tests/conftest.py`: fixtures returning `VMConfig` objects (minimal, full,
  EFI, multi-disk, ISO-attached, 4-NIC).
- No VirtualBox required for any test in this suite — parser and emitter are
  pure functions over strings.

### T-02 — Capture real VBoxManage fixtures · S (needs a VirtualBox host)
**Prerequisite:** this dev box has no `VBoxManage`. On a machine that has one,
capture raw output into `tests/fixtures/`:

```bash
VBoxManage showvminfo <vm> --machinereadable > tests/fixtures/showvminfo_<label>.txt
VBoxManage showmediuminfo <disk>            > tests/fixtures/showmediuminfo_<label>.txt
VBoxManage list vms                         > tests/fixtures/list_vms.txt
VBoxManage --version                        > tests/fixtures/version.txt
```

Capture at least: a BIOS Linux VM, an **EFI** VM, a VM with an **ISO** in an
optical drive, a VM with 2+ disks across two controllers, a VM with bridged +
hostonly + intnet adapters. These five fixtures are what make F-01..F-05
provable. Scrub hostnames/paths if they are sensitive.

### T-03 — Golden tests for current behaviour · M
- `test_parser.py`: `_parse_machinereadable` + `_dict_to_vmconfig` against each
  fixture, asserting the full resulting `VMConfig`.
- `test_emitter.py`: `emit_create_vm` against each fixture VM, asserting the
  exact argv list (golden files under `tests/golden/`).
- `test_roundtrip.py`: parse → `to_dict` → `from_dict` → emit, asserting the
  argv list is identical to parse → emit. **This test fails today** for F-02
  and F-05 — mark those `xfail(strict=True)` so the Phase 1 fix flips them
  green and they can never silently regress.
- `test_serializers.py`: save/load symmetry for YAML and JSON.
- `test_cli.py`: `click.testing.CliRunner` over every command, mocking
  `subprocess.run`; assert exit codes (this catches the `delete` abort code).

### T-04 — Make the parser testable without VirtualBox · S
`vmctl/providers/virtualbox/parser.py:256`

`_parse_machinereadable(text)` is already pure, but `_dict_to_vmconfig` calls
`self.get_disk_info(disk_path)`, which shells out to `VBoxManage
showmediuminfo` once per disk. So the function that turns native state into a
`VMConfig` cannot run on a machine without VirtualBox — which is where `T-03`'s
golden tests have to run.

- Inject the lookup: `_dict_to_vmconfig(name, config, probe: MediumProbe)`,
  where `MediumProbe` is a small callable/protocol. Production passes the
  VBoxManage-backed one; tests pass a dict-backed fake built from the
  `showmediuminfo` fixtures captured in `T-02`.
- This is a pure seam change with no behaviour difference, and it is the
  smallest possible down-payment on the transport/parser split that Phase 5
  formalises — do it now so `T-03`'s tests are written against the right
  surface and do not need rewriting later.
- Bonus: it exposes an N+1 subprocess call per export (one `showmediuminfo` per
  disk). Batching is a Phase 5 concern, but note it here.

**Phase 0 acceptance:** `pytest` runs green (with the documented xfails) on a
machine with no VirtualBox installed, in under 5 seconds.

---

## Phase 1 — Round-trip correctness (the blocking bugs)

> **Status: complete and verified on real hardware.** F-01…F-05, F-13, F-15…F-21
> are fixed. Verified end to end against VirtualBox 7.1.18 on the Windows host:
> four captured VMs were exported to YAML, re-imported under new names, created
> on the real hypervisor, read back and compared field by field — **17/17, 17/17,
> 19/19 and 19/19 fields matched**. The README's own hand-written config, which
> previously failed at `storageattach` with *"Could not find a controller named
> 'SATA'"*, now applies cleanly. Suite: 155 passed, 13 xfailed (every remaining
> xfail is a Phase 2+ finding). Still open from this phase: **F-22** (an empty
> removable drive is dropped), deferred to `M-02`.

Order matters: F-01 and F-02 are what make a restored VM unbootable.

### F-01 — Synthesize storage controllers when none are declared · M
`vmctl/providers/virtualbox/emitter.py:101`

The emitter only emits `storagectl` from `vm.storage_controllers`; an empty list
means no controller is created, yet `storageattach --storagectl SATA` is still
emitted and fails. The README's own example config hits this.

- In `emit_create_vm`, before the disk loop: collect the distinct
  `disk.controller` values, and for any that has no matching entry in
  `vm.storage_controllers`, synthesize a `StorageControllerConfig` with a
  canonical name (`"SATA Controller"`, `"IDE Controller"`, `"SCSI Controller"`,
  `"SAS Controller"`) and the provider default port count.
- Resolve `disk.controller_name` against the final controller set. Drop the
  `ctrl_name == "SATA"` magic-string branch (`emitter.py:141`) — resolution
  becomes: explicit `controller_name` if it matches a controller, else the
  synthesized name for `disk.controller`, else `ValidationError` naming both
  the disk and the available controllers.
- Consider changing `DiskConfig.controller_name` default from `"SATA"` to
  `None` (`vmconfig.py:195`) so "unset" is distinguishable from "named SATA".
  Keep accepting the old value on load.

**Worse than first assessed** (found by Phase 0's golden tests): modern
VirtualBox names its default SATA controller literally `SATA`, so the
magic-string branch fires on *ordinary exported VMs*, discards the parsed
controller name, and re-resolves by type — picking the **first type-matching
controller in the list**. Combined with `F-15` (a floppy controller mis-typed as
SATA), the system disk is attached to the floppy controller. Pinned by
`tests/test_emitter.py::test_disk_is_attached_to_the_controller_it_was_parsed_from`.

**Acceptance:** the README example config produces a `storagectl` command
before its `storageattach`, and golden output for already-working exported
configs is byte-identical.

### F-02 — Firmware detection is case-sensitive; EFI64/32 never detected · S
`vmctl/providers/virtualbox/parser.py:160`

`config.get('firmware', 'bios') == 'efi'` compares against VBoxManage's
uppercase `"EFI"`, so every EFI VM exports as BIOS and is recreated unbootable.

- Normalize with `.lower()` and map through a dict covering
  `bios / efi / efi32 / efi64`.
- Same treatment for every other string compare in `_dict_to_vmconfig` —
  audit `pae`, `acpi`, `ioapic`, `secureboot`, `rtcuseutc`, `usb`, `audio`
  and normalize all of them (helper: `_flag(config, key, default=False)`).

**Acceptance:** the EFI fixture from T-02 parses to `FirmwareType.EFI64` (or
`EFI`, per what the fixture reports) and re-emits `--firmware efi64`.

### F-03 — Hyphenated VBoxManage keys are unreachable · S
`vmctl/providers/virtualbox/parser.py:132`

The fallback regex `^(\w+)="?(.*?)"?$` excludes `-`, so `nested-hw-virt="on"`
never enters the parsed dict and `cpu.nested_virt` is always `False`.

- Widen to `^([\w.-]+)="?(.*?)"?$`.
- Re-check every key the parser reads against a real fixture after the change;
  other hyphenated keys may start arriving and must not break parsing.

**Acceptance:** a test asserts `nested-hw-virt` is present in the parsed dict
and reaches `cpu.nested_virt`.

### F-04 — Optical drives cannot be recreated · M
`vmctl/providers/virtualbox/emitter.py:110`

Every disk including `DiskType.DVD` goes through `createhd`, then the resulting
`.vdi` is attached with `--type dvddrive`, which VBoxManage rejects.

- Skip medium creation entirely for `DiskType.DVD`.
- Attach `--medium emptydrive` by default. Add an opt-in for attaching the
  original ISO: keep `disk_path` for DVDs in the export (it is currently
  stripped by `to_dict`, `vmconfig.py:216`) under a distinct field such as
  `iso_path`, and attach it when the file exists on the target host, otherwise
  fall back to `emptydrive` with a warning.
- Decide and document: ISO paths are host-specific, so `emptydrive` is the
  correct portable default.

**Acceptance:** the ISO fixture round-trips to a valid command sequence with no
`createhd` for the optical drive.

### F-05 — Ten model fields are exported but never emitted · M
Parsed and written to YAML, silently ignored on create: `cpu.hotplug`,
`cpu.execution_cap`, `cpu.pae`, `cpu.nested_virt`, `memory.page_fusion`,
`memory.ballooning`, `firmware.tpm`, `clipboard_mode`, `draganddrop`,
`boot.hpet`.

Emit the ones VBoxManage supports directly, on the existing `modifyvm` call
where possible:

| Field | Flag | Note |
|---|---|---|
| `cpu.pae` | `--pae on\|off` | |
| `cpu.nested_virt` | `--nested-hw-virt on\|off` | depends on F-03 to ever be `True` |
| `cpu.hotplug` | `--cpuhotplug on\|off` | |
| `cpu.execution_cap` | `--cpuexecutioncap N` | emit only when `!= 100` |
| `memory.page_fusion` | `--pagefusion on\|off` | |
| `boot.hpet` | `--hpet on\|off` | |
| `clipboard_mode` | `--clipboard-mode <mode>` | validate against the 4 modes |
| `draganddrop` | `--draganddrop <mode>` | validate against the 4 modes |
| `firmware.tpm` | `--tpm-type 2.0` | VBox 7+ only — gate on H-07 version check |
| `memory.ballooning` | — | no boolean flag exists; see below |

`ballooning` as a bool has no VBoxManage equivalent (`--guestmemoryballoon`
takes a size). Replace it with `balloon_mb: Optional[int] = None`, keep
accepting a legacy bool on load (`True` → ignore + warn, `False` → `None`).

**Acceptance:** `test_roundtrip.py` xfail from T-03 flips to pass; a config
with every field set emits a flag for each one (or a warning saying it can't).

### F-13 — Hardcoded VirtualBox machine folder · S *(found while planning)*
`vmctl/providers/virtualbox/emitter.py:108`

`os.path.join(os.path.expanduser("~"), "VirtualBox VMs")` ignores the user's
configured default machine folder, so on any customised install the disks are
created outside the VM's own directory.

- Read it once from `VBoxManage list systemproperties`
  (`Default machine folder:`) and cache it on the backend.
- The emitter must not shell out itself — pass the folder into the emitter's
  constructor so it stays a pure function of its inputs (and stays testable).
- Fall back to `~/VirtualBox VMs` only if the query fails.
- Also move `import os` / `import sys` out of the function bodies to module
  scope while in this file.

---

### F-17 — `--secureboot` is not a VBoxManage option; secure boot is unreadable · M
`vmctl/providers/virtualbox/emitter.py:95`, `parser.py:165`

Verified against the real host (VirtualBox 7.1.18):

```
$ VBoxManage modifyvm vmctl-t-efi --secureboot on
VBoxManage.exe: error: Unknown option: --secureboot
```

Two independent defects:

- **The emitter produces an invalid command.** `firmware.secure_boot: true` makes
  `create_vm` emit `modifyvm --secureboot on`, which VirtualBox rejects. Since
  `_run_command` uses `check=True`, the whole create **aborts partway**, leaving
  a registered VM with no disks. Any Windows 11-shaped config hits this.
- **The parser can never read it back.** `showvminfo --machinereadable` emits no
  `secureboot` key at all (grep count: 0, with EFI64 firmware active), so
  `config.get('secureboot', 'off')` is always `'off'`. The field is
  write-only-from-file and silently lost on export.

On VirtualBox 7.x secure boot lives in NVRAM, not in `modifyvm`: it is enrolled
with `VBoxManage modifynvram <vm> enrollmssignatures`. TPM *is* a `modifyvm`
option (`--tpm-type= none | 1.2 | 2.0 | host | swtpm`, plus `--tpm-location`),
which confirms `F-05`'s planned `--tpm-type` mapping.

Fix: emit `modifynvram enrollmssignatures` when secure boot is requested and the
firmware is EFI; warn and skip when the firmware is BIOS; read the state back
from `VBoxManage showvminfo` NVRAM output or drop the field from the model
rather than pretend it round-trips. Gate on the `H-07` version floor — the
option set differs across 6.1 / 7.0 / 7.1.

### F-22 — An empty removable drive is not represented at all · S
`vmctl/providers/virtualbox/parser.py` (disk attachment filter)

Found by recreating a config on the real host and reading it back. The parser
keeps an attachment only when its value looks like a path to a disk image, so
`"IDE Controller-0-0"="emptydrive"` is discarded. Consequences:

- export a VM that has an empty optical or floppy drive, re-import it, and **the
  drive is gone** — not just its medium;
- so a VM whose installer DVD has been ejected loses its DVD drive on the
  next round trip, and will not boot from one without manual work.

The model conflates "device" with "medium": `DiskConfig` cannot express *a drive
with nothing in it*. That is exactly what `M-01`/`M-02` separate (`DeviceKind`
plus an optional `source`), so the real fix belongs there. A narrower Phase 1
fix is possible — keep attachments whose value is `emptydrive` and record them
as a removable device with `source=None` — and is worth doing if empty drives
matter before Phase 5.

### F-21 — `audio="default"` is a driver name, not an enable flag · S
`vmctl/providers/virtualbox/parser.py`

VirtualBox 7.1.18 reports, for a VM created with no audio at all:

```
audio="default"
audio_out="off"
audio_in="off"
```

`audio_enabled = config.get('audio', 'none') != 'none'` is therefore **True for
every modern VM**, so every export claims audio is enabled and every recreated
VM gets an audio controller switched on. Read `audio_out` / `audio_in` instead.

Confirmed for the same reason that `clipboard` and `draganddrop` *are* reported
(`clipboard="disabled"`, `draganddrop="disabled"`) and were simply never read.

**Option spellings, verified live on 7.1.18** (relevant to `H-07`, not a bug on
this version): `--audiocontroller` / `--audio-controller`, and `--draganddrop` /
`--drag-and-drop`, are both accepted — the 6.x forms survive as aliases. The
canonical 7.x names are the hyphenated ones. `--cpuhotplug`, `--pagefusion`,
`--nested-hw-virt`, `--cpuexecutioncap` and `--clipboard-mode` all accepted.
`--secureboot` does **not** exist (see `F-17`). Declaring the version floor and
picking spellings accordingly remains `H-07`'s job.

### F-18 — `showmediuminfo` needs a device type; ISOs silently fall back · S
`vmctl/providers/virtualbox/parser.py` (`get_disk_info`)

Verified on VirtualBox 7.1.18:

```
$ VBoxManage showmediuminfo "C:\...\vmctl-test.iso"
error: The medium '...vmctl-test.iso' can't be used as the requested device
       type (HDD, detected DVD)
$ VBoxManage showmediuminfo dvd "C:\...\vmctl-test.iso"      # works
```

`get_disk_info` never passes a device type, so the command **fails for every
optical medium** and the `except CalledProcessError` branch returns defaults:
20480 MB and a format guessed from the extension (`.iso` is not in the map, so
VDI). That bogus 20 GB VDI is then what `F-04` hands to `createhd`. Fixing
`F-18` is a prerequisite for `F-04` being correct rather than merely different.

Pass `disk` or `dvd` based on the attachment's device type. Note the real ISO
reports `Capacity: 0 MBytes`, so size must not be used to size a created medium.

### F-19 — Host-only adapter name read from a key VirtualBox does not emit · S
`vmctl/providers/virtualbox/parser.py:399`

The parser reads `hostonlyif{n}`. VirtualBox 7.1.18 emits `hostonlyadapter{n}`:

```
bridgeadapter1="Intel(R) Dual Band Wireless-AC 7265"
hostonlyadapter2="VirtualBox Host-Only Ethernet Adapter #4"
intnet3="lab-backend"
natnet4="nat"
```

So **every host-only adapter loses its interface name** on export, and the
recreated VM gets a host-only NIC attached to nothing. The emitter already
writes `--hostonlyadapter{n}`, so this is a read/write asymmetry of the same
family as `F-05`: the two directions were written separately and drifted.

**Also verify `natnetwork`:** a plain NAT adapter emits `natnet4="nat"`, which
suggests a NAT-network adapter emits its name in `natnet{n}` too — while the
parser reads `natnetwork{n}`. Not confirmed here (the host has no NAT network
defined); confirm before trusting that path. `A-11`'s field table is the
structural fix: one declaration per field, used in both directions, so read and
write cannot disagree.

### F-20 — Disk allocation variant is never detected; every disk looks thin · S
`vmctl/providers/virtualbox/parser.py:108`

The parser matches `line.startswith('Variant:')`. VirtualBox 7.1.18 emits
**`Format variant:`**:

```
Storage format: VHD
Format variant: fixed default
Capacity:       128 MBytes
```

The branch never fires, so `variant` is always `THIN`. Confirmed end to end: a
medium created with `--variant Fixed` parses as `thin` and would be recreated as
a dynamically-allocated disk. Silent, and it changes the recreated VM's disk
performance and space behaviour.

Accept both prefixes (older VirtualBox used `Variant:`), matched
case-insensitively, and add the `Format variant:` form to the fixtures.

### F-16 — Machine-readable values are escaped and never unescaped · S
`vmctl/providers/virtualbox/parser.py:132`

`showvminfo --machinereadable` escapes backslashes and quotes inside quoted
values. Verified on the real Windows host (VirtualBox 7.1.18):

```
"IDE Controller-0-0"="C:\\vagrant-storage-labs\\vms\\ch-driver-lab\\Rocky-9...vmdk"
```

vmctl takes the value verbatim, so every Windows disk path is parsed with
doubled separators (`C:\\vagrant-storage-labs\\...`). Consequences:

- `get_disk_info` is invoked with a malformed path, so size/format/variant
  quietly fall back to defaults (20480 MB, VDI) when Windows rejects it;
- anything that later *uses* `disk_path` — `F-04`'s ISO re-attachment, `E-03`
  `--clone-disks`, `P-06` `migrate` — inherits the broken path;
- the quoted-key regex `^"([^"]+)"="([^"]*)"$` cannot represent a value
  containing an escaped `\"`, so a VM or path with a quote in its name is
  dropped silently rather than reported.

Invisible on Linux hosts, which is why it survived: there are no backslashes to
double. It matters here because the primary host is Windows.

Fix: unescape `\\` and `\"` when decoding a quoted value, and make the key/value
regex tolerate escaped quotes. This is a decoder concern, so in Phase 5 it
belongs to `core/decoders.py` and is then fixed once for every provider that
uses a key/value format (VirtualBox *and* VMware `.vmx`).

### F-14 — Controller order was non-deterministic · S *(fixed in Phase 0)*
`vmctl/providers/virtualbox/parser.py:186`

`controller_indices` was a `set` of index strings. Iterating it varies with
`PYTHONHASHSEED`, so **controller order changed between runs of the same
command** — and because the emitter resolves a disk's controller by scanning
that list (`F-01`), *which controller a disk was attached to* changed too.
Measured across six hash seeds on the `iso_attached` fixture, the system disk
landed on the floppy controller in three of them.

This was fixed during Phase 0 (`sorted(set(...), key=int)`) because golden
testing is impossible against non-deterministic output — the same reasoning as
`T-04`. It changes emitted command **order**, never content, relative to 1.1.9.
Guarded by `test_controller_order_is_deterministic` and
`test_emitted_command_order_is_deterministic`.

### F-15 — Unmapped controller types silently become SATA · S
`vmctl/providers/virtualbox/parser.py:195`

`vbox_controller_map` has no entry for `I82078` (floppy), so
`.get(type, StorageControllerType.SATA)` types a **floppy controller as SATA**.
Two consequences, both pinned by tests:

- the controller is re-created with `storagectl --add sata`, i.e. a SATA
  controller wearing the name `Floppy`;
- it becomes a candidate for `F-01`'s type-based disk resolution, which is how a
  system disk ends up on the floppy bus.

The fix is not just adding `i82078`: the fallback itself is wrong. An unknown
controller type must raise or warn, never silently claim to be SATA. The same
applies to `nvme`, which is mapped to `SCSI` "for now" on the line above — that
becomes correct only once `M-01` adds a real `NVME` bus. Add `FLOPPY` to
`StorageControllerType` as part of `M-01`'s `BusType`, and until then have the
parser preserve the native type string rather than guess.

---

## Phase 2 — Validation and error UX

> **Status: complete.** F-06, F-07 and F-08 are fixed, plus L-05. `validate` now
> reports the field at fault instead of raising `TypeError`; `from_dict` no
> longer mutates its input; the validator is pure, capability-driven, and its
> warnings actually fire. Suite: 197 passed, 4 xfailed (all Phase 3).
> Re-verified end to end on VirtualBox 7.1.18 after the change — 19/19 and
> 19/19 fields matched, and the emitter goldens did not move, which is what
> proves the `controller_name` default change was behaviour-neutral.
>
> Two judgement calls worth knowing about:
>
> * **The ostype warning was dropped, not implemented.** VirtualBox reports
>   display names (`"Ubuntu (64-bit)"`) while the capability list holds internal
>   ids (`Ubuntu_64`), so any static comparison warns on *every real VM*. A
>   warning that fires on correct input is worse than no warning; this needs
>   `VBoxManage list ostypes`, i.e. `E-05`.
> * **Per-missing-boot-device warnings were dropped for the same reason.**
>   VirtualBox's default boot order is floppy, dvd, disk, and most VMs have
>   neither a floppy nor a DVD. The rule now fires only when *nothing* in the
>   boot order exists, which is genuinely unbootable.
>
> `DiskConfig.controller_name` now defaults to `None` rather than the literal
> `"SATA"` (the deferred half of F-01). The old default claimed a specific
> controller even for a disk on another bus, which produced a false slot-clash
> error the moment F-08's collision check existed. Resolution now goes through
> one shared `resolve_controller()` in `core/vmconfig.py`, used by both the
> emitter and the validator, so the two cannot disagree about where a disk lands
> — they did, and that disagreement is how a system disk reached a floppy
> controller.

### F-06 — `validate` crashes instead of validating · M
`vmctl/core/vmconfig.py:361`, `vmctl/serializers/yaml_serializer.py:24`

Three one-line inputs produce raw tracebacks from the command whose only job is
diagnosing bad files: an unknown field (`TypeError: unexpected keyword
argument`), an empty file (`TypeError: argument of type 'NoneType'`), and a bad
enum value (`ValueError: 'nvme' is not a valid StorageControllerType`).

- Rewrite `VMConfig.from_dict` as strict-but-friendly:
  - `data is None` or not a dict → `ValidationError("config file is empty or not a mapping")`.
  - Unknown top-level/nested keys → `ValidationError` listing the unknown key
    and the valid keys for that section, with a `difflib.get_close_matches`
    "did you mean" hint.
  - Enum coercion through a helper that raises
    `ValidationError(field=..., value=..., expected=[...])` — the
    `ValidationError` class already accepts exactly those kwargs.
  - Type errors (`cpu.count: "four"`) → `ValidationError`, not `TypeError`.
- Widen the serializers' `except` clauses so nothing but `VMToolError`
  subclasses escape `load()`.
- Resolve the capabilities/model disagreement: `nvme` appears in
  `capabilities.max_ports_per_controller` but not in `StorageControllerType`.
  Either add `NVME` to the enum and emit `--add pcie --controller NVMe`, or
  drop it from capabilities. Recommend adding it — it is a real VBox 6.1+ bus.

**Acceptance:** each of the three inputs above exits 1 with a one-line message
naming the offending field; no traceback. Add all three to `test_cli.py`.

### F-07 — `from_dict` destroys its input dict · S
`vmctl/core/vmconfig.py:389`

The `data.pop(...)` calls mutate the caller's dict; after `from_dict(d)`,
`d == {'name': 'x'}`. `BatchCreator._get_base_vm` (`batch.py:62`) passes a slice
of the loaded batch file straight in.

- `data = copy.deepcopy(data)` at the top, or build from explicit `data.get()`
  reads without popping.
- Test: assert the input dict is unchanged after the call.

### F-08 — The warnings pathway is dead code; the validator mutates instead · M
`vmctl/validators/vm_validator.py:23`

`validate()` builds `warnings = []`, never appends, returns it. So
`engine.create_vm`'s print loop, `cmd_validate`'s `"Warnings:"` block and
`BatchCreator`'s per-VM warnings can never fire. Meanwhile
`_validate_logical_constraints` silently flips `disks[0].bootable = True`
(`:118`) where a warning is what is wanted.

- Make the validator **pure**: never mutate the `VMConfig`. Move the
  "first disk becomes bootable" default into `VMConfig.__post_init__` (next to
  the existing default-disk logic) or into the emitter, and have the validator
  emit a warning instead.
- Populate real warnings: non-multiple-of-4 memory (the `pass` at `:96`),
  memory above host RAM, `vram_mb` below what the OS type needs, secure boot
  requested with BIOS firmware, `ostype` not in
  `capabilities.supported_os_types`, more adapters than
  `capabilities.max_network_adapters`, DVD-only boot order with no ISO.
- Drive limits from `capabilities`, not hardcoded literals: `_validate_schema`
  hardcodes the 8-adapter limit (`:60`) while `max_network_adapters`,
  `max_disks`, `supported_os_types` and `supported_network_types` are never
  read.
- Add the missing structural check: **two disks on the same
  (controller, port, device)** — currently accepted and fails at VBoxManage
  time, mid-create.

**Acceptance:** `vmctl validate` on a config with 2048-MB-plus-3 memory and an
unknown ostype prints two warnings and still exits 0; a port collision exits 1.

---

## Phase 3 — CLI honesty

### F-09 — `completion` emits a wrong env var name · S
`vmctl/cli/main.py:567` — `env_var` is already `_VMCTL_COMPLETE`, then the code
prints `f'_{env_var}...'`, producing `__VMCTL_COMPLETE` (double underscore).
Following the documented `eval "$(vmctl completion bash)"` sets a variable Click
ignores and evals `vmctl`'s help text. Completion has never worked this way.

- Drop the extra underscore and, better, have the command *invoke* Click's own
  completion generator and print the real script, so `eval "$(vmctl completion
  bash)"` works as documented rather than printing an instruction to run.
- Test: assert the emitted script contains `_VMCTL_COMPLETE` exactly once and
  not `__VMCTL_COMPLETE`.
- Fix `docs/sphinx*/guide/completion.rst` to match whatever the command does.

### F-10 — `edit` does not edit · M
`vmctl/cli/main.py:363` reads the VM, mutates the in-memory object, prints YAML
and admits "full apply support requires VBoxManage modifyvm integration" — but
the README command table says "Modify running VM properties."

Two options; recommend (a):
- **(a) Implement it.** Add `VirtualBoxBackend.edit_vm` that emits a targeted
  `modifyvm` for only the changed fields, refuses (or warns) when the VM is
  running for fields that need it stopped, and handles `--new-name` via
  `modifyvm --name`. Keep dry-run default with `--execute`, consistent with
  every other mutating command.
- (b) Rename it `vmctl preview-edit` / fold it into `read --set key=value` and
  fix the README. Cheaper, but leaves an obvious gap.

### F-11 — `BaseProvider.edit_vm` is a data-loss trap · S
`vmctl/providers/base.py:156` — the default implementation is `delete_vm()` then
`create_vm()`, and `delete_vm` passes `--delete`, destroying the disks.
`VirtualBoxBackend` does not override it and `engine.edit_vm` exposes it.
Nothing calls it today; that is luck, not design.

- Replace the body with `raise NotImplementedError`, and let F-10's real
  implementation be the only `edit_vm` that exists.

### F-12 — Batch: nameless instances collide, failures leave half a cluster · M
`vmctl/core/batch.py:74` — two instances without `name` both yield `base`.
With `--execute`, the first is created and the second fails at VBoxManage with
no rollback.

- Require `name` on every instance; `ValidationError` naming the instance index
  when it is missing.
- Reject duplicate names within the batch, and pre-flight every name against
  `backend.list_vms()` before creating anything.
- Add `--continue-on-error` (default off: stop at the first failure) and print a
  final summary of created / skipped / failed.
- Optional `--rollback-on-error` that deletes the VMs this run created. Must be
  explicit, never the default.
- Pre-flight the whole batch through the validator before the first create, so
  a bad instance 9 fails before instance 1 exists.

### Low-tier CLI/model cleanups · S each
- `L-01` `cmd_delete` prints "Deletion cancelled." and exits **0** on abort
  (`main.py:420`) — scripts cannot tell it from success. Exit 1 (or 130).
- `L-02` `--description` is wrapped in literal quotes (`emitter.py:197`); argv
  is a list, so the quotes land in the stored description. Remove them.
- `L-03` Parser marks *every* disk bootable when `boot.order[0] == 'disk'`
  (`parser.py:269`), DVDs included. Mark only the disk at the lowest
  (controller, port, device) on a bootable controller.
- `L-04` Disk names from the parser (`disk_SATA Controller_0_0`) become disk
  filenames with spaces. Slugify for the filename; keep the readable name in
  the config.
- `L-05` `engine.py` repeats `try: ... except ProviderError as e: raise e` in
  nine methods. Delete the no-op wrappers.
- `L-06` `stop_vm` (graceful) returns immediately; the guest may take a minute.
  Add `--wait[=SECONDS]` polling `get_vm_status`.
- `L-07` `import`/`create` do not check whether the target name already exists;
  add a pre-flight so the failure is a clean message, not a half-created VM.

---

## Phase 4 — Repo, release and dependency hygiene

### H-01 — `.gitignore` ignores all YAML · S
`.gitignore:47` has `*.yaml` / `*.yml` globally. `BMR.yaml` is untracked because
of it, and `.github/workflows/ci.yml` would be too — which is likely why there
is no CI. Verified:

```
.gitignore:47:*.yaml    BMR.yaml
.gitignore:48:*.yml     .github/workflows/ci.yml
```

- Narrow to root-only (`/*.yaml`) plus explicit negations for `.github/`,
  `tests/fixtures/`, and any example configs that should ship.

### H-02 — 242 Sphinx `_build/` files are committed · S
Including `environment.pickle`. Ignore `docs/sphinx*/_build/` and generate in
CI. Use `git rm -r --cached` so the working tree is untouched.

### H-03 — Single-source the version · S
`pyproject.toml:7` and `vmctl/__init__.py:8` both hardcode `1.1.9`, and
`scripts/release.sh` sed-patches both. Use
`[tool.setuptools.dynamic] version = {attr = "vmctl.__version__"}` and delete
the second sed.

### H-04 — CI · M *(blocked on H-01)*
`.github/workflows/ci.yml`: matrix over Python 3.8–3.12 running `pytest`,
`black --check`, `flake8`, `mypy`. All four tools are already declared in the
`dev` extra and none of them runs anywhere today. Add a docs job building both
Sphinx trees, and a release job gated on the test job.

### H-05 — Changelog and release script · S
- The Sphinx changelog stops at **v1.1.8**; v1.1.9 shipped without an entry.
  Add a root `CHANGELOG.md` as the single source and have Sphinx include it via
  `myst-parser` (already in the `docs` extra).
- `scripts/release.sh` does not run the tests, does not update the changelog and
  does not tag. Add: `pytest` before build (hard fail), changelog-entry check,
  `git tag -a v$VERSION`, and a `--dry-run` mode.

### H-06 — Documentation truth pass · M
Do this *after* Phase 1–3, then re-read every doc against the code:
- `README.md:7` badge still says v1.1.8.
- README dry-run sample shows `VBoxManage createmedium`; the code emits
  `createhd` (see H-07).
- README claims ISOs are handled (true of the parser, false of the emitter
  until F-04) and that `edit` modifies a running VM (false until F-10).
- The README example config must be a file in the repo that CI validates, so it
  can never drift from the code again. Same for `docs/features.md` and the
  Arabic and English Sphinx trees — they duplicate every claim.

### H-07 — Declare and enforce a VirtualBox version floor · M
The emitter mixes eras: `createhd` is the deprecated 5.x alias of
`createmedium`, while audio mixes 7.x `--audio-driver` with 6.x
`--audiocontroller` (and 6.1 has no `--audio-driver` at all, so audio emission
breaks there).

- Pick a floor (recommend **6.1**, with 7.x-only features like `--tpm-type`
  feature-gated).
- Add `VirtualBoxBackend.version()` parsing `VBoxManage --version`, cached.
- Raise the existing-but-unused `DependencyError` with a real hint when
  VBoxManage is missing or below the floor — currently the message is a bare
  `ProviderError("VBoxManage not found")` repeated in six places.
- Switch `createhd` → `createmedium disk` and normalize the audio flags for the
  chosen floor. State the floor in README and `docs/sphinx*/guide/install.rst`.

### H-08 — Prune the exception module · S
`vmctl/core/exceptions.py` is 669 lines; **17 of its 20 classes and all 4 helper
functions have zero references** outside the file
(`VMNotFoundError`, `VMStateError`, `BatchError`, `DiskError`, `NetworkError`,
`TimeoutError`, `ResourceExhaustedError`, `wrap_exception`,
`is_recoverable_error`, `get_error_summary`, `create_error_context`, …), while
the code raises bare `ProviderError`/`ValidationError` strings everywhere.

Also: `PermissionError` and `TimeoutError` **shadow Python builtins** — a real
hazard for anyone doing `except TimeoutError` in this codebase.

- Start using the specific ones that carry real value: `VMNotFoundError`,
  `VMAlreadyExistsError`, `VMStateError`, `DependencyError`, `BatchError`
  (F-06, F-12 and H-07 all want them).
- Delete the rest, and rename the two builtin-shadowing classes
  (`VMPermissionError`, `VMTimeoutError`).
- Fix the stale module docstring: it says "vmtool", not vmctl.

---

## Phase 5 — Portable core: neutral model, storage matrix, plan abstraction

This phase adds no new hypervisor. Its only job is to make the **existing
structure** carry more than one, so that every later provider is four small
files and no changes to the core. Do it after Phase 1 (a provider seam built on
known-broken round-trip logic just multiplies the bugs) and before Phase 6.

**What stays exactly as it is:** the layered package layout; `VMConfig` as the
single canonical model; the parser / emitter / capabilities / backend split
inside each provider; serializers and validators knowing nothing about
providers; dry-run-by-default.

**The design rule for this phase:** *shared code is anything that reasons about
the canonical model; provider code is only translation to and from native
form.* Each provider ships exactly four files and nothing more:

| File | Responsibility | Must not |
|---|---|---|
| `tables.py` | **data only**: field map + enum translation tables | contain logic |
| `capabilities.py` | declarative limits and support matrix | contain logic |
| `parser.py` | native form → canonical `VMConfig` | shell out itself (takes text in) |
| `emitter.py` | canonical `VMConfig` → `Plan` | run anything, or read the host |
| `backend.py` | execute a `Plan`, lifecycle ops (start/stop/status/delete) | make config decisions |

Only `tables.py` grows much per provider, and it is data. If a provider needs
an extra file of *logic*, that logic belongs in `core/`.

### Two kinds of parser — settle this before Phase 0

"Parser" is overloaded in the current code, and the answer differs for the two
jobs it names:

| | What it reads | How many |
|---|---|---|
| **Config parser** | our own YAML/JSON config files | **exactly one, shared, forever** |
| **Native reader** | the hypervisor's own output | **one per hypervisor — unavoidable** |

**The config parser is never per-provider.** The config file *is* the canonical
neutral model, so `serializers/{yaml,json}_serializer.py` plus
`VMConfig.from_dict` / `to_dict` stay the single implementation for every
hypervisor, and they keep knowing nothing about providers. A file written for
libvirt and a file written for VirtualBox are the same format; only the
`provider:` key (and any `provider_options`) differ. Anything else would mean a
config could not be moved between hypervisors, which is the whole point of
`P-06 migrate`.

**Native readers must be per-hypervisor**, because the inputs are different
languages, not different dialects: VBoxManage emits line-oriented `key="value"`,
libvirt emits a domain XML document, VMware emits a `.vmx` key/value file,
Hyper-V emits PowerShell/CIM objects, Proxmox emits JSON over HTTP. No single
parser can read those; pretending otherwise would just move the branching
inside one file.

**But "one per hypervisor" must not mean "821 lines per hypervisor."** That is
what today's `providers/virtualbox/` costs (parser 326 + emitter 260 +
backend 195 + capabilities 40), and copying that shape three more times is
exactly the redundancy to avoid. Split each native reader into four layers and
only the innermost is provider code:

```
  transport      run the command / read the file / call the API   -> SHARED (backend)
  decoder        raw text -> generic intermediate structure       -> SHARED (~3 total)
  field mapping  intermediate <-> canonical model                 -> SHARED engine
                                                                     + per-provider TABLE
  assembly       group entries into devices, resolve addresses    -> SHARED helpers
                                                                     + per-provider extractors
```

- **Transport** is not the parser's job. Today `VirtualBoxParser.get_vm_info`
  and `get_disk_info` shell out from inside the parser (`parser.py:30`, `:56`),
  which is why the parser cannot be tested without VirtualBox. The backend
  acquires text; the parser takes text and returns a model. See `T-04`.
- **Decoders are shared across providers, not written per provider.** There are
  three shapes, not N: line-oriented key/value (VBoxManage machine-readable
  *and* VMware `.vmx` — same tokenizer, different quoting/comment rules), XML
  (libvirt), and JSON (Hyper-V, Proxmox). `core/decoders.py` holds all three.
  The *key vocabulary* is per-provider data; the tokenizing is not.
- **Field mapping is a table, not code.** This is where the redundancy actually
  lives today: 28 hand-written lookups-and-compares in one parser, each
  re-deciding how to read a boolean or coerce an int. See `A-11`.
- **Assembly** (grouping native entries into disks/NICs, resolving addresses) is
  the irreducibly provider-shaped part, but the pattern repeats: find the
  entries, build devices, allocate slots. Core supplies the loop and `slots.py`;
  the provider supplies a small extractor that says where the entries are.

Target cost of a new provider, once this is in place: a `tables.py` that is
mostly data, a `parser.py` of roughly 50–80 lines (pick decoder, declare
extractors), an `emitter.py` of similar size (table-driven plus native-artifact
assembly), a declarative `capabilities.py`, and a `backend.py` that mostly runs
a `Plan`. If a new provider needs more than that, the seam is wrong — fix
`core/`, do not grow the provider.

---

### Relationship to earlier phases

Three Phase 1–3 tasks are deliberately tactical and get generalized here — do
the small fix first, do not try to do both at once:

- **F-04** (optical drives) — do the minimal fix now (skip `createhd`, attach
  `emptydrive`). `M-01` replaces the conflated `DiskType` with a proper device
  model afterwards.
- **F-01** (synthesize controllers) — the resolution logic it adds becomes the
  shared allocator in `A-06`.
- **F-08** (capability-driven validation) — becomes matrix-driven in `A-02`.
- **E-13** (the libvirt/qemu keywords in `pyproject.toml`) is answered by this
  phase plus Phase 6, and is removed from the Tier C list.

### Target layout (additions marked NEW)

```
vmctl/
  core/
    vmconfig.py      canonical model — hypervisor-neutral, no vendor strings
    devices.py       NEW  DeviceKind / BusType / DiskFormat / Allocation / attachment
    capabilities.py  NEW  typed Capabilities + support-matrix types
    plan.py          NEW  Plan / Step — replaces List[List[str]]
    decoders.py      NEW  key/value, XML, JSON tokenizers — 3 for all providers
    codecs.py        NEW  OnOff / Int / EnumMap / ... — written once, used by all
    mapping.py       NEW  bidirectional field-table engine (A-11)
    translate.py     NEW  shared neutral<->native resolution + lossiness policy
    oscatalog.py     NEW  neutral guest-OS ids + per-provider tables
    slots.py         NEW  deterministic controller / slot / unit allocation
    convert.py       NEW  medium format conversion orchestration
    registry.py      NEW  provider registry + entry-point discovery
    engine.py        provider-agnostic (no `if provider == "virtualbox"`)
    batch.py  exceptions.py
  providers/
    base.py          BaseProvider + Parser / Emitter / Capabilities / Backend ABCs
    virtualbox/      tables.py (DATA)  parser  emitter  capabilities  backend
    libvirt/    NEW  same four files — emitter produces domain XML
    vmware/     NEW  same four files — emitter produces .vmx + vmrun
    hyperv/     NEW  same four files — emitter produces PowerShell
  serializers/  validators/  cli/
tests/
  providers/conformance/  NEW  one contract suite every provider must pass
```

---

### M — Storage and device model (the "all formats, all interfaces" work)

#### M-01 — Split the three axes the model currently conflates · L
`vmctl/core/vmconfig.py:22` — `DiskType{HDD, SSD, DVD}` mixes *what the guest
sees* (a disk vs an optical drive) with *a performance hint* (SSD), and
`StorageControllerType` is named after buses but its docstrings and values are
VirtualBox chipsets (`IntelAHCI`, `LsiLogic`). Neither survives a second
hypervisor. Replace with four independent enums in `core/devices.py`:

```python
class DeviceKind(Enum):     # what the guest sees
    DISK, CDROM, FLOPPY

class BusType(Enum):        # the interface it hangs off
    IDE, SATA, SCSI, SAS, NVME, VIRTIO_BLK, VIRTIO_SCSI, USB, FLOPPY

class DiskFormat(Enum):     # the backing file format
    VDI, VMDK, VHD, VHDX, QCOW2, QED, RAW, ISO

class Allocation(Enum):     # how space is claimed
    THIN, THICK             # sparse / preallocated
```

`SSD` stops being a type and becomes `nonrotational: bool` on the device, which
is what every hypervisor actually models it as.

#### M-02 — `StorageDevice` and `StorageController` · L
Replace `DiskConfig` with a device that can be a disk, a CD-ROM **or** a floppy,
keeping field names where they already fit so old configs still load:

```python
@dataclass
class StorageDevice:
    name: str
    kind: DeviceKind = DeviceKind.DISK
    controller: str = ""            # logical controller id, e.g. "sata0"
    slot: int = 0                   # port / bus number
    unit: int = 0                   # device on that slot (0/1 for IDE)
    size_mb: Optional[int] = None   # None for CDROM/FLOPPY
    format: Optional[DiskFormat] = None   # None => provider native default
    allocation: Allocation = Allocation.THIN
    source: Optional[str] = None    # existing image / ISO to attach
    readonly: bool = False
    nonrotational: bool = False     # was DiskType.SSD
    discard: bool = False           # TRIM/UNMAP passthrough
    hotpluggable: bool = False
    bootable: bool = False
    provider_options: Dict[str, Any] = field(default_factory=dict)

@dataclass
class StorageController:
    id: str                         # stable logical key: "sata0", "nvme0"
    bus: BusType = BusType.SATA
    model: Optional[str] = None     # neutral model name; chipset resolved by tables
    port_count: Optional[int] = None    # None => provider default
    bootable: bool = False
    native_name: Optional[str] = None   # round-trip fidelity ("SATA Controller")
```

The `id` / `native_name` split is the important part. Today
`DiskConfig.controller_name` is simultaneously the join key and the literal
VirtualBox string (`vmconfig.py:195`), which is why F-01's `"SATA"` magic value
exists. `id` is what devices reference; `native_name` is what a provider calls
it and is preserved only for faithful same-provider round trips.

#### M-03 — Declare the support matrix, once per provider · M
The point of "write once": all validation, defaulting and error messaging reads
this and nothing else. In `core/capabilities.py`:

```python
class Support(Enum):
    NATIVE, READ_WRITE, READ_ONLY, CONVERT_ONLY, UNSUPPORTED

@dataclass
class BusSpec:
    max_controllers: int
    max_slots: int              # ports per controller
    units_per_slot: int = 1     # 2 for IDE (master/slave)
    models: List[str] = field(default_factory=list)
    bootable: bool = True
    hotplug: bool = False

@dataclass
class Capabilities:
    provider: str
    formats: Dict[DiskFormat, Support]
    buses: Dict[BusType, BusSpec]
    attach: Dict[Tuple[DeviceKind, BusType], bool]   # <-- cdrom/floppy/disk per bus
    native_format: DiskFormat
    max_cpus: int; max_memory_mb: int; max_vram_mb: int
    max_network_adapters: int
    nic_models: Dict[NicModel, str]      # neutral -> native
    firmware: Dict[FirmwareType, Support]
    name_pattern: str; name_max_len: int
    min_version: str
    version_deltas: Dict[str, Dict[str, Any]]   # capability changes per version
```

The `attach` table is exactly what "all the interfaces for cdrom, floppy and
disk drive" needs to be checkable rather than aspirational. Sketch for
VirtualBox (**every cell to be verified against the H-07 version floor before
it is trusted — do not ship this table from memory**):

| | IDE | SATA | SCSI | SAS | NVMe | virtio-blk | virtio-scsi | USB | FDC |
|---|---|---|---|---|---|---|---|---|---|
| disk | ✓ | ✓ | ✓ | ✓ | ✓ (6.x+) | — | ✓ (7.x+) | ? | — |
| cdrom | ✓ | ✓ | ✓ | ? | — | — | ? | ? | — |
| floppy | — | — | — | — | — | — | — | — | ✓ |

**Verification task, not a guess:** on a host at the version floor, probe every
cell with `VBoxManage storagectl`/`storageattach` against a scratch VM and
record the result. Commit the output as a fixture; generate the table in the
docs from the capability declaration (H-06's "generate, don't hand-maintain"
rule) so it can never drift. Repeat per provider in Phase 6.

#### M-04 — Format is an option, everywhere · M
- `--disk-format {vdi,vmdk,vhd,vhdx,qcow2,raw}` on `create` / `import`, with the
  choices **filtered at parse time by the selected provider's matrix**, so
  `--provider libvirt --disk-format vdi` fails with a list of what that
  provider does support instead of failing later inside the hypervisor.
- `format: None` on a device means "use `capabilities.native_format`" — so a
  config stays portable by default and only pins a format when the user cares.
- Per-device override in config, plus a batch/global default.
- `Support.CONVERT_ONLY` drives M-05 instead of erroring.

#### M-05 — Medium conversion as a shared service · M
`core/convert.py` decides *what* conversion is needed from the matrix; providers
supply *how* (`VBoxManage clonemedium --format`, `qemu-img convert`,
`vmware-vdiskmanager`). One neutral API:

```python
convert(source: Path, target_format: DiskFormat, dest: Path) -> Plan
```

Returns a `Plan`, so conversion inherits dry-run, `--out`, and progress for
free. This is what makes cross-hypervisor moves real rather than nominal, and it
is also how E-03 (`--clone-disks`) should be implemented — one code path.

#### M-06 — Backward compatibility for existing configs · S
Old exports must keep loading (ground rule 2). In `from_dict`:

| 1.1.x input | Maps to |
|---|---|
| `type: hdd` | `kind: DISK` |
| `type: ssd` | `kind: DISK, nonrotational: true` |
| `type: dvd` | `kind: CDROM, size_mb: None` |
| `controller: sata` + `controller_name: "SATA Controller"` | controller `id` derived from bus + index; `native_name` preserved |
| `controller_name: "SATA"` (the old default) | treated as unset, resolved by `A-06` |
| `variant: thin/thick` | `allocation` (keep `variant` as an accepted alias) |

Keep `disks:` as an accepted alias for the new `storage:` key indefinitely, and
keep emitting whichever key the file used so a load/save cycle is not a
gratuitous diff.

---

### A — Core plumbing that makes providers pluggable

#### A-01 — `Plan` / `Step`: retire `List[List[str]]` · L  ← **the critical one**
`providers/base.py:49`, `core/engine.py:46` and `core/engine.py:58` all declare
`-> List[List[str]]`, and the CLI renders it with `' '.join(cmd)`
(`cli/main.py:298`, `:347`). That type *is* the VirtualBox CLI, and it cannot
express what other providers do: libvirt needs an XML document handed to
`define`, VMware needs a `.vmx` file written then `vmrun` called, Hyper-V needs
a PowerShell pipeline, Proxmox needs HTTP calls. Introduce in `core/plan.py`:

```python
class StepKind(Enum):
    EXEC, WRITE_FILE, API_CALL, CONNECT   # extend deliberately, not freely

@dataclass
class Step:
    kind: StepKind
    description: str                # one human line, always present
    argv: Optional[List[str]] = None
    path: Optional[Path] = None
    content: Optional[str] = None
    request: Optional[Dict[str, Any]] = None
    undo: Optional["Step"] = None   # enables real rollback
    destructive: bool = False

@dataclass
class Plan:
    provider: str
    steps: List[Step]
    warnings: List[str]             # where translation lossiness surfaces
    def render(self) -> str: ...            # dry-run text, any provider
    def to_json(self) -> dict: ...          # machine-readable plans
    def native_artifacts(self) -> Dict[Path, str]: ...   # XML / .vmx / .ps1
```

Everything downstream gets better for free: dry-run output stays uniform across
providers; `--out` can dump the native artifact for review or Git (E-16 below);
`undo` gives F-12 a real `--rollback-on-error`; `warnings` is where M-03's
matrix reports what it had to substitute; `to_json` serves E-07.

Migration is mechanical and worth doing in one commit: `emit_create_vm` returns
`Plan`, `backend.create_vm` executes one, the CLI calls `plan.render()`. Keep a
`Plan.as_argv_lists()` shim only if something external depends on the old shape.

#### A-11 — Declarative field mapping: one engine, N tables · L
*(design together with `A-01`; they are the two halves of the same seam)*

The per-field redundancy is measurable: `providers/virtualbox/parser.py` contains
**28 hand-written `config.get(...)` lookups and string compares**, each
independently deciding how to read a boolean, coerce an int, or match an enum.
Two Phase 1 bugs are direct symptoms rather than accidents — `F-02` (comparing
VirtualBox's `"EFI"` against lowercase `'efi'`) and the on/off compares next to
it exist *because* every field is decided separately. Multiply that by four
providers and both directions and the bug count multiplies with it.

Replace it with a mapping table interpreted by a shared engine. In
`core/codecs.py`, the coercions are written **once**:

```python
OnOff()          # "on"/"off"/"true"/"1", case-insensitive, both directions
Int()  Str()  Path()  MacAddr()  CSV()
EnumMap(table, ci=True, on_unknown=Policy.WARN)   # neutral <-> native enum
Const(value)     # emit always, ignore on read
```

In `core/mapping.py`, the engine walks a table in both directions — `read()`
builds the canonical model from the decoded intermediate structure, `write()`
contributes native key/values or `Plan` steps:

```python
# providers/virtualbox/tables.py  -- DATA, not logic
FIELDS = [
    Field("cpu.count",       native="cpus",           codec=Int()),
    Field("cpu.pae",         native="pae",            codec=OnOff()),
    Field("cpu.nested_virt", native="nested-hw-virt", codec=OnOff()),
    Field("memory.mb",       native="memory",         codec=Int()),
    Field("memory.vram_mb",  native="vram",           codec=Int()),
    Field("firmware.type",   native="firmware",       codec=EnumMap(FIRMWARE, ci=True)),
    Field("rtc_utc",         native="rtcuseutc",      codec=OnOff()),
    ...
]
```

What this buys, concretely:

- **Case-insensitivity, on/off handling and unknown-value policy are implemented
  once** and are then correct for every field of every provider. `F-02` becomes
  unrepeatable rather than fixed.
- **A field is added in one line, in one place**, and it is automatically
  bidirectional — which is the structural answer to `F-05` (ten fields that were
  parsed but never emitted, because read and write were separate hand-written
  code paths that drifted).
- **Round-trip fidelity is checkable generically**: one property test asserts
  that for every `Field` in every provider's table, `write(read(x)) == x`. That
  single test replaces per-field round-trip tests and would have caught `F-05`
  and `F-02` on day one.
- **`A-04`'s lossiness report comes for free**, because `EnumMap`'s
  `on_unknown` policy is the one place unknown values are handled.
- The table is also the natural source for `E-06`'s JSON Schema and `E-18`'s
  generated docs — one declaration, several outputs.

Scope discipline: tables cover **scalar and enum fields**, which is the large
majority and where the copy-paste lives. Collections (storage devices,
controllers, NICs) go through the assembly layer instead — core supplies the
iteration plus `slots.py`, the provider supplies an extractor describing where
its device entries live and how they are keyed. Do not try to force device
assembly into the field table; that is where declarative mapping stops paying.

**Sequencing:** build the engine and codecs while porting VirtualBox to a table
(so the first table is written against a provider whose behaviour is already
pinned by Phase 0's golden tests), and only then write `P-01`'s table. If the
VirtualBox port needs a codec that feels provider-specific, that is the signal
to fix the engine before a second provider inherits the problem.

#### A-02 — Typed capabilities, and make the validator read them · M
Replace the loose dict in `providers/virtualbox/capabilities.py:14` with the
`Capabilities` dataclass. `VMValidator` then validates against the matrix
instead of hardcoded literals (finishing F-08), and gains the checks a matrix
makes possible: illegal `(kind, bus)` pairs, slot/unit collisions, unsupported
format for the target provider, a name violating `name_pattern`, boot from a
bus that cannot boot.

#### A-03 — Provider registry and selection · M
`core/engine.py:28` hardcodes `if provider == "virtualbox"`. Replace with
`core/registry.py`: a decorator-based registry, plus discovery of external
providers via a `vmctl.providers` entry-point group.

- `-p/--provider` on every command, `VMCTL_PROVIDER` env var, config-file
  `provider:` key, and a default resolved by **detection** (which hypervisor is
  actually installed) rather than a hardcoded name.
- `vmctl providers` lists registered providers, whether each is usable on this
  host, and its detected version.
- Keep VirtualBox the default when several are present, for compatibility.
- Optional dependencies stay optional: `pip install vmctl[libvirt]`, with lazy
  imports so a missing binding degrades to "provider unavailable", never an
  import error at startup.

#### A-04 — Shared translation engine and a lossiness report · M
`core/translate.py` holds the resolution logic every provider would otherwise
duplicate: neutral value → native via the provider's tables; unsupported value →
policy. One policy flag, three behaviours:

- `strict` (default for `apply`/`migrate`): unsupported field is an error.
- `nearest`: substitute the closest supported value **and record a warning**.
- `convert`: allowed to insert conversion steps (M-05).

Every substitution or drop lands in `Plan.warnings` and is printed as a
**translation report** — "`virtio-blk` is not supported by virtualbox, used
`sata`; `qcow2` will be converted to `vdi`; `balloon_mb` has no equivalent and
was dropped." Silent lossy translation is the failure mode that makes a
multi-hypervisor tool untrustworthy; this is the guardrail against it.

#### A-05 — Neutral guest-OS catalog · M
`ostype: "Ubuntu_64"` (`vmconfig.py:319`) is a VirtualBox identifier sitting in
the canonical model, and the display-name→VBox-ID map is hardcoded *inside the
emitter* (`emitter.py:31`) — the wrong layer twice over. Move to
`core/oscatalog.py`: neutral ids (recommend libosinfo-style short ids, e.g.
`ubuntu22.04`, `rhel9`, `win11`, plus `family`/`version`/`arch`), and one
translation table per provider (`Ubuntu_64`, `ubuntu-64`, Hyper-V generation,
libvirt osinfo metadata). Accept raw provider strings as a passthrough so
existing configs keep working.

#### A-06 — Deterministic slot/address allocation · M
Providers address devices differently: VirtualBox by controller name + port +
device; libvirt by `target dev` (`vda`, `sda`) + bus + address; Hyper-V by
controller type + number + location. Shared `core/slots.py` assigns
`controller`/`slot`/`unit` deterministically when the config omits them
(respecting `BusSpec.units_per_slot` so IDE master/slave is right), and each
provider renders addresses from that. Deterministic means the same config
produces the same layout every run — a precondition for E-01 `diff` and E-02
`apply` to be meaningful.

#### A-07 — Provider conformance suite · L
The mechanism that keeps "write once" honest. `tests/providers/conformance/`
holds one parameterized suite that every registered provider must pass:

- capability declaration is internally consistent (`attach` only references
  buses in `buses`; `native_format` is `NATIVE` in `formats`);
- parser(fixture) → emitter → parser is stable (idempotent round trip);
- every `(kind, bus)` the matrix claims actually emits a well-formed plan;
- unsupported values raise or warn per policy, never silently vanish;
- emitted artifacts survive hostile names (see A-08);
- `Plan` steps all carry a `description`, and destructive steps are flagged.

A provider that does not pass is not registered. This is what lets a fourth
provider be added confidently by someone who did not write the first three.

#### A-08 — Escaping and injection safety for non-argv emitters · M ⚠ new risk
Today every command is an argv list with no shell, so VM names are safe.
The moment emitters produce **XML, PowerShell, `.vmx` files or shell scripts**,
a VM name or description becomes injection-capable — `<name>&`, `"; rm -rf`,
`$(...)`, `'; Remove-VM`. Requirements:

- each provider supplies escaping helpers (`xml_escape`, `ps_quote`,
  `vmx_quote`) and the emitter must route every interpolated value through one;
- `Capabilities.name_pattern` is enforced *before* emission;
- the conformance suite includes a hostile-input case per provider with names
  containing quotes, angle brackets, `$`, backticks, newlines and `;`;
- `--out` written artifacts get restrictive permissions, since they may embed
  paths and (later) credentials.

#### A-09 — Storage location abstraction · S
VirtualBox uses a machine folder (F-13), libvirt uses storage pools and volumes,
Proxmox uses storage ids, VMware uses datastores. Add a neutral
`StorageLocation` (`pool` or `directory`) resolved per provider, defaulting to
the provider's own default. Without this, every path in a config is
host-specific and the config stops being portable.

#### A-10 — Neutral fields the model is missing for other hypervisors · M
Add now, capability-gated, rather than discovering them mid-provider:

- `arch` (`x86_64`, `aarch64`) and `machine` (`q35`, `i440fx`) — required by
  libvirt/QEMU, ignored by VirtualBox.
- CPU topology: `sockets` / `cores` / `threads` and optional `cpu.model`
  (host-passthrough vs a named model) — libvirt and Hyper-V both model this;
  VirtualBox only has a count, so `nearest` policy collapses it and reports.
- `NicModel` enum (`virtio`, `e1000`, `e1000e`, `rtl8139`, `vmxnet3`) replacing
  the raw `"82540EM"` string in `vmconfig.py:232`, with per-provider tables.
- NAT port-forwarding rules (E-10) — neutral, since every provider has them.
- Second wave, behind capability flags: serial/console, video model, watchdog,
  shared folders, USB controller version, `boot_menu`, cloud-init/ignition seed.

---

## Phase 6 — Additional hypervisors

One provider at a time, each gated on the conformance suite (A-07). The order is
chosen so that the abstraction is stress-tested early rather than confirmed
late.

#### P-01 — libvirt / QEMU-KVM · L  ← do this one first
Deliberately the least similar to VirtualBox, which is the point: it is
**declarative** (define a domain XML) where VirtualBox is imperative (a
sequence of CLI calls). If `Plan` and the four-file contract survive libvirt,
they will survive anything else. It is also the highest-value target on Linux.

- `emitter.py` builds a domain XML document (`WRITE_FILE` + `EXEC virsh define`,
  or `API_CALL` via the `libvirt` python binding when installed).
- `parser.py` reads `virsh dumpxml` — XML in, canonical model out, no shelling
  out inside the parser.
- Native formats `qcow2` / `raw`; `vmdk`/`vdi` as `READ_WRITE` or
  `CONVERT_ONLY` per what qemu actually supports at the pinned version.
- Buses: `virtio-blk`, `virtio-scsi`, `sata`, `ide`, `nvme`, `usb`, `fdc` —
  this is the provider that justifies M-01's full `BusType` list.
- Firmware: BIOS (SeaBIOS) vs UEFI (OVMF loader paths), secure boot, TPM via
  swtpm.
- Storage pools (A-09), `qemu-img` as the conversion backend (M-05).
- Optional dependency: `vmctl[libvirt]`, lazy-imported.

**Validated against a live libvirt (2026-09-27)** by defining a hand-written
domain and reading it back with `dumpxml`. Three findings that shape the design:

- **libvirt normalises and *adds to* what you define.** A 20-line domain came
  back with a generated UUID, `machine='q35'` canonicalised to
  `pc-q35-rhel9.8.0`, an inserted `<cpu mode='custom'><model>qemu64</model>`,
  PCI addresses on every device, and auto-added USB / SATA / pcie-root
  controllers. So an emitter must *under-specify* and let libvirt allocate,
  which is the opposite of VirtualBox where vmctl picks port/device itself.
  `A-06` must therefore treat addresses as "pinned only if the user pinned
  them", per provider.
- **This confirms `A-07`'s conformance assertion is correctly formulated** as
  *parse → emit → parse is stable*, not *emit == original input*: for libvirt
  the second generation differs from the first by design, and only the third
  onwards is fixed. A naive byte-equality test would fail forever.
- **`cdrom` on `sata`, `disk` on `virtio-blk`, and a `virtio-scsi` controller
  all defined cleanly**, so `M-03`'s `(DeviceKind, BusType)` matrix can be
  probed here rather than guessed.

#### P-02 — VMware Workstation / Fusion · M
- `.vmx` is a key/value file: `WRITE_FILE` plus `vmrun` for lifecycle.
- `vmdk` only — the cleanest test of `CONVERT_ONLY` in the matrix.
- Buses: IDE, SATA, SCSI (with `lsilogic` / `pvscsi` models), NVMe.
- `vmware-vdiskmanager` as the conversion backend.
- ESXi/vCenter via `govc` is a separate provider later, not this one.

#### P-03 — Hyper-V · M
- Emitter produces PowerShell (`New-VM`, `Set-VM`, `New-VHD`,
  `Add-VMHardDiskDrive`); backend runs `pwsh`/`powershell`.
- `vhdx` native, `vhd` legacy; generation 1 ↔ BIOS, generation 2 ↔ UEFI is a
  clean example of a capability-gated firmware mapping.
- Buses: IDE (gen 1 only) and SCSI — the matrix earns its keep here, since
  gen 2 forbids IDE entirely.
- First provider whose emitter output is a script, so A-08's escaping rules are
  mandatory, not theoretical.

#### P-04 — Proxmox VE · M (optional)
REST API / `qm`. Exercises `StepKind.API_CALL` and remote (non-localhost)
operation, which in turn forces the connection/credential design
(`CONNECT` step, no secrets in plan output or `--out` artifacts). Worth doing
mainly to prove the API path exists before someone needs it.

#### P-05 — Plain QEMU · S
Argv generation, largely reusing libvirt's tables. Cheap once P-01 exists;
useful for throwaway VMs and CI.

#### P-06 — `vmctl migrate --from A --to B <vm>` · L  ← the payoff
The feature that only a genuinely neutral model can offer: read a VM from one
hypervisor, translate, convert its disks (M-05), create it on another. Dry-run
by default, printing the full translation report (A-04) before anything runs.
This is the strongest argument for the whole phase, and it is also the most
honest test of it — anything the model gets wrong shows up here immediately.

---

## Phase 7 — Enhancements

Tiered by how much they advance the stated goal (Infrastructure as Code for a
local VirtualBox lab). None of these should start before Phase 1 lands; the Tier A items that touch
the model (E-03, E-05) want Phase 5 first.

### Tier A — completes the promise

- **E-01 `vmctl diff <vm> <file>` · M.** Show the drift between a live VM and a
  config file, as a field-level diff. This is the single most valuable missing
  command for a config-as-code tool, it is read-only and safe, and it reuses
  the parser plus the serializer you already have. Also the fastest way for a
  user to see that a round trip was faithful.
- **E-02 `vmctl apply <file>` (idempotent converge) · L.** Create when absent,
  otherwise emit only the `modifyvm`/`storageattach` calls needed to reconcile.
  Dry-run by default, printing the plan like `import` does. This is the real
  endgame: `apply` makes the tool declarative rather than one-shot. Depends on
  E-01 and F-10.
- **E-03 `--clone-disks` on `import`/`create` · M.** Every doc warns that disk
  contents are not copied. `VBoxManage clonemedium` can copy them when the
  source is local. Opt-in, with a clear size/time warning up front.
- **E-04 `vmctl export --all -d <dir>` · S.** The README's "lab snapshots" use
  case currently needs a shell loop. One command, one file per VM, plus a
  manifest — and it turns the whole lab into something committable.
- **E-05 Live capability probing · M.** Replace parts of the static
  `capabilities.py` dict with real queries: `VBoxManage list ostypes`,
  `list bridgedifs`, `list hostonlyifs`, `list systemproperties`. This turns
  three currently-unused capability keys into real validation — catching
  "ostype `Ubuntu24_64` does not exist on this host" and "bridged adapter
  `eth0` does not exist here" at validate time instead of mid-create. Cache per
  process; keep the static dict as the offline fallback so tests stay
  hermetic.
- **E-06 `vmctl schema -o vmctl.schema.json` · S.** Emit a JSON Schema for the
  config format. Editors then autocomplete and validate config files in place,
  which is exactly what config-as-code users expect. Generate it from the
  dataclasses so it cannot drift; have CI assert the committed copy is current.

- **E-16 `--out <path>` native artifact export · S.** Once `A-01` exists, dump
  what would run — a shell script, a libvirt domain XML, a `.vmx`, a PowerShell
  script — instead of only printing it. Reviewable, committable, and the natural
  bridge for anyone who wants to hand the artifact to their own tooling.
- **E-17 `vmctl capabilities [-p provider]` · S.** Print the support matrix:
  formats, buses, which device kinds attach to which bus, limits, detected
  version. Answers "can this hypervisor do NVMe CD-ROM?" without reading source,
  and it is the same declaration the docs and the validator read (M-03).

### Tier B — quality of life

- **E-07 `--json` output on `list` / `status` / `validate` · S.** The README
  pitches CI pipelines; machine-readable output is what those need. `simple`
  covers names only today.
- **E-08 `-v/--verbose` command echo · S.** With `--execute`, print each
  VBoxManage command as it runs, so a mid-batch failure is diagnosable. Pair
  with a `--quiet`.
- **E-09 Snapshots · M.** `vmctl snapshot take|list|restore|delete`. A thin
  wrapper over `VBoxManage snapshot`, and directly serves the documented
  "before a destructive test" workflow.
- **E-10 Port-forwarding rules for NAT · M.** `NetworkConfig` has no
  `port_forwards`, so a NAT VM's SSH forward is lost on every round trip — a
  very common lab setting.
- **E-11 Config profiles / includes · M.** Let a config reference a base file
  (`extends: base.yaml`) so the batch file's base/override idea works for
  single VMs too, without duplicating YAML across a lab.
- **E-12 `vmctl doctor` · S.** One command reporting VBoxManage presence and
  version, default machine folder, free disk space, host RAM, and kernel module
  status. Cheap to write and it will absorb a lot of support questions.

### Tier C — strategic

- **E-13 — superseded.** The multi-provider question this used to pose is
  answered by **Phase 5** (portable core) and **Phase 6** (the providers
  themselves). The `pyproject.toml` keywords advertising `libvirt` and `qemu`
  stay, but only become true at P-01; until then H-06's docs pass must not claim
  multi-hypervisor support.
- **E-14 `schema_version` field in configs · S.** Add it now, while there are
  few users, so later format changes can migrate rather than break. Default it
  for files that lack it.
- **E-15 Plugin entry points · M.** `vmctl.providers` and `vmctl.serializers`
  group entry points so providers can ship as separate packages. Delivered as
  part of `A-03`; keep it here as the public, documented contract for
  third-party providers once P-01 has proved the abstraction.
- **E-18 Generated compatibility docs · S.** Render the per-provider matrices
  and the command reference from the code (`sphinx-click` is already a
  dependency). Hand-maintained tables across two Sphinx trees, `features.md` and
  the README are why the docs drifted in the first place.
- **E-19 `vmctl selftest -p <provider>` · M.** Create / start / stop / delete a
  throwaway VM on a real host and assert each step. The conformance suite
  (A-07) proves the *translation* is right offline; this proves the hypervisor
  agrees. The natural gate for a nested-virt CI runner.

---

## Release sequencing

- **v1.1.10 — trust** *(Phase 0 + F-06/F-07 + H-01…H-04)*
  No behaviour change to the happy path except `F-14`: emitted command *order*
  is now stable (content unchanged), which also removes a non-deterministic
  wrong-controller attachment. Tests, CI, hygiene, and `validate`
  stops throwing tracebacks. Safe to ship fast.
- **v1.2.0 — the round trip actually works** *(Phase 1: F-01…F-05, F-13, plus
  F-08)* The headline release. `import` of a hand-written config works, EFI VMs
  survive, ISOs survive, no field is silently dropped. Needs a changelog note
  that exports from 1.1.x now recreate *differently* — that is the fix, and it
  should be stated plainly.
- **v1.3.0 — CLI honesty** *(Phase 3: F-09…F-12, L-01…L-07, H-05…H-08)*
  `edit` works or is renamed, completion works, batch is safe, docs match code.
- **v1.4.0 — portable core, one provider** *(Phase 5: M-01…M-06, A-01…A-10)*
  Still VirtualBox-only on the outside, but every seam is in place: full device
  model (disk / cdrom / floppy across IDE, SATA, SCSI, SAS, NVMe, virtio, USB,
  FDC), `--disk-format` driven by a real support matrix, `Plan` instead of argv
  lists, provider registry, conformance suite. The user-visible wins are the new
  buses and formats; the invisible win is that v1.5.0 becomes small.
  Ship `A-01` (Plan) and `A-11` (field-table mapping) as their own release
  candidate — together they touch every layer, and every later provider
  inherits whatever they get wrong.
- **v1.5.0 — second hypervisor** *(P-01 libvirt/QEMU-KVM)* The release that
  proves the abstraction. If P-01 needs core changes, they are abstraction bugs:
  fix them in `core/`, never with a special case in the provider.
- **v1.6.0 / v1.7.0 — more providers** *(P-02 VMware, P-03 Hyper-V; P-04/P-05
  as demand appears)* Each should be additive only — a new provider that forces
  a core change means A-07's conformance suite was too weak.
- **v1.8.0 — Tier A features** *(E-01 diff, E-04 export --all, E-06 schema,
  E-16 --out, E-17 capabilities, then E-03 clone-disks, E-05 probing)*
- **v2.0.0 — declarative + cross-hypervisor** *(E-02 `apply`, P-06 `migrate`)*
  Batch every breaking config change here: `DiskConfig`→`StorageDevice` key
  rename, `ballooning`→`balloon_mb`, `ostype`→neutral `guest_os`, and make
  `schema_version` (E-14) mandatory for new files.

**Sequencing rule:** no provider ships before the conformance suite it must
pass. No feature in Tier A/B ships before the model it depends on
(`diff` and `apply` both need A-06's deterministic allocation to be meaningful).

---

## Backward-compatibility contract

Configs exported by 1.1.x must keep loading through 2.x. Concretely:

| Change | Compatibility measure |
|---|---|
| `DiskConfig.controller_name` default `"SATA"` → unset (F-01, M-02) | still accept `"SATA"`; resolve as "unset" when no controller by that name exists |
| `memory.ballooning: bool` → `balloon_mb: int` (F-05) | accept the bool on load; `True` warns and is dropped |
| Unknown keys now error (F-06) | only *unknown* keys error; every 1.1.x-emitted key stays valid. Run the T-02 fixtures' exports through the new loader as a test |
| DVD disks gain `iso_path` / `source` (F-04, M-02) | optional field, defaults to `None` |
| `DiskType{hdd,ssd,dvd}` → `DeviceKind` + `nonrotational` (M-01) | mapped on load per the M-06 table; `type:` stays an accepted alias |
| `disks:` → `storage:` (M-02) | `disks:` accepted indefinitely; save re-emits whichever key the file used |
| `variant` → `allocation` (M-01) | `variant` stays an accepted alias |
| `ostype` → neutral `guest_os` (A-05) | raw provider strings accepted as passthrough forever |
| `adapter_type: "82540EM"` → `NicModel` (A-10) | raw native strings accepted as passthrough |
| `List[List[str]]` → `Plan` (A-01) | internal API; `Plan.as_argv_lists()` shim if anything external depends on it |
| `schema_version` added (E-14) | absent means "1", accepted forever |

Two tests enforce this, in every release:
1. load a committed, 1.1.9-produced export file and assert it still parses;
2. load a committed 1.1.9 file, save it, and assert the diff is empty.

---

## Risk register

- **No VirtualBox on this dev box.** Every parser/emitter test is designed to be
  hermetic (fixtures + mocked `subprocess`), but T-02's capture, M-03's matrix
  probing, and any end-to-end verification need a real host. Book that time
  before Phase 1, or Phase 1 ships unverified against the real thing.
  *Mitigated for VirtualBox:* this VM's Windows host runs VirtualBox 7.1.18 and
  is reachable read-only over `ssh winhost` (see `~/HOST-ACCESS.md`), so
  captures and matrix probes can be driven from here.
  *Resolved for libvirt:* qemu 10.1.0 + libvirt 11.10.0 + `python3-libvirt` are
  now installed locally (2026-09-27), so P-01 can be developed and probed on
  this box. Only **TCG** is available — the host VM has `nested-hw-virt="off"`,
  so there is no `/dev/kvm` and `<domain type='kvm'/>` is not offered. That is
  sufficient for P-01, which needs libvirt to *define and read* domains, not to
  boot them quickly. Booting a guest for E-19 `selftest` would need nested virt
  enabled on the host VM (a power-cycle of this machine).
- **The support matrices must be probed, not recalled.** Every cell in M-03 and
  in each provider's table is a version-specific claim about a third-party tool.
  Probe on a real host at the version floor, commit the probe output as a
  fixture, and generate the docs from the declaration. A matrix written from
  memory is worse than no matrix, because validation will then confidently
  reject valid configs.
- **`A-01` (Plan) touches every layer** — providers, engine, CLI, tests. Land it
  alone, behind the Phase 0 golden tests, not bundled with feature work.
- **Declarative mapping can be over-applied** (A-11). Scalar and enum fields
  belong in the table; device assembly does not. A table that grows conditionals
  and callbacks to express disk layout has become code again, with worse
  ergonomics than the code it replaced. The stop line is written into A-11 —
  hold it.
- **Abstraction drift.** The realistic failure mode for multi-provider support is
  a provider that "almost" fits and gets a special case in `core/`. The
  conformance suite (A-07) and the four-file rule are the defences; treat any
  `if provider ==` in `core/` as a bug.
- **Injection risk is new** (A-08). VirtualBox's argv lists are safe by
  construction; XML, PowerShell, `.vmx` and shell output are not. This must be
  designed in from the first non-argv provider, not retrofitted.
- **Silent lossy translation** is the credibility risk for cross-hypervisor work.
  A-04's translation report is not optional polish — without it `migrate`
  produces VMs that differ from the source in ways nobody was told about.
- **F-01 could change output for configs that already work.** Mitigation: the
  golden tests from T-03 must be byte-identical for every exported-config
  fixture; only the empty-`storage_controllers` case may change.
- **Hypervisor version drift** is the main source of "works on my machine".
  H-07's version floor generalizes in Phase 6 to `Capabilities.min_version` +
  `version_deltas` per provider; without it, every emitter is a guess.
- **Optional dependencies must stay optional.** `pip install vmctl` must not
  require libvirt bindings, PowerShell or VMware tooling. Extras plus lazy
  imports plus "provider unavailable" reporting (A-03).
- **Doc surface is large and duplicated** — README, `docs/features.md`,
  `docs/USER_GUIDE.md`, two Sphinx trees (English + Arabic), two HTML landing
  pages. Every behaviour change touches several. Generate the command reference
  (`sphinx-click` is already a dependency) and the capability matrices instead
  of hand-maintaining them.
- **Scope creep.** The Phase 1 bugs are what make the tool untrustworthy;
  neither a second hypervisor nor a new bus helps until a restored VM boots.
  Resist reordering.

---

## Flat task checklist

```
Phase 0  [x] T-01 test scaffolding        [x] T-02 capture fixtures (real, VirtualBox 7.1.18)
         [x] T-03 golden parser/emitter/roundtrip/CLI tests
         [x] T-04 inject MediumProbe so the parser runs without VirtualBox
         [x] F-14 controller order made deterministic (test-enabling fix)
Phase 1  [x] F-01 synthesize controllers  [x] F-02 firmware case + EFI64/32
         [x] F-03 hyphenated keys regex   [x] F-04 optical drives (minimal)
         [x] F-05 ten unemitted fields    [x] F-13 machine folder from VBox
         [x] F-15 unmapped controller types must not default to SATA
         [x] F-16 unescape \\ and \" when decoding machinereadable values
         [x] F-17 secure boot: invalid emit + unreadable parse
         [x] F-18 showmediuminfo device type (dvd) for optical media
         [x] F-19 hostonlyadapter{n} not hostonlyif{n}; verify natnet{n}
         [x] F-20 'Format variant:' prefix -> variant always thin
         [x] F-21 audio="default" misread as audio enabled
         [ ] F-22 empty removable drive dropped entirely (narrow fix or M-02)
Phase 2  [x] F-06 friendly config errors  [x] F-07 from_dict must not mutate
         [x] F-08 real warnings; pure validator; port-collision check
Phase 3  [ ] F-09 completion env var      [ ] F-10 make `edit` edit
         [ ] F-11 neuter BaseProvider.edit_vm
         [ ] F-12 batch names + preflight + failure handling
         [ ] L-01..L-04, L-06, L-07 small CLI/model cleanups  [x] L-05 engine no-op wrappers
Phase 4  [ ] H-01 .gitignore              [ ] H-02 drop _build from git
         [ ] H-03 single-source version   [ ] H-04 CI
         [ ] H-05 CHANGELOG + release.sh  [ ] H-06 docs truth pass
         [ ] H-07 VBox version floor      [ ] H-08 prune exceptions
Phase 5  [ ] M-01 DeviceKind/BusType/DiskFormat/Allocation split
         [ ] M-02 StorageDevice + StorageController (id vs native_name)
         [ ] M-03 support matrix incl. (kind x bus) attach table  + PROBE it
         [ ] M-04 --disk-format option, provider-filtered choices
         [ ] M-05 shared medium conversion      [ ] M-06 config back-compat mapping
         [ ] A-01 Plan/Step replaces List[List[str]]   <-- land alone
         [ ] A-11 field-table mapping engine + codecs + decoders (with A-01)
         [ ] A-02 typed Capabilities, matrix-driven validator
         [ ] A-03 provider registry + --provider + entry points
         [ ] A-04 translation engine + policy + lossiness report
         [ ] A-05 neutral guest-OS catalog     [ ] A-06 deterministic slot allocation
         [ ] A-07 provider conformance suite   [ ] A-08 escaping / injection safety
         [ ] A-09 storage location abstraction [ ] A-10 arch/machine/topology/NicModel
Phase 6  [ ] P-01 libvirt/QEMU-KVM (first)  [ ] P-02 VMware Workstation/Fusion
         [ ] P-03 Hyper-V                    [ ] P-04 Proxmox (optional)
         [ ] P-05 plain QEMU                 [ ] P-06 vmctl migrate --from/--to
Phase 7  [ ] E-01 diff   [ ] E-04 export --all  [ ] E-06 schema
         [ ] E-16 --out native artifacts       [ ] E-17 capabilities command
         [ ] E-03 clone-disks  [ ] E-05 capability probing
         [ ] E-02 apply  [ ] E-07..E-12 Tier B  [ ] E-14/E-15/E-18/E-19 Tier C
```
