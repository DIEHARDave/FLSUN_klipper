#!/bin/bash
# How wheels/numpy-2.2.6-cp313-cp313-linux_armv7l.whl was built: on a T1 Pro
# (Debian 10, GCC 8.3, glibc 2.28, armv7l) for FLSUN_moonraker's Python 3.13,
# which no wheel on PyPI or piwheels fits (piwheels' needs glibc 2.38 and
# OpenBLAS). About 35 minutes. It adds a temporary 1 GB swap file (the T1 has
# 715 MB of RAM and no swap), builds without BLAS (Klipper only needs FFT and
# basic math), relaxes numpy's GCC >= 8.4 check (an x86 compiler bug) to 8.3,
# and installs the wheel into ~/klippy-env-upstream.
# Run as root: sudo bash build_numpy.sh; log in /home/pi/numpy_build/build.log.
set -u
V=2.2.6
W=/home/pi/numpy_build
SWAP=/numpy_build.swap
LOG=$W/build.log
mkdir -p $W; chown pi:pi $W
exec >> $LOG 2>&1
echo "== $(date) start"
cleanup() { swapoff $SWAP 2>/dev/null; rm -f $SWAP; echo "== $(date) swap removed"; }
trap cleanup EXIT
if ! swapon --show | grep -q $SWAP; then
    fallocate -l 1G $SWAP && chmod 600 $SWAP && mkswap $SWAP >/dev/null && swapon $SWAP || { echo "swap failed"; exit 1; }
fi
free -m
command -v ninja >/dev/null || command -v ninja-build >/dev/null || NEED_NINJA=1
su pi -s /bin/bash -c "
set -eu
cd $W
PY=/home/pi/klippy-env-upstream/bin/python
[ -x venv/bin/python ] || /usr/local/bin/python3.13 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q --only-binary=:all: 'meson-python>=0.15' 'Cython>=3.0.6' || venv/bin/pip install -q 'meson-python>=0.15' 'Cython>=3.0.6'
venv/bin/pip install -q ninja || true
venv/bin/ninja --version || ninja --version
if [ ! -d numpy-$V ]; then
    U=\$(curl -s https://pypi.org/pypi/numpy/$V/json | venv/bin/python -c 'import json,sys; print([f[\"url\"] for f in json.load(sys.stdin)[\"urls\"] if f[\"packagetype\"]==\"sdist\"][0])')
    curl -sL \"\$U\" -o numpy-$V.tar.gz
    tar xzf numpy-$V.tar.gz
fi
cd numpy-$V
# NumPy requires GCC >= 8.4 for an x86 (AVX512) bug; this is ARM with Debian 10's 8.3.
sed -i \"s/version_compare('>=8.4')/version_compare('>=8.3')/\" meson.build
grep -n 'GCC >= 8.4' -B2 meson.build
echo \"== \$(date) building\"
PATH=$W/venv/bin:\$PATH nice -n 19 $W/venv/bin/pip wheel . --no-build-isolation --no-deps -w $W/wheel -v \
    -Csetup-args=-Dallow-noblas=true -Csetup-args=-Dblas=none -Csetup-args=-Dlapack=none \
    -Ccompile-args=-j2
echo \"== \$(date) built\"
ls -l $W/wheel
\$PY -m pip install --no-deps $W/wheel/numpy-$V-*.whl
cd / && \$PY -c 'import numpy as np; print(\"numpy\", np.__version__); print(np.fft.rfft(np.arange(8.)))'
"
echo "== $(date) done rc=$?"
