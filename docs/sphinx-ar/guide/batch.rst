إنشاء أجهزة افتراضية دفعةً واحدة (Batch)
==========================================

أنشئ عدة أجهزة افتراضية من ملف تعريف واحد باستخدام أمر ``batch create``.

توليد قالب
-----------

.. code-block:: bash

   vmctl batch template -o my-cluster.yaml

بنية ملف الدُّفعة
-----------------

.. code-block:: yaml

   name: dev-cluster
   description: بيئة التطوير

   base_vm: ubuntu-template     # اسم جهاز افتراضي موجود، أو مسار ملف، أو تعريف مدمج

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
       disks:
         - size_mb: 204800      # تجاوز حجم القرص الأول
       metadata:
         role: database

تشغيل تجريبي (Dry-run)
-----------------------

.. code-block:: bash

   vmctl batch create dev-cluster.yaml

يعرض الأمر ملخصاً بما سيتم إنشاؤه دون تعديل أي شيء في VirtualBox::

   Would create 3 VMs:
     dev-web-01  (4 CPUs, 4096 MB RAM, 1 disk(s))
     dev-web-02  (4 CPUs, 4096 MB RAM, 1 disk(s))
     dev-db-01   (8 CPUs, 16384 MB RAM, 1 disk(s))

   Run with --execute to apply.

التنفيذ الفعلي
--------------

.. code-block:: bash

   vmctl batch create dev-cluster.yaml --execute

تُنشأ الأجهزة الافتراضية بالتسلسل. إن فشل أحدها، تتوقف الدُّفعة وتعرض رسالة الخطأ.

تعريف القاعدة مباشرةً في الملف
--------------------------------

لا حاجة إلى جهاز افتراضي موجود — يمكن تعريف الجهاز الأساسي مباشرةً:

.. code-block:: yaml

   name: test-cluster

   base_vm:
     ostype: Ubuntu_64
     cpu:
       count: 2
     memory:
       mb: 2048
     disks:
       - name: system
         size_mb: 20480
         type: HDD
         controller: SATA
         port: 0
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
