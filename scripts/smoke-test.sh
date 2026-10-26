#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
output_dir="$project_dir/build/simple_add"
mkdir -p "$output_dir"
exec > >(tee "$output_dir/smoke-test.log") 2>&1

echo "Running XLS on $(hostname) ($(uname -m))"
for tool in interpreter_main ir_converter_main opt_main codegen_main; do
    xls "$tool" --version
done

echo "Running DSLX tests and comparing function results with LLVM JIT"
xls interpreter_main \
    --compare=jit --lower_to_ir=true --convert_tests=true --seed=42 tests/smoke/simple_add.x

echo "Running the same tests using the IR interpreter"
xls interpreter_main \
    --evaluator=ir-interpreter --lower_to_ir=true --convert_tests=true \
    --run_quickcheck_when_interpreting=true --seed=42 tests/smoke/simple_add.x

echo "Running the same tests using LLVM JIT"
xls interpreter_main \
    --evaluator=ir-jit --lower_to_ir=true --convert_tests=true --seed=42 tests/smoke/simple_add.x

echo "Converting DSLX to XLS IR and optimizing"
xls ir_converter_main \
    --top=add tests/smoke/simple_add.x > "$output_dir/simple_add.ir"
xls opt_main "$output_dir/simple_add.ir" > "$output_dir/simple_add.opt.ir"

echo "Generating one-stage pipelined Verilog"
xls codegen_main --pipeline_stages=1 --delay_model=unit \
    --module_name=simple_add --use_system_verilog=false \
    --output_signature_path="$output_dir/simple_add.sig.textproto" \
    "$output_dir/simple_add.opt.ir" > "$output_dir/simple_add.v"
test -s "$output_dir/simple_add.v"

echo "Simulating the XLS-generated adder with Icarus Verilog and Verilator"
(
    # Verilator's generated Makefiles require a build path without spaces.
    simulation_dir="$(mktemp -d /tmp/eda-simulation.XXXXXX)"
    trap 'rm -rf -- "$simulation_dir"' EXIT
    cp "$output_dir/simple_add.v" "$project_dir/tests/smoke/simple_add_tb.sv" "$simulation_dir/"
    cd "$simulation_dir"

    iverilog -g2012 -s simple_add_tb -o simulation.vvp simple_add.v simple_add_tb.sv \
        2>&1 | tee "$output_dir/iverilog-compile.log"
    vvp simulation.vvp 2>&1 | tee "$output_dir/iverilog.log"
    grep -Fxq 'RTL_SIMULATION_PASS' "$output_dir/iverilog.log"
    test -s simulation.vcd
    mv simulation.vcd "$output_dir/iverilog.vcd"
    mv simulation.vvp "$output_dir/simple_add.vvp"

    verilator --binary --timing --trace --assert -j 2 --top-module simple_add_tb \
        -Wno-TIMESCALEMOD --Mdir obj_dir -o simulation simple_add.v simple_add_tb.sv \
        2>&1 | tee "$output_dir/verilator-build.log"
    ./obj_dir/simulation 2>&1 | tee "$output_dir/verilator.log"
    grep -Fxq 'RTL_SIMULATION_PASS' "$output_dir/verilator.log"
    test -s simulation.vcd
    mv simulation.vcd "$output_dir/verilator.vcd"
)

echo "Mapping the XLS-generated adder to Nangate45 cells"
cp "$project_dir/tests/smoke/simple_add.sdc" "$output_dir/simple_add.sdc"
(
    cd "$output_dir"
    cat > synth.ys <<YOSYS
read_liberty -lib "$NANGATE45_LIBERTY"
read_verilog simple_add.v
synth -top simple_add
dfflibmap -liberty "$NANGATE45_LIBERTY"
abc -liberty "$NANGATE45_LIBERTY"
hilomap -singleton -hicell LOGIC1_X1 Z -locell LOGIC0_X1 Z
clean
check -assert
stat -liberty "$NANGATE45_LIBERTY"
write_verilog -noattr -noexpr simple_add.mapped.v
YOSYS
    yosys -Q -T -l synthesis.log -s synth.ys
    echo "Placing and routing the adder and its clock tree with OpenROAD"
    openroad -no_init -no_splash -exit -threads 2 \
        "$project_dir/tests/smoke/route-adder.tcl" 2>&1 | tee openroad.log
    grep -Fxq 'OPENROAD_ROUTING_PASS DRC=0' openroad.log
    for artifact in placed.def placed.odb routed.def routed.odb routed.v routed.sdc route.guide; do
        test -s "$artifact"
    done
    echo "Reloading the routed database and checking its cells and wires"
    openroad -no_init -no_splash -exit -threads 2 \
        "$project_dir/tests/smoke/check-routed-adder.tcl" 2>&1 | tee routed-reload.log
    grep -Fxq 'OPENROAD_ROUTED_RELOAD_PASS' routed-reload.log
)

echo "PASS: XLS tests, Verilog generation, Icarus/Verilator simulation, Nangate45 mapping, placement, and routing"
echo "Outputs: $output_dir"
