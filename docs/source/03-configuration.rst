===================
Configuration files
===================

A VM configuration is a plain YAML (or JSON) document. Export produces one from
a live VM; import consumes one.

VM configuration
================

.. code-block:: yaml

   name: ubuntu-server
   ostype: Ubuntu_64
   description: Ubuntu 22.04 LTS Server

   cpu:
     count: 4
     nested_virt: false

   memory:
     mb: 4096
     vram_mb: 16

   firmware:
     type: efi
     secure_boot: false

   disks:
     - name: system
       size_mb: 51200
       type: hdd
       controller: sata
       port: 0
       bootable: true

   networks:
     - network_type: bridged
       adapter_type: 82540EM
       adapter_name: eth0

   boot:
     order: [disk, dvd, none, none]
     acpi: true
     ioapic: true

Only ``name`` is strictly required; everything else falls back to sensible
defaults (:meth:`vmctl.core.vmconfig.VMConfig.from_dict`).

Supported hardware
==================

The values a configuration can use — modeled by the VirtualBox provider
(:class:`vmctl.providers.virtualbox.capabilities.VirtualBoxCapabilities`):

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Field
     - Supported values
   * - ``firmware.type``
     - ``bios`` · ``efi`` · ``efi32`` · ``efi64`` (Secure Boot and TPM supported)
   * - ``disks[].type``
     - ``hdd`` · ``ssd`` · ``dvd`` (CD/DVD drive)
   * - Disk allocation
     - ``thin`` (dynamic — grows as needed) · ``thick`` (fixed — preallocated)
   * - Disk formats
     - ``vdi`` · ``vmdk`` · ``vhd`` · ``raw``
   * - ``disks[].controller`` (storage controllers)
     - ``ide`` · ``sata`` · ``scsi`` · ``sas`` (up to 255 disks per controller)
   * - ``networks[].network_type``
     - ``nat`` · ``bridged`` · ``hostonly`` · ``internal`` · ``natnetwork`` (up to 8 adapters)
   * - ``boot.order`` devices
     - ``disk`` · ``dvd`` · ``floppy`` · ``network`` · ``none``
   * - Capacity
     - up to 128 vCPUs, 1 TB RAM, 256 MB VRAM

Batch configuration
===================

A batch file defines a base VM and a list of instances that inherit from it,
each with its own overrides:

.. code-block:: yaml

   name: dev-cluster
   description: Development environment
   base_vm: ubuntu-template

   instances:
     - name: dev-web-01
       memory: 4096
       cpu: 4
       metadata: { role: webserver }
     - name: dev-db-01
       memory: 8192
       cpu: 8
       disks:
         - size_mb: 102400
       metadata: { role: database }

Generate a starter file with ``vmctl batch template``, then
``vmctl batch create dev-cluster.yaml --apply``.
