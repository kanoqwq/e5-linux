"""Validate packaged ABIs and Android ARM64 16 KiB LOAD alignment."""
import io
import struct
import zipfile


def elf_alignment(data):
    if data[:6]!=b'\x7fELF\x02\x01':raise ValueError('Expected little-endian ELF64')
    offset=struct.unpack_from('<Q',data,32)[0]
    size,count=struct.unpack_from('<HH',data,54)
    if size<56 or not count or offset+size*count>len(data):raise ValueError('Invalid ELF program headers')
    return [struct.unpack_from('<Q',data,offset+i*size+48)[0] for i in range(count) if struct.unpack_from('<I',data,offset+i*size)[0]==1]


def validate(path,target):
    abi={'aarch64':'arm64-v8a','x86_64':'x86_64'}[target]
    checked=[]
    def inspect(name,data):
        if target=='aarch64' and any(value<16384 for value in elf_alignment(data)):
            raise ValueError('Android library lacks 16 KiB LOAD alignment: '+name)
        checked.append(name)
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate APK entries')
        abis={n.split('/')[1] for n in names if n.startswith('lib/') and n.endswith('.so')}
        if abis!={abi} or 'lib/'+abi+'/libopenwrt_tools_mobile.so' not in names:
            raise ValueError('APK ABI differs from selected target: '+str(abis))
        for name in names:
            if name.startswith('lib/') and name.endswith('.so'):inspect(name,archive.read(name))
            elif name.startswith('assets/chaquopy/') and name.endswith('.so'):inspect(name,archive.read(name))
            elif name.startswith('assets/chaquopy/') and name.endswith('.imy'):
                with zipfile.ZipFile(io.BytesIO(archive.read(name))) as wheels:
                    for member in wheels.namelist():
                        if member.endswith('.so'):inspect(name+'/'+member,wheels.read(member))
    return dict(abi=abi,native_libraries=len(checked),page_size=16384 if target=='aarch64' else 4096)
