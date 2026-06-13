#!/bin/bash
# scripts/batch_run.sh
# Batch run the VLM routing agent across multiple ISPD 2018 benchmarks.

DATA_DIR="${1:-./data/ispd2018}"
WORK_DIR="${2:-./outputs/agent_runs}"
MAX_ITER="${3:-15}"

BENCHMARKS="ispd18_test1 ispd18_test2 ispd18_test3 ispd18_test4 ispd18_test5"

for bench in $BENCHMARKS; do
    echo "========================================="
    echo "Processing: $bench"
    echo "========================================="

    python scripts/run_agent.py \
        --benchmark "$bench" \
        --data-dir "$DATA_DIR" \
        --work-dir "$WORK_DIR" \
        --max-iter "$MAX_ITER" \
        --scoring-mode simplified

    echo "Completed: $bench"
    echo ""
done

echo "All benchmarks completed!"
echo "Results in: $WORK_DIR"
