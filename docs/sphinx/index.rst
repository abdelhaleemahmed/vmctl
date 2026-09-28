vmctl Documentation
===================

**vmctl** describes a virtual machine in a file and then makes a real one --- on
VirtualBox, libvirt/QEMU-KVM, plain QEMU or VMware Workstation, with the same
commands and the same files.  Export a VM you already have, recreate it on a
different hypervisor, see what has drifted away from the file, and converge it back.

.. code-block:: bash

   pip install https://github.com/abdelhaleemahmed/vmctl/releases/download/v3.0.0/vmctl-3.0.0-py3-none-any.whl

   # Export an existing VM
   vmctl export my-vm -o my-vm.yaml

   # Recreate it -- here, or on another hypervisor (dry-run first)
   vmctl import my-vm.yaml --new-name test-vm
   vmctl -p libvirt import my-vm.yaml --policy nearest --execute

   # What no longer matches the file, and converge it back
   vmctl diff test-vm my-vm.yaml
   vmctl apply my-vm.yaml --execute

   # Spin up a whole cluster
   vmctl batch create cluster.yaml --execute

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   guide/install
   guide/quickstart
   guide/providers
   guide/configuration
   guide/batch
   guide/completion

.. toctree::
   :maxdepth: 2
   :caption: CLI Reference

   cli/index
   reference

.. toctree::
   :maxdepth: 3
   :caption: API Reference

   api/index

.. toctree::
   :maxdepth: 1
   :caption: Project

   writing-a-provider
   changelog

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
