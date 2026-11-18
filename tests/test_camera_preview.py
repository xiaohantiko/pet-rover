import http.client
import io
import shutil
import threading
import time
import unittest

from camera_preview import CameraServer, Frames, commands, jpeg_parts, listener_address


class CameraPolicyTests(unittest.TestCase):
    def test_explicit_family_network_opt_in(self):
        self.assertEqual(listener_address("127.0.0.1",False),"127.0.0.1")
        self.assertEqual(listener_address("192.168.1.5",True),"192.168.1.5")
        for address, option in (("192.168.1.5",False),("0.0.0.0",True),("8.8.8.8",True)):
            with self.assertRaises(ValueError):
                listener_address(address,option)

    def test_explicit_local_sources_only(self):
        with self.assertRaises(ValueError):
            commands("usb","rtsp://example.invalid")
        self.assertEqual(commands("pi","")[0][0],"rpicam-vid")
        self.assertIn("testsrc2=size=640x360:rate=5",commands("pattern","")[1])
        self.assertIn("/dev/video0",commands("usb","/dev/video0")[1])

    def test_incomplete_capture_is_not_exposed(self):
        broken=io.BytesIO(b"--ffmpeg\r\nContent-length: 12\r\n\r\n\xff\xd8")
        with self.assertRaises(ValueError):
            list(jpeg_parts(broken))
        source=Frames("pattern")
        source.frame,source.when=b"stale",time.monotonic()-3
        self.assertIsNone(source.latest()[1])

    @unittest.skipUnless(shutil.which("ffmpeg"),"optional FFmpeg not installed")
    def test_real_ffmpeg_pattern_through_authenticated_http(self):
        frames = Frames("pattern")
        frames.start()
        self.addCleanup(frames.close)
        server = CameraServer(("127.0.0.1",0),frames,"fixture-key")
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join,2)
        self.addCleanup(server.shutdown)

        def get(path, auth):
            connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3)
            try:
                headers={"Authorization":"Bearer fixture-key"} if auth else {}
                connection.request("GET",path,headers=headers)
                response=connection.getresponse()
                return response.status,response.getheader("X-Frame"),response.read()
            finally:
                connection.close()

        self.assertEqual(get("/frame.jpg",False)[0],401)
        self.assertEqual(get("/api/command",True)[0],404)
        self.assertIn("只读镜头台".encode(),get("/",False)[2])
        status, sequence, jpeg = get("/frame.jpg",True)
        self.assertEqual(status,200)
        self.assertTrue(jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9"))
        with frames.cond:
            self.assertTrue(frames.cond.wait_for(lambda: frames.seq > int(sequence),timeout=3))
        status, next_sequence, next_jpeg = get("/frame.jpg",True)
        self.assertEqual(status,200)
        self.assertGreater(int(next_sequence),int(sequence))
        self.assertNotEqual(jpeg,next_jpeg)


if __name__ == "__main__":
    unittest.main()
