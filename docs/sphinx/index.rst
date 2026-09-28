vmctl Documentation
===================

**vmctl** is a command-line tool for managing VirtualBox VMs with
config-as-code support.  Export any VM to YAML or JSON, recreate it
anywhere, and spin up entire clusters from a single batch file.

.. code-block:: bash

   pip install vmctl

   # Export an existing VM
   vmctl export my-vm -o my-vm.yaml

   # Recreate it (dry-run first)
   vmctl import my-vm.yaml --new-name test-vm
   vmctl import my-vm.yaml --new-name test-vm --execute

   # Spin up a whole cluster
   vmctl batch create cluster.yaml --execute

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   guide/install
   guide/quickstart
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
