التثبيت
=======

المتطلبات
---------

* Python 3.8 أو أحدث
* VirtualBox مثبت على الجهاز المضيف
* ``VBoxManage`` متاح في متغير البيئة ``PATH``

تحقق من إمكانية الوصول إلى VirtualBox::

   VBoxManage --version

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
   vmctl list
