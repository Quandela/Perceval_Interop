Usage
=====

Installation
------------

To use interop, you can directly install the pip wheel in a virtual environment:

.. code-block:: bash

   (venv) $ pip install perceval-interop

Alternatively, if you are interested in contributing to the project - you can clone the project from github:

.. code-block:: bash

   (venv) $ git clone https://github.com/quandela/Perceval_Interop
   (venv) $ cd Perceval_Interop
   (venv) $ pip install . # or pip install -e . for developers
   (venv) $ pip install .[qiskit_bridge] # to install with qiskit and seaborn
   (venv) $ pip install .[qutip_bridge] # to install with qutip
   (venv) $ pip install .[myqlm_bridge] # to install with myqlm
   (venv) $ pip install .[cqasm_bridge] # to install with cqasm
   (venv) $ pip install .[all] # to install all of the above bridges

Tutorial
--------

At this point you can directly use Perceval_Interop. You can look at the notebooks in the Tutorials section for examples.
If you want to learn how to install and use Perceval, please read: https://github.com/Quandela/Perceval?tab=readme-ov-file#perceval---
