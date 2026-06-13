# Metrics extraction script (wirelength + vias)
# Usage: openroad -exit extract_metrics.tcl

read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

report_wire_length -net * -detailed_route -summary \
    -file $::env(WIRELENGTH_RPT)
