# Convenience targets. Cross-platform Linux / macOS / Windows.

.PHONY: test verify verify-live serve run train retrain data-snapshot

test:
	python -m pytest

verify: test
	python scripts/verify_system.py

verify-live:
	python -m pytest -m live --timeout 300 -v

serve:
	python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000

run:
	python scripts/run_system.py

train:
	python scripts/train_model.py

retrain:
	python scripts/retrain_challenger.py

data-snapshot:
	python scripts/build_features_snapshot.py
