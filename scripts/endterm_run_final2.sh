#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
until [ results/endterm/samples/correlated_trace.json -nt results/endterm/logs/chain2.marker ]; do sleep 15; done
python -B scripts/endterm_experiments.py --only E1,E2,E5,E6,E7 --configs "" --variants no-netpol --variants-all --reps 3 --no-samples
