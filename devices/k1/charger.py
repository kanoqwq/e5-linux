"""K1 stock REG0E encoding with this board's native battery voltage."""
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent


def apply(kernel):
    profile = json.loads((HERE / 'profile.json').read_text(encoding='utf-8'))
    voltage = profile['battery_max_voltage_uv']
    if type(voltage) is not int or voltage != 4380000:
        raise ValueError('K1 battery voltage differs from the verified stock DT')
    runpy.run_path(str(HERE.parent / 'm8/charger.py'))['apply'](
        kernel, max_voltage_mv=voltage // 1000, board='K1')
