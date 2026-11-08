#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
python -B scripts/endterm_experiments.py --only E4,E5,E6,E7 --reps 3 --no-samples
python -B scripts/endterm_experiments.py --only E3 --configs "" --ablation --reps 3 --no-samples
python -B scripts/endterm_experiments.py --only E1 --reps 0 --configs ""
