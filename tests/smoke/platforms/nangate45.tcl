# Nangate45 platform settings shared by the smoke flow.
set tech_lef $::env(NANGATE45_TECH_LEF)
set cells_lef $::env(NANGATE45_CELLS_LEF)
set liberty $::env(NANGATE45_LIBERTY)
set rc_file $::env(NANGATE45_RC)
set site FreePDK45_38x28_10R_NP_162NW_34O
set pin_h metal3
set pin_v metal2
set signal_layers metal2-metal6
set clock_layers metal2-metal6
set route_adjustment 0.3
set clock_root BUF_X4
set clock_buffers {BUF_X2 BUF_X4 BUF_X8}
set make_tracks_script ""
set tapcell_script ""
set tapcell_master ""
set tie_pins {LOGIC0_X1/Z LOGIC1_X1/Z}
set flop_pattern "DFF_X*"
set platform_label "Nangate45 typical"
