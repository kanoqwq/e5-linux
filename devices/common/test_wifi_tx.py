#!/usr/bin/env python3
"""Execute SC2355 packet classification and check common multicast adaptation."""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
function = runpy.run_path(str(HERE/'test_sipa.py'))['function']
apply = runpy.run_path(str(HERE/'wifi-tx.py'))['apply']

STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <arpa/inet.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/udp.h>
#include <linux/tcp.h>
#define __force
#define MORE_DEBUG 1
#define cpu_to_be16 htons
#define ETHER_HDR_LEN ETH_HLEN
#define SPRD_MODE_AP 3
#define SPRD_MODE_P2P_GO 4
#define SPRD_MODE_STATION 5
#define SPRD_CIPHER_NONE 0
#define SPRD_NPI_NORMAL_ALL 0
#define SPRD_NPI_NORMAL_UNENCRYP 1
#define DHCP_SERVER_PORT 67
#define DHCP_CLIENT_PORT 68
#define DHCP_SERVER_PORT_IPV6 547
#define DHCP_CLIENT_PORT_IPV6 546
#define DNS_SERVER_PORT 53
#define RTSP_SERVER_PORT 7236
#define CHECKSUM_PARTIAL 1
#define CHECKSUM_NONE 0
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 16
#define WAPI_TYPE 0x88b4
#define wl_info(...) ((void)0)
#define wl_debug(...) ((void)0)
#define pr_debug_ratelimited(...) ((void)0)
#define spin_lock_bh(...) ((void)0)
#define spin_unlock_bh(...) ((void)0)
struct sprd_peer_entry { int ip_acquired,ba_tx_done_map,vowifi_enabled,vowifi_pkt_cnt; };
struct sprd_hif { struct sprd_peer_entry peer_entry[8]; struct { int tx_arp_num,tx_multicast; } stats; };
/* BA lifecycle is tested separately using its real controller and work code. */
static inline void sc2355_ba_reset(struct sprd_hif *h,unsigned char lut) { h->peer_entry[lut].ba_tx_done_map=0; }
struct sprd_priv { struct sprd_hif hif; };
struct sprd_vif { struct sprd_priv *priv; int mode,prwise_crypto; };
struct net_device { struct sprd_vif *vif; };
struct sk_buff { unsigned char *data; int len,ip_summed; uint16_t protocol; };
static struct { int adap_lock,special_data_flag; } adap_info = {0,32};
static int commands;
static bool vowifi_command;
static void *netdev_priv(struct net_device *d) { return d->vif; }
static int sc2355_xmit_data2cmd_wq(struct sk_buff *s,struct net_device *d) { commands++; return 0; }
static bool sc2355_is_group(const unsigned char *p) { return p[0]&1; }
static unsigned char sc2355_find_lut_index(struct sprd_hif *h,struct sprd_vif *v,const unsigned char *p) {
    return sc2355_is_group(p)?4:6;
}
static bool sc2355_is_vowifi_pkt(struct sk_buff *s,bool *cmd) { *cmd=vowifi_command; return vowifi_command; }
static void sc2355_vowifi_data_protection(struct sprd_vif *v) {}
static int ip_hdrlen(struct sk_buff *s) { return 20; }
static int tcp_hdrlen(struct sk_buff *s) { return 20; }
static unsigned int tx_do_csum(const unsigned char *p,int len) { return 0xffff; }
'''

MAIN = r'''
int main(void) {
    struct sprd_priv priv = {0};
    struct sprd_vif vif = {&priv,SPRD_MODE_AP,0};
    struct net_device dev = {&vif};
    unsigned char storage[386] __attribute__((aligned(16))),before[384];
    unsigned char *packet=storage+2;
    struct sk_buff skb = {packet,sizeof(before),CHECKSUM_PARTIAL,0};
    struct ethhdr *eth=(void *)packet;
    for(int ipv6=0;ipv6<2;ipv6++) for(int multicast=0;multicast<2;multicast++)
    for(int mode=SPRD_MODE_AP;mode<=SPRD_MODE_STATION;mode++) {
        memset(packet,0,sizeof(before));
        eth->h_dest[0]=multicast?0x33:0x02;
        eth->h_proto=htons(ipv6?ETH_P_IPV6:ETH_P_IP);skb.protocol=eth->h_proto;
        int iplen=ipv6?40:20;
        if(ipv6) ((struct ipv6hdr *)(packet+ETH_HLEN))->nexthdr=IPPROTO_UDP;
        else { struct iphdr *ip=(void *)(packet+ETH_HLEN);ip->ihl=5;ip->protocol=IPPROTO_UDP; }
        struct udphdr *udp=(void *)(packet+ETH_HLEN+iplen);
        udp->source=udp->dest=htons(5353);udp->check=htons(0x1234);
        vif.mode=mode;commands=0;skb.ip_summed=CHECKSUM_PARTIAL;
        memcpy(before,packet,sizeof(before));
        assert(sc2355_tx_special_data(&skb,&dev)==1 && commands==0);
        assert(skb.ip_summed==CHECKSUM_PARTIAL && !memcmp(before,packet,sizeof(before)));
        // DNS keeps AP/GO data routing and STA's command route.
        udp->source=udp->dest=htons(53);skb.ip_summed=CHECKSUM_NONE;commands=0;
        int expected=mode==SPRD_MODE_STATION;
        assert(sc2355_tx_special_data(&skb,&dev)==(expected?NETDEV_TX_OK:1));
        assert(commands==expected);
        // DHCPv4/v6 and VoWiFi remain commands for every mode/address class.
        udp->source=htons(ipv6?547:67);commands=0;
        assert(sc2355_tx_special_data(&skb,&dev)==NETDEV_TX_OK && commands==1);
        udp->source=udp->dest=htons(4500);vowifi_command=true;commands=0;
        assert(sc2355_tx_special_data(&skb,&dev)==NETDEV_TX_OK && commands==1);
        vowifi_command=false;
        skb.protocol=htons(ETH_P_PAE);commands=0;
        assert(sc2355_tx_special_data(&skb,&dev)==NETDEV_TX_OK && commands==1);
    }
    // IPv6 ICMP and extension headers retain the data route without touching checksums.
    eth->h_proto=skb.protocol=htons(ETH_P_IPV6);eth->h_dest[0]=0x33;vif.mode=SPRD_MODE_AP;
    for(int proto=0;proto<=58;proto+=58) {
        ((struct ipv6hdr *)(packet+ETH_HLEN))->nexthdr=proto;
        skb.ip_summed=CHECKSUM_PARTIAL;commands=0;memcpy(before,packet,sizeof(before));
        assert(sc2355_tx_special_data(&skb,&dev)==1 && commands==0);
        assert(skb.ip_summed==CHECKSUM_PARTIAL && !memcmp(before,packet,sizeof(before)));
    }
    // Existing RTSP keepalive command handling is retained.
    memset(packet,0,sizeof(before));eth->h_proto=skb.protocol=htons(ETH_P_IP);
    struct iphdr *ip=(void *)(packet+ETH_HLEN);ip->ihl=5;ip->protocol=IPPROTO_TCP;
    struct tcphdr *tcp=(void *)(packet+ETH_HLEN+20);tcp->source=htons(RTSP_SERVER_PORT);
    memcpy(packet+ETH_HLEN+40,"GET_PARAMETER",13);skb.ip_summed=CHECKSUM_NONE;commands=0;
    assert(sc2355_tx_special_data(&skb,&dev)==NETDEV_TX_OK && commands==1);
    return 0;
}
'''


def main():
    kernel = Path(sys.argv[1]).resolve()
    base = kernel/'drivers/unisoc_platform/sprd_wlan_combo'
    text = (base/'sc2355/tx.c').read_text()
    assert 'AP multicast uses the data queue' in text
    headers = (base/'common/iface.h').read_text()
    macros = []
    for name in ('IP_DNS', 'IPV4_DHCP', 'IPV6_DHCP'):
        start = headers.index('#define '+name)
        macros.append(headers[start:headers.index('\n\n',start)])
    # The helper returns a pointer, beyond the common extractor's type list.
    iface = (base/'common/iface.c').read_text()
    start = iface.index('struct udphdr *sprd_get_udphdr(')
    udp = iface[start:iface.index('\n}',start)+2]
    body = '\n'.join(function(text,name) for name in (
        'tx_filter_ip_pkt','sprd_tx_filter_packet','sc2355_tx_special_data'))
    with tempfile.TemporaryDirectory(prefix='e5-wifi-tx-') as directory:
        root = Path(directory)
        src = root/'test.c';binary=root/'test'
        src.write_text(STUBS+'\n'.join(macros)+'\n'+udp+'\n'+body+MAIN)
        subprocess.run(['cc','-std=gnu11','-Wall','-Werror','-Wno-pointer-sign',
                        '-fsanitize=address,undefined','-fno-pie','-no-pie',str(src),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
        target=root/'drivers/unisoc_platform/sprd_wlan_combo/sc2355/tx.c'
        target.parent.mkdir(parents=True);target.write_text(text)
        apply(root);assert target.read_text()==text,'Not idempotent'
        target.write_text(text+'\nstatic int tx_mc_pkt(void) { return 0; }\n')
        before=target.read_bytes()
        try: apply(root)
        except ValueError: pass
        else: raise AssertionError('Partial source adaptation was accepted')
        assert target.read_bytes()==before
    print('PASS: production IPv4/IPv6 AP/GO/STA filters, multicast/unicast, checksums, DNS/DHCP/VoWiFi/EAPOL/RTSP and idempotent adaptation')


if __name__ == '__main__':
    main()
