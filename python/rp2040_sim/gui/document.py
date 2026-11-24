"""GUI document identities and circuit transactions; no engine or widget access."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json


def fingerprint(project, source=None, *, circuit=False):
    data = asdict(project)
    if circuit:
        data = {"components": [{k: v for k, v in asdict(c).items() if k not in ("x", "y", "label")}
                                for c in project.components],
                "wires": sorted(sorted(wire) for wire in project.wires),
                "clock": project.clock_hz, "startup": project.startup,
                "bootrom": str(project.resolve(project.bootrom)) if project.bootrom else ""}
    else:
        data.pop("path")
        data["source_text"] = source
        for key in ("firmware", "source", "linker", "bootrom"):
            data[key] = str(project.resolve(getattr(project, key))) if getattr(project, key) else ""
        if project.sdk_project:
            data['sdk_project']['root'] = str(project.resolve(project.sdk_project.root))
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def validate_circuit(project):
    project.validate()
    kinds = {c.id: c.kind for c in project.components}
    for group in project.groups():
        pulls = {kinds.get(endpoint.split(":")[0]) for endpoint in group}
        if "pullup" in pulls and "pulldown" in pulls:
            raise ValueError("Opposing external pulls on the same net")


class CircuitHistory:
    def __init__(self):
        self.entries = []
        self.index = 0

    def commit(self, project, change, title):
        candidate = deepcopy(project)
        change(candidate)
        validate_circuit(candidate)
        before = deepcopy((project.components, project.wires))
        after = deepcopy((candidate.components, candidate.wires))
        if before == after:
            return False
        self.entries[self.index:] = [(title, before, after)]
        self.index += 1
        project.components, project.wires = candidate.components, candidate.wires
        return True

    def undo(self, project):
        if self.index:
            self.index -= 1
            project.components, project.wires = deepcopy(self.entries[self.index][1])

    def redo(self, project):
        if self.index < len(self.entries):
            project.components, project.wires = deepcopy(self.entries[self.index][2])
            self.index += 1
