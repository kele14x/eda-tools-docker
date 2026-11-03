# Load the routed DEF and its selected platform libraries for interactive inspection.
set smoke_platform [expr {[info exists ::env(EDA_SMOKE_PLATFORM)] ? $::env(EDA_SMOKE_PLATFORM) : "asap7"}]
source [file join [file dirname [info script]] platforms "$smoke_platform.tcl"]
set output_dir [expr {[info exists ::env(EDA_SMOKE_OUTPUT_DIR)] ? $::env(EDA_SMOKE_OUTPUT_DIR) : "build/simple_add"}]
read_lef $tech_lef
read_lef $cells_lef
foreach lib_file $liberty {
    read_liberty $lib_file
}
read_def [file join $output_dir routed.def]
read_sdc [file join $output_dir routed.sdc]
