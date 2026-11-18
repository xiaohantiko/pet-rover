"""Fetch opt-in third-party weights; never add them to Git."""
import argparse
import hashlib
from pathlib import Path
import tempfile
from urllib.request import urlopen

BASE = 'https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/bb17b6c3eef36d80be441ae8e5339be66e8e3b7a/'
FILES = [('deploy.prototxt', 'deploy.prototxt', '2d180f723b3109e21f8287f6b3c691390d07b60eed998327cd3259ffa0e50608'), ('mobilenet_iter_73000.caffemodel', 'model.bin', '52eed8be80522c152a17fb56740de705b79881bde1a167e0e747310523685fc7')]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.directory) as temp:
        for remote, local, expected in FILES:
            target = Path(temp) / local
            with urlopen(BASE + remote, timeout=60) as response:
                target.write_bytes(response.read())
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise SystemExit('Checksum mismatch: ' + remote)
            target.replace(args.directory / local)

if __name__ == '__main__':
    main()
