PYTHON ?= .venv/bin/python
WORKSPACE ?= .lab
.PHONY: check check-core check-api check-eval check-flow check-negative init flow serve evaluate baseline candidate measure verify package
check: check-core check-api check-eval
check-core:
	$(PYTHON) -m compileall -q release.py publication.py lab.py backends.py service_state.py service.py api.py flow.py gate_bridge.py scripts tests tests_integration
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m unittest discover -s tests_integration -v
check-api:
	SERVICE_ENTRY=api.py $(PYTHON) -m unittest discover -s tests_integration -p test_process.py -v
check-eval:
	PYTHONPATH=../hw67 $(PYTHON) -m unittest discover -s ../hw67/tests -p test_eval.py -v
check-flow:
	$(PYTHON) scripts/check_flow.py
check-negative:
	$(PYTHON) scripts/mutation_check.py
init:
	$(PYTHON) lab.py --workspace $(WORKSPACE) init
flow:
	$(PYTHON) flow.py --workspace $(WORKSPACE)
serve:
	$(PYTHON) api.py --workspace .real --backend transformers --port 8091
baseline:
	$(PYTHON) flow.py --workspace .real --manifest manifest-baseline.json --report report-baseline.json
candidate:
	$(PYTHON) flow.py --workspace .real --manifest manifest-candidate.json --report report-candidate.json
evaluate:
	$(PYTHON) scripts/real_evaluation.py
measure:
	$(PYTHON) scripts/measure_api.py --workspace .real
verify:
	$(PYTHON) scripts/verify_evidence.py
package:
	$(PYTHON) scripts/package_submission.py
