"""Resolve one selected runtime layout, without fallback to another repository."""
from pathlib import Path
import sys
from . import host


def resource(name):
    if host.current() is not None:
        return host.resource(name)
    if getattr(sys, 'frozen', False):
        path = Path(sys._MEIPASS) / name
    else:
        # Standalone CLI export copies runtime helpers beside this module.
        beside = Path(__file__).with_name(name)
        if beside.is_file():
            return beside
        if name == 'engine.py' and beside.with_name('boot.py').is_file() and beside.with_name('ota_engine.py').is_file():
            return beside.with_name('ota_engine.py')
        import importlib.util
        source = Path(__file__).resolve().parents[1] / 'resources.py'
        spec = importlib.util.spec_from_file_location('openwrt_tools_assets', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.asset(name)
    if not path.is_file():
        raise ValueError('Installer runtime asset is missing: ' + name)
    return path
