#!/usr/bin/python3
"""Charge-only fbdev view: no compositor, web server or device model labels."""
import array
import fcntl
import os
from pathlib import Path
import struct
import subprocess
import time
import zipfile

ASSETS = Path('/usr/share/e5-charge/art.zip')
SYS = Path('/sys')
VISIBLE_SECONDS = 15


def blit(frame, sprite, x, y, width, height):
    for row in range(height):
        dst=((y+row)*320+x)*4
        src=row*width*4
        for col in range(width):
            s=src+col*4;d=dst+col*4;alpha=sprite[s+3]
            if alpha==255:
                frame[d:d+4]=sprite[s:s+4]
            elif alpha:
                for c in range(3):
                    frame[d+c]=(sprite[s+c]*alpha+frame[d+c]*(255-alpha))//255


class Artwork:
    def __init__(self, path=ASSETS, language='zh'):
        self.archive=zipfile.ZipFile(path)
        self.language='zh' if language=='zh' else 'en'
        self.base=self.read('base-'+self.language)
        self.bolts=[self.read('bolt-'+str(n)) for n in range(16)]
        self.previous=None

    def read(self,name):
        return self.archive.read(name+'.rgba')

    def frame(self, capacity, status, phase):
        capacity=max(0,min(100,int(capacity)))
        status='charging' if status=='Charging' else 'full' if status=='Full' else 'paused'
        if (capacity,status)!=self.previous:
            self.still=bytearray(self.base)
            for name,rect in [('battery-'+str(capacity),(112,113,96,148)),
                              ('number-'+str(capacity),(0,291,320,76)),
                              ('status-'+self.language+'-'+status,(0,363,320,32))]:
                blit(self.still,self.read(name),*rect)
            self.previous=(capacity,status)
        result=self.still.copy()
        if status!='paused':
            blit(result,self.bolts[phase%16 if status=='charging' else 15],138,158,44,64)
        # fbdev's XRGB8888 is little endian B,G,R,X in memory.
        result[0::4],result[2::4]=result[2::4],result[0::4]
        return result

    def close(self):
        self.archive.close()


class Framebuffer:
    """Fit the shared artwork without changing its aspect ratio or pixel format."""
    def __init__(self, var, stride):
        width,height,virtual_width,virtual_height,xoffset,yoffset,bpp = struct.unpack_from('@7I',var)
        if (bpp!=32 or xoffset or not 320<=width<=4096 or not 480<=height<=4096
                or not width<=virtual_width<=4096
                or not height+yoffset<=virtual_height<=4096):
            raise RuntimeError('unsupported charging framebuffer geometry')
        if struct.unpack_from('@9I',var,32)!=(16,8,0,8,8,0,0,8,0):
            raise RuntimeError('unsupported charging framebuffer pixel format')
        if stride<virtual_width*4 or stride%4 or stride*virtual_height>64*1024*1024:
            raise RuntimeError('unsupported charging framebuffer stride')
        self.stride=stride;self.height=height;self.offset=yoffset*stride
        self.scale=min(width//320,height//480)
        self.left=(width-320*self.scale)//2
        self.top=(height-480*self.scale)//2
        self.native=(width,height,stride)==(320,480,1280)
        self.canvas=None if self.native else bytearray(stride*height)

    def frame(self, source):
        if len(source)!=320*480*4:
            raise ValueError('invalid charging artwork size')
        if self.native:return source
        scale=self.scale
        row=bytearray(320*4*scale)
        for y in range(480):
            src=source[y*1280:(y+1)*1280]
            # Expand channels with C-level slice copies, not per-pixel Python
            # loops. Keep the padded/letterboxed area black on larger panels.
            for copy in range(scale):
                for channel in range(4):
                    row[copy*4+channel::4*scale]=src[channel::4]
            start=(self.top+y*scale)*self.stride+self.left*4
            for copy in range(scale):
                at=start+copy*self.stride
                self.canvas[at:at+len(row)]=row
        return self.canvas


class Display:
    def __init__(self, backlights):
        self.fd=None;self.tty=None;self.mode=None;self.art=None
        self.backlights=backlights;self.lit=False
        self.until=0;self.next_frame=0;self.next_status=0;self.phase=0
        self.capacity=0;self.status='Unknown'
        try:
            self.fd=os.open('/dev/fb0',os.O_RDWR|os.O_CLOEXEC)
            var=bytearray(160);fcntl.ioctl(self.fd,0x4600,var)
            stride=int((SYS/'class/graphics/fb0/stride').read_text())
            self.framebuffer=Framebuffer(var,stride)
            lang=subprocess.run(['uci','-q','get','e5-infoscreen.main.lang'],
                                capture_output=True,text=True,timeout=3).stdout.strip() or 'zh'
            self.art=Artwork(language=lang)
            self.tty=os.open('/dev/tty0',os.O_RDWR|os.O_CLOEXEC)
            mode=array.array('i',[0]);fcntl.ioctl(self.tty,0x4b3b,mode)
            self.mode=mode[0];fcntl.ioctl(self.tty,0x4b3a,1)  # KD_GRAPHICS
            self.show(time.monotonic())
        except BaseException:
            self.close()
            raise

    def light(self,level):
        for path in self.backlights:
            path.write_text(str(level)+'\n')
        self.lit=level>0

    def show(self,now):
        self.until=now+VISIBLE_SECONDS
        self.next_frame=0

    def tick(self,now):
        if now>=self.until:
            if self.lit:self.light(0)
            return
        if now<self.next_frame:return
        if now>=self.next_status:
            battery=SYS/'class/power_supply/battery'
            try:
                self.capacity=int((battery/'capacity').read_text())
                self.status=(battery/'status').read_text().strip()
            except (OSError,ValueError):
                self.status='Unknown'
            self.next_status=now+2
        frame=self.framebuffer.frame(self.art.frame(self.capacity,self.status,self.phase))
        if os.pwrite(self.fd,frame,self.framebuffer.offset)!=len(frame):
            raise OSError('incomplete charging frame')
        if not self.lit:self.light(150)
        self.phase+=1;self.next_frame=now+.125

    def close(self):
        if self.art:self.art.close();self.art=None
        if self.tty is not None:
            try:
                if self.mode is not None:fcntl.ioctl(self.tty,0x4b3a,self.mode)
            finally:
                os.close(self.tty);self.tty=None
        if self.fd is not None:os.close(self.fd);self.fd=None
        self.light(0)
