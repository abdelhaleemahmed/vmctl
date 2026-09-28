التثبيت
=======

المتطلبات
---------

Python 3.8 أو أحدث، وأدوات سطر الأوامر لمُشرف افتراضي واحد على الأقل. يقود vmctl
أربعة، ولا يحتاج إلا الذي تستخدمه:

.. list-table::
   :header-rows: 1
   :widths: 26 34 40

   * - المُشرف الافتراضي
     - ما يحتاجه vmctl
     - للتحقق
   * - VirtualBox 7.0+
     - ``VBoxManage`` في ``PATH``
     - ``VBoxManage --version``
   * - libvirt / QEMU-KVM
     - ``virsh`` و ``qemu-img``
     - ``virsh --version``
   * - QEMU المجرّد
     - ``qemu-system-x86_64`` (أو ``qemu-kvm``) و ``qemu-img``
     - ``qemu-img --version``
   * - VMware Workstation 17+
     - ``vmrun`` و ``vmware-vdiskmanager``
     - يجدهما vmctl في مجلد التثبيت

أمر واحد يجيب عن ذلك كله، وعمّا إذا كانت هذه الآلة قادرة على تشغيل جهاز افتراضي أصلاً::

   vmctl doctor

وأمر آخر يتحقق من أن المُشرف الافتراضي موافق فعلاً، بإنشاء جهاز زائل ثم حذفه::

   vmctl selftest

التثبيت من PyPI
---------------

.. code-block:: bash

   pip install vmctl

التثبيت من المصدر
-----------------

.. code-block:: bash

   git clone https://github.com/abdelhaleemahmed/vmctl.git
   cd vmctl
   pip install -e .

التحقق من التثبيت
-----------------

.. code-block:: bash

   vmctl --version
   vmctl providers     # أي المُشرفات قابل للاستخدام هنا؛ النجمة تعني الافتراضي
   vmctl list
