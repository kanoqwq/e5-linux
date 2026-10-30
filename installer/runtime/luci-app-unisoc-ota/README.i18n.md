# Menu translations

The menu uses LuCI's native `System patches` message key. English is the source
language; Simplified Chinese and Turkish catalogs are bundled in `root/`, so
both package installation and copying the overlay install the translations.
Page content keeps its existing language selection.

Regenerate the committed LMO files after changing PO sources, using LuCI's
`po2lmo` (from this directory):

```sh
po2lmo po/zh_Hans/unisoc-ota.po root/usr/lib/lua/luci/i18n/unisoc-ota.zh-cn.lmo
po2lmo po/tr/unisoc-ota.po root/usr/lib/lua/luci/i18n/unisoc-ota.tr.lmo
```
