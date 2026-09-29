Creating a VM with virsh, by hand
=================================

This page creates one VM using nothing but ``virsh`` and ``qemu-img``, start to
finish, and then creates the same VM with vmctl. It is here for two reasons: so you
can see exactly what vmctl is doing on your behalf, and because ``virsh`` is still
the right tool for everything vmctl does not model.

Every command and every message below was run on a real host --- libvirt 11.10.0,
QEMU 10.1.0, a ``qemu:///session`` connection --- and pasted as it came out,
including the two attempts that failed. The VM is deliberately small (128 MiB, a 1 GiB
disk) because the host it was built on runs other machines; the shape is identical at
any size.

.. contents:: On this page
   :local:
   :depth: 1

Which libvirt you are talking to
--------------------------------

``virsh`` connects to a *user session* or to the *system* daemon, and they have
separate lists of domains:

.. code-block:: console

   $ virsh -c qemu:///session list --all      # yours, no root needed
   $ virsh -c qemu:///system  list --all      # the machine's, needs privileges

Everything here uses ``qemu:///session``. A domain defined in one is invisible from
the other, which is the usual reason a VM "disappears". Set ``LIBVIRT_DEFAULT_URI``
if you tire of typing ``-c``.

Step 1 --- create the disk
--------------------------

libvirt does not make disks for you. Nothing creates the image except you:

.. code-block:: console

   $ mkdir -p ~/.local/share/libvirt/images
   $ qemu-img create -f qcow2 ~/.local/share/libvirt/images/web-01.qcow2 1G
   Formatting '/home/vagrant/.local/share/libvirt/images/web-01.qcow2', fmt=qcow2
   cluster_size=65536 extended_l2=off compression_type=zlib size=1073741824
   lazy_refcounts=off refcount_bits=16

Step 2 --- write the domain XML
-------------------------------

A domain is an XML document. This is the smallest one that is actually useful --- a
disk, an optical drive, a network card, a console you can reach:

.. code-block:: xml
   :caption: web-01.xml
   :linenos:

   <domain type='qemu'>
     <name>web-01</name>
     <memory unit='MiB'>128</memory>
     <currentMemory unit='MiB'>128</currentMemory>
     <vcpu>2</vcpu>
     <os>
       <type arch='x86_64' machine='q35'>hvm</type>
       <boot dev='hd'/>
       <boot dev='cdrom'/>
     </os>
     <features>
       <acpi/>
     </features>
     <cpu mode='host-model'/>
     <clock offset='utc'>
       <timer name='hpet' present='no'/>
     </clock>
     <devices>
       <emulator>/usr/libexec/qemu-kvm</emulator>
       <disk type='file' device='disk'>
         <driver name='qemu' type='qcow2'/>
         <source file='/home/vagrant/.local/share/libvirt/images/web-01.qcow2'/>
         <target dev='sda' bus='sata'/>
       </disk>
       <disk type='file' device='cdrom'>
         <driver name='qemu' type='raw'/>
         <target dev='sdb' bus='sata'/>
         <readonly/>
       </disk>
       <controller type='sata' index='0'/>
       <interface type='user'>
         <model type='virtio'/>
       </interface>
       <graphics type='vnc' port='-1'/>
       <video><model type='virtio'/></video>
       <memballoon model='virtio'/>
     </devices>
   </domain>

Thirty-eight lines, and several of them are decisions you have to know to make:

``<domain type='...'>``
   ``kvm`` for hardware acceleration, ``qemu`` for emulation. Choose wrong and the
   domain will not define --- see below.

``<emulator>``
   The absolute path to the QEMU binary. It is ``/usr/libexec/qemu-kvm`` on RHEL and
   its rebuilds, ``/usr/bin/qemu-system-x86_64`` on Debian and Arch.

``<target dev='sda' bus='sata'/>``
   You name the guest device. Get two disks the same name and libvirt refuses the
   document.

``<interface type='user'>``
   User-mode networking, the equivalent of VirtualBox's NAT. ``type='network'`` wants
   a libvirt network that exists, and on a session connection there usually is not
   one.

The first attempt does not work
-------------------------------

Written with ``type='kvm'`` on a host without ``/dev/kvm``:

.. code-block:: console

   $ virsh -c qemu:///session define web-01.xml
   error: Failed to define domain from web-01.xml
   error: unsupported configuration: Emulator '/usr/libexec/qemu-kvm' does not
   support virt type 'kvm'

The fix is ``<domain type='qemu'>``. Worth dwelling on: whether this document is
valid depends on the machine it is defined on, so an XML file is not portable the way
it looks portable. The same is true of the emulator path, the machine type, and which
buses and image formats that QEMU build supports.

Step 3 --- define it
--------------------

.. code-block:: console

   $ virsh -c qemu:///session define web-01.xml
   Domain 'web-01' defined from web-01.xml

   $ virsh -c qemu:///session list --all
    Id   Name     State
   -------------------------
    -    web-01   shut off

``define`` makes it persistent. ``create`` would start a domain that vanishes when it
stops, which is rarely what you want.

Step 4 --- start it
-------------------

.. code-block:: console

   $ virsh -c qemu:///session start web-01
   Domain 'web-01' started

   $ virsh -c qemu:///session domstate web-01
   running

With nothing installed it will sit at the firmware looking for something to boot.
``virsh console web-01`` or a VNC client gets you a screen.

What libvirt added that you did not write
-----------------------------------------

The document you get back is not the document you handed over:

.. code-block:: console

   $ wc -l web-01.xml
   38 web-01.xml
   $ virsh -c qemu:///session dumpxml web-01 | wc -l
   146

The extra hundred lines are libvirt's own decisions --- a UUID, every PCI address, a
security label, the machine type resolved to a versioned one, default drivers:

.. code-block:: xml

   <uuid>67e6b140-cb45-47fd-b78c-384e07872243</uuid>
   <currentMemory unit='KiB'>131072</currentMemory>
   <address type='pci' domain='0x0000' bus='0x00' slot='0x1f' function='0x2'/>
   <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>

This matters when you keep the XML in Git. The file you wrote and the domain that
exists are two different documents, and ``dumpxml`` will never match your file ---
so you cannot diff them to see what changed.

Changing something afterwards
-----------------------------

Memory, on a *running* domain:

.. code-block:: console

   $ virsh -c qemu:///session setmaxmem web-01 512M --live
   error: Unable to change MaxMemorySize
   error: Requested operation is not valid: cannot resize the maximum memory on an
   active domain

``--config`` is accepted while it runs, and changes the *stored* definition only:

.. code-block:: console

   $ virsh -c qemu:///session setmaxmem web-01 512M --config
   $ virsh -c qemu:///session dumpxml web-01 --inactive | grep '<memory'
     <memory unit='KiB'>524288</memory>
   $ virsh -c qemu:///session dumpxml web-01 | grep '<memory'
     <memory unit='KiB'>262144</memory>

Two different answers for the same domain, and no warning that they differ: the
persistent definition says 512 MiB, the running machine still has 256. It takes
effect at the next boot. ``virsh edit`` opens the whole document in ``$EDITOR`` and
has the same split.

Tearing it down
---------------

A running domain will not have its storage removed:

.. code-block:: console

   $ virsh -c qemu:///session undefine web-01 --remove-all-storage
   error: Storage volume deletion is supported only on stopped domains

Stop it first --- and then read the next two lines carefully:

.. code-block:: console

   $ virsh -c qemu:///session destroy web-01
   Domain 'web-01' destroyed

   $ virsh -c qemu:///session undefine web-01 --remove-all-storage
   error: Storage volume 'sda'(/home/vagrant/.local/share/libvirt/images/web-01.qcow2)
   is not managed by libvirt. Remove it manually.
   Domain 'web-01' has been undefined

   $ ls ~/.local/share/libvirt/images/web-01.qcow2
   /home/vagrant/.local/share/libvirt/images/web-01.qcow2

The domain is gone. **The disk is still there.** ``--remove-all-storage`` only removes
volumes inside a libvirt storage pool, and an image you made with ``qemu-img`` in your
own directory is not one. The command reported the fact and undefined the domain
anyway, so a script that checks the exit status alone will think it cleaned up.

.. code-block:: console

   $ rm ~/.local/share/libvirt/images/web-01.qcow2

The same VM, with vmctl
-----------------------

.. code-block:: yaml
   :caption: web-01.yaml

   name: web-01
   cpu:
     count: 2
   memory:
     mb: 128
   storage:
     - name: system
       size_mb: 1024
       bus: sata
       bootable: true
   networks:
     - network_type: nat
       model: virtio

.. code-block:: console

   $ vmctl -p libvirt import web-01.yaml --policy nearest --execute
   Warning: guest_os: ubuntu is not supported, used linux instead (libosinfo has no
   id for a family without a version)
   Created VM 'web-01' from web-01.yaml
       1: mkdir -p ~/.local/share/libvirt/images
       2: qemu-img create -f qcow2 ~/.local/share/libvirt/images/web-01_system.qcow2 1024M
       3: write /tmp/web-01.xml (942 bytes)
       4: virsh define /tmp/web-01.xml

Without ``--execute`` those four lines are all you get, and nothing is touched. The
teardown removes the image as well, because vmctl knows it created it:

.. code-block:: console

   $ vmctl -p libvirt delete web-01 --force
   Deleted VM 'web-01'
   $ ls ~/.local/share/libvirt/images/web-01*
   ls: cannot access '/home/vagrant/.local/share/libvirt/images/web-01*': No such
   file or directory

Side by side
------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * -
     - virsh
     - vmctl
   * - To describe the VM
     - 38 lines of XML
     - 13 lines of YAML
   * - To build it
     - ``qemu-img create`` then ``virsh define``
     - one command
   * - Emulator path, machine type
     - you write them, per distribution
     - resolved for the host
   * - Preview before doing it
     - none; ``define`` defines
     - dry-run is the default
   * - Unsupported setting
     - the domain fails to define
     - refused, or substituted and reported, by ``--policy``
   * - The file vs the live VM
     - ``dumpxml`` never matches your file
     - ``vmctl diff`` compares only what the file states
   * - Changing one field
     - ``setmaxmem --config`` --- stored and live disagree silently
     - ``vmctl apply`` changes what drifted and says what it changed
   * - Deleting it
     - disk left behind unless it is in a pool
     - images vmctl created are removed
   * - Another hypervisor
     - rewrite for that hypervisor
     - same file, different ``-p``

When to use virsh anyway
------------------------

vmctl emits ``virsh`` commands; it is not a replacement for knowing them. Reach for
``virsh`` directly for anything vmctl does not model:

* storage pools and volumes --- ``pool-define``, ``vol-create``
* libvirt networks --- ``net-define``, ``net-start``
* live migration --- ``migrate``
* hotplug --- ``attach-device``, ``detach-device`` on a running domain
* anything in the domain XML vmctl has no field for; ``vmctl capabilities`` lists what
  it can express for this provider, and ``--policy strict`` refuses the rest rather
  than guessing

A useful habit while learning either one: ``vmctl -p libvirt import vm.yaml --out ./``
writes the domain XML and the shell script it *would* run, without running them. It is
the quickest way to see how a field you wrote becomes XML.
