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

echo "PASS: DSLX tests, IR interpreter, LLVM JIT, and Verilog generation"
echo "Outputs: $output_dir"
