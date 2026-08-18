# vmctl

**A powerful command-line tool for managing virtual machines with config-as-code support.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Overview

vmctl is a unified CLI for managing virtual machines across different hypervisors. Export VM configurations as YAML/JSON, create VMs from templates, and manage entire fleets with batch operations.

```bash
# Export existing VM to YAML
vmctl export my-vm -o my-vm.yaml

# Create new VM from config
vmctl import my-vm.yaml --new-name test-vm --apply

# Batch create multiple VMs
vmctl batch create cluster.yaml --apply
```

## Features

### Config-as-Code
- Export VM configurations to **YAML** or **JSON**
- Version control your infrastructure
- Create reproducible environments

### Multi-Provider Architecture
| Provider | Status |
|----------|--------|
| VirtualBox | Full support |
| libvirt/KVM | Coming soon |
| QEMU | Coming soon |

Extensible provider system for adding new hypervisors.

### VM Lifecycle Management
```bash
vmctl list                    # List all VMs with status
vmctl start <vm>              # Start a VM
vmctl stop <vm>               # Graceful shutdown
vmctl stop <vm> --force       # Force power off
vmctl status <vm>             # Get VM status
vmctl delete <vm>             # Delete VM
```

### Batch Operations
Create multiple VMs from a single template:

```yaml
# cluster.yaml
base_vm:
  ostype: Ubuntu_64
  cpu: { count: 2 }
  memory: { mb: 2048 }
  disks:
    - name: system
      size_mb: 20480
      type: hdd

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
```

```bash
vmctl batch create cluster.yaml --apply
```

### Dry-Run Mode
Preview commands before execution:

```bash
$ vmctl create ubuntu-vm --new-name test-vm

Dry-run mode. Commands that would be executed:
  1: VBoxManage createvm --name test-vm --ostype Ubuntu_64 --register
  2: VBoxManage modifyvm test-vm --memory 2048 --vram 16 --cpus 2 ...
  ...
```

## Installation

### From a GitHub release (recommended)
```bash
pip install https://github.com/abdelhaleemahmed/vmctl/releases/download/v2.0.0/vmctl-2.0.0-py3-none-any.whl
```

### From Source
```bash
git clone https://github.com/abdelhaleemahmed/vmctl.git
cd vmctl
pip install -e .
```

## Quick Start

### 1. List VMs
```bash
vmctl list
```
```
NAME                           STATUS
------------------------------------------
ubuntu-server                  running
windows-dev                    stopped
```

### 2. Export VM Configuration
```bash
vmctl export ubuntu-server -o ubuntu-server.yaml
```

### 3. Create VM from Config
```bash
# Dry-run first
vmctl import ubuntu-server.yaml --new-name test-server

# Actually create
vmctl import ubuntu-server.yaml --new-name test-server --apply
```

### 4. Manage VM Lifecycle
```bash
vmctl start test-server
vmctl status test-server
vmctl stop test-server
```

## Configuration Format

### VM Configuration (YAML)

```yaml
name: ubuntu-server
ostype: Ubuntu_64
description: Ubuntu 22.04 LTS Server

cpu:
  count: 4
  nested_virt: false

memory:
  mb: 4096
  vram_mb: 16

firmware:
  type: efi
  secure_boot: false

disks:
  - name: system
    size_mb: 51200
    type: hdd
    controller: sata
    port: 0
    bootable: true

networks:
  - network_type: bridged
    adapter_type: 82540EM
    adapter_name: eth0

boot:
  order: [disk, dvd, none, none]
  acpi: true
  ioapic: true
```

### Batch Configuration

```yaml
name: dev-cluster
description: Development environment

base_vm: ubuntu-template  # Reference existing VM

instances:
  - name: dev-web-01
    memory: 4096
    cpu: 4
    metadata:
      role: webserver

  - name: dev-db-01
    memory: 8192
    cpu: 8
    disks:
      - size_mb: 102400
    metadata:
      role: database
```

## Command Reference

| Command | Description |
|---------|-------------|
| `vmctl list` | List all VMs with status |
| `vmctl read <vm>` | Display VM configuration |
| `vmctl export <vm> -o <file>` | Export VM config to file |
| `vmctl import <file>` | Create VM from config file |
| `vmctl create <vm> --new-name <name>` | Clone VM configuration |
| `vmctl start <vm>` | Start a VM |
| `vmctl stop <vm>` | Stop a VM (graceful) |
| `vmctl stop <vm> -f` | Force stop a VM |
| `vmctl status <vm>` | Get VM status |
| `vmctl delete <vm>` | Delete a VM |
| `vmctl validate <file>` | Validate configuration |
| `vmctl batch create <file>` | Batch create VMs |
| `vmctl batch template` | Generate batch template |
| `vmctl --version` | Show version |

## Use Cases

### Disaster Recovery Testing
Export production VM configs, create test clones, validate backup restoration:

```bash
# Export production config
vmctl export prod-server -o prod-server.yaml

# Create test VM for bare metal recovery
vmctl import prod-server.yaml --new-name bmr-test --apply
```

### Development Environments
Spin up identical development environments:

```bash
vmctl batch create dev-environment.yaml --apply
```

### Infrastructure as Code
Store VM configurations in Git:

```bash
vmctl export web-server -o infra/web-server.yaml
git add infra/
git commit -m "Add web server configuration"
```

## Project Structure

```
vmctl/
├── cli/                 # Command-line interface
├── core/
│   ├── engine.py        # Main orchestration
│   ├── vmconfig.py      # VM configuration models
│   └── batch.py         # Batch operations
├── providers/
│   ├── base.py          # Provider interface
│   └── virtualbox/      # VirtualBox implementation
├── serializers/         # YAML/JSON import/export
└── validators/          # Configuration validation
```

## Contributing

Contributions are welcome! Areas of interest:

- [ ] libvirt/KVM provider
- [ ] QEMU provider
- [ ] Snapshot management
- [ ] VM cloning with disk copy
- [ ] Network configuration templates

## Author

**Ahmed Abdelhaleem Ahmed**

- Email: ahmedhal@gmail.com
- GitHub: [@abdelhaleemahmed](https://github.com/abdelhaleemahmed)

## License

MIT License - see [LICENSE](LICENSE) for details.
