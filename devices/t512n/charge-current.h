/* SPDX-License-Identifier: GPL-2.0-only */
/* Stock T512N bq2560x-charger.ko, set_current 0x2c60, REG0B low bits == 1.
 * Stock setter upper limit 2875 mA; this is register encoding, not DCP policy.
 */
#ifndef T512N_CHARGE_CURRENT_H
#define T512N_CHARGE_CURRENT_H
static inline unsigned int t512n_charge_code(unsigned int ma)
{
	if (ma > 2875)
		ma = 2875;
	if (ma < 59)
		return 0;
	if (ma <= 815)
		return (ma - 59) / 63 + 1;
	return (ma * 2 - 1610) / 115 + 14;
}

static inline unsigned int t512n_charge_ma(unsigned int code)
{
	if (!code)
		return 0;
	if (code <= 13)
		return (code - 1) * 63 + 59;
	return (code - 14) * 115 / 2 + 805;
}
#endif
