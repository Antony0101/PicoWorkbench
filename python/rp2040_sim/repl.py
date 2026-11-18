"""Bounded UART raw-REPL upload, advanced by the normal simulation worker."""


class PythonTransfer:
    def __init__(self, source: str, ready=False):
        self.source = source.encode('utf-8')
        if not self.source or len(self.source) > 32768 or b'\x00' in self.source or b'\x04' in self.source:
            raise ValueError('Python source must contain 1–32768 UTF-8 bytes without NUL/EOT')
        self.phase = 'enter' if ready else 'boot'
        self.pending = bytearray(b'\x03\x03\x02\x01' if ready else b'')
        self.received = bytearray()
        self.last_byte_ns = 0
        self.scheduled = 0
        self.stderr = b''
        self.eot_count = 0
        self.error_text = bytearray()
        self.done = False

    def consume(self, simulation, events):
        for event in events:
            if event.kind == 3 and event.source == 30 and self.scheduled:
                # Record actual delivery boundaries, including nanosecond rounding.
                simulation.recording.append({'time_ns': event.time_ns, 'kind': 'uart',
                                             'port': 0, 'hex': bytes([event.value]).hex()})
                self.scheduled -= 1
            if event.kind != 2 or event.source != 0:
                continue
            self.received.append(event.value)
            if self.phase == 'execute':
                if event.value == 4:
                    self.eot_count += 1
                elif self.eot_count == 1 and len(self.error_text) < 4096:
                    self.error_text.append(event.value)
            if self.phase == 'boot' and self.received.endswith(b'>>> '):
                self.pending = bytearray(b'\x03\x03\x02\x01')
                self.phase = 'enter'; self.received.clear()
            elif self.phase == 'enter' and b'raw REPL; CTRL-B to exit' in self.received and self.received.endswith(b'>'):
                self.pending = bytearray(self.source + b'\x04')
                self.phase = 'upload'; self.received.clear()
            elif self.phase == 'execute' and self.eot_count >= 2 and self.received.endswith(b'>'):
                self.stderr = bytes(self.error_text)
                self.pending = bytearray(b'\x02')
                self.phase = 'exit'; self.received.clear()
            elif self.phase == 'exit' and self.received.endswith(b'>>> '):
                self.phase = 'done'; self.done = True
            # Keep prompt/status parsing bounded even for verbose/long programs.
            if len(self.received) > 4096:
                del self.received[:-4096]

    def advance(self, simulation):
        if not self.pending or self.scheduled:
            return
        state = simulation.engine.snapshot()
        period = max(100_000, (1000 * 1_000_000_000 + state.clock_hz - 1) // state.clock_hz)
        now = state.time_ns
        batch = self.pending[:32]
        del self.pending[:32]
        for i, byte in enumerate(batch):
            simulation.engine.uart(bytes([byte]), time_ns=now + i * period)
        self.scheduled = len(batch)
        self.last_byte_ns = now + (len(batch) - 1) * period
        if self.phase == 'upload' and not self.pending:
            self.phase = 'execute'
