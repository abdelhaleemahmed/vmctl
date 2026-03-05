توثيق vmctl
===========

**vmctl** أداة سطر أوامر لإدارة الأجهزة الافتراضية في VirtualBox مع دعم كامل لمبدأ
"الإعدادات كأكواد برمجية". صدِّر أي جهاز افتراضي إلى ملف YAML أو JSON، وأعد إنشاءه
في أي مكان، وأنشئ مجموعات كاملة من الأجهزة بأمر واحد.

.. code-block:: bash

   pip install vmctl

   # تصدير جهاز افتراضي موجود
   vmctl export my-vm -o my-vm.yaml

   # إعادة إنشائه (تشغيل تجريبي أولاً)
   vmctl import my-vm.yaml --new-name test-vm
   vmctl import my-vm.yaml --new-name test-vm --execute

   # تشغيل مجموعة كاملة من الأجهزة
   vmctl batch create cluster.yaml --execute

.. toctree::
   :maxdepth: 2
   :caption: دليل المستخدم

   guide/install
   guide/quickstart
   guide/configuration
   guide/batch
   guide/completion

.. toctree::
   :maxdepth: 2
   :caption: مرجع سطر الأوامر

   cli/index

.. toctree::
   :maxdepth: 3
   :caption: مرجع API

   api/index

.. toctree::
   :maxdepth: 1
   :caption: معلومات المشروع

   changelog

الفهارس والجداول
----------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
