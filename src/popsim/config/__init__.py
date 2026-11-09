"""YAML configuration (REQ-1A.10, REQ-4.2.6).

A scenario file may start with ``extends: <path or "default">``. Files are merged
depth-first: mappings merge key by key, every other value (lists included)
replaces the inherited one.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).with_name("default.yaml")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_file(path: Path, seen: tuple[Path, ...] = ()) -> dict:
    path = path.resolve()
    if path in seen:
        raise ValueError(f"circular 'extends' at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    parent = data.pop("extends", None)
    if parent is None:
        return data
    parent_path = DEFAULT_PATH if parent == "default" else (path.parent / parent)
    return deep_merge(_load_file(parent_path, seen + (path,)), data)


def resolve(data: dict, base_dir: str | Path) -> dict:
    """Resolve an in-memory scenario mapping (with optional ``extends``) as if it
    were a file in ``base_dir``, then fill in defaults like ``load_config``."""
    data = copy.deepcopy(data)
    parent = data.pop("extends", None)
    if parent is None:
        merged = data
    else:
        parent_path = DEFAULT_PATH if parent == "default" else (Path(base_dir) / parent)
        if not parent_path.exists():
            raise FileNotFoundError(f"extends: {parent!r} not found")
        merged = deep_merge(_load_file(parent_path), data)
    if "pipeline" not in merged:
        merged = deep_merge(_load_file(DEFAULT_PATH), merged)
    return merged


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> dict:
    cfg = _load_file(DEFAULT_PATH) if path is None else _load_file(Path(path))
    if path is not None and "pipeline" not in cfg:
        cfg = deep_merge(_load_file(DEFAULT_PATH), cfg)
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg
