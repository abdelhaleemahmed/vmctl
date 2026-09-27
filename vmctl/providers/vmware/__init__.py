"""
VMware Workstation / Fusion.

The provider whose native format is a **key/value file**. A ``.vmx`` is a list of
``key = "value"`` lines, which is the same shape as ``VBoxManage showvminfo
--machinereadable`` output -- so the field tables in :mod:`vmctl.core.mapping` read
it directly, and the reading half of this provider is almost entirely declaration
(A-11 in PLAN.md predicted this and it held).

Three facts, measured rather than recalled, shape everything else:

* **A duplicated key makes the whole file unreadable.** VMware answers "Cannot read
  the virtual machine configuration" and will not open the VM, so the emitter has to
  write each key exactly once -- there is no last-one-wins.
* **A .vmx needs PCI bridge boilerplate** that has nothing to do with vmctl's model.
  Without ``pciBridge0/4/5/6/7`` an NVMe or pvscsi device is refused with "Device
  nvme0 requested without secondary PCI slots available", which at first reads
  exactly like the product not supporting NVMe.
* **VMware silently drops a device it cannot place.** An out-of-range disk does not
  fail: the VM powers on without it and nothing is said. That is the strongest
  argument in this codebase for vmctl checking slots against a declared port count
  itself (A-06), because the hypervisor will not.
"""

from .backend import VMwareBackend

__all__ = ["VMwareBackend"]
