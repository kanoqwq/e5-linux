"""T512N REG0B=0x11 charge-current map, verified against its stock module."""
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent


def apply(kernel):
    replace = runpy.run_path(str(HERE.parent / 't551m/prepare-kernel.py'))['replace_function']
    path = Path(kernel) / 'drivers/power/supply/bq2560x-charger.c'
    text = path.read_text()
    text = text.replace('val != 0x12 || client->addr != 0x6b', 'val != 0x11 || client->addr != 0x6b')
    text = text.replace('unsupported T551M charger ID', 'unsupported T512N charger ID')
    text = text.replace('"T551M-0x12"', '"T512N-0x11"')
    header = '#include "t512n-charge-current.h"\n'
    if header not in text:
        text = header + text
    text = replace(text, 'bq2560x_charger_set_current', '''
\tu8 code = t512n_charge_code(cur / 1000);
\treturn bq2560x_update_bits(info, BQ2560X_REG_2,
\t\tBQ2560X_REG_ICHG_MASK, code);''')
    text = replace(text, 'bq2560x_charger_get_current', '''
\tu8 value;
\tint ret = bq2560x_read(info, BQ2560X_REG_2, &value);
\tif (ret < 0) return ret;
\t*cur = t512n_charge_ma(value & BQ2560X_REG_ICHG_MASK) * 1000;
\treturn 0;''')
    path.write_text(text)
    (path.parent / 't512n-charge-current.h').write_bytes((HERE / 'charge-current.h').read_bytes())


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
