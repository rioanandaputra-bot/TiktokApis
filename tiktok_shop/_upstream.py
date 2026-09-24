"""Upstream signing modules, loaded by path.

``signing/__init__.py`` imports every upstream primitive (ticket guard, AWS v4, ...), and a
top-level package named ``signing`` is too generic to import by name in a host application,
so the two modules this package uses are loaded from their files under private names.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    path = ROOT / relative
    if not path.is_file():
        raise RuntimeError(f"{path} missing: is the TiktokApis checkout complete?")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pure = _load("tiktokapis_signing_pure", "signing/pure.py")
lucifer_bsid = _load("tiktokapis_signing_lucifer_bsid", "signing/lucifer_bsid.py")
