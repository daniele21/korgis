#!/usr/bin/env bash
# Bounded deterministic validation for central Mac CI.
# This is NOT the complete risk-based Korgis CI (matrix, UI, security, release).
set -euo pipefail

python3 scripts/verify_repository.py --skip-specialists
python3 scripts/verify_operations.py
python3 scripts/verify_e2e.py
