"""Read a local video file; export evidence for stationary-camera cat/dog sightings."""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import cv2
import numpy as np

def detect(net, frame, threshold):
    height, width = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(frame, 1 / 127.5, (300, 300), 127.5)
    net.setInput(blob)
    pets = []
    for row in net.forward().reshape(-1, 7):
        label, score = int(row[1]), float(row[2])
        if label not in (8, 12) or score < threshold:
            continue
        left, top, right, bottom = np.clip(row[3:7], 0, 1) * [width, height, width, height]
        if right - left < 2 or bottom - top < 2:
            continue
        pets.append((score, 'cat' if label == 8 else 'dog', tuple(map(int, (left, top, right, bottom)))))
    return max(pets, default=None)

def run(source, weights, output, room, threshold):
    if not source.is_file() or not shutil.which('ffmpeg'):
        raise SystemExit('Need a local video file and FFmpeg on PATH')
    capture = cv2.VideoCapture(str(source))
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not capture.isOpened() or not math.isfinite(fps) or fps <= 0:
        raise SystemExit('Invalid video or frame rate')
    net = cv2.dnn.readNetFromCaffe(str(weights / 'deploy.prototxt'), str(weights / 'model.bin'))
    output.mkdir(parents=True, exist_ok=False)
    stride = max(1, round(fps / 2))
    events, group = [], []
    best = None
    frame_no = samples = 0

    def flush():
        nonlocal best
        if len(group) >= 3:
            stem = f'event-{len(events) + 1:03}'
            start = max(0.0, group[0][0] - 2)
            clip_length = group[-1][0] - start + 2
            if not cv2.imwrite(str(output / (stem + '.jpg')), best[1]):
                raise OSError('JPEG write failed')
            subprocess.run(['ffmpeg','-loglevel','error','-nostdin','-ss',str(start),'-i',str(source),'-t',str(clip_length),'-map','0:v:0','-an','-vf','scale=trunc(iw/2)*2:trunc(ih/2)*2','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',str(output / (stem + '.mp4'))], check=True)
            events.append(dict(species=group[0][1], room=room, start_seconds=round(group[0][0],2), last_seen_seconds=round(group[-1][0],2), state='持续入镜，姿态未判定', samples=len(group), best_confidence=round(max(item[2] for item in group),4), snapshot=stem+'.jpg', clip=stem+'.mp4'))
        group.clear()
        best = None

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_no % stride == 0:
                samples += 1
                result = detect(net, frame, threshold)
                stamp = frame_no / fps
                if group and (not result or result[1] != group[-1][1] or stamp-group[0][0] >= 15):
                    flush()
                if result:
                    confidence, species, box = result
                    left, top, right, bottom = box
                    sharpness = cv2.Laplacian(cv2.cvtColor(frame[top:bottom,left:right],cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()
                    rank = confidence * (1 + min(sharpness, 1000) / 1000)
                    if best is None or rank > best[0]:
                        best = rank, frame.copy()
                    group.append((stamp,species,confidence))
            frame_no += 1
        flush()
    finally:
        capture.release()
    if not frame_no:
        raise SystemExit('No frames decoded')
    report = dict(model='MobileNet-SSD VOC', threshold=threshold, frames=frame_no, sampled=samples, events=events)
    (output/'diary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('weights', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--room', default='未标注')
    parser.add_argument('--threshold', type=float, default=0.60)
    args = parser.parse_args()
    if not 0 < args.threshold <= 1:
        parser.error('threshold must be in (0,1]')
    run(args.source, args.weights, args.output, args.room, args.threshold)
