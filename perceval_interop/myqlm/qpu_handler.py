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

try:
    from qat.core import HardwareSpecs
    from qat.core.wrappers import Job, Result
    from qat.core.qpu import QPUHandler
except ImportError:
    class QPUHandler:
        # Needed so we can inherit from it
        pass

from perceval import (RemoteJob, RemoteProcessor, Experiment, PayloadGenerator, ProcessorType,
                      __version__ as pcvl_version)
from perceval.serialization import serialize, deserialize

from .myqlm_converter import MyQLMConverter


class PercevalHandler:
    PAYLOAD_KEY = "perceval_payload"
    SPECS_KEY = "platform_specs"
    PERF_KEY = "platform_perf"
    TYPE_KEY = "platform_type"
    RESULTS_KEY = "perceval_results"


    @staticmethod
    def make_job(command: str,
                 experiment: Experiment = None,
                 params: dict[str, any] = None,
                 platform_name: str = "",
                 **kwargs) -> "Job":
        """
        :param command: name of the method used
        :param experiment: (optional) Perceval experiment, by default an empty Experiment will be generated
        :param params: (optional) parameters to be listed in the 'parameters' section of the payload
        :param platform_name: (optional) name of the platform used
        :param kwargs: (optional) arguments to add to the payload, such as `max_shots` or `max_samples`
        :return: A MyQLM Job instance containing the perceval payload as a string in the meta_data field.
        """
        payload = PayloadGenerator.generate_payload(command, experiment, params, platform_name, **kwargs)
        job = Job()
        PercevalHandler.write_meta_data(job, PercevalHandler.PAYLOAD_KEY, payload)
        return job

    @staticmethod
    def parse_meta_data(obj, key: str):
        if not hasattr(obj, "meta_data") or obj.meta_data is None:
            return None
        return deserialize(json.loads(obj.meta_data[key]))

    @staticmethod
    def write_meta_data(obj, key: str, value):
        if not hasattr(obj, "meta_data") or not obj.meta_data:
            obj.meta_data = {}
        obj.meta_data[key] = json.dumps(serialize(value))

    @staticmethod
    def retrieve_results(results: "Result") -> dict:
        assert PercevalHandler.RESULTS_KEY in results.meta_data, "Results don't come from a perceval job"
        return PercevalHandler.parse_meta_data(results, PercevalHandler.RESULTS_KEY)

    @staticmethod
    def retrieve_specs(hw: "HardwareSpecs") -> dict:
        """
        :param hw: A HardwareSpecs instance got from requesting the specs from a perceval QPU
        :return:
        """
        assert PercevalHandler.SPECS_KEY in hw.meta_data, "Hardware specs don't come from a quandela qpu"
        return PercevalHandler.parse_meta_data(hw, PercevalHandler.SPECS_KEY)


class QuandelaQPUHandler(QPUHandler):

    def __init__(self, remote_processor: RemoteProcessor):
        super().__init__()
        self.processor = remote_processor  # Used to get the specs
        self.handler = remote_processor.get_rpc_handler()  # Used to submit jobs

    def get_specs(self) -> "HardwareSpecs":
        hw = HardwareSpecs()
        PercevalHandler.write_meta_data(hw, PercevalHandler.SPECS_KEY, self.processor.specs)
        PercevalHandler.write_meta_data(hw, PercevalHandler.TYPE_KEY, self.processor.type.name)
        if self.processor.type == ProcessorType.PHYSICAL:
            PercevalHandler.write_meta_data(hw, PercevalHandler.PERF_KEY, self.processor.performance)
        return hw

    def submit_job(self, job: "Job"):
        full_payload = None
        if job.circuit is not None and job.nbshots:
            converter = MyQLMConverter()
            p = converter.convert(job.circuit, use_postselection=True)
            full_payload = {
                "platform_name": self.processor.name,
                "pcvl_version": pcvl_version,
                "payload": {
                    "command": "sample_count",
                    "experiment": p.experiment,
                    "max_shots": job.nbshots,
                    "max_samples": job.nbshots,
                }
            }
        else:
            full_payload = PercevalHandler.parse_meta_data(job, PercevalHandler.PAYLOAD_KEY)

        if full_payload is None:
            raise RuntimeError(f"No valid payload data found")

        if full_payload['platform_name'] != self.processor.name:
            raise RuntimeError("Platform name mismatch")

        job_name = full_payload['payload'].get("command", "MyJob")
        job = RemoteJob(full_payload, self.handler, job_name)
        pcvl_results = job.execute_sync()

        result = Result()
        # Note: we could avoid a deserialization/serialization
        PercevalHandler.write_meta_data(result, PercevalHandler.RESULTS_KEY, pcvl_results)
        return result
