"""Fix M8 DSI transport accounting without disabling the panel's ESD policy.

Keep stock DT/DTBO and the selected panel's register/value/period unchanged.
A failed or short DCS read is an I/O error, never a valid zero status byte.
"""
from pathlib import Path
import runpy


def apply(kernel):
    kernel = Path(kernel)
    base = kernel / 'drivers/unisoc_platform/sprd_disp'
    api = base / 'dsi/sprd_dsi_api.c'
    panel = base / 'sprd_dsi_panel.c'
    source = api.read_text()
    old = '\t/* 2nd: wait peripheral response completed */\n\tdsi_wait_rd_resp_completed(dsi);'
    new = '\t/* 2nd: wait peripheral response completed */\n\tret = dsi_wait_rd_resp_completed(dsi);\n\tif (ret)\n\t\treturn ret;'
    if new not in source and source.count(old) != 1:
        raise ValueError('Unexpected M8 DSI response wait')
    text = panel.read_text()
    begin = text.index('static int sprd_panel_esd_check(')
    end = text.index('\nstatic int sprd_panel_te_check(', begin)
    body = text[begin:end]
    if '/* M8: compare only a complete status register reply. */' not in body:
        edits = [
            ('\tu8 read_val = 0;', '\tu8 read_val = 0;\n\tint ret;'),
            ('\tmipi_dsi_set_maximum_return_packet_size(panel->slave, 1);',
             '\tret = mipi_dsi_set_maximum_return_packet_size(panel->slave, 1);\n'
             '\tif (ret < 0)\n\t\tgoto out_unlock_vrr;'),
            ('\tmipi_dsi_dcs_read(panel->slave, info->esd_check_reg,\n\t\t\t  &read_val, 1);',
             '\tret = mipi_dsi_dcs_read(panel->slave, info->esd_check_reg,\n\t\t\t  &read_val, 1);'),
            ('\tmutex_unlock(&dpu->ctx.vrr_lock);',
             'out_unlock_vrr:\n\tmutex_unlock(&dpu->ctx.vrr_lock);\n'
             '\t/* M8: compare only a complete status register reply. */\n'
             '\tif (ret != 1) {\n'
             '\t\tDRM_ERROR("ESD status read failed: %d\\n", ret);\n'
             '\t\tmutex_unlock(&panel->lock);\n'
             '\t\treturn ret < 0 ? ret : -EIO;\n\t}\n'),
        ]
        for before, after in edits:
            if body.count(before) != 1:
                raise ValueError('Unexpected M8 ESD read anchor: ' + before)
            body = body.replace(before, after, 1)
        text = text[:begin] + body + text[end:]
    runpy.run_path(str(Path(__file__).parents[1] / 'common/display-dsi-fifo.py'))['apply'](kernel)
    # The FIFO fix above edits the same file; keep that change when adding
    # the response timeout check.
    source = api.read_text()
    api.write_text(source.replace(old, new, 1))
    panel.write_text(text)
