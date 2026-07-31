#!/usr/bin/env sh
# Lint and format-check the repository, using ruff.toml so the result does not
# depend on which flags were typed. Runs in the same container as the tests.
#
#   scripts/lint.sh          # check only
#   scripts/lint.sh --fix    # apply the fixes ruff can make safely
set -eu

HA_VERSION="${HA_VERSION:-2026.7.4}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"
IMAGE="broadlink_ir-tests:${HA_VERSION}-py${PYTHON_VERSION}"

cd "$(dirname "$0")/.."
if [ -n "${MSYSTEM:-}" ]; then
    MSYS_NO_PATHCONV=1
    MSYS2_ARG_CONV_EXCL='*'
    export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
    REPO_ROOT="$(pwd -W)"
else
    REPO_ROOT="$(pwd)"
fi

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "Test image ${IMAGE} not found; run scripts/run_tests.sh first" >&2
    exit 1
fi

TARGETS="custom_components/ tests/ scripts/"

# Images built before ruff was added to Dockerfile.tests do not have it, and
# rebuilding a whole Home Assistant image just to lint would be wasteful.
ENSURE_RUFF='command -v ruff >/dev/null 2>&1 ||
    pip install --quiet --root-user-action=ignore ruff'

if [ "${1:-}" = "--fix" ]; then
    exec docker run --rm --entrypoint sh -v "${REPO_ROOT}:/repo" "${IMAGE}" -c \
        "${ENSURE_RUFF}
         cd /repo && ruff check --fix ${TARGETS}; ruff format ${TARGETS}"
fi

exec docker run --rm --entrypoint sh -v "${REPO_ROOT}:/repo" "${IMAGE}" -c \
    "${ENSURE_RUFF}
     cd /repo && ruff check ${TARGETS} && ruff format --check ${TARGETS}"
