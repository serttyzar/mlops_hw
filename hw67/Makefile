.PHONY: install up down sweep promote predict check check-storage
install:
	uv sync --frozen
up:
	docker compose -p mlops-hw6 up --build -d --wait
down:
	docker compose -p mlops-hw6 down
sweep:
	uv run python -m src.sweep
promote:
	uv run python -m evaluation.cli promote
predict:
	uv run python -m src.predict --uri models:/amasum-qwen-adapter@champion --prompt "Product: Mug. Reviews: Keeps coffee hot but the lid leaks. Summarize."
check:
	bash tests/check.sh
check-storage:
	uv run python tests/check_storage.py

.PHONY: report candidate baseline-init evaluate eval check-eval check-negative check-all baseline-update predict-candidate
report:
	uv run python -m src.report
candidate:
	uv run python -m evaluation.cli candidate
baseline-init:
	uv run python -m evaluation.cli baseline-init
evaluate:
	uv run python -m evaluation.cli evaluate
eval:
	uv run python -m evaluation.cli gate
check-eval:
	uv run python -m unittest discover -s tests -p 'test_*.py' -v
check-negative:
	uv run python -m evaluation.cli negative
check-all: check check-eval
baseline-update:
	uv run python -m evaluation.cli baseline-update
predict-candidate:
	uv run python -m src.predict --uri models:/amasum-qwen-adapter@candidate --prompt "Product: Mug. Reviews: Keeps coffee hot but the lid leaks. Summarize."

.PHONY: prepare-eval verify-lineage report-all verify-evidence package
prepare-eval:
	uv run python -m scripts.prepare_eval
verify-lineage:
	uv run python scripts/verify_lineage.py
report-all:
	uv run python -m scripts.build_report
verify-evidence:
	uv run python -m scripts.verify_evidence
package:
	uv run python -m scripts.package_submission
