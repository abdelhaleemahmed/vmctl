# vmctl User Guide

Practical workflows for managing VirtualBox VMs with vmctl.

---

## Installation

### From PyPI

```bash
pip install vmctl
```

### From Source

```bash
git clone https://github.com/ahmedhal/vmctl.git
cd vmctl
pip install -e .
```

### Prerequisites

- Python 3.8 or later
- VirtualBox installed
- `VBoxManage` available in your `PATH`

Verify VirtualBox is accessible:

```bash
VBoxManage --version
```

---

## Getting Started

### See All Your VMs

```bash
vmctl list
```

```
NAME                           STATUS
------------------------------------------
ubuntu-server                  running
windows-dev                    stopped
test-env                       saved
```

```bash
vmctl list --format simple    # just the names
```

### Check a VM's Status

```bash
vmctl status ubuntu-server
# running
```

Status values: `running`, `stopped`, `paused`, `saved`, `aborted`, `starting`, `stopping`, `unknown`

---

## Lifecycle Commands

### Start a VM

VMs always start in headless mode (no GUI window):

```bash
vmctl start ubuntu-server
```

Use the VirtualBox GUI or SSH to interact with the VM once started.

### Stop a VM

```bash
vmctl stop ubuntu-server          # Graceful — sends ACPI shutdown signal
vmctl stop ubuntu-server --force  # Force — equivalent to pulling the power cord
```

Use `--force` only when the guest OS is unresponsive.

### Delete a VM

```bash
vmctl delete old-vm               # Asks for confirmation
vmctl delete old-vm --force       # Skips confirmation prompt
```

This unregisters the VM from VirtualBox **and** deletes all associated disk files.

---

## Export and Import

The core workflow: capture a VM as a file, recreate it elsewhere.

### Export a VM

```bash
vmctl export ubuntu-server -o ubuntu-server.yaml
vmctl export ubuntu-server -o ubuntu-server.json --format json
```

This reads the VM's current configuration from VirtualBox and writes it to a file.
The VM does not need to be stopped to export.

### Import (Create from File)

Always dry-run first to review the commands:

```bash
vmctl import ubuntu-server.yaml --new-name test-server
```

```
Dry-run mode. Commands that would be executed:
  1: VBoxManage createvm --name test-server --ostype Ubuntu_64 --register
  2: VBoxManage modifyvm test-server --memory 8192 --vram 16 --cpus 4 ...
  3: VBoxManage storagectl test-server --name SATA Controller --add sata ...
  4: VBoxManage createmedium disk --filename ~/VirtualBox VMs/test-server/test-server_system.vdi --size 51200 ...
  5: VBoxManage storageattach test-server --storagectl SATA Controller --port 0 --device 0 --type hdd ...

Run with --execute to apply.
```

Then execute:

```bash
vmctl import ubuntu-server.yaml --new-name test-server --execute
```

> **Note:** `import` creates a new VM with a **new blank disk**. It does not copy disk contents. Use Bareos, rsync, or a similar tool to restore data into the new VM.

### View a VM's Configuration

```bash
vmctl read ubuntu-server             # YAML output to terminal
vmctl read ubuntu-server --format json
```

---

## Cloning from an Existing VM

`create` is like `import` but reads the source config directly from VirtualBox instead of a file:

```bash
# Dry-run
vmctl create ubuntu-server --new-name ubuntu-clone

# Execute
vmctl create ubuntu-server --new-name ubuntu-clone --execute

# Override resources
vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute
```

---

## Editing VM Properties

Modify CPU or memory of an existing VM:

```bash
vmctl edit my-vm --cpus 8
vmctl edit my-vm --memory 16384
vmctl edit my-vm --new-name renamed-vm
```

> The VM should be stopped before editing CPU and memory settings.

---

## Keeping a VM Matching Its File

`vmctl edit` changes one setting at a time from the command line. `vmctl apply` does
the declarative version: it takes the file as the description of how the VM should be
and makes reality match it.

```bash
vmctl apply web-01.yaml            # show what it would do
vmctl apply web-01.yaml --execute  # do it
vmctl apply web-01.yaml            # "already matches the file; nothing to do"
```

The name inside the file says which VM it is about. If there is no such VM it is
created; if there is, only what drifted is changed.

Three rules are worth knowing, because they are what make it safe to run repeatedly:

- **Only what the file states is applied.** A file that mentions just the CPU count
  changes just that. A default in the model is not a request, so nothing you did not
  write down gets reset.
- **What the file cannot know is kept.** The path an image has on this host, the MAC
  the hypervisor generated for an adapter, a libvirt domain's UUID: all carried over
  from the VM, because dropping them would detach a disk or give the guest a new
  network card.
- **Disks are never created, resized or removed on an existing VM.** A definition is
  cheap to rewrite; an image is not. If the file asks for a size that does not match,
  or adds a disk with no image behind it, `apply` says so and leaves the data alone.

With `--execute` the VM is read back afterwards and anything that did not converge is
listed; that exits 1, so a pipeline notices. A setting this hypervisor cannot express
at all is reported once, by the translator, and not counted as a failure to converge.

Use `vmctl diff` when you only want to look:

```bash
vmctl diff web-01 web-01.yaml || vmctl apply web-01.yaml --execute
```

---

## Validating Configuration Files

Check a YAML/JSON file for errors before using it:

```bash
vmctl validate my-config.yaml
```

```
Configuration is valid!
  VM Name:  ubuntu-server
  CPU:      4 cores
  Memory:   8192 MB
  Disks:    2
```

If there are errors:

```
Validation failed:
  Error: CPU count must be between 1 and 128
  Error: Disk name 'system' is not unique
```

Warnings are shown but don't fail validation:

```
Warnings:
  Warning: Memory (6000 MB) is not aligned to 4 MB boundary
```

---

## Batch Operations

Create multiple VMs from a single definition file.

### Generate a Starter Template

```bash
vmctl batch template -o my-cluster.yaml
```

### Batch File Structure

```yaml
name: dev-cluster
description: Development environment

base_vm: ubuntu-template     # Existing VM name, or a file path, or inline config

instances:
  - name: dev-web-01
    cpu: 4
    memory: 4096
    metadata:
      role: webserver
      env: dev

  - name: dev-web-02
    cpu: 4
    memory: 4096
    metadata:
      role: webserver
      env: dev

  - name: dev-db-01
    cpu: 8
    memory: 16384
    disks:
      - size_mb: 204800      # Override first disk size
    metadata:
      role: database
      env: dev
```

### Run in Dry-Run Mode

```bash
vmctl batch create dev-cluster.yaml
```

Shows a summary of all VMs that would be created:

```
Would create 3 VMs:
  dev-web-01  (4 CPUs, 4096 MB RAM, 1 disk)
  dev-web-02  (4 CPUs, 4096 MB RAM, 1 disk)
  dev-db-01   (8 CPUs, 16384 MB RAM, 1 disk)
```

### Execute

```bash
vmctl batch create dev-cluster.yaml --execute
```

VMs are created sequentially. If one fails, the batch stops and reports the error.

### Inline Base VM (No Existing VM Required)

```yaml
name: test-cluster

base_vm:
  ostype: Ubuntu_64
  cpu:
    count: 2
  memory:
    mb: 2048
  disks:
    - name: system
      size_mb: 20480
      type: HDD
      controller: SATA
      port: 0
      device: 0
      bootable: true
  networks:
    - network_type: NAT
  firmware:
    type: BIOS
  boot:
    order: [disk, dvd, none, none]

instances:
  - name: node-01
    memory: 4096
  - name: node-02
    memory: 4096
  - name: node-03
    cpu: 4
    memory: 8192
```

---

## Writing VM Configuration from Scratch

You don't have to start from an existing VM. Write the YAML directly:

### Minimal Linux VM

```yaml
name: minimal-linux
ostype: Ubuntu_64

cpu:
  count: 2

memory:
  mb: 2048

disks:
  - name: system
    size_mb: 20480
    type: HDD
    controller: SATA
    port: 0
    device: 0
    bootable: true

networks:
  - network_type: NAT

firmware:
  type: BIOS

boot:
  order: [disk, dvd, none, none]
  acpi: true
```

### Windows VM with EFI

```yaml
name: windows-dev
ostype: Windows10_64
description: Windows 10 development VM

cpu:
  count: 4

memory:
  mb: 8192
  vram_mb: 128

firmware:
  type: EFI64
  secure_boot: false

disks:
  - name: system
    size_mb: 102400
    type: HDD
    format: VDI
    variant: THIN
    controller: SATA
    port: 0
    device: 0
    bootable: true

networks:
  - network_type: NAT
  - network_type: HOSTONLY
    adapter_name: VirtualBox Host-Only Ethernet Adapter

boot:
  order: [disk, dvd, none, none]
  acpi: true
  ioapic: true

audio_enabled: true
usb_enabled: true
```

### VM with Multiple Disks

```yaml
name: fileserver
ostype: Ubuntu_64

cpu:
  count: 2

memory:
  mb: 4096

disks:
  - name: os
    size_mb: 20480
    type: HDD
    controller: SATA
    port: 0
    bootable: true
  - name: data1
    size_mb: 512000     # 500 GB
    type: HDD
    format: VDI
    variant: THIN
    controller: SATA
    port: 1
  - name: data2
    size_mb: 512000
    type: HDD
    format: VDI
    variant: THIN
    controller: SATA
    port: 2

networks:
  - network_type: BRIDGED
    adapter_name: enp3s0
```

---

## Workflows

### Disaster Recovery Testing

Capture a production VM and test that recovery works:

```bash
# 1. Export the VM config
vmctl export prod-server -o infra/prod-server.yaml

# 2. Create a blank recovery VM
vmctl import infra/prod-server.yaml --new-name bmr-test --execute

# 3. Boot the recovery VM from your rescue ISO (attach ISO in VirtualBox)
# 4. Run your restore procedure (Bareos, rsync, etc.)
# 5. Verify the restored VM boots correctly
# 6. Delete the test VM
vmctl delete bmr-test --force
```

### Infrastructure as Code

Store all VM definitions in a Git repository:

```
infra/
  vms/
    web-server.yaml
    db-server.yaml
    dev-env.yaml
  clusters/
    lab.yaml
    staging.yaml
```

```bash
# Reproduce the entire lab
vmctl batch create infra/clusters/lab.yaml --execute

# Update a VM spec and push the change
vmctl export web-server -o infra/vms/web-server.yaml
git add infra/vms/web-server.yaml
git commit -m "Increase web-server RAM to 8 GB"
```

### Development Environment

Every team member runs the same setup:

```bash
# Clone the repo
git clone https://github.com/yourteam/devenv.git
cd devenv

# Spin up everything
vmctl batch create cluster.yaml --execute
```

### Before a Destructive Test

```bash
# Export all VMs
for vm in $(vmctl list --format simple); do
    vmctl export "$vm" -o "backup/$vm.yaml"
done

# ... run tests ...

# Recreate any VMs that were destroyed
vmctl import backup/test-env.yaml --new-name test-env --execute
```

---

## Tips

**Check the dry-run output carefully.** The disk paths show exactly where files will be created (`~/VirtualBox VMs/<vm-name>/`). Make sure you have enough disk space.

**Use `vmctl validate` before import.** Catches typos and constraint violations before VirtualBox sees the file.

**Disk names must be unique within a VM.** If you have two disks, name them `os` and `data`, not both `disk`.

**Import creates blank disks by default.** The disk size and format match the original, but content is not copied — that gives you a clean VM with the right hardware profile, fast and at no cost in disk space. Pass `--clone-disks` to copy the contents too, when the images can be read from this machine; it tells you how much data that is before it starts. A config file does not record where a VM's data was (that describes a host, not the VM), so on a file the image has to be named in `source:`, and it is copied rather than shared with whatever else uses it.

**Storage controller names matter.** When importing a config exported from VirtualBox, the `controller_name` field holds the actual VirtualBox controller name (e.g., `SATA Controller`). Leave it as-is unless you know what you're changing.

**CD-ROM drives are not imported as hard disks.** vmctl correctly identifies ISO-backed optical drives as `DVD` type and handles them separately from hard disks.

---

## Troubleshooting

### `VBoxManage: command not found`

VirtualBox is not installed or not in PATH.

```bash
which VBoxManage
# Install VirtualBox, then try again
```

### `VM already exists`

The `--new-name` you chose is already registered in VirtualBox.

```bash
vmctl list                     # find existing VMs
vmctl delete old-test --force  # remove if no longer needed
```

### Validation errors on export

If a VM was created with unusual settings in the VirtualBox GUI, the exported YAML may have values that fail vmctl's schema validation when you try to import it. Edit the YAML to fix the flagged fields, then re-import.

### `permission denied` on disk path

VirtualBox stores disk images in `~/VirtualBox VMs/`. If that directory has wrong permissions:

```bash
ls -la ~/VirtualBox\ VMs/
chmod 755 ~/VirtualBox\ VMs/
```

### Batch stops midway

If a batch job fails on instance 3 of 10, instances 1 and 2 were created. Delete them before retrying:

```bash
vmctl delete dev-web-01 --force
vmctl delete dev-web-02 --force
# fix the batch file, then retry
vmctl batch create cluster.yaml --execute
```
