# Test fixtures

Captured hypervisor output. The suite parses these instead of talking to a real
hypervisor, which is what lets `pytest` run anywhere.

## Provenance: real captures from VirtualBox 7.1.18

These were captured from purpose-built VMs on a real Windows host
(VirtualBox 7.1.18r173720) with `../../scripts/capture-fixtures.sh`, then the
VMs were deleted. Each was a 128 MB / 1 vCPU machine with 64-128 MB media, built
to exercise one shape of configuration.

Re-capturing on a different VirtualBox version is expected to produce golden
diffs; read them rather than accepting them, and regenerate with
`python tests/regenerate_golden.py`.

### What the captures established

The synthetic fixtures these replaced encoded six assumptions. All six were
checked against the real host; two were wrong, and both mattered:

| Assumption | Verdict |
|---|---|
| `ostype` is the display name (`"Ubuntu (64-bit)"`) | correct |
| `firmware` is upper case (`BIOS`/`EFI`/`EFI32`/`EFI64`) | correct — so F-02 is real |
| `nested-hw-virt` is hyphenated | correct — so F-03 is real |
| a floppy controller's type is `I82078` | correct — so F-15 is real |
| empty drives read `emptydrive` | correct |
| metadata keys are suffixed (`"SATA-0-0-nonrotational"`) | **wrong**: they are infixed, `"IDE Controller-nonrotational-0-0"` |
| the medium variant line starts `Variant:` | **wrong**: 7.1 prints `Format variant:` — this hid F-20 |

The captures also exposed five bugs no synthetic fixture would have shown:
F-16 (backslashes are escaped in values), F-18 (`showmediuminfo` needs a device
type for an ISO), F-19 (`hostonlyadapter{n}`, not `hostonlyif{n}`), F-20 (the
variant prefix above) and F-21 (`audio="default"` does not mean audio is on).

### One deliberate mismatch with production

`showmediuminfo_iso_attached_0.txt` was captured with the `dvd` device type,
because that is the only way to read an ISO's medium info. Production code does
not probe removable media at all (see F-18/F-04), so this fixture describes what
a probe *would* return rather than something vmctl asks for.

## Layout

| File | Purpose |
|---|---|
| `showvminfo_<label>.txt` | `VBoxManage showvminfo <vm> --machinereadable` |
| `showmediuminfo_<label>_<n>.txt` | `VBoxManage showmediuminfo <medium>` |
| `showmediuminfo_<label>_<n>.path` | the medium path that `.txt` describes, so the fake probe can map it |
| `list_vms.txt` | `VBoxManage list vms` |
| `version.txt` | `VBoxManage --version` |
| `configs/v1_1_9_*.{yaml,json}` | **genuine** exports produced by the shipped v1.1.9 code |

`configs/v1_1_9_*` are the exception: those *are* real artifacts of this
codebase, and `test_compat.py` asserts they keep loading forever. Never
regenerate them — regenerating would defeat the guarantee they encode.

## The labels and what each one covers

| Label | Covers |
|---|---|
| `bios_minimal` | baseline: BIOS, 1 SATA disk, 1 NAT NIC; disk-metadata keys that must not be mistaken for disks |
| `efi_secureboot` | EFI64 + TPM 2.0 + nested virt + PAE + fixed-allocation disk (`F-02`, `F-03`, `F-20`). Note: VirtualBox reports no secure-boot state at all (`F-17`) |
| `iso_attached` | optical drive with an ISO, an `emptydrive` floppy, a `none` attachment (`F-04`) |
| `multidisk` | 3 disks, 2 controllers, VDI + VMDK + VHD (one `--variant Fixed`), a controller name containing a dash, and 15 empty `none` slots |
| `multinic` | bridged / host-only / internal / NAT, with adapter names containing spaces, parentheses and `#`; `rtcuseutc="off"` |
| `floppy_first` | a floppy controller with a *lower* index than the SATA controller (`F-14`, `F-15`) |
