"""Grab an explicitly requested bounded observation from the loopback preview."""
import argparse
import getpass
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--seconds', type=int, choices=range(3, 31), default=8)
    args = parser.parse_args()
    if args.output.suffix.lower() != '.mp4' or args.output.exists() or not shutil.which('ffmpeg'):
        parser.error('Choose a NEW .mp4 file; FFmpeg required')
    token = getpass.getpass('Local preview key (after #key=): ')
    request = urllib.request.Request(f'http://127.0.0.1:{args.port}/frame.jpg', headers={'Authorization':'Bearer '+token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory() as directory:
        start = time.monotonic()
        for index in range(args.seconds * 4):
            with opener.open(request, timeout=1) as response:
                jpeg = response.read(2_000_001)
                if response.headers.get_content_type() != 'image/jpeg' or len(jpeg) > 2_000_000 or not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
                    raise SystemExit('Invalid frame; observation cancelled')
            (Path(directory) / f'{index:04d}.jpg').write_bytes(jpeg)
            remaining = start + (index + 1) / 4 - time.monotonic()
            if remaining < -0.5:
                raise SystemExit('Capture fell behind; observation cancelled')
            time.sleep(max(0, remaining))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-n','-framerate','4','-i',str(Path(directory)/'%04d.jpg'),'-vf','scale=trunc(iw/2)*2:trunc(ih/2)*2','-c:v','libx264','-pix_fmt','yuv420p',str(args.output)],check=True)
    print(args.output)

if __name__ == '__main__':
    main()
