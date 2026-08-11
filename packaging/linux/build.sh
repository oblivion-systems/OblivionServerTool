#!/usr/bin/env bash
# ============================================================================
# build.sh — build the Linux artifacts for Oblivion Server Tool.
#
#   packaging/linux/build.sh [binary|appimage|deb|all]   (default: all)
#
# Produces, under dist/:
#   oblivion-server-tool                              (PyInstaller onefile)
#   Oblivion_Server_Tool-<version>-x86_64.AppImage    (portable, any distro)
#   oblivion-server-tool_<version>_amd64.deb          (Debian/Ubuntu + systemd)
#
# Run on Ubuntu 22.04 for broad glibc compatibility (matches the Docker base
# and the CI runner). appimagetool + nfpm are fetched to .build-tools/ if not
# already on PATH.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${0}")")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
DIST="${ROOT}/dist"
TOOLS="${HERE}/.build-tools"
TARGET="${1:-all}"

NFPM_VERSION="2.41.0"

cd "${ROOT}"
mkdir -p "${DIST}" "${TOOLS}"

VERSION="$(python3 -c 'from cs2servergui.config import APP_VERSION; print(APP_VERSION)')"
echo "== Oblivion Linux build — v${VERSION} — target: ${TARGET} =="

# ── helpers ────────────────────────────────────────────────────────────────
ensure_appimagetool() {
    if command -v appimagetool >/dev/null 2>&1; then APPIMAGETOOL="appimagetool"; return; fi
    APPIMAGETOOL="${TOOLS}/appimagetool"
    if [ ! -x "${APPIMAGETOOL}" ]; then
        echo "-- fetching appimagetool"
        curl -fsSL -o "${APPIMAGETOOL}" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        chmod +x "${APPIMAGETOOL}"
    fi
    # Extract-and-run avoids needing FUSE on CI runners / minimal containers.
    export APPIMAGE_EXTRACT_AND_RUN=1
}

ensure_nfpm() {
    if command -v nfpm >/dev/null 2>&1; then NFPM="nfpm"; return; fi
    NFPM="${TOOLS}/nfpm"
    if [ ! -x "${NFPM}" ]; then
        echo "-- fetching nfpm v${NFPM_VERSION}"
        curl -fsSL -o "${TOOLS}/nfpm.tar.gz" \
            "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz"
        tar -xzf "${TOOLS}/nfpm.tar.gz" -C "${TOOLS}" nfpm
        chmod +x "${NFPM}"
    fi
}

# ── 1. onefile binary ──────────────────────────────────────────────────────
build_binary() {
    echo "== [1] PyInstaller onefile =="
    python3 -m pip install --quiet -r requirements-headless.txt pyinstaller
    rm -f "${DIST}/oblivion-server-tool"
    python3 -m PyInstaller --noconfirm --distpath "${DIST}" \
        "${HERE}/oblivion-server-tool.spec"
    test -x "${DIST}/oblivion-server-tool" \
        || { echo "ERROR: binary missing after build"; exit 20; }
    echo "   -> ${DIST}/oblivion-server-tool ($(stat -c%s "${DIST}/oblivion-server-tool") bytes)"
}

# ── 2. AppImage ────────────────────────────────────────────────────────────
build_appimage() {
    echo "== [2] AppImage =="
    test -x "${DIST}/oblivion-server-tool" || build_binary
    ensure_appimagetool
    local APPDIR="${TOOLS}/AppDir"
    rm -rf "${APPDIR}"; mkdir -p "${APPDIR}/usr/bin"
    cp "${DIST}/oblivion-server-tool"          "${APPDIR}/usr/bin/"
    cp "${HERE}/AppRun"                        "${APPDIR}/AppRun"; chmod +x "${APPDIR}/AppRun"
    cp "${HERE}/oblivion-server-tool.desktop"  "${APPDIR}/"
    cp "${ROOT}/emblem.png"                    "${APPDIR}/oblivion-server-tool.png"
    local OUT="${DIST}/Oblivion_Server_Tool-${VERSION}-x86_64.AppImage"
    ARCH=x86_64 "${APPIMAGETOOL}" "${APPDIR}" "${OUT}"
    echo "   -> ${OUT}"
}

# ── 3. .deb ────────────────────────────────────────────────────────────────
build_deb() {
    echo "== [3] .deb (nfpm) =="
    test -x "${DIST}/oblivion-server-tool" || build_binary
    ensure_nfpm
    VERSION="${VERSION}" "${NFPM}" pkg \
        --config "${HERE}/nfpm.yaml" --packager deb --target "${DIST}/"
    echo "   -> $(ls -1 "${DIST}"/oblivion-server-tool_*_amd64.deb 2>/dev/null | tail -1)"
}

case "${TARGET}" in
    binary)   build_binary ;;
    appimage) build_appimage ;;
    deb)      build_deb ;;
    all)      build_binary; build_appimage; build_deb ;;
    *) echo "usage: build.sh [binary|appimage|deb|all]"; exit 2 ;;
esac

echo "== done =="
