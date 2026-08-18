========
Overview
========

Installation
============

From a GitHub release (recommended)
-----------------------------------

.. code-block:: bash

   pip install https://github.com/abdelhaleemahmed/vmctl/releases/download/v2.0.0/vmctl-2.0.0-py3-none-any.whl

From source
-----------

.. code-block:: bash

   git clone https://github.com/abdelhaleemahmed/vmctl.git
   cd vmctl
   pip install -e .

vmctl needs **Python 3.8+** and its one dependency, **PyYAML** (pulled in
automatically). To actually drive VirtualBox you also need **VBoxManage** on
your ``PATH`` (installed with VirtualBox).

Concepts
========

**Config-as-Code**
   A VM is described by a plain data document (YAML or JSON): name, OS type,
   CPU, memory, firmware, disks, networks, and boot order. Export captures a
   live VM into that document; import re-creates it.

**Providers**
   A provider is a hypervisor backend. vmctl talks to it through a small
   interface (:mod:`vmctl.providers.base`), so the same commands and config work
   across hypervisors. VirtualBox is implemented today.

**Dry-run**
   Anything that changes state (``create``, ``import``, ``batch create``) prints
   the exact provider commands first. Nothing runs until you pass ``--apply``.

A first run
===========

.. code-block:: bash

   vmctl list                                  # what's already here
   vmctl export ubuntu-server -o ubuntu.yaml   # capture it
   vmctl import ubuntu.yaml --new-name test    # dry-run: show the commands
   vmctl import ubuntu.yaml --new-name test --apply
   vmctl start test
   vmctl status test
