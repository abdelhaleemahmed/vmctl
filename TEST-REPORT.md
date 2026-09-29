# vmctl hands-on test report

Generated 2026-09-29 by `scripts/hands-on-report.py` from the results of `scripts/hands-on-matrix.py`.

## What this is, and why it is not the test suite

The pytest suite and `vmctl selftest` are both written against vmctl's own idea of itself. When that idea is wrong they agree with each other and pass: 1194 tests were green while `vmctl export vm -o vm.json` wrote YAML into the file. So this report comes from driving the command line the way a person does, on real hypervisors, and judging the result by reading the files that land on disk.

Every VM here is a real VM -- created, read back, exported, re-imported and deleted. None is started: the host has other VMs running, so these are built and removed without ever being powered on.

## What each case does

For one (disk format, bus) pair:

1. write the spec as YAML, and `validate` it
2. `import --execute` -- a real VM appears
3. `list` -- the hypervisor agrees it is there
4. `import` the same file again -- must be refused, not silently doubled
5. `import` with `--new-name`, `--set memory.mb=256`, `--set cpu.count=2` and `--add-disk` -- more RAM and more CPUs than the file says, plus hardware the file does not describe
6. `read --format json` -- did the overrides actually reach the hypervisor?
7. `export -o <name>.json` -- and the file is parsed as JSON to prove it is JSON
8. `import` that JSON -- the round trip
9. `diff` the export against the VM it came from -- should report no changes
10. `delete` all three, and `list` again to confirm nothing is left

The (format, bus) pairs are taken from each provider's own measured capabilities, never a list written by hand, so every format it can create and every bus that can carry a disk appears at least once.

## Result

| | |
|---|---|
| hypervisors | 4 |
| real VMs created and deleted | 21 |
| checks | 336 |
| failed | **0** |

| hypervisor | VMs | checks | failed |
|---|---|---|---|
| libvirt | 4 | 64 | 0 |
| qemu | 5 | 80 | 0 |
| virtualbox | 7 | 112 | 0 |
| vmware | 5 | 80 | 0 |

## What these runs found

**`export -o vm.json` wrote YAML into the file**

Found on libvirt before the matrix existed, by exporting one real VM. `--format` carried a default, so the command could not tell "not given" from "given as yaml" and the inference its own help promised never ran. `json.load` on the result failed at line 1. Nothing in the suite had ever asked for a `.json` file without also passing `--format`.

**A libvirt VM could not be exported and recreated while the original existed**

An export carries the domain's UUID, so importing it under a new name asked libvirt to define a second domain with the first one's identity: `domain 'x' is already defined with uuid ...`. The tool's central promise, failing on a whole provider. Caught by all four libvirt cases at step 8.

**A QEMU VM with two disks on `virtio-scsi` or `usb` could not be re-imported**

The `.0` after those controllers is the controller's own bus, shared by every device on it, so it cannot carry a device's address -- and the emitter left the address out. QEMU assigned LUNs itself and the command line, which for this provider *is* the VM, did not record which disk was which. Both read back at port 0 and the export failed validation for colliding slots. Measured against qemu-kvm 10.1.0 before fixing: `scsi-hd` takes `scsi-id`, `usb-storage` takes `port`.

**libvirt warned that the I/O APIC was off, and could not be asked for it either**

Every libvirt case printed "an x86 guest needs an I/O APIC to use them, and libvirt will not give it one" -- for a VM that was fine. Measured before changing anything: `info qtree` on q35 with `-smp 2` and nothing asking for an interrupt controller reports `dev: ioapic`, and each CPU carries a `/lapic (apic)`; the same on `pc`. Neither can be removed -- libvirt's `<ioapic>` only selects which component emulates one -- so the warning named a knob that does not exist. Following it up found the field was writing `<apic/>` -- the *local* APIC flag, not the I/O APIC -- and that doing so changed nothing: `virsh domxml-to-native qemu-argv` gives a byte-identical command line with and without it. So libvirt declares the field un-expressible now, as QEMU and VMware already did, and says so once instead of writing an element that meant nothing. VirtualBox is untouched, because there it is real: `VBoxManage modifyvm --ioapic off` is accepted and reported back.

**`export` wrote the file and then died printing that it had, on Windows**

`UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'` -- the arrow in the success line has no room in cp1252. The export sat on disk, correct, while the command exited 1 with a traceback. All seven VirtualBox cases failed on this and nothing else. No test on Linux could have caught it.

Every one of them is a defect in the path a user takes first, and not one was visible to the test suite. Three are round-trip breaks -- export a VM, import it back -- which is the thing vmctl exists to do. All five are fixed, each with a regression test that fails without the fix.

## Recordings

Each run was recorded with asciinema. Play one with `asciinema play casts/<provider>.cast`.

- `casts/libvirt.cast` (33 KB)
- `casts/qemu.cast` (39 KB)
- `casts/virtualbox.cast` (104 KB)
- `casts/vmware.cast` (37 KB)

## libvirt

Run locally, against libvirt/QEMU-KVM on this host. 4 VMs, 64 checks, **0 failed**.

| # | format | bus | VM | checks | result |
|---|---|---|---|---|---|
| 1 | `qcow2` | `sata` | `vmctl-t-qcow2-sata` | 16/16 | pass |
| 2 | `raw` | `virtio-scsi` | `vmctl-t-raw-virtio-scsi` | 16/16 | pass |
| 3 | `qcow2` | `usb` | `vmctl-t-qcow2-usb` | 16/16 | pass |
| 4 | `raw` | `virtio-blk` | `vmctl-t-raw-virtio-blk` | 16/16 | pass |

Every check passed on every pair.

<details><summary>The spec used, and what each VM was asked for</summary>

```yaml
name: vmctl-t-qcow2-sata
cpu:
  count: 1
memory:
  mb: 128
storage:
  - name: system
    size_mb: 100
    format: qcow2
    bus: sata
    bootable: true
```

Each case repeats this with its own format and bus, then overrides `memory.mb=256`, `cpu.count=2` and adds a second disk on the command line.

</details>

## qemu

Run locally, against plain QEMU on this host. 5 VMs, 80 checks, **0 failed**.

| # | format | bus | VM | checks | result |
|---|---|---|---|---|---|
| 1 | `qcow2` | `sata` | `vmctl-t-qcow2-sata` | 16/16 | pass |
| 2 | `raw` | `ide` | `vmctl-t-raw-ide` | 16/16 | pass |
| 3 | `qcow2` | `virtio-blk` | `vmctl-t-qcow2-virtio-blk` | 16/16 | pass |
| 4 | `raw` | `virtio-scsi` | `vmctl-t-raw-virtio-scsi` | 16/16 | pass |
| 5 | `qcow2` | `usb` | `vmctl-t-qcow2-usb` | 16/16 | pass |

Every check passed on every pair.

<details><summary>The spec used, and what each VM was asked for</summary>

```yaml
name: vmctl-t-qcow2-sata
cpu:
  count: 1
memory:
  mb: 128
storage:
  - name: system
    size_mb: 100
    format: qcow2
    bus: sata
    bootable: true
```

Each case repeats this with its own format and bus, then overrides `memory.mb=256`, `cpu.count=2` and adds a second disk on the command line.

</details>

## virtualbox

Run on the Windows host over `ssh winhost`, against VirtualBox. 7 VMs, 112 checks, **0 failed**.

| # | format | bus | VM | checks | result |
|---|---|---|---|---|---|
| 1 | `parallels` | `ide` | `vmctl-t-parallels-ide` | 16/16 | pass |
| 2 | `qcow2` | `sata` | `vmctl-t-qcow2-sata` | 16/16 | pass |
| 3 | `qed` | `scsi` | `vmctl-t-qed-scsi` | 16/16 | pass |
| 4 | `raw` | `sas` | `vmctl-t-raw-sas` | 16/16 | pass |
| 5 | `vdi` | `nvme` | `vmctl-t-vdi-nvme` | 16/16 | pass |
| 6 | `vhd` | `virtio-scsi` | `vmctl-t-vhd-virtio-scsi` | 16/16 | pass |
| 7 | `vmdk` | `usb` | `vmctl-t-vmdk-usb` | 16/16 | pass |

Every check passed on every pair.

<details><summary>The spec used, and what each VM was asked for</summary>

```yaml
name: vmctl-t-parallels-ide
cpu:
  count: 1
memory:
  mb: 128
storage:
  - name: system
    size_mb: 100
    format: parallels
    bus: ide
    bootable: true
```

Each case repeats this with its own format and bus, then overrides `memory.mb=256`, `cpu.count=2` and adds a second disk on the command line.

</details>

## vmware

Run on the Windows host over `ssh winhost`, against VMware Workstation. 5 VMs, 80 checks, **0 failed**.

| # | format | bus | VM | checks | result |
|---|---|---|---|---|---|
| 1 | `vmdk` | `ide` | `vmctl-t-vmdk-ide` | 16/16 | pass |
| 2 | `vmdk` | `sata` | `vmctl-t-vmdk-sata` | 16/16 | pass |
| 3 | `vmdk` | `scsi` | `vmctl-t-vmdk-scsi` | 16/16 | pass |
| 4 | `vmdk` | `sas` | `vmctl-t-vmdk-sas` | 16/16 | pass |
| 5 | `vmdk` | `nvme` | `vmctl-t-vmdk-nvme` | 16/16 | pass |

Every check passed on every pair.

<details><summary>The spec used, and what each VM was asked for</summary>

```yaml
name: vmctl-t-vmdk-ide
cpu:
  count: 1
memory:
  mb: 128
storage:
  - name: system
    size_mb: 100
    format: vmdk
    bus: ide
    bootable: true
```

Each case repeats this with its own format and bus, then overrides `memory.mb=256`, `cpu.count=2` and adds a second disk on the command line.

</details>

## How it was run

- this host: Linux-5.14.0-687.47.1.el9_8.x86_64-x86_64-with-glibc2.34, Python 3.13.15 for the report; the runs themselves used Python 3.13
- `scripts/hands-on-matrix.py <provider>` per hypervisor, under `asciinema rec`
- `--policy nearest`, so a provider substituting a value reports it rather than refusing -- which is what a user meets

