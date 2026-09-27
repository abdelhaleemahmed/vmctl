Batch VM Creation
=================

Create multiple VMs from a single definition file using the ``batch create``
command.

Generate a template
-------------------

.. code-block:: bash

   vmctl batch template -o my-cluster.yaml

Batch file structure
--------------------

.. code-block:: yaml

   name: dev-cluster
   description: Development environment

   base_vm: ubuntu-template     # Existing VM name, file path, or inline config

   instances:
     - name: dev-web-01
       cpu: 4
       memory: 4096
       metadata:
         role: webserver

     - name: dev-web-02
       cpu: 4
       memory: 4096

     - name: dev-db-01
       cpu: 8
       memory: 16384
       storage:
         - size_mb: 204800      # Override the first device's size
       metadata:
         role: database

Dry-run
-------

.. code-block:: bash

   vmctl batch create dev-cluster.yaml

This shows a summary of what would be created without touching VirtualBox::

   Would create 3 VMs:
     dev-web-01  (4 CPUs, 4096 MB RAM, 1 disk(s))
     dev-web-02  (4 CPUs, 4096 MB RAM, 1 disk(s))
     dev-db-01   (8 CPUs, 16384 MB RAM, 1 disk(s))

   Run with --execute to apply.

Execute
-------

.. code-block:: bash

   vmctl batch create dev-cluster.yaml --execute

VMs are created sequentially.  If one fails, the batch stops and reports
the error.

Inline base VM
--------------

No existing VM required — define the base inline:

.. code-block:: yaml

   name: test-cluster

   base_vm:
     ostype: Ubuntu_64
     cpu:
       count: 2
     memory:
       mb: 2048
     storage:
       - name: system
         size_mb: 20480
         bus: sata
         bootable: true
     networks:
       - network_type: NAT
     firmware:
       type: BIOS
     boot:
       order: [disk, dvd, none, none]

   instances:
     - name: node-01
       memory: 4096
     - name: node-02
       memory: 4096
