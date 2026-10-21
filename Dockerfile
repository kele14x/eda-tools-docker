FROM ubuntu:24.04

LABEL org.opencontainers.image.title="XLS, Yosys, and OpenSTA toolchain" \
      org.opencontainers.image.description="Pinned Linux amd64 tools with slang, ABC, DSLX stdlib, and Nangate45" \
      org.opencontainers.image.version="v1"

RUN test "$(dpkg --print-architecture)" = amd64 \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libstdc++6 libgcc-s1 libffi8 libreadline8t64 \
        libtomlplusplus3t64 tcl8.6 zlib1g python3 \
    && rm -rf /var/lib/apt/lists/*

# Use the real versioned directories, rather than the host's convenience links.
COPY tools/xls-v0.0.0-10788-g6e55d7763-linux-x64/ /opt/eda/xls/
COPY tools/yosys-0.69-linux-x64/ /opt/eda/yosys/
COPY tools/opensta-3.1.0-gd1e43c6f9f4e-linux-x64/ /opt/eda/opensta/
COPY tools/nangate45-8d540a4d4cf6/ /opt/eda/nangate45/
COPY config/tools/ /opt/eda/manifests/
COPY docker/verify-installations.py /opt/eda/verify-installations.py
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/eda-entrypoint

ENV PATH="/opt/eda/xls:/opt/eda/yosys/bin:/opt/eda/opensta:${PATH}" \
    NANGATE45_LIBERTY="/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib"

RUN ln -s eda-entrypoint /usr/local/bin/xls \
    && python3 /opt/eda/verify-installations.py \
    && xls interpreter_main --version \
    && yosys -V \
    && sta -version

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/eda-entrypoint"]
CMD ["bash"]
