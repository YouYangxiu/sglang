"""
Copyright 2023-2024 SGLang Team
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

"""
A controller that manages multiple data parallel workers.
Each data parallel worker can manage multiple tensor parallel workers.
"""

import dataclasses
import logging
import multiprocessing
import multiprocessing.shared_memory
import os
from enum import Enum, auto
import random
import numpy as np
import zmq

from sglang.srt.managers.controller_single import (
    start_controller_process as start_controller_process_single,
)
from sglang.srt.managers.io_struct import (
    AbortReq,
    ControllerInfo,
    FlushCacheReq,
    TokenizedGenerateReqInput,
)
from sglang.srt.server_args import PortArgs, ServerArgs
from sglang.srt.utils import kill_parent_process
from sglang.utils import get_cache_info, get_exception_traceback

logger = logging.getLogger(__name__)


class LoadBalanceMethod(Enum):
    """Load balance method."""

    ROUND_ROBIN = auto()
    SHORTEST_QUEUE = auto()
    RESOURCES_AWARE = auto()
    POWER_OF_2_CHOICE = auto()

    @classmethod
    def from_str(cls, method: str):
        method = method.upper()
        try:
            return cls[method]
        except KeyError as exc:
            raise ValueError(f"Invalid load balance method: {method}") from exc


@dataclasses.dataclass
class WorkerHandle:
    """Store the handle of a data parallel worker."""

    proc: multiprocessing.Process
    queue: multiprocessing.Queue


# class FlexScheduler:
#     """A scheduler which dispatch """


class ControllerMultiFlex:
    """A controller that manages multiple data parallel workers."""

    def __init__(
        self,
        server_args: ServerArgs,
        port_args: PortArgs,
        model_overide_args,
    ):
        # Parse args
        self.server_args = server_args
        self.port_args = port_args
        self.model_overide_args = model_overide_args
        self.load_balance_method = LoadBalanceMethod.from_str(
            server_args.load_balance_method
        )

        # Init communication
        context = zmq.Context()
        self.recv_from_tokenizer = context.socket(zmq.PULL)
        self.recv_from_tokenizer.bind(f"tcp://127.0.0.1:{port_args.controller_port}")

        # Dispatch method
        self.round_robin_counter = 0
        dispatch_lookup = {
            LoadBalanceMethod.ROUND_ROBIN: self.round_robin_scheduler,
            LoadBalanceMethod.SHORTEST_QUEUE: self.shortest_queue_scheduler,
            LoadBalanceMethod.RESOURCES_AWARE: self.resources_aware_scheduler,
            LoadBalanceMethod.POWER_OF_2_CHOICE: self.power_of_2_choice,
        }
        self.dispatching = dispatch_lookup[self.load_balance_method]

        # Start data parallel workers
        self.workers = []
        self.controller_info = ControllerInfo(server_args, model_overide_args)
        for i in range(server_args.dp_size):
            self.start_dp_worker(i)

    def start_dp_worker(self, dp_worker_id: int):
        tp_size = self.server_args.tp_size

        pipe_controller_reader, pipe_controller_writer = multiprocessing.Pipe(
            duplex=False
        )

        gpu_ids = list(range(dp_worker_id * tp_size, (dp_worker_id + 1) * tp_size))
        queue = multiprocessing.Queue()
        proc = multiprocessing.Process(
            target=start_controller_process_single,
            args=(
                self.server_args,
                self.port_args,
                pipe_controller_writer,
                self.model_overide_args,
                True,
                gpu_ids,
                dp_worker_id,
                queue,
                self.controller_info,
            ),
        )
        proc.start()

        controller_init_state = pipe_controller_reader.recv()
        if controller_init_state != "init ok":
            raise RuntimeError(
                f"Initialization failed. controller_init_state: {controller_init_state}"
            )
        self.workers.append(
            WorkerHandle(
                proc=proc,
                queue=queue,
            )
        )

    def resources_aware_scheduler(self, input_requests):
        if len(input_requests) == 0:
            return
        remained_token = [k.value for k in self.controller_info.waiting_prefill_compute]
        available_mem = [k.value for k in self.controller_info.available_kv_cache]
        num_reqs_waiting = [k.value for k in self.controller_info.waiting_reqs]
        num_reqs_running = [k.value if k.value != 0 else 1 for k in self.controller_info.running_reqs]
        with open('three_list.txt', 'a') as file:  # 'a' 模式表示追加到文件末尾
            file.write(f"available_mem={available_mem}\num_reqs_waiting={num_reqs_waiting}\nnum_reqs_running={num_reqs_running}\n")
        
        waiting_main = True
        if max(num_reqs_waiting) == 0: # 没有排队，按照之前的策略调度
            waiting_main = False
        for r in input_requests:
            if waiting_main: # 说明全部都是0
                # 选择最少的排队的gpu
                index = num_reqs_waiting.index(min(num_reqs_waiting))
                self.workers[index].queue.put(r)
                num_reqs_waiting[index] += 1
            else:
                flag = [a / b for a, b in zip(available_mem, num_reqs_running)]
                index = flag.index(max(flag))
                self.workers[index].queue.put(r)
                available_mem[index] -= len(r.input_ids)
                num_reqs_running[index] += 1
                
                

    def power_of_2_choice(self, input_requests):
        if len(input_requests) == 0:
            return
        num_reqs_waiting = [k.value for k in self.controller_info.waiting_reqs]
        num_reqs_running = [k.value for k in self.controller_info.running_reqs]
        available_mem = [k.value for k in self.controller_info.available_kv_cache]
        
        instances_len = len(self.workers)
        

        # 比较两个worker的指标
        def compare_metrics(ins1, ins2):
            if num_reqs_waiting[ins1] != num_reqs_waiting[ins2]:
                return ins1 if num_reqs_waiting[ins1] < num_reqs_waiting[ins2] else ins2
            if num_reqs_running[ins1] != num_reqs_running[ins2]:
                return ins1 if num_reqs_running[ins1] < num_reqs_running[ins2] else ins2
            if available_mem[ins1] != available_mem[ins2]:
                return ins1 if available_mem[ins1] > available_mem[ins2] else ins2
            return ins1

        for r in input_requests:
            # 随机选两个worker
            ins1, ins2 = random.sample(range(0, instances_len), 2)
            ins_end = compare_metrics(ins1, ins2)
            self.workers[ins_end].queue.put(r)
            # available_mem[ins_end] -= len(r.input_ids)
            # num_reqs_running[ins_end] += 1
            # num_reqs_waiting[ins_end] += 1
            
            
    def round_robin_scheduler(self, input_requests):
        for r in input_requests:
            self.workers[self.round_robin_counter].queue.put(r)
            self.round_robin_counter = (self.round_robin_counter + 1) % len(
                self.workers
            )

    def shortest_queue_scheduler(self, input_requests):
        for r in input_requests:
            queue_sizes = [worker.queue.qsize() for worker in self.workers]
            wid = np.argmin(queue_sizes)
            self.workers[wid].queue.put(r)

    def loop_for_forward(self):
        while True:
            recv_reqs = self.recv_requests()
            self.dispatching(recv_reqs)

    def recv_requests(self):
        recv_reqs = []

        while True:
            try:
                recv_req = self.recv_from_tokenizer.recv_pyobj(zmq.NOBLOCK)
            except zmq.ZMQError:
                break

            if isinstance(recv_req, FlushCacheReq):
                # TODO(lsyin): apply more specific flushCacheReq
                for worker in self.workers:
                    worker.queue.put(recv_req)
            elif isinstance(recv_req, AbortReq):
                in_queue = False
                for i, req in enumerate(recv_reqs):
                    if req.rid == recv_req.rid:
                        recv_reqs[i] = recv_req
                        in_queue = True
                        break
                if not in_queue:
                    # Send abort req to all TP groups
                    for worker in self.workers:
                        worker.queue.put(recv_req)
            elif isinstance(recv_req, TokenizedGenerateReqInput):
                recv_reqs.append(recv_req)
            else:
                logger.error(f"Invalid object: {recv_req}")

        return recv_reqs


def start_controller_process(
    server_args: ServerArgs,
    port_args: PortArgs,
    pipe_writer,
    model_overide_args: dict,
):
    """Start a controller process."""

    logging.basicConfig(
        level=getattr(logging, server_args.log_level.upper()),
        format="%(message)s",
    )

    try:
        controller = ControllerMultiFlex(server_args, port_args, model_overide_args)
    except Exception:
        pipe_writer.send(get_exception_traceback())
        raise

    pipe_writer.send("init ok")

    try:
        controller.loop_for_forward()
    except Exception:
        logger.error("Exception in ControllerMultiFlex:\n" + get_exception_traceback())
    finally:
        for w in controller.workers:
            os.kill(w.proc.pid, 9)
        kill_parent_process()
