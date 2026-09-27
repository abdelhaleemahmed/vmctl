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

```bash
vmctl providers                 # what is usable on this machine
vmctl -p libvirt list           # talk to a specific one
export VMCTL_PROVIDER=libvirt   # ...or set a default
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

### Config-as-Code
Every VM is a plain YAML (or JSON) file. Put it in Git, share it with a team, or use it to recreate the machine after a disk failure.

```yaml
name: ubuntu-server
ostype: Ubuntu_64
cpu:
  count: 4
memory:
  mb: 8192
boot:
  ioapic: true      # VirtualBox needs I/O APIC for more than one CPU
disks:
  - name: system
    size_mb: 51200
    type: hdd
    controller: sata
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
  1: VBoxManage createvm --name dev-server --ostype Ubuntu_64 --register
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
    disks:
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
| `vmctl list [--format table\|simple]` | List all VMs with status |
| `vmctl status <vm>` | Show current VM state |
| `vmctl start <vm>` | Start VM in headless mode |
| `vmctl stop <vm> [-f] [--wait SECONDS]` | Stop VM (graceful or forced), optionally waiting for it |
| `vmctl read <vm> [--format yaml\|json]` | Print VM configuration |
| `vmctl export <vm> -o <file>` | Save VM config to file |
| `vmctl import <file> [--new-name <n>] [--execute]` | Create VM from config file |
| `vmctl create <vm> --new-name <n> [--execute]` | Clone VM config from existing VM |
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
