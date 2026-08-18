=========================================
vmctl — virtual machines as code
=========================================

**vmctl** is a small Python command-line tool for managing virtual machines with
**config-as-code**. Export a VM's configuration to YAML or JSON, re-create it
reproducibly, and manage whole fleets with batch operations — across a
pluggable set of hypervisor providers (VirtualBox today; libvirt/KVM and QEMU
planned).

.. code-block:: bash

   vmctl export my-vm -o my-vm.yaml            # capture an existing VM
   vmctl import my-vm.yaml --new-name test --apply
   vmctl batch create cluster.yaml --apply   # a whole fleet from one file

.. toctree::
   :maxdepth: 2
   :caption: Contents

   01-overview
   02-commands
   03-configuration
   04-api

The core ideas
==============

- **Config-as-Code.** A VM's shape — CPU, memory, firmware, disks, networks,
  boot order — is data. Export it to YAML/JSON, keep it in Git, re-create it
  anywhere.
- **Dry-run by default.** ``create``/``import``/``batch`` print the exact
  provider commands they *would* run; add ``--apply`` to actually run them.
- **Multi-provider architecture.** A thin provider interface
  (:mod:`vmctl.providers`) isolates the hypervisor. VirtualBox is fully
  supported; new backends slot in without touching the core.
- **Validated before it runs.** :mod:`vmctl.validators` checks a config against
  the target provider's capabilities and surfaces problems up front.

Where to start: :doc:`01-overview` for install and concepts, :doc:`02-commands`
for the command reference, and :doc:`03-configuration` for the YAML schema.

Features
========

- **Export / import** VM configurations as **YAML** or **JSON**.
- **Full lifecycle** — ``list``, ``start``, ``stop`` (graceful or ``--force``),
  ``status``, ``edit``, ``delete``.
- **Clone & templatize** — ``create`` from an existing VM, ``import`` from a
  file, with per-instance overrides.
- **Batch operations** — ``batch create`` many VMs from one template file;
  ``batch template`` scaffolds the file.
- **Validation** — ``validate`` checks a config before you build anything.
- **Extensible** — pluggable providers, serializers, and validators.

Indices
=======

* :ref:`genindex`
* :ref:`search`
