/* SPDX-License-Identifier: GPL-2.0-only */
/* Static laboratory watchdog: survive switch_root with an already-open fd. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char *end;
    if (argc != 3) return 2;
    long seconds = strtol(argv[1], &end, 10);
    if (*end || seconds < 30 || seconds > 1200) return 2;
    int trigger = open("/proc/sysrq-trigger", O_WRONLY);
    if (trigger < 0) return 3;
    FILE *ready = fopen(argv[2], "w");
    if (!ready) return 4;
    fprintf(ready, "%ld\n", (long)getpid());
    if (fclose(ready)) return 4;
    struct timespec remaining = { .tv_sec = seconds, .tv_nsec = 0 };
    while (nanosleep(&remaining, &remaining) && errno == EINTR) { }
    sync();
    return write(trigger, "b", 1) == 1 ? 0 : 5;
}
