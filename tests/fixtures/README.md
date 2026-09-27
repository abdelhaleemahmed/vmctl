# Test fixtures

Captured hypervisor output. The suite parses these instead of talking to a real
hypervisor, which is what lets `pytest` run anywhere.

## ⚠ Provenance: these are SYNTHETIC and need verifying

The fixtures in this directory were **hand-authored**, not captured from a real
VirtualBox host, because the machine the suite was written on has no VirtualBox
installed (`T-02` in `../../PLAN.md`). They follow the documented shape of
`VBoxManage showvminfo --machinereadable` and `showmediuminfo`, and they are
internally consistent — but they are a model of VirtualBox's output, not
evidence of it.

**Before trusting Phase 1's fixes, replace them with real captures:**

```bash
./scripts/capture-fixtures.sh --list                      # see available VMs
./scripts/capture-fixtures.sh bios_minimal   <a-bios-vm>
./scripts/capture-fixtures.sh efi_secureboot <an-efi-vm>
./scripts/capture-fixtures.sh iso_attached   <a-vm-with-an-iso>
./scripts/capture-fixtures.sh multidisk      <a-multi-disk-vm>
./scripts/capture-fixtures.sh multinic       <a-multi-nic-vm>
./scripts/capture-fixtures.sh floppy_first   <a-vm-with-a-floppy-controller>

rm tests/fixtures/SYNTHETIC          # once every label is real
python tests/regenerate_golden.py    # review the diff carefully
pytest -q
```

A golden diff after re-capturing is *expected* and *informative*: it is the
difference between what we assumed VirtualBox emits and what it actually emits.
Read it rather than accepting it.

### Assumptions to check first

These are the specific guesses that, if wrong, would invalidate tests:

1. **`ostype` is the display name, not the internal id** — the fixtures use
   `ostype="Ubuntu (64-bit)"`. The emitter's display-name→id map
   (`emitter.py:31`) only makes sense if this is true, but confirm it.
2. **`firmware` is capitalised** — `firmware="BIOS"` / `"EFI"`. `F-02` depends
   entirely on this. If VirtualBox emits lowercase, `F-02` is not a bug.
3. **`nested-hw-virt` is the real key spelling** (hyphens) — `F-03` depends on
   it.
4. **An ISO's `showmediuminfo` reports `Storage format: RAW`** — this drives the
   `.img` extension in the `iso_attached` golden file.
5. **A floppy controller's type is `I82078`** — `F-15` depends on that string
   being absent from the parser's controller map.
6. **Optical/floppy attachments read `emptydrive`** for an empty drive.

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
| `efi_secureboot` | EFI + secure boot + TPM + nested virt + fixed-allocation disk (`F-02`, `F-03`) |
| `iso_attached` | optical drive with an ISO, an `emptydrive` floppy, a `none` attachment (`F-04`) |
| `multidisk` | 3 disks, 2 controllers, VDI + VMDK + VHD, a controller name containing a dash |
| `multinic` | all four addressable network modes, each reading its name from a different key; `rtcuseutc="off"` |
| `floppy_first` | a floppy controller with a *lower* index than the SATA controller (`F-14`, `F-15`) |
