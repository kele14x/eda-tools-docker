#!/usr/bin/env python3
"""Exercise the packaged tools and Mac launchers from an unrelated project."""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DSLX = '''import std;
pub fn add(x: u32, y: u32) -> u32 { x + y + u32:0 }
#[test]
fn addition_and_stdlib() {
    assert_eq(add(u32:2, u32:3), u32:5);
    assert_eq(add(u32:0xffff_ffff, u32:1), u32:0);
    assert_eq(std::clog2(u32:8), u32:3)
}
#[quickcheck(test_count=100)]
fn commutative(x: u32, y: u32) -> bool { add(x, y) == add(y, x) }
'''

SYSTEMVERILOG = '''module lanes(input logic [7:0] a [0:1], output logic [7:0] b [0:1]);
  assign b[0] = a[1];
  assign b[1] = a[0];
endmodule
module array_probe(input [15:0] data, output ok);
  wire [7:0] a [0:1], b [0:1];
  assign a[0] = data[7:0];
  assign a[1] = data[15:8];
  lanes dut(.a(a), .b(b));
  assign ok = {b[0], b[1]} == data;
endmodule
'''

TIMING = '''read_liberty $::env(NANGATE45_LIBERTY)
read_verilog mapped.v
link_design docker_add
create_clock -name clk -period 5 [get_ports clk]
set_clock_transition 0.05 [all_clocks]
set_load 5 [all_outputs]
report_checks -path_delay max
report_power
puts STA_SMOKE_PASS
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=ROOT / 'docker/bin')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/docker-tools')
    args = parser.parse_args()
    bin_dir = args.bin_dir.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.pop('EDA_PROJECT_ROOT', None)
    image = environment.get('EDA_IMAGE', 'local/eda-tools:v1')
    context = environment.get('EDA_DOCKER_CONTEXT', 'orbstack')
    report = {'image': image, 'context': context, 'bin_dir': str(bin_dir), 'checks': []}

    with tempfile.TemporaryDirectory(prefix='eda docker smoke ') as temporary:
        project = Path(temporary).resolve()
        report['test_project'] = str(project)

        def run(name, command, *, expected=0, stdin=None, destination=None, cwd=project, env=None):
            active_env = environment | (env or {})
            result = subprocess.run(list(map(str, command)), cwd=cwd, env=active_env,
                                    input=stdin, capture_output=True, text=True, timeout=180)
            (output / f'{name}.log').write_text(
                f'$ {shlex.join(list(map(str, command)))}\n'
                f'exit status: {result.returncode}\n\n{result.stdout}\n{result.stderr}')
            if result.returncode != expected:
                raise AssertionError(f'{name}: expected status {expected}, got {result.returncode}; '
                                     f'see {output / (name + ".log")}')
            if destination is not None:
                destination.write_text(result.stdout)
            report['checks'].append(name)
            print(f'PASS {name}', flush=True)
            return result.stdout + result.stderr

        try:
            metadata = json.loads(run('image', ['docker', '--context', context, 'image', 'inspect',
                                                image, '--format', '{{json .}}']))
            assert metadata['Architecture'] == 'amd64', metadata['Architecture']
            assert metadata['Os'] == 'linux', metadata['Os']
            report['image_id'] = metadata['Id']
            report['architecture'] = metadata['Architecture']
            run('xls-version', [bin_dir / 'xls', 'interpreter_main', '--version'])
            run('yosys-version', [bin_dir / 'yosys', '-V'])
            run('sta-version', [bin_dir / 'sta', '-version'])
            run('single-container-shell', [bin_dir / 'eda', 'bash', '-ec',
                'xls interpreter_main --version\nyosys -V\nsta -version'])
            linkage = run('shared-libraries', [bin_dir / 'eda', 'python3', '-c', '''
from pathlib import Path
import subprocess
for path in [*Path('/opt/eda/xls').iterdir(), *Path('/opt/eda/yosys/bin').iterdir(), Path('/opt/eda/opensta/sta')]:
    if not path.is_file():
        continue
    with path.open('rb') as handle:
        is_elf = handle.read(4) == b'\\x7fELF'
    if is_elf:
        print(path, flush=True)
        subprocess.run(['/lib64/ld-linux-x86-64.so.2', '--list', str(path)], check=True)
'''])
            assert 'not found' not in linkage, linkage

            (project / 'design.x').write_text(DSLX)
            run('dslx-jit-stdlib', [bin_dir / 'xls', 'interpreter_main', '--compare=jit',
                                   '--lower_to_ir=true', '--convert_tests=true', '--seed=42',
                                   project / 'design.x'])
            run('dslx-ir-interpreter', [bin_dir / 'xls', 'interpreter_main',
                                        '--evaluator=ir-interpreter', '--lower_to_ir=true',
                                        '--convert_tests=true', '--run_quickcheck_when_interpreting=true',
                                        '--seed=42', 'design.x'])
            run('dslx-ir-jit', [bin_dir / 'xls', 'interpreter_main', '--evaluator=ir-jit',
                                 '--lower_to_ir=true', '--convert_tests=true', '--seed=42', 'design.x'])
            run('ir-conversion', [bin_dir / 'xls', 'ir_converter_main', '--top=add', 'design.x'],
                destination=project / 'design.ir')
            run('ir-optimization', [bin_dir / 'xls', 'opt_main', 'design.ir'],
                destination=project / 'design.opt.ir')
            run('verilog-codegen', [bin_dir / 'xls', 'codegen_main', '--pipeline_stages=1',
                                    '--delay_model=unit', '--module_name=docker_add',
                                    '--use_system_verilog=false', '--output_signature_path=design.sig.textproto',
                                    'design.opt.ir'], destination=project / 'design.v')
            assert 'module docker_add' in (project / 'design.v').read_text()
            assert (project / 'design.sig.textproto').stat().st_size > 0

            (project / 'probe.sv').write_text(SYSTEMVERILOG)
            proof = run('slang-array-ports-sat', [bin_dir / 'yosys', '-Q', '-T', '-p',
                'read_slang --top array_probe probe.sv; synth -top array_probe; '
                'check -assert; sat -verify -prove ok 1'])
            assert 'SUCCESS' in proof, proof
            run('abc-liberty-mapping', [bin_dir / 'yosys', '-Q', '-T', '-p',
                'read_liberty -lib /opt/eda/nangate45/NangateOpenCellLibrary_typical.lib; '
                'read_verilog design.v; synth -top docker_add; '
                'dfflibmap -liberty /opt/eda/nangate45/NangateOpenCellLibrary_typical.lib; '
                'abc -liberty /opt/eda/nangate45/NangateOpenCellLibrary_typical.lib; '
                'clean; check -assert; write_verilog -noattr mapped.v'])
            assert 'DFF_X' in (project / 'mapped.v').read_text()
            (project / 'timing.tcl').write_text(TIMING)
            timing = run('sta-timing-power', [bin_dir / 'sta', '-no_init', '-no_splash', '-exit', 'timing.tcl'])
            assert not re.search(r'(?m)^Error:', timing), timing
            assert 'STA_SMOKE_PASS' in timing and 'Startpoint:' in timing, timing
            assert re.search(r'(?m)^Total\s+', timing), timing

            stdin_result = run('stdin-and-exit-status', [bin_dir / 'eda', 'bash', '-c', 'cat\nexit 23'],
                               stdin='wrapper stdin test\n', expected=23)
            assert stdin_result == 'wrapper stdin test\n', repr(stdin_result)
            run('missing-xls-tool', [bin_dir / 'xls', 'does_not_exist'], expected=2)
            nested = project / 'nested directory'
            nested.mkdir()
            parent_input = run('parent-project-mount', [bin_dir / 'eda', 'cat', '../design.x'],
                               cwd=nested, env={'EDA_PROJECT_ROOT': str(project)})
            assert parent_input == DSLX, parent_input
            run('invalid-project-root', [bin_dir / 'eda', 'true'], expected=2,
                env={'EDA_PROJECT_ROOT': str(ROOT)})
            shutil.copytree(project, output / 'artifacts', dirs_exist_ok=True)
            report['status'] = 'passed'
        except Exception as error:
            report['status'] = 'failed'
            report['error'] = str(error)
            raise
        finally:
            (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(report["checks"])} Docker tool checks; logs and artifacts: {output}')


if __name__ == '__main__':
    main()
