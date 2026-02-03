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
from qat.core import HardwareSpecs, Job as MyQLMJob, Result as MyQLMResult
from qat.core.qpu import QPUHandler

from perceval import RemoteJob, RemoteProcessor, PayloadGenerator, ProcessorType
from perceval.runtime.remote_processor import PERFS_KEY
from perceval.utils.logging import channel, get_logger
from requests import HTTPError

from .myqlm_converter import MyQLMConverter
from .myqlm_helper import MyQLMHelper


class QuandelaQPUHandler(QPUHandler):
    """
    Quandela compatible version of myQLM ``QPUHandler`` class. This class is supposed to be the middleware between a user
    script, or a Qaptiva server and a single remote platform from Quandela.

    :param remote_processor: A constructed Perceval access to a remote platform which will be used to send requests and
                             retrieve results.

    This class can be used in two ways:

      * As an object
      * As a server

    Usage as an object:

    >>> from perceval_interop import QuandelaQPUHandler
    >>> from perceval import RemoteProcessor
    >>> from qat.core import Job
    >>>
    >>> myqlm_job = Job()
    >>> # Define your quantum experiment in the job
    >>> # ...
    >>> rp = RemoteProcessor("platform:name", "valid_access_token", "address.of.the.qpu.api")
    >>> handler = QuandelaQPUHandler(rp)
    >>> myqlm_result = handler.submit_job(myqlm_job)

    Usage as a server:

    >>> from perceval import RemoteProcessor
    >>> from perceval_interop import QuandelaQPUHandler
    >>>
    >>> rp = RemoteProcessor("platform:name", "valid_access_token", "address.of.the.qpu.api")
    >>> handler = QuandelaQPUHandler(rp)
    >>> handler.serve(host_ip="middleware.host.address", port=1212)

    After that, the ``QuandelaQPUHandler`` is listening to requests and transmitting them to the Quandela platform.
    User scripts may connect by running:

    >>> from qat.qpus import RemoteQPU
    >>> from qat.core import Job
    >>>
    >>> myqlm_job = Job()
    >>> # Define your quantum experiment in the job
    >>> # ...
    >>> qpu = RemoteQPU(1212, "middleware.host.address")
    >>> result = qpu.submit_job(myqlm_job)
    """

    def __init__(self, remote_processor: RemoteProcessor):
        super().__init__()
        self.processor = remote_processor  # Used to get the specs
        self.handler = remote_processor.get_rpc_handler()  # Used to submit jobs
        self._job = None

    def get_specs(self) -> HardwareSpecs:
        """
        Retrieve the specifications of the Quandela platform and store them in the metadata field of a myQLM
        ``HardwareSpecs`` instance.

        :return: Hardware specifications

        Data is split into several chunks (some are optional, depending on the platform):

        * Full specifications
            * Available commands
            * Chip architecture
            * Platform custom options
            * Platform documentation

        * Platform name
        * Latest auto-characterisation results (QPU performance - in terms of transmittance, g², HOM, etc.)
        * Platform status (available, running, in maintenance...)
        * Current job progress (float between 0 and 1, 1 meaning 100% or no job running)
        """
        hw = HardwareSpecs()

        # These fields are not supposed to change
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.SPECS_KEY, self.processor.specs)
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.TYPE_KEY, self.processor.type.name)

        MyQLMHelper.write_meta_data(hw, MyQLMHelper.PROGRESS_KEY, self._get_progress())

        try:
            platform_details = self.handler.fetch_platform_details()
        except HTTPError:
            platform_details = {}

        MyQLMHelper.write_meta_data(hw, MyQLMHelper.STATUS_KEY, platform_details.get("status", "unreachable"))

        if self.processor.type == ProcessorType.PHYSICAL:
            if PERFS_KEY in platform_details:
                self.processor.performance.update(platform_details[PERFS_KEY])

            MyQLMHelper.write_meta_data(hw, MyQLMHelper.PERF_KEY, self.processor.performance)

        if "waiting_jobs" in platform_details:
            MyQLMHelper.write_meta_data(hw, MyQLMHelper.WAITING_JOB_KEY, platform_details["waiting_jobs"])
        return hw

    def _get_progress(self):
        return self._job.status.progress if self._job is not None else 1.

    def submit_job(self, job: MyQLMJob) -> MyQLMResult:
        """
        Submit a myQLM job to the Quandela platform.

        :param job: A myQLM ``Job`` containing

                    * either a photonic-compatible gate-based circuit
                    * or a Perceval generated payload, stored in the job metadata

        :return: A myQLM ``Result`` containing Perceval-like results in its metadata field
        """

        if job.circuit is not None and job.nbshots:
            converter = MyQLMConverter()
            p = converter.convert(job.circuit, use_postselection=True)
            full_payload = PayloadGenerator.generate_payload(command="sample_count",
                                                             experiment=p.experiment,
                                                             platform_name=self.handler.name,
                                                             max_shots=job.nbshots,
                                                             max_samples=job.nbshots)
        else:
            full_payload = MyQLMHelper.parse_meta_data(job, MyQLMHelper.PAYLOAD_KEY)

        if full_payload is None:
            raise RuntimeError("No valid payload data found")

        if not full_payload.get("platform_name", ""):
            full_payload["platform_name"] = self.processor.name

        elif full_payload['platform_name'] != self.processor.name:
            raise RuntimeError("Platform name mismatch")

        try:
            platform_details = self.handler.fetch_platform_details()
        except HTTPError:
            raise RuntimeError("Platform is not available")
        if platform_details.get("status") != 'available':
            raise RuntimeError("Platform is not available")

        job_name = full_payload['payload'].get("job_name", full_payload['payload'].get("command", "Job"))
        job_context = full_payload['payload'].get('job_context')

        if self._job is not None:
            raise RuntimeError("A job is already running")

        self._job = RemoteJob(full_payload, self.handler, job_name)
        try:
            pcvl_results = self._job.execute_sync()
        except Exception as e:
            if self._job.status.failed:
                get_logger().warn(f'The job failed: {self._job.status.stop_message}', channel.user)
                pcvl_results = {'error': self._job.status.stop_message}
            else:
                raise e

        if job_context is not None:
            pcvl_results["job_context"] = job_context

        self._job = None

        result = MyQLMResult()
        # Note: we could avoid a deserialization/serialization
        MyQLMHelper.write_meta_data(result, MyQLMHelper.RESULTS_KEY, pcvl_results)
        return result
