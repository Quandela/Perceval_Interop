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
import time
import traceback

from qat.comm.exceptions.ttypes import QPUException, ErrorType
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

    _VALID_STATUS = {"available", "computing", "calibration", "running"}
    _SLEEP_TIME = 1
    _NO_PREVIOUS_SIGTERM_HANDLER = object()

    def __init__(self, remote_processor: RemoteProcessor):
        super().__init__()
        self.processor = remote_processor  # Used to get the specs
        self.handler = remote_processor.get_rpc_handler()  # Used to submit jobs
        self._job = None
        self._job_cancel_requested = False
        self._previous_sigterm_handler = self._NO_PREVIOUS_SIGTERM_HANDLER

    @staticmethod
    def _is_stoppable_server(server_type):
        return isinstance(server_type, str) and server_type.lower() == "stoppable"

    @staticmethod
    def _is_threaded_server(server_type):
        return isinstance(server_type, str) and server_type.lower() == "threaded"

    def _install_sigterm_handler(self):
        previous_handler = signal.getsignal(signal.SIGTERM)
        self._previous_sigterm_handler = previous_handler
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        return previous_handler

    def _restore_sigterm_handler(self, previous_handler):
        if previous_handler is self._NO_PREVIOUS_SIGTERM_HANDLER:
            return

        signal.signal(signal.SIGTERM, previous_handler)
        self._previous_sigterm_handler = self._NO_PREVIOUS_SIGTERM_HANDLER

    def _call_previous_sigterm_handler(self, signum, frame):
        previous_handler = self._previous_sigterm_handler
        if previous_handler is self._NO_PREVIOUS_SIGTERM_HANDLER:
            return

        if previous_handler is None or previous_handler == signal.SIG_IGN:
            return

        if previous_handler == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return

        previous_handler(signum, frame)

    def _handle_sigterm(self, signum, frame):
        job = self._job
        if job is None:
            self._call_previous_sigterm_handler(signum, frame)
            return

        self._job_cancel_requested = True
        get_logger().info("Received SIGTERM, canceling current job", channel.user)
        try:
            job.cancel()
        except Exception as e:
            get_logger().error(f"Failed to cancel current job after SIGTERM: {e}", channel.user)

    def serve(self, port, host_ip="localhost", server_type=None, ssl_cert: str = None,
              ssl_key: str = None, ssl_ca: str = None):
        if self._is_threaded_server(server_type):
            raise ValueError('QuandelaQPUHandler does not support server_type="threaded".')

        previous_sigterm_handler = self._NO_PREVIOUS_SIGTERM_HANDLER
        if self._is_stoppable_server(server_type):
            previous_sigterm_handler = self._install_sigterm_handler()

        try:
            return super().serve(port, host_ip, server_type, ssl_cert, ssl_key, ssl_ca)
        finally:
            self._restore_sigterm_handler(previous_sigterm_handler)

    def _get_specs(self) -> HardwareSpecs:
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
        try:
            return self._get_specs()

        except Exception as e:
            raise QPUException(code=ErrorType.ABORT, modulename="QuandelaQPUHandler", message=str(e)) # Error ABORT (code 1) raised when the execution is stopped

    def _get_progress(self):
        return self._job.status.progress if self._job is not None else 1.

    def _submit_job(self, job: MyQLMJob) -> MyQLMResult:
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
        if platform_details.get("status") not in self._VALID_STATUS:
            raise RuntimeError("Platform is not available")

        if "command" not in full_payload['payload']:
            raise ValueError("Did not receive any command")

        command = full_payload['payload']["command"]
        if command not in self.processor.available_commands:
            raise ValueError(f"Received unknown command {command} - Possible commands are {self.processor.available_commands}")

        job_name = full_payload['payload'].get("job_name", command)
        job_context = full_payload['payload'].get('job_context')

        if self._job is not None:
            raise RuntimeError("A job is already running")

        self._job_cancel_requested = False
        self._job = RemoteJob(full_payload, self.handler, job_name)


        try:
            if self._job_cancel_requested:
                raise RuntimeError("Job has been canceled.")

            self._job.execute_async()

            if self._job_cancel_requested:
                self._job.cancel()
                raise RuntimeError("Job has been canceled.")

            while not self._job.is_complete:
                if self._job_cancel_requested:
                    self._job.cancel()
                    raise RuntimeError("Job has been canceled.")

                platform_details = self.handler.fetch_platform_details()
                if platform_details.get("status") not in self._VALID_STATUS:
                    self._job.cancel()
                    raise RuntimeError("Platform was made unavailable during job completion; Job has been canceled")

                time.sleep(self._SLEEP_TIME)

            if self._job_cancel_requested:
                raise RuntimeError("Job has been canceled.")

            pcvl_results = self._job.get_results()
            get_logger().debug("Results obtained from the job")

        except KeyboardInterrupt:
            self._job.cancel()
            raise RuntimeError("Job has been canceled.")
        except Exception as e:
            if self._job_cancel_requested:
                raise RuntimeError("Job has been canceled.") from e

            if self._job.status.failed:
                get_logger().warn(f'The job failed: {self._job.status.stop_message}', channel.user)
                pcvl_results = {'error': self._job.status.stop_message}
            else:
                try:
                    self._job.cancel()
                except Exception as cancel_error:
                    raise cancel_error from e
                raise e
        finally:
            self._job_cancel_requested = False

        if job_context is not None:
            pcvl_results["job_context"] = job_context

        pcvl_results["job_id"] = self._job.id
        pcvl_results["job_duration"] = self._job.status.duration

        self._job = None

        result = MyQLMResult()
        # Note: we could avoid a deserialization/serialization
        MyQLMHelper.write_meta_data(result, MyQLMHelper.RESULTS_KEY, pcvl_results)
        return result

    def submit_job(self, job: MyQLMJob) -> MyQLMResult:
        """
        Submit a myQLM job to the Quandela platform.

        :param job: A myQLM ``Job`` containing

                    * either a photonic-compatible gate-based circuit
                    * or a Perceval generated payload, stored in the job metadata

        :return: A myQLM ``Result`` containing Perceval-like results in its metadata field
        """

        try:
            get_logger().info("Got a new job", channel.user)
            res = self._submit_job(job)
            get_logger().info("Job finished successfully", channel.user)
            return res

        except Exception as e:
            get_logger().error(f"The job failed: {type(e).__name__}: {e}", channel.user)
            get_logger().error(traceback.format_exc(), channel.user)
            raise QPUException(code=ErrorType.ABORT, modulename="QuandelaQPUHandler", message=str(e)) # Error ABORT (code 1) raised when the execution is stopped
