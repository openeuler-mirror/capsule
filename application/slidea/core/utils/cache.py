import json
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
from core.utils.config import settings


def new_run_id(prefix: str = "run") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{prefix}"


def ensure_dir(path: str | Path) -> str:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def run_dir(base_dir: str, run_id: str) -> str:
    return ensure_dir(Path(base_dir) / "output" / run_id)


def get_run_id(config: dict | None) -> str:
    if not config:
        return ""
    cfg = config.get("configurable") if isinstance(config, dict) else None
    if isinstance(cfg, dict):
        rid = cfg.get("run_id")
        return rid or ""
    return ""


def _cache_enabled() -> bool:
    return settings.USE_CACHE


def run_dir_from_config(config: dict | None, base_dir: str) -> str:
    if not _cache_enabled():
        return ""
    rid = get_run_id(config)
    if not rid:
        return ""
    return run_dir(base_dir, rid)


def save_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: str | Path) -> Optional[Any]:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_text(path: str | Path, text: str) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def load_text(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")
