# Validate the persisted route in a fresh OpenROAD process.
read_db routed.odb
set block [ord::get_db_block]
if {[$block getName] ne "simple_add"} { error "Wrong block after database reload" }
set flop_count 0
set clock_buffer_count 0
set routed_net_count 0
foreach inst [$block getInsts] {
    if {[$inst getPlacementStatus] ni {PLACED FIRM LOCKED}} {
        error "Unplaced instance after reload: [$inst getName]"
    }
    if {[string match DFF_X* [[$inst getMaster] getName]]} { incr flop_count }
    if {[string match clkbuf_* [$inst getName]]} { incr clock_buffer_count }
}
if {$flop_count != 96} { error "Expected 96 XLS pipeline flops, found $flop_count" }
if {$clock_buffer_count == 0} { error "The clock tree is missing" }
check_placement -verbose
if {![design_is_routed -verbose]} { error "Missing signal/clock routes after reload" }
foreach net [$block getNets] {
    if {[$net isSpecial]} { continue }
    set wire [$net getWire]
    if {$wire ne "NULL"} {
        incr routed_net_count
    }
}
if {$routed_net_count == 0} { error "The database contains no routed wires" }
if {![file exists route-drc.rpt] || [file size route-drc.rpt] != 0} {
    error "The detailed-routing DRC report is missing or contains violations"
}
set report [open routing-summary.json w]
puts $report "\{"
puts $report "  \"design\": \"simple_add\","
puts $report "  \"cells\": [llength [$block getInsts]],"
puts $report "  \"pipeline_flops\": $flop_count,"
puts $report "  \"clock_buffers\": $clock_buffer_count,"
puts $report "  \"routed_nets\": $routed_net_count,"
puts $report "  \"detailed_route_drcs\": 0,"
puts $report "  \"scope\": \"signal and clock routing; no power grid\""
puts $report "\}"
close $report
puts OPENROAD_ROUTED_RELOAD_PASS
