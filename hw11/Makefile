PYTHON ?= python3
RESULTS ?= results/cluster
.PHONY: check preflight tools serve setup experiments report verify cleanup
check:
	$(PYTHON) -m compileall -q app scripts tests
	$(PYTHON) -m unittest discover -s tests -v
preflight:
	$(PYTHON) scripts/preflight.py
tools:
	sh scripts/install_tools.sh
serve:
	HW11_WARMUP=2 $(PYTHON) -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
setup:
	$(PYTHON) scripts/cluster.py setup --output $(RESULTS)
experiments:
	$(PYTHON) scripts/cluster.py run --output $(RESULTS)
report:
	MPLCONFIGDIR=$(CURDIR)/.state/matplotlib $(PYTHON) scripts/build_report.py --results $(RESULTS)
verify:
	$(PYTHON) scripts/verify_evidence.py $(RESULTS)
cleanup:
	$(PYTHON) scripts/cluster.py cleanup
