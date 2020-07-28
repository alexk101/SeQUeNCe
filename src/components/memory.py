from math import sqrt, inf
from typing import Any, TYPE_CHECKING

from numpy import random

if TYPE_CHECKING:
    from ..entanglement_management.entanglement_protocol import EntanglementProtocol
    from ..kernel.timeline import Timeline
    from ..topology.node import QuantumRouter

from .photon import Photon
from ..kernel.entity import Entity
from ..kernel.event import Event
from ..kernel.process import Process
from ..utils.encoding import single_atom
from ..utils.quantum_state import QuantumState


# array of atomic ensemble memories
class MemoryArray(Entity):
    def __init__(self, name: str, timeline: "Timeline", num_memories=10,
                 fidelity=0.85, frequency=80e6, efficiency=1, coherence_time=-1, wavelength=500):
        Entity.__init__(self, name, timeline)
        self.memories = []
        self.owner = None

        for i in range(num_memories):
            memory = Memory(self.name + "[%d]" % i, timeline, fidelity, frequency, efficiency, coherence_time,
                            wavelength)
            memory.parents.append(self)
            self.memories.append(memory)

    def __getitem__(self, key):
        return self.memories[key]

    def __len__(self):
        return len(self.memories)

    def init(self):
        for mem in self.memories:
            mem.owner = self.owner

    def pop(self, memory: "Memory"):
        # notify node the expired memory
        self.owner.memory_expire(memory)

    def update_memory_params(self, arg_name: str, value: Any) -> None:
        for memory in self.memories:
            memory.__setattr__(arg_name, value)

    def set_node(self, node: "QuantumRouter") -> None:
        self.owner = node


# single-atom memory
class Memory(Entity):
    def __init__(self, name: str, timeline: "Timeline", fidelity: float, frequency: float,
                 efficiency: float, coherence_time: int, wavelength: int):
        Entity.__init__(self, name, timeline)
        assert 0 <= fidelity <= 1
        assert 0 <= efficiency <= 1

        self.fidelity = 0
        self.raw_fidelity = fidelity
        self.frequency = frequency
        self.efficiency = efficiency
        self.coherence_time = coherence_time  # coherence time in seconds
        self.wavelength = wavelength
        self.qstate = QuantumState()

        self.photon_encoding = single_atom.copy()
        self.photon_encoding["memory"] = self
        # keep track of previous BSM result (for entanglement generation)
        # -1 = no result, 0/1 give detector number
        self.previous_bsm = -1

        # keep track of entanglement
        self.entangled_memory = {'node_id': None, 'memo_id': None}

        # keep track of current memory write (ignore expiration of past states)
        self.expiration_event = None
        self.excited_photon = None

        self.next_excite_time = 0

    def init(self):
        pass

    def excite(self, dst="") -> None:
        # if can't excite yet, do nothing
        if self.timeline.now() < self.next_excite_time:
            return

        state = self.qstate.measure(single_atom["bases"][0])
        # create photon and check if null
        photon = Photon("", wavelength=self.wavelength, location=self,
                        encoding_type=self.photon_encoding)
        if state == 0:
            photon.is_null = True

        if self.frequency > 0:
            period = 1e12 / self.frequency
            self.next_excite_time = self.timeline.now() + period

        # send to direct receiver or node
        if (state == 0) or (random.random_sample() < self.efficiency):
            self.owner.send_qubit(dst, photon)
            self.excited_photon = photon

    def expire(self) -> None:
        if self.excited_photon:
            self.excited_photon.is_null = True
        # pop expiration message
        if self.upper_protocols:
            for protocol in self.upper_protocols:
                protocol.memory_expire(self)
        else:
            self._pop(memory=self)
        self.reset()

    def flip_state(self) -> None:
        # flip coefficients of state (apply x-gate)
        assert len(self.qstate.state) == 2, "qstate length error in memory {}".format(self.name)
        new_state = self.qstate.state
        new_state[0], new_state[1] = new_state[1], new_state[0]
        self.qstate.set_state_single(new_state)

    def reset(self) -> None:
        self.fidelity = 0
        if len(self.qstate.state) > 2:
            self.qstate.measure(single_atom["bases"][0])  # to unentangle
        self.qstate.set_state_single([complex(1), complex(0)])  # set to |0> state
        self.entangled_memory = {'node_id': None, 'memo_id': None}
        if self.expiration_event is not None:
            self.timeline.remove_event(self.expiration_event)
            self.expiration_event = None

    def set_plus(self) -> None:
        self.qstate.set_state_single([complex(1 / sqrt(2)), complex(1 / sqrt(2))])
        self.previous_bsm = -1
        self.entangled_memory = {'node_id': None, 'memo_id': None}

        # schedule expiration
        if self.coherence_time > 0:
            self._schedule_expiration()

    def _schedule_expiration(self) -> None:
        if self.expiration_event is not None:
            self.timeline.remove_event(self.expiration_event)

        decay_time = self.timeline.now() + int(self.coherence_time * 1e12)
        process = Process(self, "expire", [])
        event = Event(decay_time, process)
        self.timeline.schedule(event)

        self.expiration_event = event

    def add_protocol(self, protocol: "EntanglementProtocol") -> None:
        self.upper_protocols.append(protocol)

    def remove_protocol(self, protocol: "EntanglementProtocol") -> None:
        self.upper_protocols.remove(protocol)

    def update_expire_time(self, time: int):
        time = max(time, self.timeline.now())
        if self.expiration_event is None:
            if time >= self.timeline.now():
                process = Process(self, "expire", [])
                event = Event(time, process)
                self.timeline.schedule(event)
        else:
            self.timeline.update_event_time(self.expiration_event, time)

    def get_expire_time(self) -> int:
        return self.expiration_event.time if self.expiration_event else inf