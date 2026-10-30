"""Explicit embedded-host services; desktop execution keeps its existing path.

Installed once by the Android application before importing the service. No
availability probe or failed desktop operation may select another backend.
"""
from pathlib import Path
import threading

_provider = None
_lock = threading.Lock()


def install(provider):
    global _provider
    with _lock:
        if _provider is not None and _provider is not provider:
            raise RuntimeError('Embedded host is already initialized')
        _provider = provider


def current():
    return _provider


def resource(name):
    if _provider is None:return None
    base=Path(_provider.resources).resolve();path=(base/name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError('Embedded resource is missing or unsafe: '+name)
    return path
