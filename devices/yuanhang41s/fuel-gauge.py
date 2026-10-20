"""Preserve P720S11's stock boot-OCV consistency check.

Actual sc27xx_fuel_gauge.ko: boot_cap_calibration at 0x43ac,
0x4598..0x45e0 reads live OCV and rejects abs(POCV - OCV) > 300000 uV.
Do not replace retained SOC, force recalibration or change valid-OCV policy.
"""
from pathlib import Path


def apply(kernel):
    path=Path(kernel)/'drivers/power/supply/sprd_fuel_gauge_core.c'
    source=path.read_text()
    marker='P720S11 stock boot-OCV consistency'
    if marker in source:return
    start=source.index('static void sprd_fgu_boot_cap_calibration(')
    end=source.index('\n/*',start)
    function=source[start:end]
    anchor='\tdata->track.pocv_info.valid = true;'
    if function.count(anchor)!=1:raise ValueError('Boot calibration anchor changed')
    function=function.replace(anchor,'''\t/* P720S11 stock boot-OCV consistency, sc27xx_fgu +0x1ec. */
\t{
\t\tint live_ocv_mv;
\t\ts64 delta_uv;
\t\tret = sprd_fgu_get_vbat_ocv(data, &live_ocv_mv);
\t\tif (ret)
\t\t\treturn;
\t\tdelta_uv = (s64)pocv_uv - (s64)live_ocv_mv * 1000;
\t\tif (delta_uv > 300000 || delta_uv < -300000) {
\t\t\tdev_warn(data->dev, "Boot calib: stale POCV %d, live OCV %d mV\\n",
\t\t\t\t pocv_uv, live_ocv_mv);
\t\t\treturn;
\t\t}
\t}

'''+anchor)
    path.write_text(source[:start]+function+source[end:])


if __name__=='__main__':
    import sys
    apply(sys.argv[1])
