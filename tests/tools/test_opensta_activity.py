"""Verify the installed analyzer retains measured cell and gated-clock activity."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "tools/nangate45/NangateOpenCellLibrary_typical.lib"
ANALYZER = Path(os.environ.get("OPENSTA_TEST_BINARY", ROOT / "bin/sta")).resolve()

NETLIST = '''module power_probe(input clk, en, d, output q, z);
  wire gated_clk;
  CLKGATE_X1 gate (.CK(clk), .E(en), .GCK(gated_clk));
  DFF_X1 flop (.CK(gated_clk), .D(d), .Q(q));
  BUF_X1 probe (.A(d), .Z(z));
endmodule
'''


class OpenSTAActivityTest(unittest.TestCase):
    def analyze(self, clock_activity):
        scratch = ROOT / "build/tests"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="opensta-activity-", dir=scratch) as temporary:
            folder = Path(temporary).resolve()
            netlist = folder / "probe.v"
            script = folder / "probe.tcl"
            netlist.write_text(NETLIST)
            script.write_text(f'''read_liberty {{{LIBRARY}}}
read_verilog {{{netlist}}}
link_design power_probe
create_clock -name clk -period 2 [get_ports clk]
set_input_transition 0.05 [all_inputs]
set_clock_transition 0.05 [all_clocks]
set_load 5 [all_outputs]
set_power_activity -input_ports [get_ports {{en d}}] -activity 0.1 -duty 0.5
set_power_activity -pins [get_pins probe/Z] -activity 0.75 -duty 0.6
set_power_activity -pins [get_pins gate/GCK] -activity {clock_activity} -duty {clock_activity / 4}
report_power -digits 9
foreach pin {{probe/Z gate/GCK}} {{
  puts "OBSERVED $pin [get_property [get_pins $pin] activity]"
}}
''')
            result = subprocess.run([str(ANALYZER), "-no_init", "-no_splash", "-exit", str(script)],
                                    cwd=ROOT, text=True, capture_output=True, timeout=60)
            log = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, log)
            self.assertNotRegex(log, r"(?m)^Error:")
            self.assertRegex(log, r"(?m)^Total\s+\S+\s+\S+\s+\S+\s+\S+")
            observed = {}
            for pin, density, duty, origin in re.findall(
                    r"(?m)^OBSERVED (\S+) (\S+) (\S+) (\S+)$", log):
                observed[pin] = (float(density) * 2e-9, float(duty), origin)
            return observed

    def test_measured_cell_pin_survives_power_propagation(self):
        activity, duty, origin = self.analyze(0.5)["probe/Z"]
        self.assertAlmostEqual(activity, 0.75, places=5)
        self.assertAlmostEqual(duty, 0.6, places=5)
        self.assertEqual(origin, "user")

    def test_gated_clock_preserves_full_half_sparse_and_idle_activity(self):
        for expected in (2.0, 1.0, 0.5, 0.0):
            with self.subTest(transitions_per_cycle=expected):
                activity, duty, origin = self.analyze(expected)["gate/GCK"]
                self.assertAlmostEqual(activity, expected, places=5)
                self.assertAlmostEqual(duty, expected / 4, places=5)
                self.assertEqual(origin, "user")


if __name__ == "__main__":
    unittest.main()
