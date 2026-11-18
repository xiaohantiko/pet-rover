"""Local half-duplex voice bench. No robot commands or persistent recordings."""
import argparse
import array
import hmac
import io
import json
import math
import os
import secrets
import shutil
import ssl
import struct
import subprocess
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RATE = 16000
CAPTURE_S = 2
MAX_S = 4
MAX_BYTES = 44 + RATE * 2 * MAX_S
PAGE = Path(__file__).with_name("panel.html")


def wav_bytes(pcm):
    result = io.BytesIO()
    with wave.open(result, "wb") as writer:
        writer.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        writer.writeframes(pcm)
    return result.getvalue()


def unpack_wav(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            frames = reader.getnframes()
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(),
                    reader.getcomptype()) != (1, 2, RATE, "NONE") or not 0 < frames <= RATE * MAX_S:
                raise ValueError("need mono 16kHz 16-bit PCM, 0–4 seconds")
            pcm = reader.readframes(frames)
    except (EOFError, wave.Error, struct.error) as exc:
        raise ValueError("invalid WAV") from exc
    if len(pcm) != frames * 2:
        raise ValueError("truncated WAV")
    return pcm


def synthetic(seconds, frequency=440):
    return b"".join(struct.pack("<h", round(1600 * math.sin(index * 2 * math.pi * frequency / RATE)
                                         * min(1, index / 200, (seconds * RATE - index) / 200)))
                    for index in range(int(seconds * RATE)))


class AudioBench:
    def __init__(self, record_device=None, play_device=None):
        self.record_device = record_device
        self.play_device = play_device
        self.lock = threading.Lock()

    def listen(self):
        if not self.record_device:
            return synthetic(CAPTURE_S)
        result = subprocess.run(["arecord", "-q", "-D", self.record_device, "-t", "raw",
                                 "-f", "S16_LE", "-c", "1", "-r", str(RATE), "-d", str(CAPTURE_S)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                check=True, timeout=CAPTURE_S + 2)
        if len(result.stdout) != RATE * CAPTURE_S * 2:
            raise OSError("unexpected number of PCM frames")
        return result.stdout

    def play(self, pcm):
        samples = array.array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        for index, value in enumerate(samples):
            samples[index] = round(value * 0.15)
        peak = max(abs(value) for value in samples)
        if sys.byteorder != "little":
            samples.byteswap()
        if self.play_device:
            subprocess.run(["aplay", "-q", "-D", self.play_device, "-t", "raw", "-f", "S16_LE",
                            "-c", "1", "-r", str(RATE)], input=samples.tobytes(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, timeout=MAX_S + 2)
        return {"frames": len(samples), "peak_pcm": peak,
                "sink": "ALSA" if self.play_device else "simulated"}


def handler_type(token, bench):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(7)

        def log_message(self, *_):
            pass

        def reply(self, status, value, mime="application/json"):
            content = json.dumps(value).encode() if mime == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(content)

        def authorized(self):
            supplied = self.headers.get("Authorization", "").encode("utf-8")
            return hmac.compare_digest(supplied, ("Bearer " + token).encode("utf-8"))

        def do_GET(self):
            if self.path == "/":
                return self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path != "/api/status":
                return self.reply(404, {"error": "not found"})
            if not self.authorized():
                return self.reply(401, {"error": "token"})
            self.reply(200, {"mic": "ALSA" if bench.record_device else "synthetic",
                             "speaker": "ALSA" if bench.play_device else "simulated",
                             "max_s": MAX_S, "capture_s": CAPTURE_S})

        def do_POST(self):
            if self.path not in ("/api/listen", "/api/talk", "/api/tone"):
                return self.reply(404, {"error": "not found"})
            if not self.authorized():
                return self.reply(401, {"error": "token"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, {"error": "length"})
            if self.headers.get("Transfer-Encoding") or not 0 <= length <= MAX_BYTES:
                return self.reply(413, {"error": "size"})
            if (self.path == "/api/talk") != bool(length):
                return self.reply(400, {"error": "expected WAV only for talk"})
            if not bench.lock.acquire(blocking=False):
                return self.reply(409, {"error": "busy"})
            try:
                if self.path == "/api/listen":
                    return self.reply(200, wav_bytes(bench.listen()), "audio/wav")
                if self.path == "/api/talk":
                    if self.headers.get_content_type() != "audio/wav":
                        return self.reply(415, {"error": "expected audio/wav"})
                    pcm = unpack_wav(self.rfile.read(length))
                else:
                    pcm = synthetic(0.35, 520)
                self.reply(200, bench.play(pcm))
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
            except (OSError, subprocess.SubprocessError):
                self.reply(503, {"error": "audio unavailable or timed out"})
            finally:
                bench.lock.release()
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--record-device")
    parser.add_argument("--play-device")
    parser.add_argument("--allow-live-audio", action="store_true")
    parser.add_argument("--cert")
    parser.add_argument("--key")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("supply both certificate and private key")
    if args.bind not in ("127.0.0.1", "localhost") and not args.cert:
        parser.error("non-loopback requires HTTPS --cert and --key")
    if (args.record_device or args.play_device) and not args.allow_live_audio:
        parser.error("explicit --allow-live-audio required")
    for device, binary in ((args.record_device, "arecord"), (args.play_device, "aplay")):
        if device and not shutil.which(binary):
            parser.error(binary + " missing (alsa-utils required on Raspberry Pi)")
    token = os.environ.get("PET_AUDIO_TOKEN")
    if token and (len(token) < 24 or not token.isascii() or not token.isprintable() or " " in token):
        parser.error("PET_AUDIO_TOKEN requires at least 24 printable ASCII characters without spaces")
    token = token or secrets.token_urlsafe(32)
    server = ThreadingHTTPServer((args.bind, args.port), handler_type(token, AudioBench(args.record_device, args.play_device)))
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    if not os.environ.get("PET_AUDIO_TOKEN"):
        print("One-session audio key (share privately):", token, flush=True)
    print(f"{'https' if args.cert else 'http'}://{args.bind}:{server.server_port} • Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
