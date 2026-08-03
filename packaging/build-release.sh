#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: build-release.sh [--version <version>] [--output <dir>]

Build a self-contained versioned tarball for this repository.

Options:
  --version <version>   Version to use instead of VERSION file value
  --output <dir>        Output directory for archive (default: ./dist)
  --help                Show this help text
USAGE
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${ROOT_DIR}/VERSION"
OUTPUT_DIR="${ROOT_DIR}/dist"
VERSION=""

while (( "$#" )); do
  case "$1" in
    --version)
      if [[ "$#" -lt 2 ]]; then
        echo "--version requires a value" >&2
        usage
        exit 1
      fi
      shift
      VERSION="${1:-}"
      ;;
    --output)
      if [[ "$#" -lt 2 ]]; then
        echo "--output requires a value" >&2
        usage
        exit 1
      fi
      shift
      OUTPUT_DIR="${1:-}"
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

if [[ -z "${VERSION}" ]]; then
  if [[ ! -f "${VERSION_FILE}" ]]; then
    echo "Missing version file: ${VERSION_FILE}" >&2
    exit 1
  fi
  VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
fi

if [[ -z "${VERSION}" ]]; then
  echo "Version is empty. Set --version or edit ${VERSION_FILE}." >&2
  exit 1
fi

if ! [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version '${VERSION}' does not look like semver (x.y.z)." >&2
  exit 1
fi

ARTIFACT_NAME="fleet-node-observability-${VERSION}"
OUTPUT_DIR="$(mkdir -p "${OUTPUT_DIR}"; cd "${OUTPUT_DIR}" && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "${TMP_ROOT}"' EXIT

STAGING="${TMP_ROOT}/${ARTIFACT_NAME}"
mkdir -p "${STAGING}"

include_paths=(
  "README.md"
  "LICENSE"
  "CHANGELOG.md"
  "VERSION"
  "docs"
  "examples"
  "packaging"
)

for path in "${include_paths[@]}"; do
  if [[ -e "${ROOT_DIR}/${path}" ]]; then
    cp -R "${ROOT_DIR}/${path}" "${STAGING}/${path}"
  fi
done

if [[ -d "${ROOT_DIR}/src" ]]; then
  cp -R "${ROOT_DIR}/src" "${STAGING}/"
fi
if [[ -d "${ROOT_DIR}/bin" ]]; then
  cp -R "${ROOT_DIR}/bin" "${STAGING}/"
fi

find "${STAGING}" \( -name '__pycache__' -type d -o -name '*.pyc' -type f \) -prune -exec rm -rf {} +

COPYFILE_DISABLE=1 tar -C "${TMP_ROOT}" -czf "${OUTPUT_DIR}/${ARTIFACT_NAME}.tar.gz" "${ARTIFACT_NAME}"

if command -v sha256sum >/dev/null 2>&1; then
  archive_sha256="$(sha256sum "${OUTPUT_DIR}/${ARTIFACT_NAME}.tar.gz" | awk '{print $1}')"
else
  archive_sha256="$(shasum -a 256 "${OUTPUT_DIR}/${ARTIFACT_NAME}.tar.gz" | awk '{print $1}')"
fi
printf '%s  %s\n' "$archive_sha256" "${ARTIFACT_NAME}.tar.gz" > "${OUTPUT_DIR}/${ARTIFACT_NAME}.sha256"

cat <<EOF
Built:
  Artifact: ${OUTPUT_DIR}/${ARTIFACT_NAME}.tar.gz
  SHA256:  $(cut -d' ' -f1 "${OUTPUT_DIR}/${ARTIFACT_NAME}.sha256")
  Version:  ${VERSION}
EOF
