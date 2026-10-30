"""One topology/identity contract for eMMC and UFS installation paths."""
from pathlib import Path
import re
import sys


def disk_node(node):
    match = re.fullmatch(r'(mmcblk[0-9]+)p[1-9][0-9]*|(sd[a-z]+)[1-9][0-9]*', node)
    if not match:
        raise ValueError('Invalid internal partition / 内置分区节点无效: '+str(node))
    return match[1] or match[2]


def part_node(disk, number):
    if not re.fullmatch(r'mmcblk[0-9]+|sd[a-z]+', disk) or not isinstance(number,int) or number < 1:
        raise ValueError('Invalid disk/partition number / 磁盘或分区号无效')
    return disk+('p' if disk.startswith('mmcblk') else '')+str(number)


def family_for(root=None, family=None):
    if family is None:
        family = 'mu300' if root is not None and (Path(root)/'desktop/manifest.json').is_file() else 'e5'
    if family not in ('e5','mu300'):
        raise ValueError('Unknown installer storage family: ' + str(family))
    return family


def library(root=None, family=None):
    family = family_for(root, family)
    embedded = sys.modules.get('backend.host')
    if embedded is not None and embedded.current() is not None:
        path = embedded.resource('storage-' + family + '.sh')
    elif getattr(sys,'frozen',False):
        path = Path(sys._MEIPASS) / ('storage-' + family + '.sh')
    elif Path(__file__).parent.name == 'backend':
        path = Path(__file__).resolve().parents[1] / 'runtime' / ('storage-' + family + '.sh')
    else:
        # Exported CLI helpers carry their selected family library beside them.
        path = Path(__file__).with_name('storage-lib.sh')
    source = path.read_text(encoding='utf-8')
    names = ('parent','part','disk','identity','kind','sector_size')
    if not all(family+'_storage_'+name+'()' in source for name in names):
        raise ValueError('Storage library does not match the selected family')
    return source + '\n' + '\n'.join(
        'openwrt_storage_'+name+'() { '+family+'_storage_'+name+' "$@"; }' for name in names) + '\n'


def inventory_script(root, family=None):
    family = family_for(root, family)
    labels = 'mu300root e5root' if family == 'mu300' else 'e5root'
    return library(root, family)+'\necho FAMILY='+family+'\n'+r'''
set -e
d=$(readlink -f /dev/block/by-name/userdata)
n=${d##*/}
b=$(openwrt_storage_parent "$n")
[ "$(openwrt_storage_part userdata "$b")" = "$n" ]
echo NODE=$n
echo DISK=$b
echo STORAGE_KIND=$(openwrt_storage_kind "$b")
case $(openwrt_storage_kind "$b") in ufs) echo IDENTITY_KIND=@UFS_ID_KIND@ ;; emmc) echo IDENTITY_KIND=mmc-cid ;; esac
echo CID=$(openwrt_storage_identity "$b")
echo SECTOR_SIZE=$(openwrt_storage_sector_size "$b")
echo START=$(cat /sys/class/block/$n/start)
echo SIZE=$(cat /sys/class/block/$n/size)
echo DISK_SIZE=$(cat /sys/class/block/$b/size)
echo FREE=$(df -Pk /data | tail -n1 | awk '{print $4}')
echo STATE=$(getprop ro.crypto.state)
echo TYPE=$(getprop ro.crypto.type)
echo SOURCE=$(awk '$2=="/data" {print $1;exit}' /proc/mounts)
echo MAGIC=$(dd if="$d" bs=1 skip=1024 count=4 2>/dev/null | od -An -tx1 | tr -d ' \n')
echo EXT=$(dd if="$d" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')
for label in @ROOT_LABELS@; do
    candidate=$(openwrt_storage_part "$label" "$b") || continue
    [ -z "${root_found:-}" ] || { echo 'Multiple installed OpenWrt roots' >&2; exit 1; }
    root_found=$candidate
    echo ROOT=$candidate
done
'''.replace('@ROOT_LABELS@',labels).replace('@UFS_ID_KIND@','ufs-descriptor-v1' if family == 'e5' else 'ufs-sysfs-v1')


def parse_inventory(text):
    result = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
    disk = disk_node(result['NODE'])
    if result.get('DISK',disk) != disk or not re.fullmatch('[a-f0-9]{32}',result.get('CID','')):
        raise ValueError('Cannot establish storage identity / 无法确认存储身份')
    kind = result.get('STORAGE_KIND','emmc')
    if kind not in ('emmc','ufs') or (kind == 'emmc') != disk.startswith('mmcblk'):
        raise ValueError('Storage type/topology differs / 存储类型与拓扑不匹配')
    result['DISK'] = disk
    result['STORAGE_KIND'] = kind
    if kind=='ufs' and result.get('FAMILY','e5')=='e5' and result.get('IDENTITY_KIND')!='ufs-descriptor-v1':
        raise ValueError('UFS raw descriptor identity is required / UFS 必须使用原始描述符身份')
    result['SECTOR_SIZE'] = int(result.get('SECTOR_SIZE',512))
    if result['SECTOR_SIZE'] not in (512,4096):
        raise ValueError('Unsupported storage logical block size / 不支持的存储逻辑块大小')
    for key in ('START','SIZE','DISK_SIZE','FREE'):
        result[key] = int(result[key])
        if result[key] < 0:
            raise ValueError('Invalid storage geometry / 存储范围无效')
    factor = result['SECTOR_SIZE']//512
    if any(result[key] % factor for key in ('START','SIZE','DISK_SIZE')):
        raise ValueError('Storage alignment differs / 存储对齐不匹配')
    if not result['SIZE'] or result['START']+result['SIZE'] > result['DISK_SIZE']:
        raise ValueError('Partition exceeds its disk / 分区超出所在磁盘范围')
    if result.get('ROOT') and disk_node(result['ROOT']) != disk:
        raise ValueError('Root is on another disk / 根分区不在同一磁盘')
    return result


def gpt_header_commands(info, prefix='/dev/block/'):
    disk = prefix+disk_node(info['NODE'])
    block = info.get('SECTOR_SIZE',512)
    return (f'dd if={disk} bs={block} skip=1 count=1 2>/dev/null',
            f'dd if={disk} bs={block} skip={info["DISK_SIZE"]//(block//512)-1} count=1 2>/dev/null')


def runtime_identity_script(family='e5'):
    return library(family=family)+'\nd=$(openwrt_storage_disk) && openwrt_storage_identity "$d"'
