Quick Start
===========

Which hypervisor
----------------

Every command below works the same way on VirtualBox, libvirt/QEMU-KVM, plain QEMU
and VMware Workstation. vmctl picks one — ``$VMCTL_PROVIDER`` if set, otherwise
whichever it finds installed — and ``-p`` says which:

.. code-block:: bash

   vmctl providers                 # what is usable here; * marks the default
   vmctl doctor                    # and whether this machine can run a VM at all
   vmctl -p libvirt list
   export VMCTL_PROVIDER=libvirt   # or set it once

See :doc:`providers` for what differs between them, and ``examples/`` for a worked
configuration per hypervisor.

List VMs
--------

.. code-block:: bash

   vmctl list
   vmctl list --format simple   # names only
   vmctl list --format json     # for a script

Export a VM
-----------

Capture a running or stopped VM as a YAML file:

.. code-block:: bash

   vmctl export ubuntu-server -o ubuntu-server.yaml
   vmctl export ubuntu-server -o ubuntu-server.json --format json

The VM does not need to be stopped before exporting.

Import (recreate from file)
---------------------------

Always dry-run first to review what will be created:

.. code-block:: bash

   vmctl import ubuntu-server.yaml --new-name test-server

Then execute:

.. code-block:: bash

   vmctl import ubuntu-server.yaml --new-name test-server --execute

.. note::

   ``import`` creates a new VM with a **blank disk**.  Disk contents are copied
   only when you ask for them with ``--clone-disks``, and only when the images can
   be read from this machine — a config file names the image in ``source:``.
   Otherwise use Bareos, rsync, or a similar tool to restore data into the new VM.

Clone from an existing VM
--------------------------

``create`` reads the source configuration live from the hypervisor instead of from a
file:

.. code-block:: bash

   vmctl create ubuntu-server --new-name ubuntu-clone --execute
   vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute
   vmctl create ubuntu-server --new-name full-clone --clone-disks --execute

Lifecycle commands
------------------

.. code-block:: bash

   vmctl start ubuntu-server
   vmctl stop  ubuntu-server
   vmctl stop  ubuntu-server --force   # force power-off
   vmctl status ubuntu-server          # running / stopped / paused / saved
   vmctl delete old-vm                 # asks for confirmation
   vmctl delete old-vm --force         # skips confirmation

Keep a VM matching its file
---------------------------

.. code-block:: bash

   vmctl diff  web-01 web-01.yaml      # read-only; exits 1 when they differ
   vmctl apply web-01.yaml             # dry-run: what it would change
   vmctl apply web-01.yaml --execute   # change only what drifted

Only what the file actually states is applied, so a short file changes what it
mentions and leaves the rest as the hypervisor has it.

Snapshots
---------

.. code-block:: bash

   vmctl snapshot take web-01 before-upgrade -d "why"
   vmctl snapshot list web-01
   vmctl snapshot restore web-01 before-upgrade
   vmctl snapshot delete web-01 before-upgrade

Validate a config file
----------------------

.. code-block:: bash

   vmctl validate my-config.yaml
   vmctl -p libvirt validate my-config.yaml   # what is valid depends on the provider
   vmctl validate my-config.yaml --format json

Check that it all works here
----------------------------

.. code-block:: bash

   vmctl doctor      # the host, the hypervisor, the image directory, free space
   vmctl selftest    # create, check, start, stop and delete a throwaway VM

``doctor`` only looks; ``selftest`` uses the hypervisor, which is how a setup that
validates but cannot run a VM is told apart from one that works.
