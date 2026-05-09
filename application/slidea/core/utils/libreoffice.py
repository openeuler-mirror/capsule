import os
import platform
import shutil
from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

LIBREOFFICE_DIR = Path(app_base_dir) / "libreoffice"
LINUX_SYSTEM_LIBREOFFICE_CANDIDATES = ("libreoffice26.2", "libreoffice", "soffice")
MACOS_SYSTEM_LIBREOFFICE_CANDIDATES = ("libreoffice", "soffice")
WIN_SYSTEM_LIBREOFFICE_CANDIDATES = ("soffice.com", "soffice.exe", "libreoffice", "soffice")


def _get_local_libreoffice_executable() -> Path:
    os_type = platform.system()

    if os_type == "Windows":
        program_files_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        return program_files_dir / "LibreOffice" / "program" / "soffice.com"
    if os_type == "Darwin":
        return LIBREOFFICE_DIR / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
    if os_type == "Linux":
        return LIBREOFFICE_DIR / "libreoffice-app" / "AppRun"

    raise RuntimeError(f"Unsupported operating system for LibreOffice: {os_type}")


def _get_system_libreoffice_executable() -> Path | None:
    os_type = platform.system()

    candidates = []
    if os_type == "Linux":
        candidates = LINUX_SYSTEM_LIBREOFFICE_CANDIDATES
    elif os_type == "Darwin":
        candidates = MACOS_SYSTEM_LIBREOFFICE_CANDIDATES
    elif os_type == "Windows":
        candidates = WIN_SYSTEM_LIBREOFFICE_CANDIDATES

    for candidate in candidates:
        executable = shutil.which(candidate)
        logger.info(f"Found LibreOffice {candidate} executable: {executable}")
        if executable:
            return Path(executable)
    return None


def get_available_libreoffice_executable() -> Path | None:
    system_executable = _get_system_libreoffice_executable()
    if system_executable is not None:
        return system_executable

    local_executable = _get_local_libreoffice_executable()
    if local_executable.exists():
        return local_executable

    return None
