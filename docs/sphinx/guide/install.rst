Installation
============

Requirements
------------

* Python 3.8 or later
* VirtualBox installed on the host
* ``VBoxManage`` available in your ``PATH``

Verify VirtualBox is accessible::

   VBoxManage --version

From PyPI
---------

.. code-block:: bash

   pip install vmctl

From Source
-----------

.. code-block:: bash

   git clone https://github.com/abdelhaleemahmed/vmctl.git
   cd vmctl
   pip install -e .

Verify the installation
-----------------------

.. code-block:: bash

   vmctl --version
   vmctl list
