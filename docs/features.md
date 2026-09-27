# vmctl — Feature Reference

Complete reference for all configuration fields, enums, and capabilities.

---

## VM Configuration Fields

### Top-Level Properties

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | VM name (must be unique in VirtualBox) |
| `ostype` | string | `Ubuntu_64` | VirtualBox OS type identifier |
| `description` | string | `null` | Optional description |
| `audio_enabled` | bool | `false` | Enable audio device |
| `clipboard_mode` | string | `disabled` | Clipboard sharing (see values below) |
| `draganddrop` | string | `disabled` | Drag-and-drop mode (same values) |
| `usb_enabled` | bool | `false` | Enable USB controller |
| `rtc_utc` | bool | `true` | Use UTC for hardware clock |
| `metadata` | dict | `{}` | Arbitrary key-value annotations |

**Clipboard / drag-and-drop values:** `disabled`, `hosttoguest`, `guesttohost`, `bidirectional`

---

### CPU (`cpu`)

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `count` | int | `2` | 1–128 | Number of virtual CPUs |
| `execution_cap` | int | `100` | 1–100 | CPU usage cap (%) |
| `hotplug` | bool | `false` | — | CPU hotplug support |
| `pae` | bool | `false` | — | Physical Address Extension |
| `nested_virt` | bool | `false` | — | Nested virtualization (KVM-in-VBox etc.) |

```yaml
cpu:
  count: 4
  execution_cap: 80
  nested_virt: true
```

---

### Memory (`memory`)

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `mb` | int | `2048` | 4–1,048,576 | RAM in megabytes |
| `vram_mb` | int | `16` | 1–256 | Video RAM in megabytes |
| `page_fusion` | bool | `false` | — | Kernel same-page merging |
| `ballooning` | bool | `false` | — | Dynamic memory ballooning |

```yaml
memory:
  mb: 8192
  vram_mb: 128
```

---

### Firmware (`firmware`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | enum | `BIOS` | Firmware type: `BIOS` `EFI` `EFI64` `EFI32` |
| `secure_boot` | bool | `false` | Enable Secure Boot (requires EFI) |
| `tpm` | bool | `false` | Enable TPM chip |

```yaml
firmware:
  type: EFI64
  secure_boot: false
  tpm: true
```

---

### Boot (`boot`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `order` | list | `[disk, dvd, none, none]` | Boot device priority order |
| `boot1`–`boot4` | string | — | Individual boot slots (derived from `order`) |
| `acpi` | bool | `true` | ACPI support |
| `ioapic` | bool | `false` | I/O APIC |
| `hpet` | bool | `false` | High Precision Event Timer |

**Valid boot devices:** `none`, `floppy`, `dvd`, `disk`, `network`

```yaml
boot:
  order: [disk, dvd, none, none]
  acpi: true
  ioapic: true
```

---

### Disks (`disks`)

Each entry in the `disks` list:

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | string | required | unique | Disk identifier |
| `size_mb` | int | `20480` | 10–1,048,576 | Disk size in MB |
| `type` | enum | `HDD` | — | Disk type (see below) |
| `format` | enum | `VDI` | — | Disk image format (see below) |
| `variant` | enum | `THIN` | — | Allocation type (see below) |
| `controller` | enum | `SATA` | — | Storage controller type |
| `controller_name` | string | `null` | — | Exact controller name; `null` means "whichever controller serves `controller`", and one is created if the config declares none |
| `port` | int | `0` | 0–port max | Controller port number |
| `device` | int | `0` | 0–1 on IDE, 0 elsewhere | Device on port |
| `bootable` | bool | `false` | — | Informational only: VirtualBox has no per-disk bootable flag, boot selection is `boot.order` plus the controller's bootable setting |

#### Disk Types

`DVD` and `FLOPPY` are removable: no medium is created for them, `size_mb` is
ignored, and `source` names an existing image to insert (an ISO path for a DVD).

| Value | Description |
|-------|-------------|
| `HDD` | Hard disk drive |
| `SSD` | Solid state drive (same performance as HDD in VBox) |
| `DVD` | Optical drive / ISO image |
| `FLOPPY` | Floppy drive |

#### Disk Formats
| Value | Extension | Notes |
|-------|-----------|-------|
| `VDI` | `.vdi` | VirtualBox native — recommended |
| `VMDK` | `.vmdk` | VMware-compatible |
| `VHD` | `.vhd` | Microsoft Hyper-V compatible |
| `RAW` | `.img` | Raw disk image — always thick |

#### Disk Variants
| Value | VBoxManage | Description |
|-------|------------|-------------|
| `THIN` | `Standard` | Dynamic allocation — grows as needed |
| `THICK` | `Fixed` | Pre-allocates full size on creation |

> **Note:** RAW format does not support thin provisioning; it is always created as fixed.

```yaml
disks:
  - name: system
    size_mb: 51200
    type: HDD
    format: VDI
    variant: THIN
    controller: SATA
    port: 0
    device: 0
    bootable: true
  - name: data
    size_mb: 102400
    type: HDD
    controller: SATA
    port: 1
    device: 0
```

---

### Networks (`networks`)

Each entry in the `networks` list (max 8):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `network_type` | enum | `NAT` | Network mode (see below) |
| `adapter_type` | string | `82540EM` | NIC model emulation |
| `adapter_name` | string | `null` | Physical interface (bridged/host-only) |
| `mac_address` | string | `null` | Custom MAC address |
| `promiscuous_mode` | bool | `false` | Allow promiscuous mode |

#### Network Types
| Value | Description |
|-------|-------------|
| `NAT` | Outbound internet through host NAT |
| `BRIDGED` | Direct access to physical network |
| `HOSTONLY` | Isolated host-to-VM only network |
| `INTERNAL` | VM-to-VM isolated network (no host) |
| `NATNETWORK` | NAT with multi-VM DHCP |

```yaml
networks:
  - network_type: NAT
    adapter_type: 82540EM
  - network_type: BRIDGED
    adapter_name: enp3s0
  - network_type: INTERNAL
    adapter_name: labnet
```

---

### Storage Controllers (`storage_controllers`)

Normally auto-populated by `vmctl export`. Define manually when creating from scratch:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Unique controller name |
| `controller_type` | enum | — | Controller bus type |
| `port_count` | int | `30` | Number of available ports |
| `bootable` | bool | `false` | Mark controller as bootable |

#### Controller Types and Limits

Every row below was verified against VirtualBox 7.1.18 by creating the controller
on a live host.

| Type | `--add` | VBox Chipset | Ports | Use Case |
|------|---------|--------------|-------|----------|
| `ide` | `ide` | PIIX4 | 2 | Legacy disks, optical drives |
| `sata` | `sata` | IntelAhci | 30 | Standard disks |
| `scsi` | `scsi` | LSILogic | 16 | High port count |
| `sas` | `sas` | LSILogicSAS | 255 | Enterprise storage |
| `nvme` | `pcie` | NVMe | 255 | Fast virtual NVMe (VirtualBox 6.0+) |
| `virtio-scsi` | `virtio-scsi` | VirtIO | 256 | Paravirtualised, VirtualBox 7.x |
| `usb` | `usb` | USB | 8 (exactly) | USB mass storage |
| `floppy` | `floppy` | I82078 | 1 | Floppy drives; one controller per VM |

Two of these are not obvious from `VBoxManage storagectl --help`: `virtio-scsi`
is a valid `--add` value even though the help text omits it, and a USB controller
accepts *only* 8 ports (`Invalid port count: 1 (must be in range [8, 8])`).

---

## Batch File Format

```yaml
name: cluster-name             # Optional name for the batch
description: What this is      # Optional description

base_vm: ubuntu-template       # Existing VM name  OR  file path  OR  inline config

instances:
  - name: vm-01                # Required: unique VM name
    cpu: 4                     # Integer shorthand for count
    memory: 8192               # Integer shorthand for mb
    metadata:
      role: web

  - name: vm-02
    cpu:                       # Full CPU config override
      count: 8
      nested_virt: true
    memory:
      mb: 16384
    disks:
      - size_mb: 204800        # Override first disk size
    networks:
      - network_type: BRIDGED  # Override first adapter
```

**Base VM reference options:**
- `base_vm: my-vm-name` — reads config from existing VirtualBox VM
- `base_vm: /path/to/config.yaml` — reads from file
- `base_vm:` as a dict — inline full VM config

Each instance gets a deep copy of the base configuration, with only the specified fields overridden.

---

## Supported OS Types

Common values for the `ostype` field:

| Value | OS |
|-------|----|
| `Ubuntu_64` | Ubuntu 64-bit |
| `Ubuntu` | Ubuntu 32-bit |
| `Debian_64` | Debian 64-bit |
| `RedHat_64` | Red Hat / CentOS / Rocky 64-bit |
| `Fedora_64` | Fedora 64-bit |
| `Windows10_64` | Windows 10 64-bit |
| `Windows11_64` | Windows 11 64-bit |
| `Windows2019_64` | Windows Server 2019 |
| `Linux_64` | Generic Linux 64-bit |
| `Other_64` | Unknown / other |

Run `VBoxManage list ostypes` for the complete list.

---

## Validation Rules

vmctl validates configuration in four phases before any VM is created:

**Phase 1 — Schema**
- VM name must be non-empty
- CPU count: 1–128
- Memory: 4 MB minimum
- VRAM: 1–256 MB
- Disk size: 10 MB to 1 TB
- Disk names must be unique within the VM
- Storage controller names must be unique

**Phase 2 — Provider Limits** (VirtualBox-specific)
- Max 8 network adapters
- Max 255 disks per controller
- Port numbers within controller range

**Phase 3 — Logical Constraints**
- Boot device values must be valid (`none`, `floppy`, `dvd`, `disk`, `network`)
- If boot order includes `disk`, the first disk is automatically marked bootable

**Phase 4 — Warnings** (non-fatal, printed but don't block creation)
- Memory not aligned to 4 MB boundary

---

## File Format Detection

Format is detected from the file extension:

| Extension | Format |
|-----------|--------|
| `.yaml`, `.yml` | YAML |
| `.json` | JSON |

Both formats support identical configuration fields.
