#!/bin/sh
# Online: download apt metadata and every .deb the offline build installs into /downloads/platform/apt.
set -eu
apt_dir=/downloads/platform/apt
export DEBIAN_FRONTEND=noninteractive
rm -f "$apt_dir/complete"
# Resolve against the untouched base system, which is what the offline build starts from.
cp /var/lib/dpkg/status /tmp/pristine-status
apt-get update
apt-get install -y --no-install-recommends ca-certificates
sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources
apt-get update
mkdir -p "$apt_dir/archives/partial" /tmp/empty-archives/partial
: > /tmp/needed
for selection in 'runtime simulation' 'runtime build'; do
    packages=$(for name in $selection; do cat "/opt/build/apt/$name.txt"; done)
    # shellcheck disable=SC2086
    apt-get install -y --no-install-recommends --print-uris \
        -o Dir::State::status=/tmp/pristine-status -o Dir::Cache::Archives=/tmp/empty-archives \
        $packages | sed -n "s/^'[^']*' \([^ ]*\) .*/\1/p" >> /tmp/needed
    # shellcheck disable=SC2086
    apt-get install -y --no-install-recommends --download-only \
        -o Dir::State::status=/tmp/pristine-status -o Dir::Cache::Archives="$apt_dir/archives" \
        $packages
done
for deb in "$apt_dir"/archives/*.deb; do
    grep -qxF "$(basename "$deb")" /tmp/needed || rm -f "$deb"
done
rm -rf "$apt_dir/lists.new"
mkdir "$apt_dir/lists.new"
cp -a /var/lib/apt/lists/. "$apt_dir/lists.new/"
rm -rf "$apt_dir/lists.new/partial" "$apt_dir/lists.new/lock" "$apt_dir/lists"
mv "$apt_dir/lists.new" "$apt_dir/lists"
printf '%s\n' "$APT_REQUEST" > "$apt_dir/complete"
chown -R "$HOST_UID:$HOST_GID" "$apt_dir"
echo "FETCH: $(sort -u /tmp/needed | wc -l) apt packages ready"
