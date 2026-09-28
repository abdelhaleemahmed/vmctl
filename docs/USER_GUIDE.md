# vmctl User Guide

Practical workflows for managing virtual machines as configuration files — on
VirtualBox, libvirt/QEMU-KVM, plain QEMU or VMware Workstation, with the same commands
and the same files.

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

Python 3.8 or later, and at least one hypervisor's command-line tools:

| Hypervisor | vmctl needs | Check it |
|---|---|---|
| VirtualBox 7.0+ | `VBoxManage` | `VBoxManage --version` |
| libvirt / QEMU-KVM | `virsh`, `qemu-img` | `virsh --version` |
| Plain QEMU | `qemu-system-x86_64` (or `qemu-kvm`), `qemu-img` | `qemu-img --version` |
| VMware Workstation 17+ | `vmrun`, `vmware-vdiskmanager` | vmctl finds them in the install directory |

One command answers all of it, including whether this machine can run a VM at all:

```bash
vmctl doctor
```

---

## Choosing a Hypervisor

Everything below works the same way whichever hypervisor you use. vmctl picks one for
you — `$VMCTL_PROVIDER` if set, otherwise whichever is installed — and `-p` says which:

```bash
vmctl providers                 # what vmctl can talk to here, and which is the default
vmctl -p libvirt list
export VMCTL_PROVIDER=libvirt   # or set it once
```

What differs between them is what they can *express*, and that is not a matter of
opinion — each provider's declaration was measured against the running product:

```bash
vmctl capabilities              # for the provider in use
vmctl -p vmware capabilities    # formats, buses, the attach matrix, limits, and
                                # where each figure came from
```

[`docs/providers.md`](providers.md) is the same information for all four at once,
generated from the code. There is a worked example per hypervisor in
[`examples/`](../examples/), each written in that provider's dialect and explaining in
comments why it looks the way it does.

A config that names nothing only one hypervisor has is portable between all of them; one
that asks for something a hypervisor cannot do is *told about it* rather than silently
changed — see **Moving a VM to Another Hypervisor** below.

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
vmctl list --format json      # for a script: name, status and provider per VM
```

### Check a VM's Status

```bash
vmctl status ubuntu-server
# running
```

```bash
vmctl status ubuntu-server --format json
```

Status values: `running`, `stopped`, `paused`, `saved`, `aborted`, `starting`,
`stopping`, `unknown`

---

## Lifecycle Commands

### Start a VM

VMs always start in headless mode (no GUI window):

```bash
vmctl start ubuntu-server
```

Use the hypervisor's own GUI, a console (`virsh console`, `vmrun`) or SSH to interact
with the guest once it has booted.

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

This removes the VM from the hypervisor **and** deletes the disk images vmctl created
for it. An image you attached from somewhere else is left alone: it was not vmctl's to
create, so it is not vmctl's to delete.

### Drift, and What Fixed It

```bash
vmctl diff web-01 web-01.yaml       # read-only; exits 1 when they differ
vmctl diff web-01 web-01.yaml --format json
```

Only what the file actually says is compared: a default is not a request, so a file that
never mentions `bootable` does not "disagree" with every VM whose first disk boots.
Devices are matched by where they are rather than by name, because most hypervisors have
nowhere to store a device's name. It is also the quickest way to check that an export and
re-import was faithful.

### See What a Command Will Do

Every command that changes a VM's definition — `import`, `create`, `edit`, `migrate`,
`apply` — is a **dry run** unless you pass `--execute`. It prints the exact commands it
would run, which is also the answer to "what is vmctl actually doing":

```bash
vmctl import my-vm.yaml               # prints the plan
vmctl import my-vm.yaml --execute     # runs it
vmctl import my-vm.yaml --out plan.sh # writes the plan as a runnable script
vmctl import my-vm.yaml --out dir/    # plan.sh plus the hypervisor's own artifact
                                      # (the libvirt XML, the .vmx, the QEMU script)
```

With `-v`, each command is echoed as it runs, so a plan that fails half way through can
be traced to the step it failed on. With `-q`, warnings are suppressed — errors are not.

---

## Export and Import

The core workflow: capture a VM as a file, recreate it elsewhere.

### Export a VM

```bash
vmctl export ubuntu-server -o ubuntu-server.yaml
vmctl export ubuntu-server -o ubuntu-server.json --format json
```

This reads the VM's current configuration from the hypervisor and writes it to a file.
The VM does not need to be stopped to export.

To capture a whole lab at once:

```bash
vmctl export --all -d lab/
```

One file per VM plus a manifest, and nothing in it changes between runs — no timestamps,
no versions — so `lab/` is a directory you can commit and review.

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

> **Note:** `import` creates a new VM with a **new blank disk** by default: a
> configuration file describes a machine, not its contents.

To bring the data as well, when the images can be read from this machine:

```bash
vmctl import ubuntu-server.yaml --clone-disks --execute
```

It says how many images and roughly how much data before it starts, converts anything
the target cannot write, and reports an image it cannot read rather than quietly
creating a blank one. A config file does not record *where* a VM's data was — that
describes a host, not the VM — so on a file the image has to be named in `source:`, and
it is then copied rather than shared with whatever else uses it. Cloning from a live VM
needs no such thing:

```bash
vmctl create ubuntu-server --new-name ubuntu-clone --clone-disks --execute
```

### View a VM's Configuration

```bash
vmctl read ubuntu-server             # YAML output to terminal
vmctl read ubuntu-server --format json
```

---

## Cloning from an Existing VM

`create` is like `import` but reads the source configuration live from the hypervisor
instead of from a file:

```bash
# Dry-run
vmctl create ubuntu-server --new-name ubuntu-clone

# Execute
vmctl create ubuntu-server --new-name ubuntu-clone --execute

# Override resources
vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute

# Change the disk format on the way, and bring the data
vmctl create ubuntu-server --new-name qcow-clone --disk-format qcow2 --clone-disks --execute
```

`--disk-format` offers only the formats the provider can actually create, so the choices
differ per hypervisor — `vmctl capabilities` lists them.

---

## Moving a VM to Another Hypervisor

```bash
vmctl migrate ubuntu-server --to libvirt                  # dry-run: what would happen
vmctl migrate ubuntu-server --to libvirt --execute
vmctl migrate ubuntu-server --to libvirt --with-disks --execute
```

Reading is the source's job, expressing is the target's, and what does not carry over
exactly is **reported rather than dropped**:

```
$ vmctl migrate vbox-desktop --to libvirt --policy nearest
vbox-desktop: virtualbox -> libvirt
  storage[0].format: vdi is not supported, used qcow2 instead (this format is
      attachable but not writable here)
  networks[0].model: e1000 kept
Dry-run mode.  Commands that would be executed:
  ...
```

Three policies decide what happens to a value the target cannot express:

| `--policy` | Means |
|---|---|
| `strict` (default) | Refuse, and list everything that would have to change |
| `nearest` | Substitute the closest thing the target does have, and say what changed |
| `convert` | As `nearest`, and convert disk images too |

`--with-disks` converts and attaches the real images instead of creating blank ones,
using the target's own tool (`qemu-img`, `VBoxManage clonemedium`,
`vmware-vdiskmanager`). A disk format the target cannot write is refused rather than
producing a VM with an empty disk.

To convert an image on its own:

```bash
vmctl convert disk.vdi disk.qcow2 --execute
```

---

## Editing VM Properties

Modify CPU, memory or video memory of an existing VM:

```bash
vmctl edit my-vm --cpus 8
vmctl edit my-vm --memory 16384 --execute
vmctl edit my-vm --vram 64 --execute
vmctl edit my-vm --new-name renamed-vm --execute
```

Only the settings you pass are changed, and only the ones that actually differ produce a
command — editing one setting runs one command. Like every command that changes a VM,
it is a dry run unless you pass `--execute`.

> VirtualBox refuses most of these on a running VM, so vmctl refuses too rather than
> half-applying them. libvirt and QEMU rewrite the definition instead, and say that the
> change takes effect when the VM is next started.

For anything beyond these four settings — storage, networks, firmware — write it in the
file and use `vmctl apply`, below.

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

## Building One Config on Another

Most of a lab is the same machine with a different name, so a file can extend another:

```yaml
# lab/base.yaml
guest_os: ubuntu22.04
cpu: {count: 2}
memory: {mb: 2048}
networks:
  - network_type: nat

# lab/web-01.yaml
extends: base.yaml
name: web-01
memory: {mb: 4096}          # overrides just the memory
storage:
  - name: system
    size_mb: 40960
```

The rules:

- **A mapping merges; the child wins.** `memory: {mb: 4096}` above keeps the base's
  `vram_mb` if it had one.
- **A list is replaced, not appended.** A file that writes `storage:` is stating the
  whole list -- there is no key to merge list entries on, and this is the same rule
  `vmctl apply` uses against a live VM.
- **Paths are relative to the file that names them**, so a directory of configs works
  wherever it is checked out.
- `extends: [common.yaml, lab.yaml]` takes several bases, applied left to right, so the
  last one wins. A chain of files works too, and a YAML file may extend a JSON base.

`vmctl validate` prints what a file is built on. A missing base, or a cycle, is
reported with the file that names it.

> One thing to watch: anything in the base is in *every* VM built on it, including a
> NAT port forward. Two VMs cannot both forward host port 2222, so put per-VM rules in
> the per-VM file.

---

## Port Forwarding on a NAT Adapter

```yaml
networks:
  - network_type: nat
    port_forwards:
      - name: ssh          # optional; VirtualBox needs one and vmctl fills it in
        protocol: tcp      # tcp (default) or udp
        host_ip: 127.0.0.1 # optional; empty means every address on this machine
        host_port: 2222
        guest_port: 22
```

Then `ssh -p 2222 user@localhost` reaches the guest. The rules are part of the
configuration, so they are exported, diffed and applied like everything else.

What each hypervisor can do differs, and `vmctl capabilities` states it:

- **VirtualBox** and **plain QEMU** forward ports natively.
- **libvirt** forwards them through its `passt` backend. If passt is not installed,
  vmctl reports the rules as not applied rather than handing libvirt a domain it
  refuses to define.
- **VMware Workstation** has no per-VM port forwarding -- it is configured host-wide in
  `vmnetnat.conf` -- so the rules are reported as something this provider cannot express.

`vmctl validate` catches a port outside 1-65535, a protocol that is not tcp or udp, and
two rules claiming the same host port.

---

## Checking That It All Works Here

```bash
vmctl selftest              # on the default provider
vmctl -p libvirt selftest   # on a specific one
vmctl selftest --no-start   # skip powering the VM on
vmctl selftest --keep       # leave the VM behind to look at
vmctl selftest --format json
```

It creates a throwaway VM — 128 MB, one small empty disk, a name of its own — reads it
back and compares it to what was asked for, snapshots it if the provider supports that,
starts it, stops it and deletes it. Each step is asserted rather than assumed, and the
VM is deleted even when a step fails.

This is not the same as `vmctl doctor`, which only *looks*: this one uses the
hypervisor. A definition a hypervisor validates is not a VM it will run, and telling
those apart is what the command is for. It exits 1 if any step failed, so it can gate a
CI runner with nested virtualisation.

Anything the provider says it cannot express is reported when the VM is created and
excluded from the comparison — so a hypervisor with one guest-OS id per family, or one
that always adds a USB controller, is not reported as a failure.

---

## Snapshots

```bash
vmctl snapshot take web-01 before-upgrade
vmctl snapshot take web-01 before-upgrade -d "why I took it"
vmctl snapshot list web-01
vmctl snapshot restore web-01 before-upgrade
vmctl snapshot delete web-01 before-upgrade
```

Taking one is immediate, like `start` and `stop`. Restoring throws away everything the
VM has done since, and deleting throws away the snapshot, so both ask for confirmation
unless you pass `--force`.

Two differences between hypervisors are worth knowing, and `vmctl capabilities` states
both:

- **Descriptions.** VirtualBox and libvirt store one. `vmrun` and `qemu-img` have
  nowhere to put it, so vmctl tells you it was not saved rather than quietly losing it.
- **Disk formats.** VirtualBox snapshots by writing a differencing image, so the format
  does not matter. libvirt and plain QEMU keep the snapshot *inside* the qcow2, so a raw
  disk cannot be snapshotted; vmctl refuses up front with the disk that is the problem,
  rather than letting the hypervisor fail part way through.

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
    storage:
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
  guest_os: ubuntu22.04
  cpu:
    count: 2
  memory:
    mb: 2048
  storage:
    - name: system
      size_mb: 20480
      bootable: true
  networks:
    - network_type: nat
  firmware:
    type: bios
  boot:
    order: [disk, dvd, none, none]
    ioapic: true

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

You do not have to start from an existing VM. The shortest thing that works is three
fields — everything else has a default, and vmctl fills in the storage controller a disk
needs:

```yaml
name: minimal-linux
cpu:
  count: 2
memory:
  mb: 2048
```

```bash
vmctl validate minimal-linux.yaml
vmctl import minimal-linux.yaml          # dry-run: see what it would create
```

### A VM worth writing down

```yaml
schema_version: 2          # optional; a file without it is read as the current format
name: fileserver
guest_os: ubuntu22.04      # a neutral id, not a hypervisor's own string
description: The lab's file server

cpu:
  count: 4
  sockets: 1               # libvirt and VMware can express a topology;
  cores: 4                 # VirtualBox has only a count, and says so
  threads: 1
memory:
  mb: 4096
  vram_mb: 32

firmware:
  type: efi64              # bios, efi, efi32, efi64
  tpm: true

boot:
  order: [disk, dvd, none, none]
  ioapic: true             # needed for more than one CPU on x86

storage:
  - name: os               # your label for the device, not the hypervisor's
    size_mb: 20480
    bootable: true
  - name: data
    size_mb: 512000        # 500 GB
    bus: sata              # leave it out for the provider's idiomatic bus
    slot: 1
    nonrotational: true    # tell the guest it is an SSD
    discard: true          # pass TRIM through
  - name: installer
    kind: cdrom            # disk, cdrom or floppy -- one list, not three
    # source: /srv/iso/ubuntu-22.04.iso    # a medium to insert

networks:
  - network_type: nat
    model: virtio          # what the guest sees: virtio, e1000, e1000e, ...
    port_forwards:
      - host_port: 2222
        guest_port: 22
  - network_type: bridged
    adapter_name: enp3s0   # `vmctl validate` checks this host has it
```

Two things are worth knowing about what you have just written:

- **Nothing in it names a hypervisor.** `qcow2` or `vdi`, `virtio-scsi` or `sata`,
  `ubuntu22.04` rather than `Ubuntu22_LTS_64` — vmctl translates into each hypervisor's
  spelling, and *reports* anything one of them cannot express. Leave `format` and `bus`
  out and each provider uses its own idiomatic pair.
- **A field you do not write is not a request.** `vmctl diff` and `vmctl apply` compare
  only what a file actually states, so a short file stays short: it changes what it
  mentions and leaves everything else as the hypervisor has it.

### Where the whole field list is

[`docs/features.md`](features.md) has every field, its type, its default and what it
means — generated from the model that reads your file, so it cannot drift. An editor can
check the file as you type it:

```bash
vmctl schema -o vmctl.schema.json
```

```yaml
# yaml-language-server: $schema=./vmctl.schema.json
name: fileserver
```

### Per-hypervisor examples

The same VM is not the same file on four hypervisors. [`examples/`](../examples/) has one
written in each provider's dialect, with comments explaining why it looks that way:

| File | Provider |
|---|---|
| `examples/virtualbox-desktop.yaml` | VirtualBox: VDI on SATA, named port-forward rules, no `machine` at all |
| `examples/libvirt-server.yaml` | libvirt: qcow2 on virtio-scsi, a CPU topology and model, `machine: q35` |
| `examples/qemu-workstation.yaml` | Plain QEMU: virtio-blk, `hostfwd` networking, a raw disk snapshots refuse |
| `examples/vmware-lab.yaml` | VMware: VMDK only, LSI Logic and NVMe, vmxnet3, no per-VM port forwarding |

### The 1.1.x spelling still works

Files written for vmctl 1.1.x load unchanged, and always will: `disks:` for `storage:`,
`ostype:` for `guest_os:`, `type: HDD` for `kind: disk`, `adapter_type: "82540EM"` for
`model: e1000`, `variant:` for `allocation:`. `vmctl export` writes the current names, so
re-exporting a file is how to modernise one.

---

## Workflows

### Disaster Recovery Testing

Capture a production VM and test that recovery works:

```bash
# 1. Export the VM config
vmctl export prod-server -o infra/prod-server.yaml

# 2. Create a blank recovery VM
vmctl import infra/prod-server.yaml --new-name bmr-test --execute

# 3. Boot it from a rescue ISO: add `source: /srv/iso/rescue.iso` to the optical
#    drive in the file, or attach one in the hypervisor's own GUI
# 4. Run your restore procedure (Bareos, rsync, etc.)
# 5. Verify the restored VM boots correctly
# 6. Delete the test VM
vmctl delete bmr-test --force
```

Or, when the disks themselves are what you want to test:

```bash
vmctl create prod-server --new-name bmr-test --clone-disks --execute
vmctl snapshot take bmr-test before-the-test
# ... break things ...
vmctl snapshot restore bmr-test before-the-test
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

# Capture what is actually there, as something reviewable
vmctl export --all -d infra/vms/

# Update a VM spec and push the change
$EDITOR infra/vms/web-server.yaml
vmctl diff web-server infra/vms/web-server.yaml     # what would change
vmctl apply infra/vms/web-server.yaml --execute     # change only that
git commit -am "Increase web-server RAM to 8 GB"
```

The pair worth putting in a pipeline is `diff` (read-only, exits 1 on drift) and `apply`
(changes only what drifted, and only what the file states):

```bash
vmctl diff web-server infra/vms/web-server.yaml || vmctl apply infra/vms/web-server.yaml --execute
```

With `extends:`, the part every VM shares lives in one file — see **Building One Config on
Another** above, and `examples/lab/`.

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

## Tab Completion

```bash
# bash
eval "$(vmctl completion bash)"
# zsh
eval "$(vmctl completion zsh)"
# fish
vmctl completion fish | source
```

Put the line in your shell's startup file to keep it. It completes command names,
options and **the names of VMs that exist**, asked of the hypervisor in use — so
`vmctl start <tab>` lists what you could start.

---

## Tips

**Check the dry-run output carefully.** The disk paths show exactly where files will be
created, which differs per hypervisor — `vmctl doctor` prints the directory and how much
room is left on it.

**Use `vmctl validate` before import.** Catches typos and constraint violations before
the hypervisor sees the file. Pass `-p` to check against the hypervisor you mean: what is
valid depends on it.

**Disk names must be unique within a VM.** If you have two disks, name them `os` and `data`, not both `disk`.

**Import creates blank disks by default.** The disk size and format match the original, but content is not copied — that gives you a clean VM with the right hardware profile, fast and at no cost in disk space. Pass `--clone-disks` to copy the contents too, when the images can be read from this machine; it tells you how much data that is before it starts. A config file does not record where a VM's data was (that describes a host, not the VM), so on a file the image has to be named in `source:`, and it is copied rather than shared with whatever else uses it.

**Controller names are the hypervisor's own.** A config exported from VirtualBox carries
`native_name: "SATA Controller"`, which is what VirtualBox calls that controller. Leave it
alone unless you mean to change it; the portable way to say where a device goes is its
`bus`.

**Write what you mean, not what a hypervisor calls it.** `guest_os: ubuntu22.04`,
`model: virtio`, `format: qcow2` — vmctl translates those into each hypervisor's own
spelling, and tells you when one has no equivalent. A hypervisor's own string is still
accepted and passed through untranslated, which is what keeps older files working.

**CD-ROM drives are not imported as hard disks.** vmctl correctly identifies ISO-backed optical drives as `DVD` type and handles them separately from hard disks.

---

## Troubleshooting

### Start here

```bash
vmctl doctor       # the host, the hypervisor, the image directory, free space
vmctl providers    # which hypervisors vmctl can talk to, and which it would pick
vmctl selftest     # use the hypervisor: create, check, start, stop, delete
```

`doctor` only looks; `selftest` actually uses the hypervisor, which is how a setup that
*validates* but cannot run a VM is told apart from one that works.

### `VBoxManage: command not found`, or "the provider is not installed"

The hypervisor's tools are not installed or not on `PATH`. `vmctl doctor` names the tool
it could not find and where it looked. (VMware Workstation is an exception: its installer
does not put its tools on `PATH`, and vmctl searches the install directory instead.)

### vmctl cannot see my VMs

Usually because it is talking to a different hypervisor than the one they are in:

```bash
vmctl providers            # the one marked * is the default
vmctl -p virtualbox list
```

On libvirt there is a second possibility: `qemu:///session` (your own VMs) and
`qemu:///system` (the machine's) are different registries. `vmctl doctor` prints which
connection is in use, and `LIBVIRT_DEFAULT_URI` changes it.

### `VM already exists`

The name you chose is taken.

```bash
vmctl list                     # find existing VMs
vmctl delete old-test --force  # remove if no longer needed
```

### Validation errors on export

If a VM was created with unusual settings in the VirtualBox GUI, the exported YAML may have values that fail vmctl's schema validation when you try to import it. Edit the YAML to fix the flagged fields, then re-import.

### `permission denied` on a disk path

Each hypervisor keeps images somewhere of its own — `~/VirtualBox VMs/`,
`~/.local/share/libvirt/images/`, `~/.local/share/vmctl/qemu/`, `~/Documents/Virtual
Machines/`. `vmctl doctor` prints the one in use, and how much room is on it.

### "this format is attachable but not writable here"

The hypervisor can read that disk format but not create one. libvirt, for instance,
attaches VDI and VMDK read-only, so vmctl refuses up front instead of defining a domain
that fails to start. Either pick a format it can write (`vmctl capabilities` lists them)
or convert:

```bash
vmctl convert disk.vdi disk.qcow2 --execute
vmctl migrate my-vm --to libvirt --policy convert --with-disks --execute
```

### A snapshot was refused

On libvirt and plain QEMU a snapshot lives *inside* the qcow2, so a raw disk cannot have
one — vmctl names the disk that is the problem. VirtualBox and VMware snapshot any format
they can attach.

### The guest is slow

Check for hardware virtualisation:

```bash
vmctl doctor | grep -i virtualisation
```

Without it (inside a VM whose host has not enabled nested virtualisation, for example)
guests are *emulated*, which is minutes rather than seconds to boot. It is not a vmctl
setting: libvirt drops to `domain type='qemu'` and plain QEMU to `-accel tcg`
automatically, and both say so in `vmctl capabilities`.

### Batch stops midway

If a batch job fails on instance 3 of 10, instances 1 and 2 were created. Delete them before retrying:

```bash
vmctl delete dev-web-01 --force
vmctl delete dev-web-02 --force
# fix the batch file, then retry
vmctl batch create cluster.yaml --execute
```
