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
import json
import os
import signal

import pytest
from perceval import RemoteProcessor, Experiment, Matrix, Unitary, BasicState, PayloadGenerator, NoiseModel, \
    BSDistribution, FockState, ProviderFactory, BSSamples
from perceval.algorithm import Sampler
from perceval.runtime.rpc_handler import RPCHandler
from perceval.serialization import serialize

from perceval_interop import QuandelaQPUHandler, MyQLMHelper

try:
    from qat.core import HardwareSpecs, Job
except ModuleNotFoundError as e:
    assert e.name == "qat"
    pytest.skip("need `myqlm` module", allow_module_level=True)


class _MockRPCHandler(RPCHandler):

    JOB_ID = "123"

    def __init__(self, name):
        super().__init__(name, "no_url", "no_token")
        self._results = BSDistribution({FockState([1, 0]): 1})

    def create_job(self, payload) -> str:
        return _MockRPCHandler.JOB_ID

    def get_job_status(self, id: str) -> dict:
        return {'status': 'completed'}

    def get_job_results(self, id: str) -> dict:
        return {'results': json.dumps(serialize({"results": self._results}))}

    def fetch_platform_details(self) -> dict:
        return {"status": "available", "waiting_jobs": 0}

    @property
    def results(self):
        return {"results": self._results, "job_id": _MockRPCHandler.JOB_ID, "job_duration": 0}


class _MockRemoteProcessor(RemoteProcessor):

    def __init__(self, name):
        super().__init__(name, rpc_handler=_MockRPCHandler(name))

    def fetch_data(self):
        self._specs = {"name": self.name,
                       "noise": NoiseModel(0.8),  # Includes something not serializable by MyQML
                       "available_commands": ["probs"]}

    def get_expected_results(self):
        return self._rpc_handler.results


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
    rp = _MockRemoteProcessor("sim:test")
    handler = QuandelaQPUHandler(rp)

    specs = handler.get_specs()
    assert isinstance(specs, HardwareSpecs)

    _test_serialize_deserialize(specs, "test_specs.hw")

    specs = MyQLMHelper.retrieve_specs(specs)
    assert specs == rp.specs


def test_user_stack():
    # Build your experiment
    exp = Experiment()
    exp.add(0, Unitary(Matrix.random_unitary(8)))
    exp.with_input(BasicState([1, 0] * 4))  # |1,0,1,0,1...>
    exp.min_detected_photons_filter(2)

    # First, turn the experiment into a MyQLM serializable Job
    command = "probs"
    job = MyQLMHelper.make_job(command, exp, max_shots=10_000_000)

    assert isinstance(job, Job)

    full_payload = MyQLMHelper.parse_meta_data(job, MyQLMHelper.PAYLOAD_KEY)
    # Experiments don't define == so we compare the serialized results
    assert serialize(full_payload, compress=True) == PayloadGenerator.generate_payload(command, exp, max_shots=10_000_000)

    job = _test_serialize_deserialize(job, "test_job.job")

    # Assumes the job is now as it will be when given to the remote handler
    rp = _MockRemoteProcessor("sim:test")
    handler = QuandelaQPUHandler(rp)

    results = handler.submit_job(job)

    results = _test_serialize_deserialize(results, "test_results.res")

    perceval_results = MyQLMHelper.retrieve_results(results)

    assert perceval_results == rp.get_expected_results()


def test_session():
    mock_rp = _MockRemoteProcessor("sim:test")
    handler = QuandelaQPUHandler(mock_rp)

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

    mock_rp = _MockRemoteProcessor("sim:test")
    handler = QuandelaQPUHandler(mock_rp)
    running_job = _CancelableJob()

    handler._job = running_job
    handler._handle_sigterm(signal.SIGTERM, None)

    assert running_job.cancel_called
    assert handler._job_cancel_requested


def test_stoppable_serve_installs_sigterm_handler(monkeypatch):
    mock_rp = _MockRemoteProcessor("sim:test")
    handler = QuandelaQPUHandler(mock_rp)
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
    mock_rp = _MockRemoteProcessor("sim:test")
    handler = QuandelaQPUHandler(mock_rp)

    def _serve(*args, **kwargs):
        raise AssertionError("Base serve should not be called")

    monkeypatch.setattr(QuandelaQPUHandler.__mro__[1], "serve", _serve)

    with pytest.raises(ValueError, match='server_type="threaded"'):
        handler.serve(1234, server_type="threaded")
