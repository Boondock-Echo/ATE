"""Shared helpers for resolving standard configuration/data/log paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Optional


def _expand(path: Path | str) -> Path:
    return Path(path).expanduser()


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def ensure_directory(path: Path, mode: int = 0o700) -> None:
    """Create a directory (and parents) with safe permissions if missing."""

    path.mkdir(parents=True, exist_ok=True, mode=mode)


def atomic_write(
    path: Path,
    data: str | bytes,
    *,
    mode: str = "w",
    encoding: str = "utf-8",
) -> None:
    """Write data to *path* atomically via a temporary file."""

    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o750)
    except OSError:
        pass
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    open_kwargs = {} if "b" in mode else {"encoding": encoding}
    try:
        with tmp_path.open(mode, **open_kwargs) as handle:
            handle.write(data)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _resolve_standard_file(
    *,
    app_name: str,
    filename: str,
    cli_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
    search_dirs: Iterable[Path],
    bundle_path: Optional[Path] = None,
) -> Path:
    if cli_path is not None:
        return _expand(cli_path)

    if base_dir is not None:
        return _expand(base_dir) / filename

    existing = _first_existing((candidate / filename for candidate in search_dirs))
    if existing is not None:
        return existing

    if bundle_path is not None and bundle_path.exists():
        return bundle_path

    search_dirs = list(search_dirs)
    if not search_dirs:
        raise ValueError("No search directories configured")
    return search_dirs[0] / filename


def resolve_config_file(
    app_name: str,
    filename: str,
    *,
    cli_path: Optional[Path] = None,
    bundle_path: Optional[Path] = None,
) -> Path:
    config_dirs = _platform_config_dirs(app_name)
    return _resolve_standard_file(
        app_name=app_name,
        filename=filename,
        cli_path=cli_path,
        search_dirs=config_dirs,
        bundle_path=bundle_path,
    )


def resolve_data_file(
    app_name: str,
    filename: str,
    *,
    cli_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
    bundle_path: Optional[Path] = None,
) -> Path:
    data_dirs = _platform_data_dirs(app_name)
    return _resolve_standard_file(
        app_name=app_name,
        filename=filename,
        cli_path=cli_path,
        base_dir=base_dir,
        search_dirs=data_dirs,
        bundle_path=bundle_path,
    )


def resolve_log_file(
    app_name: str,
    filename: str,
    *,
    cli_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> Path:
    log_dirs = _platform_log_dirs(app_name)
    return _resolve_standard_file(
        app_name=app_name,
        filename=filename,
        cli_path=cli_path,
        base_dir=base_dir,
        search_dirs=log_dirs,
    )


def _platform_config_dirs(app_name: str) -> list[Path]:
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or _expand("~"))
        return [base / app_name]
    if sys.platform == "darwin":
        return [_expand("~/Library/Application Support") / app_name]
    return [_expand("~/.config") / app_name, Path("/etc") / app_name]


def _platform_data_dirs(app_name: str) -> list[Path]:
    if os.name == "nt":
        base = Path(
            os.getenv("LOCALAPPDATA")
            or os.getenv("APPDATA")
            or _expand("~")
        )
        return [base / app_name]
    if sys.platform == "darwin":
        return [_expand("~/Library/Application Support") / app_name]
    return [_expand("~/.local/share") / app_name, Path("/var/lib") / app_name]


def _platform_log_dirs(app_name: str) -> list[Path]:
    if os.name == "nt":
        base = Path(
            os.getenv("LOCALAPPDATA")
            or os.getenv("APPDATA")
            or _expand("~")
        )
        return [base / "Logs" / app_name]
    if sys.platform == "darwin":
        return [_expand("~/Library/Logs") / app_name]
    return [_expand("~/.local/state") / app_name, Path("/var/log") / app_name]
