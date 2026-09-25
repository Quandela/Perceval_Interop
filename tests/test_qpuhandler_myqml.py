# MIT License
#
# Copyright (c) 2025 Quandela
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import os
import signal

import pytest
from perceval import Experiment, Matrix, Unitary, BasicState, PayloadGenerator, ProviderFactory, BSSamples, \
    SimulatedComputer, RemoteComputer, ExecutionFactory
from perceval.algorithm import Sampler
from perceval.serialization import serialize
from ._test_utils import assert_bsd_close

from perceval_interop import QuandelaQPUHandler, MyQLMHelper, MyQLMCommunicationLayer

try:
    from qat.core import HardwareSpecs, Job
except ModuleNotFoundError as e:
    assert e.name == "qat"
    pytest.skip("need `myqlm` module", allow_module_level=True)


def _test_serialize_deserialize(obj, file_name):
    exception = None
    try:
        obj.dump(file_name)
        obj = type(obj).load(file_name)
    except Exception as e:
        exception = e
    finally:
        # Cleanup
        try:  # Pytest should not find this error if the file failed to be created
            os.remove(file_name)
        except:
            pass
        if exception is not None:
            raise exception  # Here is what pytest should catch

    return obj


def test_specs():
    comp = SimulatedComputer("SLOS")
    handler = QuandelaQPUHandler(comp)

    specs = handler.get_specs()
    assert isinstance(specs, HardwareSpecs)

    _test_serialize_deserialize(specs, "test_specs.hw")

    specs = MyQLMHelper.retrieve_specs(specs)
    assert specs == comp.specs


def test_user_stack():
    # LEGACY
    # Build your experiment
    exp = Experiment()
    exp.add(0, Unitary(Matrix.random_unitary(8)))
    exp.with_input(BasicState([1, 0] * 4))  # |1,0,1,0,1...>
    exp.min_detected_photons_filter(2)

    # First, turn the experiment into a MyQLM serializable Job
    command = "probs"
    job = MyQLMHelper.make_job(command, exp)

    assert isinstance(job, Job)

    full_payload = MyQLMHelper.parse_meta_data(job, MyQLMHelper.PAYLOAD_KEY)
    # Experiments don't define == so we compare the serialized results
    assert serialize(full_payload, compress=True) == PayloadGenerator.generate_payload(command, exp)

    job = _test_serialize_deserialize(job, "test_job.job")

    # Assumes the job is now as it will be when given to the remote handler
    comp = SimulatedComputer("SLOS")
    handler = QuandelaQPUHandler(comp)

    results = handler.submit_job(job)

    results = _test_serialize_deserialize(results, "test_results.res")

    perceval_results = MyQLMHelper.retrieve_results(results)
    assert "details" in perceval_results
    assert "job_duration" in perceval_results

    local_results = comp.probs(exp)
    assert perceval_results["global_perf"] == local_results["global_perf"]

    # Perceval serializes BSDs with a 1e-6 precision
    assert_bsd_close(perceval_results["results"], local_results["results"], abs=1e-6)


def test_session():
    # LEGACY
    comp = SimulatedComputer("SLOS")
    handler = QuandelaQPUHandler(comp)

    session = ProviderFactory.get_provider("MyQLM", remote_qpu=handler)

    rp = session.build_remote_processor()

    rp.add(0, Unitary(Matrix.random_unitary(8)))
    rp.with_input(BasicState([1, 0] * 4))  # |1,0,1,0,1...>
    rp.min_detected_photons_filter(2)

    sampler = Sampler(rp, max_shots_per_call=10_000)

    perceval_results = sampler.samples(1000)

    # Check that the Sampler's automatic conversion has been correctly applied
    assert isinstance(perceval_results["results"], BSSamples)
    assert len(perceval_results["results"]) == 1000


def test_sigterm_cancels_running_job():
    class _CancelableJob:
        def __init__(self):
            self.cancel_called = False

        def cancel(self):
            self.cancel_called = True

    handler = QuandelaQPUHandler(SimulatedComputer("SLOS"))
    running_job = _CancelableJob()

    handler._execution = running_job
    handler._handle_sigterm(signal.SIGTERM, None)

    assert running_job.cancel_called
    assert handler._job_cancel_requested


def test_stoppable_serve_installs_sigterm_handler(monkeypatch):
    handler = QuandelaQPUHandler(SimulatedComputer("SLOS"))
    calls = []

    def _install_sigterm_handler():
        calls.append("install")
        return "previous_handler"

    def _restore_sigterm_handler(previous_handler):
        calls.append(("restore", previous_handler))

    def _serve(self, port, host_ip="localhost", server_type=None, ssl_cert=None, ssl_key=None, ssl_ca=None):
        calls.append(("serve", port, host_ip, server_type, ssl_cert, ssl_key, ssl_ca))
        return "served"

    monkeypatch.setattr(handler, "_install_sigterm_handler", _install_sigterm_handler)
    monkeypatch.setattr(handler, "_restore_sigterm_handler", _restore_sigterm_handler)
    monkeypatch.setattr(QuandelaQPUHandler.__mro__[1], "serve", _serve)

    assert handler.serve(1234, host_ip="0.0.0.0", server_type="stoppable",
                         ssl_cert="cert", ssl_key="key", ssl_ca="ca") == "served"
    assert calls == [
        "install",
        ("serve", 1234, "0.0.0.0", "stoppable", "cert", "key", "ca"),
        ("restore", "previous_handler")
    ]


def test_threaded_serve_is_refused(monkeypatch):
    handler = QuandelaQPUHandler(SimulatedComputer("SLOS"))

    def _serve(*args, **kwargs):
        raise AssertionError("Base serve should not be called")

    monkeypatch.setattr(QuandelaQPUHandler.__mro__[1], "serve", _serve)

    with pytest.raises(ValueError, match='server_type="threaded"'):
        handler.serve(1234, server_type="threaded")


def test_communication_layer():
    computer = SimulatedComputer("SLOS")

    handler = QuandelaQPUHandler(computer)
    comm_layer = MyQLMCommunicationLayer(handler)
    remote_computer = RemoteComputer(comm_layer)

    # Command doesn't implement __eq__
    remote_specs = remote_computer.specs
    specs = computer.specs

    remote_commands = remote_specs.pop("commands")
    commands = specs.pop("commands")

    assert str(remote_commands) == str(commands)
    assert remote_specs == specs
    assert remote_computer.name == computer.name
    assert remote_computer.status == computer.status
    assert remote_computer.type == computer.type

    experiment = Experiment(Unitary.random(8))
    experiment.with_input(BasicState([1, 0] * 4))
    experiment.min_detected_photons_filter(2)

    factory = ExecutionFactory(remote_computer, experiment, max_shots_per_call=10_000)
    results = factory.samples(1000)
    assert isinstance(results["results"], BSSamples)
    assert len(results["results"]) == 1000
