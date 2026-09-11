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
from copy import copy
from types import FrameType
from typing import Callable, Any

from perceval.algorithm.processor_compatibility import computer_from_processor
from perceval.runtime.platform_specs import serialize_specs_backward_compatibility
from qat.comm.exceptions.ttypes import QPUException, ErrorType
from qat.core import HardwareSpecs, Job as MyQLMJob, Result as MyQLMResult
from qat.core.qpu import QPUHandler

from perceval import PayloadGenerator, RemoteComputer, AComputer, Execution, Computation, PayloadUpdater, AProcessor
from perceval.serialization import Serialization
from perceval.utils.logging import channel, get_logger
from requests import HTTPError

from .myqlm_converter import MyQLMConverter
from .myqlm_helper import MyQLMHelper


class QuandelaQPUHandler(QPUHandler):
    """
    Quandela compatible version of myQLM ``QPUHandler`` class.

    :param computer: A constructed Perceval computer (local or remote)

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
    >>> rc = RemoteComputer(QuandelaCommunicationLayer("platform:name", "valid_access_token", "address.of.the.qpu.api"))
    >>> handler = QuandelaQPUHandler(rc)
    >>> myqlm_result = handler.submit_job(myqlm_job)

    Usage as a server:

    >>> from perceval import RemoteProcessor
    >>> from perceval_interop import QuandelaQPUHandler
    >>>
    >>> lc = SimulatedComputer("SLOS")
    >>> handler = QuandelaQPUHandler(lc)
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

    def __init__(self, computer: AComputer):
        super().__init__()
        if isinstance(computer, AProcessor):
            computer = computer_from_processor(computer)
        self.computer = computer
        self._execution: Execution | None = None
        self._cancel_requested = False
        self._previous_sigterm_handler: Callable[[int, FrameType | None], Any] | None = None

    # sigterm handling
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
        if previous_handler is None:
            return

        signal.signal(signal.SIGTERM, previous_handler)
        self._previous_sigterm_handler = None

    def _call_previous_sigterm_handler(self, signum, frame):
        if self._previous_sigterm_handler is None or self._previous_sigterm_handler == signal.SIG_IGN:
            return

        if self._previous_sigterm_handler == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return

        self._previous_sigterm_handler(signum, frame)

    def _handle_sigterm(self, signum, frame):
        if self._execution is None:
            self._call_previous_sigterm_handler(signum, frame)
            return

        self._job_cancel_requested = True
        get_logger().info("Received SIGTERM, canceling current job", channel.user)
        try:
            self._execution.cancel()
        except Exception as e:
            get_logger().error(f"Failed to cancel current job after SIGTERM: {e}", channel.user)

    def serve(self, port, host_ip="localhost", server_type=None, ssl_cert: str = None,
              ssl_key: str = None, ssl_ca: str = None):
        if self._is_threaded_server(server_type):
            raise ValueError('QuandelaQPUHandler does not support server_type="threaded".')

        previous_sigterm_handler = None
        if self._is_stoppable_server(server_type):
            previous_sigterm_handler = self._install_sigterm_handler()

        try:
            return super().serve(port, host_ip, server_type, ssl_cert, ssl_key, ssl_ca)
        finally:
            self._restore_sigterm_handler(previous_sigterm_handler)

    # Server
    def _get_specs(self) -> HardwareSpecs:
        hw = HardwareSpecs()

        MyQLMHelper.write_meta_data(hw, MyQLMHelper.SPECS_KEY, serialize_specs_backward_compatibility(self.computer.specs))
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.TYPE_KEY, self.computer.type.name)  # For legacy clients
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.STATUS_KEY, self.computer.status)
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.PERF_KEY, self.computer.performance)
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.PROGRESS_KEY, self._get_progress())
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.NAME_KEY, self.computer.name)
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.AVAILABLE_JOBS_KEY, self.computer.available_jobs)
        MyQLMHelper.write_meta_data(hw, MyQLMHelper.DETAILS_KEY, self.computer.details, use_archive=True)

        if "waiting_jobs" in self.computer.details:    # For legacy clients
            MyQLMHelper.write_meta_data(hw, MyQLMHelper.WAITING_JOB_KEY, self.computer.details["waiting_jobs"])
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

        * Platform type
        * Latest auto-characterisation results (QPU performance - in terms of transmittance, g², HOM, etc.)
        * Platform status (available, running, in maintenance...)
        * Current job progress (float between 0 and 1, 1 meaning 100% or no job running)
        """
        try:
            return self._get_specs()

        except Exception as e:
            raise QPUException(code=ErrorType.ABORT, modulename="QuandelaQPUHandler", message=str(e)) # Error ABORT (code 1) raised when the execution is stopped

    def _get_progress(self):
        return self._execution.status.progress if self._execution is not None else 1.

    def _submit_job(self, job: MyQLMJob) -> MyQLMResult:
        try:
            platform_status = self.computer.status
        except HTTPError:
            raise RuntimeError("Platform is not available")
        if platform_status not in self._VALID_STATUS:
            raise RuntimeError("Platform is not available")

        if self._execution is not None:
            raise RuntimeError("An execution is already running")

        if job.circuit is not None and job.nbshots:
            converter = MyQLMConverter()
            e = converter.convert(job.circuit, use_postselection=True).experiment  # TODO: directly convert to an Experiment
            comp = Computation(self.computer.get_command("sample_count"), e)
            comp.add_params(max_shots = job.nbshots, max_samples = job.nbshots)
            payload = PayloadGenerator.from_computation(comp)
        else:
            cloud_data = MyQLMHelper.parse_meta_data(job, MyQLMHelper.PAYLOAD_KEY)
            if cloud_data is None:
                raise RuntimeError("No valid payload data found")

            payload = PayloadUpdater.update_payload(cloud_data["payload"], self.computer)
            comp = PayloadGenerator.get_computation(payload)
            if comp.command.name not in self.computer.available_commands:
                raise ValueError(f"Received unknown command {comp.command.name} - "
                                 f"Possible commands are {self.computer.available_commands}")

        computer = copy(self.computer)  # For the payload applier - Else, it would change the specs

        try:
            get_logger().info(f"Starting new job: {comp.job_name}", channel.user)

            self._execution = Execution(comp, computer)
            if self._cancel_requested:
                raise RuntimeError("Job was canceled due to cancel request")

            with PayloadGenerator.payload_applier(computer, payload):
                self._execution.execute_async()

                while not self._execution.is_complete:
                    if self.computer.status not in self._VALID_STATUS:
                        self._execution.cancel()
                        raise RuntimeError("Platform was made unavailable during job completion; Job has been canceled")
                    if self._cancel_requested:
                        self._execution.cancel()
                        raise RuntimeError("Job was canceled due to cancel request")

                    time.sleep(self._SLEEP_TIME)

                pcvl_results = self._execution.get_results()
                get_logger().debug("Results obtained from the job")

        except KeyboardInterrupt:
            self._execution.cancel()
            raise RuntimeError("Job has been canceled.")
        except Exception as e:
            if self._execution.status.failed:
                get_logger().warn(f'The job failed: {self._execution.status.stop_message}', channel.user)
                pcvl_results = {'error': self._execution.status.stop_message}
            else:
                try:
                    self._execution.cancel()
                except Exception as cancel_error:
                    raise cancel_error from e
                raise e

        finally:
            duration = self._execution.status.duration
            details = self._execution.get_details()
            self._cancel_requested = False
            self._execution = None

        pcvl_results["details"] = details
        pcvl_results["job_duration"] = duration

        result = MyQLMResult()
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


Serialization.register_class(QuandelaQPUHandler, ["computer", "_execution"], tag="QuandelaQPUHandler")
