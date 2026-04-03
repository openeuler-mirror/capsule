#!/usr/bin/env bash
set -euo pipefail

LIBREOFFICE_VERSION="26.2.1"
LIBREOFFICE_BUILD="26.2.1.2"
ARCHIVE_NAME="LibreOffice_${LIBREOFFICE_VERSION}_Linux_aarch64_rpm.tar.gz"
EXTRACTED_DIR_NAME="LibreOffice_${LIBREOFFICE_BUILD}_Linux_aarch64_rpm"
LIBREOFFICE_URL="https://download.documentfoundation.org/libreoffice/stable/${LIBREOFFICE_VERSION}/rpm/aarch64/${ARCHIVE_NAME}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LIBREOFFICE_DIR="${ROOT_DIR}/libreoffice"
WORK_DIR="${LIBREOFFICE_DIR}/rpm-aarch64"
ARCHIVE_PATH="${WORK_DIR}/${ARCHIVE_NAME}"
EXTRACTED_DIR="${WORK_DIR}/${EXTRACTED_DIR_NAME}"
RPM_DIR="${EXTRACTED_DIR}/RPMS"
MARKER_PATH="${ROOT_DIR}/.rhel_linux_helper_completed"

require_commands() {
    local command_name
    for command_name in "$@"; do
        if ! command -v "${command_name}" >/dev/null 2>&1; then
            echo "Missing required command: ${command_name}" >&2
            exit 1
        fi
    done
}

install_arm64_libreoffice() {
    require_commands curl tar sudo dnf

    mkdir -p "${WORK_DIR}"

    if [[ ! -f "${ARCHIVE_PATH}" ]]; then
        curl -fL --retry 3 -o "${ARCHIVE_PATH}" "${LIBREOFFICE_URL}"
    fi

    if [[ ! -d "${RPM_DIR}" ]]; then
        rm -rf "${EXTRACTED_DIR}"
        tar -xzf "${ARCHIVE_PATH}" -C "${WORK_DIR}"
    fi

    cd "${RPM_DIR}"
    shopt -s nullglob
    rpm_files=( ./*.rpm )
    if (( ${#rpm_files[@]} == 0 )); then
        echo "No LibreOffice RPM packages were found in ${RPM_DIR}" >&2
        exit 1
    fi

    sudo dnf install -y \
        "${rpm_files[@]}" \
        alsa-lib \
        at-spi2-atk \
        at-spi2-core \
        atk \
        cairo \
        cairo-gobject \
        cups-libs \
        dbus-libs \
        expat \
        fontconfig \
        freetype \
        gdk-pixbuf2 \
        glib2 \
        gtk3 \
        libX11 \
        libXcomposite \
        libXcursor \
        libXdamage \
        libXext \
        libXfixes \
        libXi \
        libXinerama \
        libXrandr \
        libXrender \
        libXScrnSaver \
        libXtst \
        libxshmfence \
        mesa-libgbm \
        nss \
        nss-util \
        nspr \
        pango \
        xorg-x11-server-utils \
        git \
        python3 \
        python3-pip

    if command -v libreoffice26.2 >/dev/null 2>&1; then
        libreoffice26.2 --version
    else
        libreoffice --version
    fi
}

install_x86_64_dependencies() {
    require_commands sudo dnf

    sudo dnf install -y \
        alsa-lib \
        at-spi2-atk \
        at-spi2-core \
        atk \
        bash \
        cairo \
        cups-libs \
        dbus-libs \
        expat \
        flac-libs \
        gdk-pixbuf2 \
        glib2 \
        glibc \
        gtk3 \
        libX11 \
        libXcomposite \
        libXdamage \
        libXext \
        libXfixes \
        libXrandr \
        libXtst \
        libcanberra-gtk3 \
        libdrm \
        libgcc \
        libstdc++ \
        libxcb \
        libxkbcommon \
        libxshmfence \
        libxslt \
        mesa-libgbm \
        nspr \
        nss \
        nss-util \
        pango \
        policycoreutils \
        policycoreutils-python-utils \
        zlib
}


write_completion_marker() {
    printf '%s\n' "completed" > "${MARKER_PATH}"
}

case "$(uname -m)" in
    aarch64|arm64)
        install_arm64_libreoffice
        write_completion_marker
        ;;
    x86_64|amd64)
        install_x86_64_dependencies
        write_completion_marker
        ;;
    *)
        echo "This installer only supports Linux ARM64 (aarch64) and x86_64." >&2
        exit 1
        ;;
esac
