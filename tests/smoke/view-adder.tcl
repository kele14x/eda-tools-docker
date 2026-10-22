# Load the routed DEF and its Nangate45 libraries for interactive inspection.
read_lef $::env(NANGATE45_TECH_LEF)
read_lef $::env(NANGATE45_CELLS_LEF)
read_liberty $::env(NANGATE45_LIBERTY)
read_def build/simple_add/routed.def
read_sdc build/simple_add/routed.sdc
