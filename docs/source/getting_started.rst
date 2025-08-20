Getting Started
===============

Installation
------------

We recommend installing `perceval-interop` with `pip` in a virtual environment:

.. code-block:: bash

   (venv) $ pip install perceval-interop
   (venv) $ pip install perceval-interop[qiskit_bridge] # to install with qiskit and seaborn
   (venv) $ pip install perceval-interop[qutip_bridge] # to install with qutip
   (venv) $ pip install perceval-interop[myqlm_bridge] # to install with myqlm
   (venv) $ pip install perceval-interop[cqasm_bridge] # to install with cqasm
   (venv) $ pip install perceval-interop[all] # to install all of the above bridges

Alternatively, if you are interested in contributing to the project - you can clone the project from github:

.. code-block:: bash

   (venv) $ git clone https://github.com/quandela/Perceval_Interop
   (venv) $ cd Perceval_Interop
   (venv) $ pip install . # or pip install -e . for developers

Hello World
-----------

The following is a minimal example demonstrating how to convert a simple circuit from Qiskit to Perceval.

>>> import qiskit
>>> from perceval_interop import QiskitConverter
>>>
>>> qc = qiskit.QuantumCircuit(1)  # 1 qubit Qiskit Circuit
>>> qc.h(0)  # Add an H gate
>>>
>>> qiskit_convertor = QiskitConverter()
>>> perceval_processor = qiskit_convertor.convert(qc)  # converted 2 mode LO circuit which can be used for photonic quantum computing

For more examples (including other frameworks) and detailed explanations, refer to the notebooks in the Tutorials section. If you want to learn
how to install and use Perceval, please read: https://github.com/Quandela/Perceval?tab=readme-ov-file#perceval---
