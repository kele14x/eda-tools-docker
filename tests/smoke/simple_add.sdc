# A relaxed constraint for testing the toolchain, rather than timing closure.
create_clock -name clk -period 5 [get_ports clk]
set_clock_transition 0.05 [all_clocks]
set_clock_uncertainty 0.05 [all_clocks]
set_input_delay 0.2 -clock clk [get_ports {x* y*}]
set_input_transition 0.05 [get_ports {x* y*}]
set_output_delay 0.2 -clock clk [all_outputs]
set_load 5 [all_outputs]
