"""Bounded, deterministic runtime captures, independent of Qt."""
from collections import deque
from dataclasses import asdict, dataclass
import csv
import io
import json
import math
import re


@dataclass(frozen=True)
class PlotSample:
    time_ns: int
    channel: str
    value: float
    segment: int = 0


class SerialPlot:
    NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z', re.ASCII)

    def __init__(self, limit=10000):
        self.limit = limit
        self.port = 0
        self.clear()

    def clear(self):
        self.channels = {}
        self.pending = bytearray()
        self.discard_line = False
        self.gaps = 0
        self.dropped_samples = 0
        self.revision = 0
        self.segment = 0

    def gap(self):
        self.pending.clear()
        self.discard_line = True
        self.gaps += 1
        self.segment += 1
        self.revision += 1

    def line(self, data, time_ns):
        try:
            text = data.decode('ascii').strip()
        except UnicodeError:
            return
        fields = text.split(',')
        if not 1 <= len(fields) <= 8:
            return
        named = ':' in fields[0]
        parsed = []
        for i, field in enumerate(fields):
            if named:
                if field.count(':') != 1:
                    return
                name, number = (s.strip() for s in field.split(':'))
                if not name or len(name) > 64:
                    return
            else:
                name, number = f'ch{i+1}', field.strip()
            if not self.NUMBER.fullmatch(number):
                return
            value = float(number)
            if not math.isfinite(value):
                return
            parsed.append((name, value))
        names = [name for name, _ in parsed]
        if len(set(names)) != len(names) or len(set(names) | self.channels.keys()) > 8:
            return
        for name, value in parsed:
            channel = self.channels.setdefault(name, deque(maxlen=self.limit))
            if len(channel) == self.limit:
                self.dropped_samples += 1
            sample = PlotSample(time_ns, name, value, self.segment)
            channel.append(sample)
        self.revision += 1

    def feed(self, events):
        for e in events:
            if e.kind != 2 or e.source != self.port:
                continue
            if e.value == 10:
                if not self.discard_line:
                    self.line(bytes(self.pending), e.time_ns)
                self.pending.clear()
                self.discard_line = False
            elif not self.discard_line:
                self.pending.append(e.value)
                if len(self.pending) > 4096:
                    self.gap()

    def snapshot(self):
        return {name: tuple(values) for name, values in self.channels.items()}

    def csv(self):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(('time_ns', 'channel', 'value', 'segment'))
        samples = sorted((p for values in self.channels.values() for p in values), key=lambda p: (p.time_ns, p.channel))
        writer.writerows((p.time_ns, p.channel, p.value, p.segment) for p in samples)
        return out.getvalue()


@dataclass(frozen=True)
class BusExchange:
    id: int
    time_ns: int
    bus: str
    controller: int
    target: int | None
    device: str
    tx: int
    rx: int
    flags: int
    acknowledged: bool
    fault: str = ''

    @property
    def status(self):
        return 'FAULT' if self.fault else 'ACK' if self.acknowledged else 'NACK'


class BusCapture:
    def __init__(self, limit=10000):
        self.limit = limit
        self.enabled = True
        self.clear()

    def clear(self):
        self.records = deque(maxlen=self.limit)
        self.dropped = 0
        self.revision = 0

    def append(self, record):
        if self.enabled:
            if len(self.records) == self.limit:
                self.dropped += 1
            self.records.append(record)
            self.revision += 1

    def export(self, format='json'):
        records = [asdict(r) for r in self.records]
        if format == 'json':
            return json.dumps({'dropped': self.dropped, 'records': records}, indent=2) + '\n'
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(BusExchange.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(records)
        return out.getvalue()
