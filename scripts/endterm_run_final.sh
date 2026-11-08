#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
python -B scripts/endterm_experiments.py --configs endterm --reps 3 --no-samples
python -B scripts/endterm_experiments.py --only E7 --configs baseline --reps 3 --no-samples
python -B scripts/endterm_experiments.py --only E3,E4 --configs "" --variants no-netpol,fresh-cni --reps 3 --no-samples
python -B scripts/endterm_experiments.py --only E1 --reps 0 --configs ""
