`timescale 1ns/1ps

module simple_add_tb;
  reg clk = 0;
  reg [31:0] x = 0, y = 0;
  wire [31:0] out;

  simple_add dut(.clk(clk), .x(x), .y(y), .out(out));
  always #5 clk = ~clk;

  task automatic check_sum(input reg [31:0] a, b, expected);
    begin
      @(negedge clk);
      x = a;
      y = b;
      // XLS's default input/output registers give this adder latency two.
      repeat (2) @(posedge clk);
      #1;
      if (out !== expected)
        $fatal(1, "sum mismatch: %h + %h = %h, expected %h", a, b, out, expected);
    end
  endtask

  initial begin
    $dumpfile("simulation.vcd");
    $dumpvars(0, simple_add_tb);
    check_sum(32'd2, 32'd3, 32'd5);
    check_sum(32'd42, 32'd0, 32'd42);
    check_sum(32'd0, 32'd42, 32'd42);
    check_sum(32'hffff_ffff, 32'd1, 32'd0);
    check_sum(32'h7fff_ffff, 32'd1, 32'h8000_0000);
    check_sum(32'h1234_5678, 32'h8765_4321, 32'h9999_9999);
    $display("RTL_SIMULATION_PASS");
    $finish;
  end

  initial begin
    #1000;
    $fatal(1, "simulation timed out");
  end
endmodule
