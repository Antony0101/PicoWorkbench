PYTHON ?= .venv/bin/python

.PHONY: install test native gui demo sdk-demo micropython-demo clean

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install -e '.[gui,dev]'

test:
	$(PYTHON) -m pytest -q

native:
	$(PYTHON) -m cmake -S . -B build/cmake -DPython3_EXECUTABLE=$(abspath $(PYTHON))
	$(PYTHON) -m cmake --build build/cmake
	$(PYTHON) -m cmake --build build/cmake --target test

gui:
	$(PYTHON) -m rp2040_sim gui

demo:
	$(PYTHON) tools/prepare_demo.py

sdk-demo:
	$(PYTHON) tools/prepare_sdk.py

micropython-demo:
	$(PYTHON) tools/prepare_micropython.py

clean:
	$(PYTHON) -c "import shutil; shutil.rmtree('build', ignore_errors=True)"
