FROM ubuntu:24.04

LABEL org.opencontainers.image.title="XLS, Yosys, OpenROAD, Icarus Verilog, and Verilator toolchain" \
      org.opencontainers.image.description="Linux amd64 synthesis, simulation, timing, and physical design with Nangate45" \
      org.opencontainers.image.version="v1"

RUN test "$(dpkg --print-architecture)" = amd64 \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libstdc++6 libgcc-s1 libffi8 libreadline8t64 \
        libtomlplusplus3t64 tcl8.6 zlib1g python3 \
        libx11-xcb1 libsm6 libice6 libxcb-cursor0 libxcb-icccm4 \
        libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render0 \
        libxcb-render-util0 libxcb-shape0 libxcb-shm0 libxcb-sync1 \
        libxcb-util1 libxcb-xfixes0 libxcb-xkb1 libdbus-1-3 \
        libfontconfig1 libxkbcommon0 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

# Verilator compiles generated C++ when users build simulations, so the compiler
# and development headers belong in the runtime image as well.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iverilog verilator build-essential zlib1g-dev \
    && mkdir -p /opt/eda/manifests \
    && dpkg-query -W iverilog verilator build-essential g++ make perl zlib1g-dev \
        > /opt/eda/manifests/simulation-packages.txt \
    && rm -rf /var/lib/apt/lists/*

# Use the real versioned directories, rather than the host's convenience links.
COPY tools/xls-v0.0.0-10788-g6e55d7763-linux-x64/ /opt/eda/xls/
COPY tools/yosys-0.69-linux-x64/ /opt/eda/yosys/
COPY tools/openroad-26Q4-878-g273fa45460-linux-x64/ /opt/eda/openroad/
COPY tools/nangate45-8d540a4d4cf6/ /opt/eda/nangate45/
COPY tools/nangate45-physical-31ae82dc3149/ /opt/eda/nangate45-physical/
COPY config/tools/ /opt/eda/manifests/
COPY docker/verify-installations.py /opt/eda/verify-installations.py
COPY docker/runtime_checksums.py /opt/eda/runtime_checksums.py
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/eda-entrypoint

ENV PATH="/opt/eda/xls:/opt/eda/yosys/bin:/opt/eda/openroad/bin:${PATH}" \
    NANGATE45_LIBERTY="/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib" \
    NANGATE45_TECH_LEF="/opt/eda/nangate45-physical/lef/NangateOpenCellLibrary.tech.lef" \
    NANGATE45_CELLS_LEF="/opt/eda/nangate45-physical/lef/NangateOpenCellLibrary.macro.mod.lef" \
    NANGATE45_RC="/opt/eda/nangate45-physical/setRC.tcl" \
    NANGATE45_RCX_RULES="/opt/eda/nangate45-physical/rcx_patterns.rules"

RUN ln -s eda-entrypoint /usr/local/bin/xls \
    && ln -s /opt/eda/openroad/bin/openroad /usr/local/bin/sta \
    && python3 /opt/eda/verify-installations.py \
    && xls interpreter_main --version \
    && yosys -V \
    && sta -version \
    && openroad -version \
    && iverilog -V && vvp -V && verilator --version

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/eda-entrypoint"]
CMD ["bash"]
