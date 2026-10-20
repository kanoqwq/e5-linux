/* SPDX-License-Identifier: GPL-2.0-only */
/* Host regression: factory SGM41513 register samples and range transitions. */
#include <assert.h>
#include <stdio.h>
#include "charge-current.h"

int main(void)
{
    static const unsigned int decoded[] = {
        0, 35, 40, 110, 130, 270, 300, 510,
        540, 960, 1020, 1440, 1500, 2340, 2460, 3000
    };
    static const unsigned int codes[] = {
        0, 7, 8, 15, 16, 23, 24, 31,
        32, 39, 40, 47, 48, 55, 56, 63
    };
    unsigned int i, previous = 0;

    assert(yh41s_charge_ma(0xbf) == 3000); /* actual stock REG02 */
    for (i = 0; i < sizeof(codes) / sizeof(codes[0]); i++)
        assert(yh41s_charge_ma(codes[i]) == decoded[i]);
    assert(yh41s_charge_code(500) == 30);
    assert(yh41s_charge_code(700) == 34);
    assert(yh41s_charge_code(2800) == 58);
    assert(yh41s_charge_code(3001) == 63);
    for (i = 0; i <= 10000; i++) {
        unsigned int code = yh41s_charge_code(i);
        unsigned int actual = yh41s_charge_ma(code);
        assert(code <= 63);
        assert(actual <= i && actual <= 3000);
        assert(actual >= previous);
        previous = actual;
    }
    puts("SGM41513 current codec: stock sample, transitions and bounds passed");
    return 0;
}
