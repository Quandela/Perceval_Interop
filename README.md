
[![GitHub release](https://img.shields.io/github/v/release/Quandela/Perceval_Interop.svg?style=plastic)](https://github.com/Quandela/Perceval_Interop/releases/latest)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/perceval-interop?style=plastic)
[![CI](https://github.com/Quandela/Perceval_Interop/actions/workflows/python-publish.yml/badge.svg)](https://github.com/Quandela/Perceval-Interop/actions/workflows/python-publish.yml)

[![CI](https://github.com/Quandela/Perceval_Interop/actions/workflows/autotests.yml/badge.svg)](https://github.com/Quandela/Perceval_Interop/actions/workflows/autotests.yml)
[![CI](https://github.com/Quandela/Perceval_Interop/actions/workflows/build-and-deploy-docs.yml/badge.svg)](https://github.com/Quandela/Perceval_Interop/actions/workflows/build-and-deploy-docs.yml)

# Perceval-Interop <a href="https://perceval.quandela.net" target="_blank"> <img src="logo-perceval.png" width="50" height="50"> </a>


Perceval-Interop is designed to facilitate a bridge between Perceval, a photonic quantum
computing framework, and several leading gate-based frameworks through a python API.

It provides converters to translate gate-based quantum circuits from various frameworks
into Perceval's linear optical circuits using dual rail encoding. Currently
supported frameworks include:

- Quantum gate circuit conversion from **Qiskit**, **myQLM**, and **cQASM**.
- Quantum states conversion from **Qutip**.


# Installation

Perceval-Interop requires:

* Python between 3.9 and 3.13

## PIP
We recommend installing it with `pip`, and selecting any interop package such as `qiskit`, `qutip`, `myqlm`, or `cqasm`:

```bash
pip install --upgrade pip
pip install perceval-interop[qiskit_bridge] #install qiskit and seaborn
pip install perceval-interop[qutip_bridge] #install qutip
pip install perceval-interop[myqlm_bridge] #install myqlm
pip install perceval-interop[cqasm_bridge] #install cqasm
pip install perceval-interop[all] #install all above
```

## GitHub
```bash
git clone https://github.com/quandela/Perceval
```
then to install Perceval:
```bash
pip install .
```
Or for developers:
```bash
pip install -e .
```

# Running tests

Unit tests files are part of the repository in `tests/` and can be run with:

```
pip install -r tests/requirements.txt
pytest
```

Additionally, you can see a coverage report with the command:

```
pytest --cov=perceval-interop
```

# Documentation and Forum

* The [documentation](https://perceval.quandela.net/interopdocs/)
* The [Community Forum](https://perceval.quandela.net/forum)

#
[<img src="logo-quandela.png" width="300" height=auto>](https://www.quandela.com/)

[![Twitter Follow](https://img.shields.io/twitter/follow/Quandela_SAS?style=social)](https://twitter.com/Quandela_SAS)
[![YouTube Channel Subscribers](https://img.shields.io/youtube/channel/subscribers/UCl5YMpSqknJ1n-IT-XWfLsQ?style=social)](https://www.youtube.com/channel/UCl5YMpSqknJ1n-IT-XWfLsQ)
