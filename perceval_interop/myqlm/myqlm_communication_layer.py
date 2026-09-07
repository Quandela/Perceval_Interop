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

import time
import uuid
from typing import TypeAlias

from perceval import CommunicationLayer, PlatformSpecs, get_logger, PayloadUpdater, ExecutionStatus, \
    RunningStatus, CommandFactory, Command
from perceval.serialization import OutputArchive, Serialization, InputArchive
from perceval.utils.constants import KEY_JOB_CONTEXT, KEY_RESULT_MAPPING, KEY_MAPPING_PARAMETERS, KEY_RESULTS_LIST, \
    KEY_ITERATION, KEY_RESULTS, KEY_GLOBAL_PERF, KEY_PHYSICAL_PERF, KEY_LOGICAL_PERF
from perceval.utils.logging import channel
from qat.core.qpu import RemoteQPU
from qat.qlmaas.result import AsyncResult
from requests import HTTPError

from .myqlm_helper import MyQLMHelper
from .qpu_handler_2 import QuandelaQPUHandler


RemoteId: TypeAlias = str | AsyncResult  # uuid if Computer answers Results, for local storage


class MyQLMCommunicationLayer(CommunicationLayer):

    def __init__(self, remote_qpu: RemoteQPU | QuandelaQPUHandler):
        self._qpu = remote_qpu

        self._specs = PlatformSpecs()
        self._status: str = ""
        self._perfs: dict[str, str] = {}
        self._progress = 0
        self._name = ""
        self._available_jobs = 1

        self._results_cache = {}  # TODO: store this in a file instead of instance?

        self.fetch_data()

    @property
    def name(self) -> str:
        return self._name

    def fetch_data(self):
        # MyQLM specific: the same method gives everything
        try:
            all_specs = self._qpu.get_specs()
        except Exception as e:
            if not len(self._specs):  # throw only the first time
                raise HTTPError(f"Error while fetching platform details: {e}") from None
            else:
                get_logger().warn(f"Error while fetching platform details: {e}")
                return

        self._status = MyQLMHelper.retrieve_status(all_specs)
        self._specs = PlatformSpecs(MyQLMHelper.retrieve_specs(all_specs))
        self._specs["type"] = MyQLMHelper.retrieve_type(all_specs)
        self._perfs.update(MyQLMHelper.retrieve_perf(all_specs))
        self._name = MyQLMHelper.retrieve_name(all_specs)  # Will remain empty if the target is not up-to-date

        # TODO + Can we do something from this ?
        # self._job_in_queue = MyQLMHelper.retrieve_job_in_queue(all_specs)
        self._available_jobs = MyQLMHelper.retrieve_availability(all_specs)
        self._progress = MyQLMHelper.retrieve_progress(all_specs)

    def get_specs(self) -> PlatformSpecs:
        return self._specs

    @staticmethod
    def _serialize(obj):
        archive = OutputArchive()
        Serialization.serialize(obj, archive)
        return archive.to_text(compress=True)  # Use other format ? Compress ?

    def send(self, payload: dict) -> RemoteId:
        if "commands" not in self._specs:  # We have a worker that knows only payloads up to version 1
            # Using self._specs is a bit of a trick, since internally,
            # we only needs the argument to have "available_commands" when downgrading to version 1
            # This might not be true anymore if we introduce a version 3 someday
            payload = PayloadUpdater.update_payload(payload, self._specs, target_payload_version=1)

        # We serialize the payload here, using the new serialization system - Needed to serialize Computation
        payload = self._serialize(payload)

        cloud_data = {"payload": payload}  # inserting the payload into a dict is only useful for backward compatibility
        job = MyQLMHelper.make_job_from_payload(cloud_data)
        intermediate_repr = self._qpu.submit_job(job)

        if isinstance(intermediate_repr, AsyncResult):
            return intermediate_repr

        job_id = str(uuid.uuid4())
        self._results_cache[job_id] = MyQLMHelper.retrieve_results(intermediate_repr)
        return job_id

    def get_results(self, remote_id: RemoteId) -> dict:
        if isinstance(remote_id, str):
            results = self._results_cache.pop(remote_id)
        else:
            results = MyQLMHelper.retrieve_results(remote_id)

        # TODO: remove (deprecated since 1.3, old return format)
        if KEY_JOB_CONTEXT in results and KEY_RESULT_MAPPING in results[KEY_JOB_CONTEXT]:
            path_parts = results[KEY_JOB_CONTEXT][KEY_RESULT_MAPPING]
            get_logger().info(f"Converting job {remote_id} results with {path_parts[1]}", channel.general)
            module = __import__(path_parts[0], fromlist=path_parts[1])
            result_mapping_function = getattr(module, path_parts[1])
            # retrieve delta parameters from the response
            delta_parameters = results[KEY_JOB_CONTEXT].get(KEY_MAPPING_PARAMETERS, {})
            if KEY_RESULTS_LIST in results:
                for res in results[KEY_RESULTS_LIST]:
                    mapping_args = {key: res[KEY_ITERATION].get(key, val) for key, val in delta_parameters.items()}
                    res[KEY_RESULTS] = result_mapping_function(res[KEY_RESULTS], **mapping_args)
            else:
                results[KEY_RESULTS] = result_mapping_function(results[KEY_RESULTS], **delta_parameters)

        if KEY_GLOBAL_PERF not in results and KEY_PHYSICAL_PERF in results and KEY_LOGICAL_PERF in results:
            results[KEY_GLOBAL_PERF] = results[KEY_PHYSICAL_PERF] * results[KEY_LOGICAL_PERF]
        return results

    def get_job_status(self, remote_id: RemoteId, refresh_errors: int = 0) -> ExecutionStatus | None:
        if isinstance(remote_id, str):
            # The job is done. We have to know whether it ended successfully or with an error
            job_status = ExecutionStatus()
            results = self._results_cache[remote_id]
            sub_results = results[KEY_RESULTS]
            if isinstance(sub_results, str):
                if "details" in results:
                    sub_results += f" ({results['details']})"
                # This is most likely an error message
                job_status.stop_run(RunningStatus.ERROR, sub_results)
            else:
                if "details" in results:
                    job_status.stop_run(RunningStatus.SUCCESS, results["details"])
                else:
                    job_status.stop_run(RunningStatus.SUCCESS)

            if "job_duration" in results:
                # Inserting 0 allows the RemoteGetter not to update the init time.
                # This will unfortunately make the completed time wrong
                job_status.update_times(0, 0, results["job_duration"])

            return job_status

        myqlm_info = remote_id.get_info()
        if remote_id.queue() == 0:  # Job is currently being executed
            self.fetch_data()
            progress = self._progress
        else:
            progress = None
        return MyQLMHelper.execution_status_from_myqlm(myqlm_info, progress)

    def get_performances(self) -> dict:
        self.fetch_data()
        return self._perfs

    def get_commands(self) -> list[Command]:
        # TODO: simply return self._specs.commands (adaptation for non-updated platforms)
        commands = list(self._specs.commands)
        add_default = False
        if any(command.name in ("probs", "sample_count", "samples") for command in commands):
            add_default = True

        if add_default:
            if all(command.name != "probs" for command in commands):
                commands.append(CommandFactory.probs)
            if all(command.name != "sample_count" for command in commands):
                commands.append(CommandFactory.sample_count)
            if all(command.name != "samples" for command in commands):
                commands.append(CommandFactory.samples)

        return commands

    def get_remote_status(self) -> str:
        self.fetch_data()
        return self._status

    def cancel(self, remote_id: RemoteId) -> None:
        if isinstance(remote_id, str):
            raise RuntimeError(f"Job is already complete")
        remote_id.cancel(remote_id)

    def get_availability(self) -> int:
        self.fetch_data()
        return self._available_jobs


# Serialization - We need to be able to get the RemoteId, and the remote qpu

# Note: I don't know how to read and store the following optional parameters:
# ssl_cert, ssl_key, check_server_cert
# Let's stick to the basic parameters
def read_qpu(qpu: RemoteQPU, archive: InputArchive, members, version: int):
    RemoteQPU.__init__(qpu, *(archive.create(members[i][1]) for i in range(len(members))))


Serialization.register_class(RemoteQPU,
                             class_serial_members_write=lambda qpu, archive: archive.save_attr(
                                 qpu.connection, ["port", "ip"]),
                             class_serial_members_read=read_qpu,
                             tag="MyQLM_RemoteQPU")


def read_async_result(async_result: AsyncResult, archive: InputArchive, members, version: int):
    # TODO
    pass


Serialization.register_class(AsyncResult,
                             class_serial_members_write=lambda res, archive:
                                archive.save_attr(res.get_info(), ["id"]),
                             class_serial_members_read=read_async_result,
                             tag="MyQLM_AsyncResult")


def read_comm_layer(comm_layer: MyQLMCommunicationLayer, archive: InputArchive, members, version: int):
    objects = {members[i][0]: archive.create(members[i][1]) for i in range(len(members))}
    comm_layer.__init__(objects["_qpu"])  # Automatically calls fetch_data() - no need to store its results
    comm_layer._results_cache = objects["_results_cache"]


Serialization.register_class(MyQLMCommunicationLayer,
                             class_serial_members_write=lambda communication_layer, archive:
                                archive.save_attr(communication_layer, ["_qpu", "_results_cache"]),
                             class_serial_members_read=read_comm_layer)
