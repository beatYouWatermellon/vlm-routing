# Baseline full routing flow: global route -> detailed route
# Usage: openroad -exit base_route.tcl

read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

if {[info exists ::env(GUIDE_FILE)] && [file exists $::env(GUIDE_FILE)]} {
    read_guides $::env(GUIDE_FILE)
}

set_routing_layers -signal Metal2-Metal5

global_route \
    -guide_file $::env(OUTPUT_DIR)/route.guide \
    -congestion_report_file $::env(OUTPUT_DIR)/congestion.rpt \
    -congestion_iterations 50 \
    -verbose

detailed_route \
    -output_drc $::env(OUTPUT_DIR)/drc.rpt \
    -output_maze $::env(OUTPUT_DIR)/maze.log \
    -verbose 1

report_wire_length -net * -detailed_route \
    -file $::env(OUTPUT_DIR)/wirelength.rpt

write_def $::env(OUTPUT_DEF)
write_db $::env(OUTPUT_DIR)/routed.odb
