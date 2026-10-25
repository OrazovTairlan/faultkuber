#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
mkdir -p results/logs
for s in baseline rolling exp1 exp1b exp2 exp3 exp3b gw dr peak; do
  python scripts/run_experiments.py $s 2>&1 | tee results/logs/$s.log
done
