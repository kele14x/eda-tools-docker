#!/bin/sh
# Offline: install package selections from the apt metadata and .deb files fetched into downloads/.
set -eu
apt_dir=/downloads/apt
packages=$(for name in "$@"; do cat "/opt/build/apt/$name.txt"; done)
sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources
cp -a "$apt_dir/lists/." /var/lib/apt/lists/
# shellcheck disable=SC2086
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-download \
    -o Dir::Cache::Archives="$apt_dir/archives" $packages
rm -rf /var/lib/apt/lists/*
