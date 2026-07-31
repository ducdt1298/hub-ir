#!/usr/bin/env sh
# Run the test suite against Home Assistant in a Linux container.
#
# Home Assistant only supports Linux (homeassistant.runner imports fcntl and
# resource), so the tests run in a container rather than on the host. The image
# is built once per HA version and reused. Extra arguments go to pytest:
#
#   scripts/run_tests.sh -k climate -vv
#   HA_VERSION=2026.6.4 scripts/run_tests.sh
set -eu

HA_VERSION="${HA_VERSION:-2026.7.4}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"
IMAGE_VARIANT="${IMAGE_VARIANT--slim}"
IMAGE="broadlink_ir-tests:${HA_VERSION}-py${PYTHON_VERSION}"

cd "$(dirname "$0")/.."
# Git Bash rewrites container-side paths into Windows paths; stop it, and hand
# docker a Windows-style host path it understands.
if [ -n "${MSYSTEM:-}" ]; then
    MSYS_NO_PATHCONV=1
    MSYS2_ARG_CONV_EXCL='*'
    export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
    REPO_ROOT="$(pwd -W)"
else
    REPO_ROOT="$(pwd)"
fi

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "Building ${IMAGE} (first run only)..." >&2
    docker build \
        --build-arg "HA_VERSION=${HA_VERSION}" \
        --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
        --build-arg "IMAGE_VARIANT=${IMAGE_VARIANT}" \
        -f scripts/Dockerfile.tests \
        -t "${IMAGE}" \
        scripts
fi

exec docker run --rm -v "${REPO_ROOT}:/repo" "${IMAGE}" "$@"
