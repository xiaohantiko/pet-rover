"""Read-only camera bench. Defaults to generated bars, not a real camera."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import re
import secrets
import shutil
import subprocess
import threading
import time


PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><link rel="icon" href="data:,"><title>只读镜头台</title>
<style>body{font:16px system-ui,sans-serif;background:#edf3ef;color:#25403c;max-width:760px;margin:24px auto;padding:0 16px}img{display:block;width:100%;min-height:140px;background:#213a42}button{font:inherit;padding:10px;margin:12px 0}p{line-height:1.6}</style>
<h1>只读镜头台</h1><p id="status">正在连接…</p><img id="view" alt="当前输入画面"><button id="save" disabled>保存当前帧到本设备</button><p>无录音、无运动控制；服务端只在内存保留最新帧。选择实体摄像头时，它会持续采集，直到你在终端按 Ctrl+C。</p>
<script>
const fragment=new URLSearchParams(location.hash.slice(1)).get('key');
if(fragment){sessionStorage.setItem('cameraKey',fragment);history.replaceState({},'',location.pathname)}
const token=sessionStorage.getItem('cameraKey'),view=document.getElementById('view'),statusBox=document.getElementById('status'),save=document.getElementById('save');
let current=null;
function clearFrame(){view.removeAttribute('src');if(current)URL.revokeObjectURL(current);current=null;save.disabled=true}
async function update(){
 if(!token){statusBox.textContent='请打开本次终端显示的完整专属链接。';return}
 try{
 const res=await fetch('/frame.jpg',{headers:{Authorization:'Bearer '+token},cache:'no-store'});
 if(!res.ok)throw Error(res.status===401?'无访问权限；请重新使用本次启动链接。':'没有新鲜帧：请检查终端和输入设备。');
 const next=URL.createObjectURL(await res.blob()),previous=current;current=next;view.src=next;if(previous)URL.revokeObjectURL(previous);
 statusBox.textContent=(res.headers.get('X-Input')==='pattern'?'合成测试图（未打开摄像头）':'实体摄像头采集中')+' · 帧 '+res.headers.get('X-Frame');save.disabled=false;
 }catch(err){clearFrame();statusBox.textContent=err.message}
 setTimeout(update,200);
}
save.onclick=()=>{if(current){const link=document.createElement('a');link.download='pet-rover-frame.jpg';link.href=current;link.click()}};
update();
</script></html>"""


def listener_address(value, allow_lan):
    ip = ipaddress.IPv4Address(value)
    if ip.is_loopback:
        return str(ip)
    if allow_lan and any(ip in ipaddress.ip_network(net) for net in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        return str(ip)
    raise ValueError("仅回环地址，或 --share-lan 加明确的家庭网 IPv4；禁止通配和公网地址")


def commands(source, device):
    ff = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    upstream = None
    if source == "pattern":
        source_args = ["-re", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=5"]
    elif source == "usb":
        if not re.fullmatch(r"/dev/video[0-9]+", device):
            raise ValueError("USB 输入仅接受 Linux /dev/videoN（N 为设备编号）")
        source_args = ["-f", "v4l2", "-i", device]
    elif source == "pi":
        upstream = ["rpicam-vid", "-t", "0", "--nopreview", "--width", "640", "--height", "360",
                    "--framerate", "5", "--codec", "mjpeg", "-o", "-"]
        source_args = ["-f", "mjpeg", "-probesize", "32", "-analyzeduration", "0", "-i", "pipe:0"]
    else:
        raise ValueError("不支持该输入")
    encode = ["-c:v", "copy"] if source == "pi" else [
        "-vf", "fps=5,scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "mjpeg", "-q:v", "5"]
    return upstream, ff + source_args + ["-an"] + encode + ["-f", "mpjpeg", "-flush_packets", "1", "pipe:1"]


def jpeg_parts(stream):
    while True:
        line = stream.readline(128)
        if not line:
            return
        if line.strip() != b"--ffmpeg":
            continue
        length = 0
        for _ in range(8):
            line = stream.readline(128)
            if not line or line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        if not 0 < length <= 4_000_000:
            raise ValueError("摄像头帧长度无效")
        frame = stream.read(length)
        if len(frame) != length or not (frame.startswith(b"\xff\xd8") and frame.endswith(b"\xff\xd9")):
            raise ValueError("摄像头帧不完整")
        yield frame


class Frames:
    def __init__(self, source, device="/dev/video0"):
        self.source, self.running, self.fail = source, [], None
        self.frame, self.seq, self.when = None, 0, 0
        self.cond, self.reader = threading.Condition(), None
        self.commands = commands(source, device)

    def start(self):
        upstream, encode = self.commands
        for cmd in (upstream, encode):
            if cmd and not shutil.which(cmd[0]):
                raise RuntimeError(f"缺少本机命令：{cmd[0]}")
        pipe = None
        try:
            if upstream:
                cam = subprocess.Popen(upstream, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
                self.running.append(cam)
                pipe = cam.stdout
            proc = subprocess.Popen(encode, stdin=pipe or subprocess.DEVNULL, stdout=subprocess.PIPE)
            self.running.append(proc)
            if pipe:
                pipe.close()

            def consume():
                try:
                    for frame in jpeg_parts(proc.stdout):
                        with self.cond:
                            self.frame, self.when = frame, time.monotonic()
                            self.seq += 1
                            self.cond.notify_all()
                except (OSError, ValueError) as exc:
                    self.fail = str(exc)
                finally:
                    with self.cond:
                        self.fail = self.fail or "输入已中断；不再展示最后一帧"
                        self.frame = None
                        self.cond.notify_all()

            self.reader = threading.Thread(target=consume, daemon=True)
            self.reader.start()
            with self.cond:
                self.cond.wait_for(lambda: self.seq or self.fail, timeout=12)
                if not self.seq or self.fail:
                    raise RuntimeError(self.fail or "等待首帧超时，请检查设备和权限")
        except BaseException:
            self.close()
            raise

    def latest(self):
        with self.cond:
            return (self.seq, self.frame if time.monotonic() - self.when <= 2 else None)

    def close(self):
        for proc in reversed(self.running):
            if proc.poll() is None:
                proc.terminate()
        for proc in reversed(self.running):
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self.reader:
            self.reader.join(timeout=2)
        for proc in self.running:
            if proc.stdout:
                proc.stdout.close()
        self.running.clear()


class CameraServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bind, frames, token):
        super().__init__(bind, CameraHandler)
        self.frames, self.token = frames, token


class CameraHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        seq = None
        if self.path == "/":
            status, mime, body = 200, "text/html; charset=utf-8", PAGE.encode()
        elif not secrets.compare_digest(self.headers.get("Authorization", "").encode(),
                                        ("Bearer " + self.server.token).encode()):
            status, mime, body = 401, "text/plain", b"Unauthorized"
        elif self.path != "/frame.jpg":
            status, mime, body = 404, "text/plain", b"Not found"
        else:
            seq, frame = self.server.frames.latest()
            status, mime, body = (200, "image/jpeg", frame) if frame else (503, "text/plain", b"No fresh frame")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if seq is not None:
            self.send_header("X-Frame", str(seq))
            self.send_header("X-Input", self.server.frames.source)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description="只读摄像台；默认生成测试图，不使用真实设备")
    parser.add_argument("--source", choices=("pattern", "pi", "usb"), default="pattern")
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--share-lan", action="store_true", help="明确同意向指定家庭网接口提供只读服务")
    options = parser.parse_args()
    try:
        bind = listener_address(options.bind, options.share_lan)
        frames = Frames(options.source, options.device)
        token = secrets.token_urlsafe(32)
        with CameraServer((bind, options.port), frames, token) as server:
            print("准备生成测试图。" if options.source == "pattern" else "准备打开实体镜头；按 Ctrl+C 停止采集。", flush=True)
            if not ipaddress.ip_address(bind).is_loopback:
                print("仅限可信家庭 Wi-Fi：HTTP 不加密，持链接者可观看。禁止端口映射。", flush=True)
            frames.start()
            try:
                print(f"http://{bind}:{server.server_port}/#key={token}", flush=True)
                server.serve_forever()
            finally:
                frames.close()
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
