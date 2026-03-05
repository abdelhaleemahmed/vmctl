Quick Start
===========

List VMs
--------

.. code-block:: bash

   vmctl list
   vmctl list --format simple   # names only

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

   ``import`` creates a new VM with a **blank disk**.  Disk contents are not
   copied.  Use Bareos, rsync, or a similar tool to restore data into the new VM.

Clone from an existing VM
--------------------------

``create`` reads the source config live from VirtualBox instead of a file:

.. code-block:: bash

   vmctl create ubuntu-server --new-name ubuntu-clone --execute
   vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute

Lifecycle commands
------------------

.. code-block:: bash

   vmctl start ubuntu-server
   vmctl stop  ubuntu-server
   vmctl stop  ubuntu-server --force   # force power-off
   vmctl status ubuntu-server          # running / stopped / paused / saved
   vmctl delete old-vm                 # asks for confirmation
   vmctl delete old-vm --force         # skips confirmation

Validate a config file
----------------------

.. code-block:: bash

   vmctl validate my-config.yaml
