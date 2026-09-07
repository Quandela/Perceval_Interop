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
from datetime import datetime
from typing import Any

from perceval import Experiment, PayloadGenerator, RunningStatus, ExecutionStatus
from perceval.serialization import serialize, deserialize
from qat.comm.qlmaas.ttypes import JobStatus, JobInfo
from qat.core import HardwareSpecs, Job as MyQLMJob, Result as MyQLMResult
from qat.qlmaas.result import AsyncResult


class MyQLMHelper:
    """
    Helper class that allows to create MyQLM jobs containing perceval experiments so they could run on a QuandelaQPUHandler.
    This class also converts results from a MyQLM job back to a perceval result object.

    Usage example with a local QuandelaQPUHandler:

    >>> from perceval import Experiment
    >>> from perceval_interop import MyQLMHelper
    >>> e = Experiment(4)
    >>> ...  # Define your perceval Experiment
    >>> my_qlm_job = MyQLMHelper.make_job("sample_count", e, max_samples=1000)
    >>> my_qlm_results = qpu_handler.submit_job(my_qlm_job)  # Where qpu_handler is a QuandelaQPUHandler
    >>> results = MyQLMHelper.retrieve_results(my_qlm_results)  # Regular results as in any perceval's Sampler results

    Usage example with a remote QuandelaQPUHandler:

    >>> from perceval import Experiment
    >>> from perceval_interop import MyQLMHelper
    >>> from qat.qpus import RemoteQPU
    >>> e = Experiment(4)
    >>> ...  # Define your perceval Experiment
    >>> my_qlm_job = MyQLMHelper.make_job("sample_count", e, max_samples=1000)
    >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
    >>> my_qlm_results = qpu.submit_job(myqlm_job)
    >>> results = MyQLMHelper.retrieve_results(my_qlm_results)  # Regular results as in any perceval's Sampler results
    """

    NAME_KEY = "name"
    PAYLOAD_KEY = "perceval_payload"
    SPECS_KEY = "platform_specs"
    PERF_KEY = "platform_perf"
    TYPE_KEY = "platform_type"
    RESULTS_KEY = "perceval_results"
    STATUS_KEY = "platform_status"
    PROGRESS_KEY = "current_job_progress"
    WAITING_JOB_KEY = "platform_waiting_jobs"
    AVAILABLE_JOBS_KEY = "available_jobs"

    @staticmethod
    def make_job(command: str,
                 experiment: Experiment,
                 params: dict[str, Any] = None,
                 platform_name: str = "",
                 **kwargs) -> MyQLMJob:
        """
        Create a myQLM ``Job`` from Perceval data

        :param command: name of the method used
        :param experiment: Perceval experiment
        :param params: (optional) parameters to be listed in the 'parameters' section of the payload
        :param platform_name: (optional) name of the platform used
        :param kwargs: (optional) arguments to add to the payload, such as `max_shots` or `max_samples`
        :return: A MyQLM Job instance containing the perceval payload as a string in the meta_data field.
        """
        payload = PayloadGenerator.generate_payload(command, experiment, params, platform_name, **kwargs)
        return MyQLMHelper.make_job_from_payload(payload)

    @staticmethod
    def make_job_from_payload(payload: dict):
        job = MyQLMJob()
        MyQLMHelper.write_meta_data(job, MyQLMHelper.PAYLOAD_KEY, payload)
        return job

    @staticmethod
    def parse_meta_data(obj, key: str, default = None):
        if not hasattr(obj, "meta_data") or obj.meta_data is None or key not in obj.meta_data:
            return default
        return deserialize(json.loads(obj.meta_data[key]), strict=False)

    @staticmethod
    def write_meta_data(obj, key: str, value):
        if not hasattr(obj, "meta_data") or not obj.meta_data:
            obj.meta_data = {}
        obj.meta_data[key] = json.dumps(serialize(value))

    @staticmethod
    def retrieve_results(results: MyQLMResult | AsyncResult) -> dict:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> myqlm_result = qpu.submit_job(myqlm_job)
        >>> results = MyQLMHelper.retrieve_results(myqlm_result)

        :param results: A MyQLM Result instance got from running a MyQLM job on a local or remote QuandelaQPU.
        :return: The specs of the QPU, as if returned by a perceval's RemoteProcessor
        """
        assert MyQLMHelper.RESULTS_KEY in results.meta_data, "Results don't come from a perceval job"
        return MyQLMHelper.parse_meta_data(results, MyQLMHelper.RESULTS_KEY)

    @staticmethod
    def retrieve_specs(hw: HardwareSpecs) -> dict:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> qpu_specs = MyQLMHelper.retrieve_specs(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The specs of the QPU, as if returned by a perceval's RemoteProcessor
        """
        assert MyQLMHelper.SPECS_KEY in hw.meta_data, "Hardware specs don't come from a quandela qpu"
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.SPECS_KEY)

    @staticmethod
    def retrieve_type(hw: HardwareSpecs) -> str:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> qpu_type = MyQLMHelper.retrieve_type(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The type of the QPU (physical or simulator)
        """
        assert MyQLMHelper.TYPE_KEY in hw.meta_data, "Hardware specs don't come from a quandela qpu"
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.TYPE_KEY)

    @staticmethod
    def retrieve_perf(hw: HardwareSpecs) -> dict:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> performances = MyQLMHelper.retrieve_perf(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The performances of the QPU, as if returned by a perceval's RemoteProcessor
        """
        assert MyQLMHelper.PERF_KEY in hw.meta_data, "Hardware specs don't come from a quandela qpu"
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.PERF_KEY)

    @staticmethod
    def retrieve_status(hw: HardwareSpecs) -> str:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> status = MyQLMHelper.retrieve_status(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The status of the QPU, as if returned by a perceval's RemoteProcessor
        """
        # assert MyQLMHelper.STATUS_KEY in hw.meta_data, "Hardware specs don't come from a quandela qpu"
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.STATUS_KEY)

    @staticmethod
    def retrieve_progress(hw: HardwareSpecs) -> dict:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> progress = MyQLMHelper.retrieve_progress(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The progress of the current job running on the QPU
        """
        # assert MyQLMHelper.PROGRESS_KEY in hw.meta_data, "Hardware specs don't come from a quandela qpu"
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.PROGRESS_KEY)

    @staticmethod
    def retrieve_job_in_queue(hw: HardwareSpecs) -> dict:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> job_in_queue = MyQLMHelper.retrieve_status(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The number of jobs currently in queue on the QPU, or None if the Hardware specs don't contain this information
        """
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.WAITING_JOB_KEY) if MyQLMHelper.WAITING_JOB_KEY in hw.meta_data else None


    @staticmethod
    def retrieve_name(hw: HardwareSpecs) -> str:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> remote_name = MyQLMHelper.retrieve_name(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The name of the perceval computer contained in the QPU,
           or an empty string if the Hardware specs don't contain this information
        """
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.NAME_KEY) if MyQLMHelper.NAME_KEY in hw.meta_data else ""

    @staticmethod
    def retrieve_availability(hw: HardwareSpecs) -> int:
        """
        >>> from perceval_interop import MyQLMHelper
        >>> from qat.qpus import RemoteQPU
        >>> qpu = RemoteQPU(1212, "middleware.host.address")  # Assuming this is a remote QuandelaQPUHandler
        >>> hardware_specs = qpu.get_specs()
        >>> availability = MyQLMHelper.retrieve_availability(hardware_specs)

        :param hw: A HardwareSpecs instance got from requesting the specs from a Quandela QPU
        :return: The number of parallel jobs that can currently be launched to the QPU,
           or 1 if the Hardware specs don't contain this information
        """
        return MyQLMHelper.parse_meta_data(hw, MyQLMHelper.AVAILABLE_JOBS_KEY) if MyQLMHelper.NAME_KEY in hw.meta_data else 1

    @staticmethod
    def running_status_from_myqlm(job_status: JobStatus) -> RunningStatus:
        mapping = {
            JobStatus.WAITING: RunningStatus.WAITING,
            JobStatus.RUNNING: RunningStatus.RUNNING,
            JobStatus.DONE: RunningStatus.SUCCESS,
            JobStatus.CANCELLED: RunningStatus.CANCELED,
            JobStatus.UNKNOWN_JOB: RunningStatus.UNKNOWN,
            JobStatus.IN_BUCKET: RunningStatus.WAITING,  # TODO: clarify the meaning of IN_BUCKET
            JobStatus.DELETED: RunningStatus.CANCELED,
            JobStatus.STOPPED: RunningStatus.SUSPENDED,  # TODO: can a STOPPED status be resumed? If not, better set CANCELED
            JobStatus.FAILED: RunningStatus.ERROR
        }

        return mapping.get(job_status, RunningStatus.UNKNOWN)

    @staticmethod
    def _datetime_to_timestamp(s: str):
        # TODO: check format
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()

    @staticmethod
    def execution_status_from_myqlm(job_info: JobInfo, progress: float | None) -> ExecutionStatus:
        status = ExecutionStatus()
        status.status = MyQLMHelper.running_status_from_myqlm(job_info.status)

        if (status.running or status.completed) and progress is not None:
            status.update_progress(progress)  # We can't get the progress anywhere :(
        if status.failed:
            status._message = job_info.message

        status.update_times(MyQLMHelper._datetime_to_timestamp(job_info.submission_date),
                            MyQLMHelper._datetime_to_timestamp(job_info.starting_date),
                            MyQLMHelper._datetime_to_timestamp(job_info.ending_date))
        return status
