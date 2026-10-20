"""Bind the 41S factory TI65132B bias chip to the panel's bias provider.

Reference: this unit's DT/config and ZTE zte_lcdbl_tps65132b.c at
b50db5b6224c11db42a246e8fcdaad1e1d59f478. Apply only in the 41S profile.
"""
from pathlib import Path


def apply(kernel):
    path = Path(kernel) / 'drivers/unisoc_platform/sprd_disp/lcd_bias/ocp2131.c'
    source = path.read_text(encoding="utf-8")
    if '"tps,ti65132b"' not in source:
        source = source.replace(
            '{ .compatible = "sprd,i2c_lcd_bias" },',
            '{ .compatible = "sprd,i2c_lcd_bias" },\n'
            '\t{ .compatible = "tps,ti65132b" },')
    # Both bias rails use 0x14 in the existing panel sequence, matching this
    # unit's zte,lcd_bl_vsp_vsn_voltage. Its Android config additionally enables
    # the 80 mA mode (REG03=43), conditional on the actual TI node property.
    anchor = '\tret = i2c_master_send(client, write_data, 2);'
    addition = '''
\t/* TI65132B factory enhancement, after both bias rails are configured. */
\tif (ret == 2 && addr == 1 &&
\t    of_device_is_compatible(client->dev.of_node, "tps,ti65132b") &&
\t    of_property_read_bool(client->dev.of_node,
\t\t"zte,lcd-bl-vsp-vsn-enhance-enable")) {
\t\tchar enhancement[] = { 0x03, 0x43 };
\t\tret = i2c_master_send(client, enhancement, sizeof(enhancement));
\t}
'''
    if 'TI65132B factory enhancement' not in source:
        if source.count(anchor) != 1:
            raise ValueError('Unexpected bias provider source')
        source = source.replace(anchor, anchor + '\n' + addition)
    # The I2C core owns device lifetime; a remove callback must not recursively
    # unregister the same device when this probe is unloaded.
    source = source.replace('\ti2c_unregister_device(client);', '')
    path.write_text(source)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
