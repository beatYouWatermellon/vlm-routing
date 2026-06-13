# Congestion map extraction script
# Usage: openroad -exit extract_congestion.tcl

read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

global_route \
    -congestion_report_file $::env(CONGESTION_RPT) \
    -congestion_iterations 1 \
    -allow_congestion
