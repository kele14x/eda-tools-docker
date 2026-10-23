"""Fetch apt's authenticated package selection concurrently, then install it."""

import concurrent.futures
import hashlib
import shlex
import subprocess
import time
import urllib.request
from pathlib import Path

# Qt's ARM64 rules link system libraries instead of the bundled x86 interface stubs.
PACKAGES = '''build-essential cmake ninja-build git curl bison flex gawk unzip zip
patch pkg-config python3-dev python-is-python3 openjdk-21-jre-headless
libffi-dev libreadline-dev tcl-dev zlib1g-dev libboost-dev libfmt-dev
libtommath-dev libtomlplusplus-dev libx11-dev libx11-xcb-dev libsm-dev libice-dev
libxcb1-dev libxcb-cursor-dev libxcb-icccm4-dev libxcb-image0-dev libxcb-keysyms1-dev
libxcb-randr0-dev libxcb-render0-dev libxcb-render-util0-dev libxcb-shape0-dev
libxcb-shm0-dev libxcb-sync-dev libxcb-util-dev libxcb-xfixes0-dev libxcb-xkb-dev
libdbus-1-dev libfontconfig-dev libxkbcommon-dev libxkbcommon-x11-dev'''.split()


def fetch(line):
    url, filename, size, checksum = shlex.split(line)
    algorithm, expected = checksum.split(':', 1)
    algorithm = {'MD5Sum': 'md5', 'SHA256': 'sha256', 'SHA512': 'sha512'}[algorithm]
    destination = Path('/var/cache/apt/archives') / filename
    partial = destination.with_suffix('.partial')
    for attempt in range(3):
        try:
            digest = hashlib.new(algorithm)
            with urllib.request.urlopen(url, timeout=90) as response, partial.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if partial.stat().st_size != int(size) or digest.hexdigest() != expected:
                raise ValueError(f'apt package checksum/size mismatch: {filename}')
            partial.replace(destination)
            print(f'Downloaded {filename}', flush=True)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


subprocess.run(['apt-get', 'update'], check=True)
install = ['apt-get', 'install', '-y', '--no-install-recommends', *PACKAGES]
selection = subprocess.check_output([*install, '--print-uris'], text=True)
uris = [line for line in selection.splitlines() if line.startswith("'https://")]
# apt still chooses versions, authenticates repository metadata and installs the
# packages. Only fetching is parallelized to avoid slow serial proxy transfers.
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    list(executor.map(fetch, uris))
subprocess.run([*install, '--no-download'], check=True)
