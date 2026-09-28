Hypervisors
===========

vmctl drives four hypervisors with the same commands and the same configuration files.
This page is about what differs between them — and about the fact that none of it is
written from documentation: every figure in a provider's declaration was measured against
the running product, and each provider says where its numbers came from.

.. code-block:: bash

   vmctl providers                 # what is usable on this machine; * marks the default
   vmctl -p vmware capabilities    # what that one can actually do
   vmctl doctor                    # and whether this machine is set up for it

:doc:`../reference` has the full matrices for all four at once, generated from the code.

What each one is
----------------

**VirtualBox** (``-p virtualbox``, needs ``VBoxManage`` on ``PATH``)
   Imperative: a VM is a sequence of ``VBoxManage`` calls. VDI is its own disk format and
   SATA the bus it puts a disk on. It has **no** chipset setting and **no** CPU topology —
   only a CPU count — so a config asking for either is told. Its NAT adapter forwards
   ports, and each rule has a name of its own that must be unique. Snapshots work in any
   format it can attach, because it snapshots by writing a differencing image.

**libvirt / QEMU-KVM** (``-p libvirt``, needs ``virsh``)
   Declarative: a VM is one XML document and a single ``virsh define``. qcow2 is its
   format, virtio-scsi the bus it prefers (it carries optical drives too, unlike
   virtio-blk). It wants an architecture and a machine type in every domain, and can
   express a CPU topology and a CPU model. Snapshots live *inside* the qcow2, so a raw
   disk cannot have one. Port forwarding needs the ``passt`` backend; ``vmctl doctor``
   says whether it is installed.

**Plain QEMU** (``-p qemu``, needs ``qemu-img`` and a ``qemu-system-*`` binary)
   No daemon and no registry: a VM is a directory holding its disks and a runnable command
   line, and that script is what vmctl reads back — so a hand-edited script still loads,
   which is the normal way to use plain QEMU. virtio-blk is the disk bus worth having and
   cannot carry a CD-ROM. It has no guest-OS field at all. Snapshots are qcow2-only, and
   refused while the VM is running, because its images are open.

**VMware Workstation** (``-p vmware``, 17+, needs ``vmrun`` and ``vmware-vdiskmanager``)
   A VM is a directory with a ``.vmx`` in it, and that file *is* the configuration. VMDK
   is the only format it attaches, so a VM arriving from libvirt or QEMU has to be
   converted. vmxnet3 is its paravirtual card and it has no virtio at all. It has **no
   per-VM port forwarding**: NAT forwards are host-wide in ``vmnetnat.conf``. Its
   installer does not put its tools on ``PATH``, so vmctl looks in the install directory.

Writing for one, or for all
---------------------------

A configuration that names nothing only one hypervisor has is portable between all four:
leave ``format`` and ``bus`` out and each provider uses its own idiomatic pair.

A configuration written *for* one hypervisor should say so. There is a worked example per
hypervisor in ``examples/``, each explaining in comments why it looks the way it does:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - What it shows
   * - ``examples/virtualbox-desktop.yaml``
     - VDI on SATA, named NAT port-forward rules, EFI64 with a TPM, no ``machine`` and no
       CPU topology at all
   * - ``examples/libvirt-server.yaml``
     - qcow2 on virtio-scsi, a CPU topology and model, ``machine: q35``, port forwards
       through passt
   * - ``examples/qemu-workstation.yaml``
     - virtio-blk (which cannot carry a CD-ROM), ``hostfwd`` networking, and a raw disk
       that snapshots refuse
   * - ``examples/vmware-lab.yaml``
     - VMDK only, LSI Logic SCSI and NVMe, vmxnet3, and no per-VM port forwarding

Moving a VM between them
------------------------

.. code-block:: bash

   vmctl migrate web-01 --to libvirt                     # dry-run
   vmctl migrate web-01 --to libvirt --policy nearest    # substitute and report
   vmctl migrate web-01 --to libvirt --with-disks --execute

Reading is the source's job and expressing is the target's; what does not carry over
exactly is reported rather than dropped. ``strict`` (the default) refuses and lists
*everything* that would have to change; ``nearest`` substitutes the closest thing the
target has and says what it changed; ``convert`` does that and converts disk images too.

Adding one of your own
----------------------

A provider can live in its own package and be installed separately — see
:doc:`../writing-a-provider` and the worked example in ``examples/vmctl-null/``.
