from mpi4py import MPI
from typing import TYPE_CHECKING, List, Any
from time import time

from .timeline import Timeline
from .event import Event
from .quantum_manager_client import QuantumManagerClient


class ParallelTimeline(Timeline):

    def __init__(self, lookahead: int, stop_time=float('inf'), formalism='KET',
                 qm_ip="127.0.0.1", qm_port="6789"):
        super(ParallelTimeline, self).__init__(stop_time, formalism)
        self.id = MPI.COMM_WORLD.Get_rank()
        self.foreign_entities = {}
        self.event_buffer = [[] for _ in range(MPI.COMM_WORLD.Get_size())]
        self.lookahead = lookahead
        self.quantum_manager = QuantumManagerClient(formalism, qm_ip, qm_port)
        self.quantum_manager.set_timeline(self)
        self.show_progress = False

        self.sync_counter = 0
        self.event_counter = 0
        self.exchange_counter = 0
        self.computing_time = 0
        self.communication_time1 = 0
        self.communication_time2 = 0
        self.communication_time3 = 0

    def schedule(self, event: 'Event'):
        if type(event.process.owner) is str \
                and event.process.owner in self.foreign_entities:
            tl_id = self.foreign_entities[event.process.owner]
            self.event_buffer[tl_id].append(event)
            self.schedule_counter += 1
        else:
            super(ParallelTimeline, self).schedule(event)

    def top_time(self) -> float:
        if len(self.events) > 0:
            return self.events.top().time
        else:
            return float('inf')

    def run(self):
        while self.time < self.stop_time:
            tick = time()
            inbox = MPI.COMM_WORLD.alltoall(self.event_buffer)
            self.communication_time2 += time() - tick

            for buff in self.event_buffer:
                buff.clear()

            for events in inbox:
                for event in events:
                    self.exchange_counter += 1
                    self.schedule(event)

            tick = time()
            min_time = MPI.COMM_WORLD.allreduce(self.top_time(),
                                                op=MPI.MIN)
            assert min_time >= self.time
            self.communication_time3 += time() - tick

            if min_time >= self.stop_time:
                break

            self.sync_counter += 1

            sync_time = min(min_time + self.lookahead, self.stop_time)
            self.time = min_time

            tick = time()
            while len(self.events) > 0 and self.events.top().time < sync_time:
                event = self.events.pop()
                if event.is_invalid():
                    continue
                assert self.time <= event.time, "invalid event time for process scheduled on " + str(
                    event.process.owner)
                self.time = event.time
                event.process.run()
                self.event_counter += 1
            self.quantum_manager.flush_message_buffer()
            self.computing_time += time() - tick

    def add_foreign_entity(self, entity_name: str, foreign_id: int):
        self.foreign_entities[entity_name] = foreign_id


class AsyncParallelTimeline(ParallelTimeline):
    def top_time(self):
        return super(AsyncParallelTimeline, self).top_time() + self.lookahead

    def run(self):
        while self.time < self.stop_time:
            tick = time()
            inbox = MPI.COMM_WORLD.alltoall(self.event_buffer)
            self.communication_time2 += time() - tick

            for buff in self.event_buffer:
                buff.clear()

            for events in inbox:
                for event in events:
                    self.exchange_counter += 1
                    self.schedule(event)

            tick = time()
            min_time = MPI.COMM_WORLD.allreduce(self.top_time(),
                                                op=MPI.MIN)
            self.communication_time3 += time() - tick

            if min_time >= self.stop_time:
                break

            self.sync_counter += 1

            sync_time = min(min_time + self.lookahead, self.stop_time)

            tick = time()
            while len(self.events) > 0 and self.events.top().time < sync_time:
                event = self.events.pop()
                if event.is_invalid():
                    continue
                assert self.time <= event.time, "invalid event time for process scheduled on " + str(
                    event.process.owner)
                self.time = event.time
                event.process.run()
                self.event_counter += 1
            self.computing_time += time() - tick
