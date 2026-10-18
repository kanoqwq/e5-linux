#!/usr/bin/env python3
"""Send the exact bring-up archive over the direct 192.168.9.x USB link."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--root-archive', type=Path, required=True)
    args = parser.parse_args()
    info = json.loads(args.image.with_suffix('.json').read_text())
    if info.get('profile') != 't512n' or info.get('ram_boot') is not True:
        raise ValueError('Expected a T512N RAM bring-up manifest')
    checksum = hashlib.sha256()
    with args.root_archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    if checksum.hexdigest() != info['root_archive_sha256']:
        raise ValueError('Root archive differs from the boot image manifest')
    port = info['transfer_port']
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid USB receiver port')
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        connection = socket.socket()
        connection.settimeout(2)
        try:
            # Bind explicitly: never send this device's property/runtime files
            # through the computer's ordinary Internet or Wi-Fi connection.
            connection.bind(('192.168.9.2', 0))
            connection.connect(('192.168.9.1', port))
            break
        except OSError:
            connection.close()
            time.sleep(2)
    else:
        raise TimeoutError('RAM receiver did not appear on the direct USB link')
    with connection:
        connection.settimeout(30)
        sent = reported = 0
        with args.root_archive.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                connection.sendall(chunk)
                sent += len(chunk)
                if sent - reported >= 32 * 1024 * 1024:
                    print('USB transfer:', sent // (1024 * 1024), 'MiB', flush=True)
                    reported = sent
        connection.shutdown(socket.SHUT_WR)
        try:
            connection.recv(1)
        except socket.timeout:
            pass
    print('Archive sent; check OpenWrt startup separately. Bytes:', sent)


if __name__ == '__main__':
    main()
