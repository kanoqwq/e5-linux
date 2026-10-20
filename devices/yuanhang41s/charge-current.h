/* SPDX-License-Identifier: GPL-2.0-only */
/* SGM41513 codec from ZTE sqc_v8 at b50db5b6224c11db42a246e8fcdaad1e1d59f478.
 * Verified on P720S11: stock driver changes 0x6b DT address to 0x1a;
 * REG02=0xbf reads 3000 mA. No firmware/board calibration is embedded. */
static unsigned int yh41s_charge_ma(unsigned int code)
{
    static const unsigned int base[] = {0,40,130,300,540,1020,1500,2460};
    static const unsigned int step[] = {5,10,20,30,60,60,120,120};
    unsigned int group = (code & 63) >> 3;
    unsigned int ma = base[group] + (code & 7) * step[group];
    return ma > 3000 ? 3000 : ma;
}
static unsigned int yh41s_charge_code(unsigned int ma)
{
    static const unsigned int high[] = {35,120,270,510,960,1440,2340,3000};
    static const unsigned int base[] = {0,40,130,300,540,1020,1500,2460};
    static const unsigned int step[] = {5,10,20,30,60,60,120,120};
    unsigned int group, n;
    if (ma > 3000) return 63;
    for (group=0; group<7 && ma>high[group]; group++) {}
    n = ma >= base[group] ? (ma-base[group])/step[group] : 0;
    if (n > 7) n = 7;
    return group && !n ? (group << 3)-1 : (group << 3)|n;
}
