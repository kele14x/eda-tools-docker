# ASAP7 TT/RVT platform settings shared by the smoke flow.
set tech_lef $::env(ASAP7_TECH_LEF)
set cells_lef $::env(ASAP7_CELLS_LEF)
set liberty $::env(ASAP7_LIBERTIES)
set rc_file $::env(ASAP7_RC)
set site $::env(ASAP7_SITE)
set pin_h M4
set pin_v M5
set signal_layers M2-M7
set clock_layers M4-M7
set route_adjustment 0.25
set clock_root $::env(ASAP7_CLOCK_BUFFER)
set clock_buffers $::env(ASAP7_CLOCK_BUFFERS)
set make_tracks_script $::env(ASAP7_TRACKS)
set tapcell_script $::env(ASAP7_TAPCELL)
set tapcell_master "TAPCELL_ASAP7_75t_R"
set tie_pins [list \
    "$::env(ASAP7_TIEHI_CELL)/$::env(ASAP7_TIEHI_PORT)" \
    "$::env(ASAP7_TIELO_CELL)/$::env(ASAP7_TIELO_PORT)"]
set flop_pattern "DFF*_ASAP7_75t_R"
set platform_label "ASAP7 TT RVT (7 nm predictive)"
