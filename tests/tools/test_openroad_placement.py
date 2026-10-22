"""Check real OpenROAD placement and database reload with pinned Nangate45 cells."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPENROAD = Path(os.environ.get("OPENROAD_TEST_BINARY", ROOT / "bin/openroad")).resolve()
PLATFORM = ROOT / "tools/nangate45-physical"


class OpenROADPlacementTest(unittest.TestCase):
    def test_placement_and_database_reload(self):
        scratch = ROOT / "build/tests"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="openroad placement ", dir=scratch) as temporary:
            folder = Path(temporary).resolve()
            cells = []
            for bit in range(16):
                cells.append(f"XOR2_X1 xor_{bit} (.A(d[{bit}]), .B(q[{bit}]), .Z(n[{bit}]));")
                cells.append(f"DFF_X1 reg_{bit} (.CK(clk), .D(n[{bit}]), .Q(q[{bit}]));")
            (folder / "probe.v").write_text(
                "module placement_probe(input clk, input [15:0] d, output [15:0] q);\n"
                "wire [15:0] n;\n" + "\n".join(cells) + "\nendmodule\n")
            preamble = f'''set tech_lef {{{PLATFORM / "lef/NangateOpenCellLibrary.tech.lef"}}}
set cells_lef {{{PLATFORM / "lef/NangateOpenCellLibrary.macro.mod.lef"}}}
set liberty {{{ROOT / "tools/nangate45/NangateOpenCellLibrary_typical.lib"}}}
set input_netlist probe.v
set design_top placement_probe
'''
            (folder / "place.tcl").write_text(
                preamble + (ROOT / "tests/smoke/openroad.tcl").read_text())

            def run(script):
                environment = os.environ.copy()
                # Repository libraries must also be visible through the Docker launcher.
                environment["EDA_PROJECT_ROOT"] = str(ROOT)
                result = subprocess.run(
                    [str(OPENROAD), "-no_init", "-exit", "-threads", "2", script],
                    cwd=folder, env=environment, capture_output=True, text=True, timeout=180)
                log = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, log)
                self.assertNotRegex(log, r"(?m)^(?:Error:|\[ERROR)")
                return log

            log = run("place.tcl")
            match = re.search(r"(?m)^OPENROAD_PLACEMENT_PASS (\d+)$", log)
            self.assertIsNotNone(match, log)
            self.assertEqual(int(match[1]), 32, log)
            self.assertGreater((folder / "placed.odb").stat().st_size, 0)
            self.assertIn("END DESIGN", (folder / "placed.def").read_text())
            (folder / "reload.tcl").write_text('''read_db placed.odb
set block [ord::get_db_block]
if {[$block getName] ne "placement_probe"} { error "Wrong block after reload" }
if {[llength [$block getInsts]] != 32} { error "Cell count changed after reload" }
check_placement -verbose
puts OPENROAD_RELOAD_PASS
''')
            self.assertIn("OPENROAD_RELOAD_PASS", run("reload.tcl"))


if __name__ == "__main__":
    unittest.main()
