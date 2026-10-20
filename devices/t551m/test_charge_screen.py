import importlib.util
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('screen',HERE/'charge-screen.py')
screen=importlib.util.module_from_spec(spec);spec.loader.exec_module(screen)


def framebuffer(width=320,height=480,stride=None,virtual_height=None,yoffset=0):
    var=bytearray(160)
    struct.pack_into('@7I',var,0,width,height,width,virtual_height or height,0,yoffset,32)
    struct.pack_into('@9I',var,32,16,8,0,8,8,0,0,8,0)
    return screen.Framebuffer(var,stride or width*4)


class ChargeScreenTest(unittest.TestCase):
    def test_native_frame_is_unchanged(self):
        source=bytearray([1,2,3,255])*(320*480)
        self.assertIs(framebuffer().frame(source),source)

    def test_large_panel_centers_and_doubles_pixels(self):
        fb=framebuffer(720,1600)
        source=bytearray(320*480*4)
        source[:8]=bytes([1,2,3,255,4,5,6,255])
        source[-4:]=bytes([7,8,9,255])
        frame=fb.frame(source)
        self.assertEqual(len(frame),720*1600*4)
        self.assertEqual((fb.scale,fb.left,fb.top),(2,40,320))
        at=320*2880+40*4
        self.assertEqual(frame[at:at+16],source[:4]*2+source[4:8]*2)
        self.assertEqual(frame[at+2880:at+2880+16],frame[at:at+16])
        self.assertEqual(frame[:at],bytes(at))
        last=(320+959)*2880+(40+638)*4
        self.assertEqual(frame[last:last+8],source[-4:]*2)
        # Reusing the canvas must replace old pixels, not leave stale artwork.
        self.assertEqual(fb.frame(bytes(len(source)))[at:at+16],bytes(16))

    def test_padded_stride_and_page_offset(self):
        fb=framebuffer(320,480,1344,960,480)
        source=bytes([1,2,3,255])*(320*480)
        frame=fb.frame(source)
        self.assertEqual(fb.offset,480*1344)
        self.assertEqual(frame[:1280],source[:1280])
        self.assertEqual(frame[1280:1344],bytes(64))

    def test_rejects_invalid_framebuffer_and_artwork(self):
        for args in [(720,1600,100),(320,480,1281),(8192,1600),(200,480),
                     (320,480,1280,480,1)]:
            with self.subTest(args=args),self.assertRaises(RuntimeError):framebuffer(*args)
        var=bytearray(160)
        struct.pack_into('@7I',var,0,320,480,320,480,0,0,32)
        with self.assertRaisesRegex(RuntimeError,'pixel format'):screen.Framebuffer(var,1280)
        with self.assertRaises(ValueError):framebuffer().frame(b'bad')

    def test_frames_track_charge_and_animation_with_both_languages(self):
        for language in ['zh','en']:
            art=screen.Artwork(HERE/'charge-art.zip',language)
            try:
                low=art.frame(15,'Charging',0)
                self.assertEqual(len(low),320*480*4)
                self.assertNotEqual(low,art.frame(15,'Charging',8))
                self.assertNotEqual(low,art.frame(97,'Charging',0))
                self.assertEqual(art.frame(100,'Full',0),art.frame(100,'Full',8))
                self.assertNotEqual(art.frame(100,'Full',0),art.frame(100,'Not charging',0))
            finally:art.close()

    def test_screen_sleeps_and_short_press_only_wakes_display(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);battery=root/'class/power_supply/battery';battery.mkdir(parents=True)
            (battery/'capacity').write_text('73');(battery/'status').write_text('Charging')
            bl=root/'brightness';bl.write_text('0')
            view=screen.Display.__new__(screen.Display)
            view.fd=9;view.tty=None;view.mode=None;view.backlights=[bl];view.lit=False
            view.until=0;view.next_frame=0;view.next_status=0;view.phase=0
            view.capacity=0;view.status='Unknown'
            view.framebuffer=framebuffer()
            view.art=screen.Artwork(HERE/'charge-art.zip')
            with patch.object(screen,'SYS',root), \
                    patch.object(screen.os,'pwrite',side_effect=lambda fd,data,offset:len(data)) as write, \
                    patch.object(screen.os,'close'):
                view.show(10);view.tick(10)
                self.assertEqual(bl.read_text().strip(),'150')
                self.assertEqual(view.capacity,73)
                view.tick(25)
                self.assertEqual(bl.read_text().strip(),'0')
                count=write.call_count;view.tick(30)
                self.assertEqual(write.call_count,count)
                view.show(35);view.tick(35)
                self.assertEqual(bl.read_text().strip(),'150')
                view.close()
                self.assertEqual(bl.read_text().strip(),'0')


if __name__=='__main__':unittest.main()
