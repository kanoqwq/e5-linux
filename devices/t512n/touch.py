#!/usr/bin/env python3
"""Use mainline FT6236 protocol for the stock FT6336U (IC ID 0x6426).

Preserves on-controller firmware; only bridges the factory DT property names.
Actual touch events remain subject to first-boot hardware validation.
"""
from pathlib import Path


def apply(kernel):
    path = Path(kernel) / 'drivers/input/touchscreen/edt-ft5x06.c'
    text = path.read_text()
    edits = [
        ('\t{ .compatible = "focaltech,ft6236", .data = &edt_ft6236_data },',
         '\t{ .compatible = "focaltech,ft6236", .data = &edt_ft6236_data },\n'
         '\t{ .compatible = "focaltech_ts,fts_ts", .data = &edt_ft6236_data },'),
        ('\tif (IS_ERR(tsdata->reset_gpio)) {',
         '\t/* T512N stock FT6336U reset property; no firmware flashing. */\n'
         '\tif (!tsdata->reset_gpio && device_is_compatible(&client->dev, "focaltech_ts,fts_ts"))\n'
         '\t\ttsdata->reset_gpio = devm_gpiod_get_optional(&client->dev,\n'
         '\t\t\t\t\t\t"focaltech,reset", GPIOD_OUT_HIGH);\n'
         '\tif (IS_ERR(tsdata->reset_gpio)) {'),
        ('\ttouchscreen_parse_properties(input, true, &tsdata->prop);',
         '\t/* Stock display-coords are axis maxima, as reported by getevent. */\n'
         '\tif (device_is_compatible(&client->dev, "focaltech_ts,fts_ts")) {\n'
         '\t\tu32 bounds[4];\n'
         '\t\terror = device_property_read_u32_array(&client->dev,\n'
         '\t\t\t\t"focaltech,display-coords", bounds, 4);\n'
         '\t\tif (error || bounds[0] >= bounds[2] || bounds[1] >= bounds[3])\n'
         '\t\t\treturn -EINVAL;\n'
         '\t\tinput_set_abs_params(input, ABS_MT_POSITION_X, bounds[0], bounds[2], 0, 0);\n'
         '\t\tinput_set_abs_params(input, ABS_MT_POSITION_Y, bounds[1], bounds[3], 0, 0);\n'
         '\t}\n'
         '\ttouchscreen_parse_properties(input, true, &tsdata->prop);'),
    ]
    for old, new in edits:
        if new in text:
            continue
        if text.count(old) != 1:
            raise ValueError('Unsupported mainline FocalTech source: ' + old)
        text = text.replace(old, new, 1)
    path.write_text(text)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
