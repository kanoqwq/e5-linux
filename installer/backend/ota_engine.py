"""Load the exact engine shipped by the LuCI package, not another implementation."""
import importlib.util
from pathlib import Path
import sys
from .resources import resource

_path = resource('engine.py')
if str(_path.parent) not in sys.path:
    sys.path.insert(0, str(_path.parent))
_spec = importlib.util.spec_from_file_location('unisoc_ota_engine', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
globals().update({k:v for k,v in vars(_module).items() if not k.startswith('__')})
