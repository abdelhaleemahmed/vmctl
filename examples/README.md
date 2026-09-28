# Example configurations

Every file here is checked, by the test suite and by CI, against the provider it is
written for — so a config in this directory cannot drift from what the code accepts.
Nothing in CI touches a hypervisor: every check is a validation or a dry run.

## One per hypervisor

The same VM is not the same file on four hypervisors, and pretending otherwise is how
a config gets written that validates everywhere and is idiomatic nowhere. Each of these
is written in its provider's dialect, and says in comments *why*:

| File | Provider | Shows |
|---|---|---|
| [`virtualbox-desktop.yaml`](virtualbox-desktop.yaml) | `-p virtualbox` | VDI on SATA, named NAT port-forward rules, EFI64 with a TPM, no `machine` or CPU topology at all |
| [`libvirt-server.yaml`](libvirt-server.yaml) | `-p libvirt` | qcow2 on virtio-scsi, a CPU topology and model, `machine: q35`, port forwards through passt |
| [`qemu-workstation.yaml`](qemu-workstation.yaml) | `-p qemu` | virtio-blk (which cannot carry a CD-ROM), `hostfwd` networking, a raw disk that snapshots refuse |
| [`vmware-lab.yaml`](vmware-lab.yaml) | `-p vmware` | VMDK only, LSI Logic SCSI and NVMe, vmxnet3, and no per-VM port forwarding |

```bash
vmctl -p virtualbox validate examples/virtualbox-desktop.yaml
vmctl -p virtualbox import   examples/virtualbox-desktop.yaml            # dry-run
vmctl -p virtualbox import   examples/virtualbox-desktop.yaml --execute
```

`vmctl -p <provider> capabilities` is the authority for any of it, and
[`docs/providers.md`](../docs/providers.md) is the same information for all four at once,
generated from the code.

## Portable, and built on one another

| File | Shows |
|---|---|
| [`ubuntu-server.yaml`](ubuntu-server.yaml) | The README's example: a plain config that works on any provider, because it names nothing only one of them has |
| [`lab/base.yaml`](lab/base.yaml) + [`lab/web-01.yaml`](lab/web-01.yaml) + [`lab/db-01.yaml`](lab/db-01.yaml) | `extends:` — the half of a lab that every VM shares, and two VMs that differ only where they differ |
| [`lab-cluster.yaml`](lab-cluster.yaml) | A batch definition: several VMs from one base in a single file |

```bash
vmctl validate examples/lab/web-01.yaml        # prints what it is built on
vmctl apply    examples/lab/web-01.yaml        # dry-run; --execute to apply
vmctl batch create examples/lab-cluster.yaml   # dry-run
```

## A provider of your own

[`vmctl-null/`](vmctl-null/) is a provider in its own package — installable, listed by
`vmctl providers`, and covered by the conformance suite, with no change to vmctl. See
[`docs/writing-a-provider.md`](../docs/writing-a-provider.md).

```bash
pip install ./examples/vmctl-null
vmctl providers
python -m pytest tests/conformance     # now runs against five providers
```
