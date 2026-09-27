# vmctl

**A command-line tool for managing virtual machines with config-as-code support.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI version](https://img.shields.io/pypi/v/vmctl)](https://pypi.org/project/vmctl/)

Export VM configurations to YAML or JSON, recreate identical VMs anywhere, and spin up entire clusters with one command. Think of it as Infrastructure as Code for your local VirtualBox lab.

---

## Install

```bash
pip install vmctl
```

Requires a supported hypervisor:

| Provider | Needs | Notes |
|---|---|---|
| `virtualbox` | VirtualBox **7.0+** and `VBoxManage` on PATH | the default |
| `libvirt` | libvirt **8.0+** and `virsh` on PATH | QEMU/KVM; works without KVM (slower) |
| `qemu` | `qemu-system-x86_64` (or `qemu-kvm`) and `qemu-img` | plain QEMU, no daemon: each VM is a directory with a runnable command line in it |
| `vmware` | VMware Workstation **17+** (or Fusion) | VMDK only; `vmrun` for the lifecycle |

```bash
vmctl providers                 # what is usable on this machine
vmctl -p libvirt list           # talk to a specific one
export VMCTL_PROVIDER=libvirt   # ...or set a default
vmctl -p vmware capabilities    # what that hypervisor can actually do
```

With no `--provider`, vmctl uses `$VMCTL_PROVIDER`, then whichever hypervisor
it finds installed.

---

## Quick Start

```bash
# See what's running
vmctl list

# Capture an existing VM as YAML
vmctl export my-vm -o my-vm.yaml

# Recreate it (dry-run first)
vmctl import my-vm.yaml --new-name test-vm
vmctl import my-vm.yaml --new-name test-vm --execute

# Spin up a whole cluster
vmctl batch create cluster.yaml --execute
```

---

## Features

### Moving a VM Between Hypervisors

```
$ vmctl migrate web-01 --from virtualbox --to libvirt
Validation failed: 2 settings are not supported by libvirt
  disks[0].controller: ide is not supported (a hdd device cannot go on that bus);
    virtio-scsi would be used instead
  disks[1].controller: ide is not supported (a dvd device cannot go on that bus);
    sata would be used instead
  hint: Pass --policy nearest to substitute these and be told what changed, or
        --policy convert to convert disk images too

$ vmctl migrate web-01 --from virtualbox --to libvirt --policy convert
web-01 (virtualbox) -> web-01 (libvirt)
Configuration only: the new VM gets blank disks. Pass --with-disks to bring the data.
Warning: disks[0].controller: ide is not supported, used virtio-scsi instead
Warning: memory.vram_mb: 16 was not applied (video memory is a device property here)
Dry-run mode.  Commands that would be executed:
    1: qemu-img create -f qcow2 /var/lib/libvirt/images/web-01_root.qcow2 51200M
    2: write /tmp/web-01.xml (922 bytes)
    3: virsh define /tmp/web-01.xml
```

Changing your VM is something you ask for, so the default refuses — and lists
**everything** that would have to change, not just the first thing it hit, so
opting in is one step. Dry-run is still the default even then.

`--with-disks` converts and attaches the real images instead of creating blank
ones. They have to be readable from the machine running vmctl; if they are not,
vmctl names them rather than quietly producing an empty VM.

### More Than One Hypervisor

The same config file works against either provider. vmctl translates it and tells
you what does not carry over, rather than dropping it silently:

```
$ vmctl -p libvirt import ubuntu-server.yaml --new-name dev --execute
Warning: memory.vram_mb is set but video memory is a device property in libvirt
    1: mkdir -p /var/lib/libvirt/images
    2: qemu-img create -f qcow2 /var/lib/libvirt/images/dev_system.qcow2 51200M
    3: write /tmp/dev.xml (812 bytes)
    4: virsh define /tmp/dev.xml
```

VirtualBox is driven with `VBoxManage` calls; libvirt gets a domain XML document
and one `virsh define`. Both are described by the same plan, so `--execute` and
dry-run behave identically whichever you use.

### Disk Image Conversion

```bash
vmctl convert disk.vdi disk.qcow2 --execute              # via qemu-img
vmctl -p virtualbox convert disk.vdi disk.vmdk --execute # via VBoxManage clonemedium
```

The formats on offer are the ones the selected provider can actually write, so
`--to vhdx` is not offered under VirtualBox — it can attach a VHDX but never
create one. Dry-run by default, like everything else that changes state.

### Nothing Changes Silently

When a configuration asks for something the target hypervisor cannot do, vmctl
says so before it does anything. `--policy` decides what happens next:

| Policy | Behaviour |
|---|---|
| `strict` *(default)* | Refuse, and name what would have to change |
| `nearest` | Substitute the closest supported value and report each one |
| `convert` | As `nearest`, and convert disk images rather than replacing their format |

```
$ vmctl -p libvirt import from-virtualbox.yaml --new-name moved --policy convert
Warning: disks[0].controller: ide is not supported, used virtio-scsi instead
Warning: disks[1].controller: ide is not supported, used sata instead
Warning: memory.vram_mb: 16 was not applied (video memory is a device property here)
```

A substitution lands on the bus that hypervisor's own users would pick — a disk
moves to `virtio-scsi` under libvirt, an optical drive to `sata` — rather than
merely somewhere valid.

### Editor Support

```bash
vmctl schema -o vmctl.schema.json
```

Then, at the top of a config file:

```yaml
# yaml-language-server: $schema=./vmctl.schema.json
```

The schema is generated from vmctl's own model, so it cannot describe a file vmctl
would reject — and it accepts the 1.1.x field names, because a schema that refused
those would be describing a tool that does not exist. It covers batch files too.

### Snapshotting A Whole Lab

```bash
vmctl export --all -d lab/
```

One file per VM plus a manifest, and nothing in it changes between runs — no
timestamps, no versions — so `lab/` is a directory you can commit and review.

### Has It Drifted?

`vmctl diff` compares a VM against a file, field by field. Read-only, and it exits 1
when they differ — so a pipeline can watch for drift.

```
$ vmctl diff web-01 web-01.yaml
web-01 vs web-01.yaml: 2 changed, 1 removed
  ~ cpu.count  vm 2  file 4
  ~ storage[sata/0].size_mb  vm 20480  file 51200
  - storage[scsi/0]  only on the VM: disk, scsi, 4096 MB
```

Only what the file actually says is compared: a default is not a request, so a file
that never mentions `bootable` does not "disagree" with every VM whose first disk
boots. Devices are matched by where they are rather than by name, because most
hypervisors have nowhere to store a device's name. It is also the quickest way to
check that an export and re-import was faithful.

### What Can This Hypervisor Do?

`vmctl capabilities` prints the declaration the validator and the translator read,
so what it shows is what vmctl will accept — including the attach matrix, which
answers questions like "can this one put a CD-ROM on NVMe?" without reading source.

```
$ vmctl -p vmware capabilities
buses:
  bus           disk    cdrom   floppy  ports
  ide           yes     yes     -       1-2 x 2
  nvme          yes     -       -       1-64
  sata          yes     yes     -       1-30   <- native for cdrom
  scsi          yes     yes     -       1-16   <- native for disk

evidence: probed on VMware Workstation 17 ... Port limits measured by attaching a
disk at each address -- VMware silently ignores one it cannot place, so the limits
are the only thing that catches it.
```

Every figure carries its provenance, because a measured limit and a remembered one
look identical in a table. `--format json` gives the same facts for a script.

Some of it is asked of the machine rather than read from a table — which bridges this
host has, which machine types this QEMU build offers, which guest OS ids this
VirtualBox knows — so a config naming an interface that does not exist here is caught
by `vmctl validate` instead of failing partway through a create:

```
$ vmctl validate web.yaml
Warning: networks[0] names 'eth0', which this host does not have;
         available: br-lab, docker0
```

### Keeping The Plan

Anything that would change a VM can be written out instead of only printed:

```bash
vmctl -p libvirt import web.yaml --out plan.sh      # a runnable shell script
vmctl -p libvirt import web.yaml --out artifacts/   # ...plus the domain XML itself
```

A plain path gets the script — complete on its own, with the native artifact inline.
A path ending in `/` also gets that artifact as its own file: the libvirt domain XML,
the VMware `.vmx`, the QEMU run script. Neither form executes anything.

### Config-as-Code
Every VM is a plain YAML (or JSON) file. Put it in Git, share it with a team, or use it to recreate the machine after a disk failure.

```yaml
name: ubuntu-server
guest_os: ubuntu22.04
cpu:
  count: 4
memory:
  mb: 8192
boot:
  ioapic: true      # VirtualBox needs I/O APIC for more than one CPU
storage:
  - name: system
    size_mb: 51200
    bus: sata
    bootable: true
networks:
  - network_type: bridged
    adapter_name: eth0
```

This file lives in the repo as [`examples/ubuntu-server.yaml`](examples/ubuntu-server.yaml)
and CI validates it on every push, so it cannot drift from what vmctl accepts.

### Dry-Run Mode
Every create/import/batch command shows you the exact `VBoxManage` commands it would run before touching anything:

```
$ vmctl import server.yaml --new-name dev-server

Dry-run mode. Commands that would be executed:
  1: VBoxManage createvm --name dev-server --ostype Ubuntu22_LTS_64 --register
  2: VBoxManage modifyvm dev-server --memory 8192 --vram 16 --cpus 4 ...
  3: VBoxManage storagectl dev-server --name SATA Controller --add sata ...
  4: VBoxManage createmedium disk --filename ...dev-server_system.vdi --size 51200 ...
  5: VBoxManage storageattach dev-server --storagectl SATA Controller --port 0 ...

Run with --execute to apply.
```

### Batch VM Creation
Define a base machine and create many instances with selective overrides:

```yaml
name: lab-cluster
base_vm: ubuntu-template    # reference an existing VM by name

instances:
  - name: web-01
    cpu: 4
    memory: 4096
  - name: web-02
    cpu: 4
    memory: 4096
  - name: db-01
    cpu: 8
    memory: 16384
    storage:
      - size_mb: 102400
```

```bash
vmctl batch create lab-cluster.yaml --execute
```

### Editing a VM in place

`vmctl edit` reads the VM, compares it to what you asked for, and emits only the
settings that actually differ — so changing one value runs one command:

```
$ vmctl edit dev-server --memory 4096 --cpus 4

Dry-run mode.  Commands that would be executed:
    1: VBoxManage modifyvm dev-server --memory 4096 --cpus 4

Run with --execute to apply.
```

The VM must be stopped. Storage and network layout cannot be changed in place;
`edit` says so rather than pretending otherwise.

### Full VM Lifecycle

```bash
vmctl start <vm>            # Start (headless)
vmctl stop <vm>             # Graceful shutdown (ACPI)
vmctl stop <vm> --wait 60    # ...and wait up to 60s for it to stop
vmctl stop <vm> --force     # Force power off
vmctl status <vm>           # running / stopped / paused / saved
vmctl delete <vm>           # Unregister and delete disk files
```

### Disk and Storage Support

| Format | Description |
|--------|-------------|
| VDI    | VirtualBox native (default) |
| VMDK   | VMware-compatible |
| VHD    | Microsoft Virtual Hard Disk |
| RAW    | Raw disk image (always fixed-size) |

Controllers: IDE, SATA, SCSI, SAS, NVMe, virtio-scsi, USB and floppy. Thin
(dynamic) and thick (fixed) allocation.
CD-ROM drives with ISO images are correctly identified as optical media.

### Network Adapter Types

| Type | Description |
|------|-------------|
| NAT | Outbound internet via host |
| Bridged | Direct connection to physical network |
| Host-only | Isolated host-to-VM network |
| Internal | VM-to-VM isolated network |
| NatNetwork | NAT with DHCP (multi-VM) |

Up to 8 adapters per VM. Bridged, host-only and NAT-network adapters keep the
interface or network they are attached to.

### Firmware Options
BIOS, EFI, EFI64 and EFI32, with TPM 2.0.

Secure boot is enrolled with `VBoxManage modifynvram` and requires an EFI
firmware type. Note that VirtualBox does not report secure-boot state in its
machine-readable output, so `vmctl export` cannot capture it from an existing
VM — set it in the config file.

---

## Command Reference

| Command | Description |
|---------|-------------|
| `vmctl providers` | List hypervisors and whether they work here |
| `vmctl export --all -d <dir>` | Export every VM, one file each, plus a manifest |
| `vmctl schema [-o <file>]` | JSON Schema for config files, generated from the model |
| `vmctl diff <vm> <file>` | Show how a VM differs from a config file (exit 1 when it does) |
| `vmctl capabilities [--format json]` | Print what this hypervisor supports: formats, buses, the attach matrix, limits — and where each figure was measured |
| `vmctl migrate <vm> --to PROVIDER [--with-disks] [--execute] [--out PATH]` | Recreate a VM on another hypervisor |
| `vmctl convert <src> <dst> [--to FMT] [--execute]` | Convert a disk image between formats |
| `vmctl list [--format table\|simple]` | List all VMs with status |
| `vmctl status <vm>` | Show current VM state |
| `vmctl start <vm>` | Start VM in headless mode |
| `vmctl stop <vm> [-f] [--wait SECONDS]` | Stop VM (graceful or forced), optionally waiting for it |
| `vmctl read <vm> [--format yaml\|json]` | Print VM configuration |
| `vmctl export <vm> -o <file>` | Save VM config to file |
| `vmctl import <file> [--new-name <n>] [--clone-disks] [--execute] [--out PATH]` | Create VM from config file |
| `vmctl create <vm> --new-name <n> [--clone-disks] [--execute]` | Clone VM config from existing VM |
| `vmctl edit <vm> [--memory MB] [--vram MB] [--cpus N] [--new-name <n>] [--execute]` | Change a stopped VM's CPU, memory or name (dry-run by default) |
| `vmctl delete <vm> [-f]` | Delete VM and disk files |
| `vmctl validate <file>` | Validate config file |
| `vmctl batch create <file> [--execute] [--continue-on-error]` | Create multiple VMs from batch file |
| `vmctl batch template [-o <file>]` | Generate a starter batch template |

---

## Use Cases

**Disaster recovery testing** — Export a production VM config, create a blank clone, restore a backup into it, and verify it boots. The VM spec is identical to production.

**Development environments** — Every developer runs `vmctl batch create devenv.yaml --execute` and gets the same set of VMs. No more "works on my machine."

**Lab snapshots** — Before a destructive test, `vmctl export` every VM in your lab. After the test, recreate exactly what you had.

**CI pipelines** — Create and delete VMs programmatically as part of automated tests.

---

## Configuration Reference

See [docs/features.md](docs/features.md) for the complete field reference.
See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for step-by-step workflows.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Author

**Ahmed Abdelhaleem Ahmed** — [ahmedhal@gmail.com](mailto:ahmedhal@gmail.com) · [@ahmedhal](https://github.com/ahmedhal)

## License

MIT — see [LICENSE](LICENSE).
