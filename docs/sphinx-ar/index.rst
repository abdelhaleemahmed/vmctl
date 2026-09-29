توثيق vmctl
===========

**vmctl** يصف الجهاز الافتراضي في ملف، ثم يصنع جهازاً حقيقياً منه --- على VirtualBox
أو libvirt/QEMU-KVM أو QEMU المجرّد أو VMware Workstation، بالأوامر نفسها والملفات
نفسها. صدِّر جهازاً لديك، وأعد إنشاءه على مُشرف افتراضي آخر، وشاهد ما انحرف عن الملف،
ثم أعده إلى ما يقوله الملف.

.. code-block:: bash

   pip install https://github.com/abdelhaleemahmed/vmctl/releases/download/v4.0.2/vmctl-4.0.2-py3-none-any.whl

   # تصدير جهاز افتراضي موجود
   vmctl export my-vm -o my-vm.yaml

   # إعادة إنشائه هنا، أو على مُشرف افتراضي آخر (تشغيل تجريبي أولاً)
   vmctl import my-vm.yaml --new-name test-vm
   vmctl -p libvirt import my-vm.yaml --policy nearest --execute

   # ما لم يعد مطابقاً للملف، ثم إعادته إلى ما يقوله الملف
   vmctl diff test-vm my-vm.yaml
   vmctl apply my-vm.yaml --execute

   # تشغيل مجموعة كاملة من الأجهزة
   vmctl batch create cluster.yaml --execute

.. toctree::
   :maxdepth: 2
   :caption: دليل المستخدم

   guide/install
   guide/quickstart
   guide/providers
   guide/configuration
   guide/batch
   guide/completion

.. toctree::
   :maxdepth: 2
   :caption: مرجع سطر الأوامر

   cli/index
   reference

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
