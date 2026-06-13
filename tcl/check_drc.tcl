# DRC check script
# Usage: openroad -exit check_drc.tcl

read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

detailed_route \
    -output_drc $::env(OUTPUT_DRC) \
    -verbose 0
