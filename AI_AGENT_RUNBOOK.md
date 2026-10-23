# Autonomous AI Agent Runbook: Linux Deployment & Model Training

> **Target Audience**: AI coding assistants (Claude Code, Antigravity, Cursor, Codex) running autonomously on the Linux server.
> **Repository**: [https://github.com/Asmitgupta30/Trade](https://github.com/Asmitgupta30/Trade)
> **Permanent Core Invariant**: ADVISORY ONLY. No order-placement routes exist or may ever be created. `risk_config.yaml` is immutable from code.

---

## 1. Environment Setup & Dependencies

Execute the following commands in the cloned repository root:

```bash
# 1. Ensure Python 3.10+ or 3.12 is available
python3 --version

# 2. Create isolated virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Upgrade pip and install all core requirements
pip install --upgrade pip
pip install -r requirements.txt

# 4. (Optional) If you have a CUDA GPU or want PyTorch sequence modeling:
# pip install torch --index-url https://download.pytorch.org/whl/cu121  # or cpu

# 5. Initialize environment variables
cp .env.example .env
```

---

## 2. Preflight Safety & System Verification

Run the stage-gate integrity test to ensure all security locks, read-only config assertions, and cross-platform primitives pass:

```bash
# Must output: "verify: ALL CHECKS GREEN"
python scripts/verify_system.py

# Run preflight unit tests
python -m pytest tests/test_preflight_linux_ready.py tests/test_audit_fixes_and_strategies.py -v
```

---

## 3. How to Start Model Training (Overnight Run)

Choose one of the two options depending on the objective:

### Option A: Standard Production Ensemble Training (~3-5 minutes)
Trains the production LightGBM + Sequence ensemble on all available history and saves the model artifact to `var/models/ensemble_latest.joblib`:

```bash
# If running in foreground:
python scripts/train_model.py

# If running in background with logging:
nohup python scripts/train_model.py > var/logs/train_model.log 2>&1 &
```

*Note: If `var/cache/features_snapshot.parquet` is absent, `scripts/train_model.py` automatically falls back to `var/cache/features_snapshot_deep.parquet` or builds it automatically from the NSE universe.*

---

### Option B: Full Governed Challenger Retrain with Bayesian HPO (Recommended Overnight)
Runs 5-fold walk-forward cross-validation, 25 Optuna Bayesian hyperparameter trials, out-of-sample Sharpe and drawdown testing, and statistical bootstrap significance checks before safely promoting:

```bash
# Launch background overnight training
nohup python scripts/retrain_challenger.py --hpo-trials 25 > var/logs/challenger_retrain.log 2>&1 &

# View process PID
echo $! > var/status/challenger_pid.txt
```

---

## 4. Monitoring Progress While Running

To monitor the training process while it runs:

```bash
# 1. Follow live training output
tail -f var/logs/challenger_retrain.log

# 2. Inspect real-time process status
cat var/status/system_status.json

# 3. Inspect the model improvement ledger (written upon cycle completion)
cat var/models/history.jsonl
```

---

## 5. Serving the Application & Web UI

Once trained (or using the pre-packaged champion model), start the server:

```bash
# Foreground single process (Stream + FastAPI + APScheduler Check-ins):
python scripts/run_system.py

# Or background daemon:
nohup python scripts/run_system.py > var/logs/server.log 2>&1 &

# The web UI is served at: http://localhost:8000
# Available on all interfaces: http://0.0.0.0:8000
```

---

## 6. Architecture & Directory Reference

```
trade/
├── advisor/           # Rules engine, 1R position sizing & Indian market scanners (VCP, 4% Burst, Hammer, Dividend)
├── backend/           # FastAPI application + APScheduler check-ins (09:00, 12:30, 15:00, 15:45 IST)
├── common/            # Config loader, procinfo cross-platform liveness, time utilities
├── data/              # Immutable bar store, NSE bhavcopy ingestion, data providers
├── features/          # Point-in-time feature extraction pipeline & universe filters
├── frontend/          # Single-page terminal UI (HTML5, modern CSS, vanilla JS)
├── models/            # Hybrid ensemble (LightGBM + LSTM), walk-forward, bootstrap, challenger
├── scripts/           # train_model.py, retrain_challenger.py, build_features_snapshot.py, verify_system.py
├── tests/             # Pytest verification suites
├── var/
│   ├── cache/         # Feature parquet snapshots
│   ├── models/        # ensemble_latest.joblib, history.jsonl
│   └── status/        # Live process status & heartbeats
├── Makefile           # Linux convenience targets: make test, make verify, make train, make serve
└── requirements.txt   # Pinned Python package dependencies
```
