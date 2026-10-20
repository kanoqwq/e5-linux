#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

/* This E5 LK uses AOSP bootloader_control at offset 2048, with two tries
 * required for an unsuccessful trial. Derive a fresh block from live misc;
 * never ship another device's template or modify the surrounding partition. */
static uint32_t crc32(const uint8_t *p, size_t size) {
    uint32_t crc=~0U;
    while (size--) {
        crc ^= *p++;
        for (int i=0;i<8;i++) crc=(crc>>1) ^ (0xedb88320U & -(crc&1));
    }
    return ~crc;
}
static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static int valid(const uint8_t *b, int android) {
    if (memcmp(b+4,"BCAB",4) || b[8]!=1 || (b[9]&7)!=2) {
        fprintf(stderr,"Unsupported boot control format (expected BCAB v1, two slots)\n"); return 0;
    }
    if (le32(b+28)!=crc32(b,28)) {
        fprintf(stderr,"Boot control CRC mismatch; nothing written\n"); return 0;
    }
    int at=12+2*android;
    if ((b[at]&15)<2 || !(b[at]&0x80) || (b[13]&1) || (b[15]&1)) {
        fprintf(stderr,"Android slot is not marked successful, or slot verity is corrupted; nothing written\n"); return 0;
    }
    return 1;
}
int main(int argc,char **argv) {
    const char *path="/dev/block/by-name/misc";
#ifdef E5_BOOTCTL_TEST
    if (argc<3) return 2;
    path=argv[--argc];
#endif
    if (argc<2 || (strcmp(argv[1],"check") && strcmp(argv[1],"arm"))) {
        fprintf(stderr,"usage: e5-bootctl check [ANDROID_SLOT] | arm BACKUP_FILE [ANDROID_SLOT]\n");return 2;
    }
    int arm=!strcmp(argv[1],"arm");
    int minimum=arm?3:2;
    if (argc<minimum || argc>minimum+1) return 2;
    const char *slot=argc>minimum?argv[minimum]:NULL;
    if (slot && (strlen(slot)!=1 || (*slot!='a' && *slot!='b'))) return 2;
    int fd=open(path,(arm?O_RDWR:O_RDONLY)|O_CLOEXEC);
    if (fd<0) {perror("open misc");return 1;}
#ifndef E5_BOOTCTL_TEST
    struct stat st;
    if (fstat(fd,&st) || !S_ISBLK(st.st_mode) || geteuid()!=0) {
        fprintf(stderr,"Requires root and the E5 misc block device\n");close(fd);return 1;
    }
#endif
    uint8_t before[32],after[32],verify[32];
    if (flock(fd,arm?LOCK_EX:LOCK_SH) || pread(fd,before,32,2048)!=32) {
        perror("read/lock misc");close(fd);return 1;
    }
    int android=slot?*slot-'a':before[1]-'a';
    if (android<0 || android>1 || !valid(before,android)) {close(fd);return 1;}
    if (!arm) {printf("Boot control: CRC OK, Android slot %c preserved\n",'a'+android);close(fd);return 0;}
    /* Backup must be durable before the single bounded partition write. */
    int backup=open(argv[2],O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if (backup<0) {perror("create boot control backup");close(fd);return 1;}
    if (write(backup,before,32)!=32 || fsync(backup)) {
        perror("save boot control backup");close(backup);close(fd);return 1;
    }
    close(backup);
    memcpy(after,before,32);memcpy(after,"_a\0\0",4);
    after[1]='a'+(1-android);
    after[12+2*android]=(before[12+2*android]&0xf0)|14;
    after[12+2*(1-android)]=0x2f; /* priority 15, two tries, not marked successful */
    uint32_t crc=crc32(after,28);
    for (int i=0;i<4;i++) after[28+i]=crc>>(i*8);
    if (pwrite(fd,after,32,2048)!=32 || fsync(fd) ||
        pread(fd,verify,32,2048)!=32 || memcmp(verify,after,32)) {
        fprintf(stderr,"Boot control write/verify failed; restoring previous block\n");
        if (pwrite(fd,before,32,2048)!=32 || fsync(fd) ||
            pread(fd,verify,32,2048)!=32 || memcmp(verify,before,32))
            fprintf(stderr,"Restore also failed; do not reboot before checking misc\n");
        close(fd);return 1;
    }
    close(fd);printf("Linux slot %c armed for a trial boot; Android slot %c preserved\n",'a'+(1-android),'a'+android);return 0;
}
