"""Respect the UMS9620 AI register bank's AON access gate.

These seven gates already declare ai-eb as their parent.  The generic Sprd
gate driver implements SPRD_GATE_NON_AON precisely for an inaccessible child
bank while that parent is off (also used by this SoC's audio gates).
CLK_IGNORE_UNUSED alone only skips the late disable pass; it does not protect
is_enabled, which is called by clk_summary.  Do not keep the AI bus powered
merely to make a diagnostic read safe.
"""
from pathlib import Path
import re

GATES = ('powervr_eb', 'mtx_apbreg_eb', 'ai_dvfs_eb', 'ocm_eb',
         'axi_pmon_eb', 'aon_to_ocm_eb', 'nic400_busmon_eb')


def apply(kernel):
    path = Path(kernel) / 'drivers/clk/sprd/ums9620-clk.c'
    source = path.read_text()
    for name in GATES:
        pattern = (r'(static SPRD_SC_GATE_CLK_HW\(' + name +
                   r',\s*"[^"]+",\s*&ai_eb\.common\.hw,\s*0x0,\s*'
                   r'0x1000,\s*BIT\([0-6]\),\s*CLK_IGNORE_UNUSED,\s*)'
                   r'(0|SPRD_GATE_NON_AON)(\);)')
        source, count = re.subn(pattern, r'\g<1>SPRD_GATE_NON_AON\3', source)
        if count != 1:
            raise ValueError('Unexpected UMS9620 AI clock: ' + name)
    path.write_text(source)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
