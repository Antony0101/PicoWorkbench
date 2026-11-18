import json
from pathlib import Path

import pytest

from rp2040_sim.project import Component, Project


def test_project_roundtrip_and_relative_path_rebase(tmp_path):
    project = Project.demo()
    image = tmp_path / "original" / "firmware.elf"
    image.parent.mkdir()
    image.write_bytes(b"test")
    project.firmware = str(image)
    destination = tmp_path / "projects"
    destination.mkdir()
    project.save(destination / "circuit.json")
    saved = Project.load(destination / "circuit.json")
    assert saved.resolve(saved.firmware).resolve() == image
    assert saved.groups() == project.groups()
    new_dir = tmp_path / "another"
    new_dir.mkdir()
    saved.save(new_dir / "circuit.json")
    assert saved.resolve(saved.firmware).resolve() == image


def test_graph_uses_ids_not_geometry():
    project = Project.demo()
    before = project.groups()
    project.components[0].x += 200
    project.components[0].y -= 200
    assert project.groups() == before


def test_project_preserves_uncompiled_editor_source(tmp_path):
    project = Project.demo()
    project.source_text = 'void reset_handler(void) { /* unsaved edits */ }\n'
    path = tmp_path / "circuit.json"
    project.save(path)
    assert Project.load(path).source_text == project.source_text


@pytest.mark.parametrize("edit", [
    lambda p: p.components.append(Component("led1", "led")),
    lambda p: p.wires.append(("pico:GP30", "led1:signal")),
    lambda p: p.wires.append(("led1:signal", "pico:GP25")),
    lambda p: setattr(p.components[0], "kind", "not-a-component"),
    lambda p: setattr(p.components[0], "x", float("nan")),
    lambda p: setattr(p, "clock_hz", -1),
])
def test_invalid_circuits_fail_validation(edit):
    project = Project.demo()
    edit(project)
    with pytest.raises(ValueError):
        project.validate()


def test_schema_rejection(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"schema": 999, "board": "pico"}))
    with pytest.raises(ValueError, match="schema"):
        Project.load(path)
