Installation
============

Requirements
------------

Python 3.8 or later, and at least one hypervisor's command-line tools. vmctl drives
four, and needs only the one you use:

.. list-table::
   :header-rows: 1
   :widths: 26 34 40

   * - Hypervisor
     - vmctl needs
     - Check it
   * - VirtualBox 7.0+
     - ``VBoxManage`` on ``PATH``
     - ``VBoxManage --version``
   * - libvirt / QEMU-KVM
     - ``virsh``, ``qemu-img``
     - ``virsh --version``
   * - Plain QEMU
     - ``qemu-system-x86_64`` (or ``qemu-kvm``), ``qemu-img``
     - ``qemu-img --version``
   * - VMware Workstation 17+
     - ``vmrun``, ``vmware-vdiskmanager``
     - vmctl finds them in the install directory

One command answers all of it, including whether this machine can run a VM at all::

   vmctl doctor

And one proves the hypervisor agrees, by creating a throwaway VM and deleting it::

   vmctl selftest

From PyPI
---------

.. code-block:: bash

   pip install vmctl

From Source
-----------

.. code-block:: bash

   git clone https://github.com/abdelhaleemahmed/vmctl.git
   cd vmctl
   pip install -e .

Verify the installation
-----------------------

.. code-block:: bash

   vmctl --version
   vmctl providers     # which hypervisors are usable here; * marks the default
   vmctl list
