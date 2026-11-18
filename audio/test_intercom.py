import http.client
import io
import json
import pathlib
import sys
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import intercom


class TestAudioHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bench = intercom.AudioBench()
        cls.token = "audio-test-only-not-a-real-key"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), intercom.handler_type(cls.token, cls.bench))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def req(self, method, path, body=None, token=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        conn.request(method, "/api/" + path, body, {"Authorization": "Bearer " + (token or self.token),
                                                    "Content-Type": "audio/wav"})
        result = conn.getresponse()
        status, data = result.status, result.read()
        conn.close()
        return status, data

    def test_synthetic_roundtrip(self):
        self.assertEqual(json.loads(self.req("GET", "status")[1])["mic"], "synthetic")
        status, audio = self.req("POST", "listen")
        self.assertEqual(status, 200)
        with wave.open(io.BytesIO(audio)) as wav:
            self.assertEqual((wav.getnframes(), wav.getframerate(), wav.getnchannels()), (32000, 16000, 1))
        status, receipt = self.req("POST", "talk", audio)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(receipt), {"sink": "simulated", "frames": 32000, "peak_pcm": 240})

    def test_auth_tone_and_bounds(self):
        self.assertEqual(self.req("POST", "listen", token="wrong")[0], 401)
        self.assertEqual(json.loads(self.req("POST", "tone")[1])["frames"], 5600)
        self.assertEqual(self.req("POST", "talk", b"not wav")[0], 400)
        self.assertEqual(self.req("POST", "talk", b"x" * (intercom.MAX_BYTES + 1))[0], 413)

    def test_single_owner(self):
        with self.bench.lock:
            self.assertEqual(self.req("POST", "listen")[0], 409)
            self.assertEqual(self.req("POST", "tone")[0], 409)
        self.assertEqual(self.req("POST", "tone")[0], 200)

    def test_bad_wave_and_truncation(self):
        good = intercom.wav_bytes(b"\x00\x00" * 20)
        self.assertEqual(self.req("POST", "talk", good[:-4])[0], 400)
        bad = bytearray(good)
        bad[22:24] = b"\x02\x00"
        self.assertEqual(self.req("POST", "talk", bad)[0], 400)


if __name__ == "__main__":
    unittest.main()
