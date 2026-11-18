"""Pet rover bench simulator. No GPIO, serial, motors or charging outputs."""
import argparse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time


MAP = (
    "#############",
    "#....#......#",
    "#....#......#",
    "#...........#",
    "#....#......#",
    "###.####.####",
    "#...........#",
    "#...........#",
    "#############",
)
DOCK = (1, 1)
PET = (10, 3)
VISITS = ((3, 3), (8, 6), (9, 3))
CELL_M = 0.5
FOOTPRINT_RADIUS = 0.25
LEASE_S = 3.0
DEADMAN_S = 0.45
CAMERA_RANGE = 2.1
CAMERA_HALF_ANGLE = math.radians(60)
LIVE = Path(__file__).with_name("console.html")


def cell(x, y):
    return math.floor(x), math.floor(y)


def centre(point):
    return point[0] + 0.5, point[1] + 0.5


def free(point):
    col, row = point
    return 0 <= row < len(MAP) and 0 <= col < len(MAP[0]) and MAP[row][col] == "."


def clear_body(x, y):
    for row in range(math.floor(y - FOOTPRINT_RADIUS), math.floor(y + FOOTPRINT_RADIUS) + 1):
        for col in range(math.floor(x - FOOTPRINT_RADIUS), math.floor(x + FOOTPRINT_RADIUS) + 1):
            if free((col, row)):
                continue
            near_x = min(max(x, col), col + 1)
            near_y = min(max(y, row), row + 1)
            if math.hypot(x - near_x, y - near_y) <= FOOTPRINT_RADIUS:
                return False
    return True


def route(start, goal):
    if not free(start) or not free(goal):
        return None
    queue, previous = deque([start]), {start: None}
    while queue:
        current = queue.popleft()
        if current == goal:
            path = []
            while current != start:
                path.append(current)
                current = previous[current]
            return list(reversed(path))
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            nxt = current[0] + dx, current[1] + dy
            if nxt not in previous and free(nxt):
                previous[nxt] = current
                queue.append(nxt)
    return None


def angle_diff(left, right):
    return math.atan2(math.sin(left - right), math.cos(left - right))


def zone(point):
    return "南区" if point[1] >= 5 else ("西区" if point[0] < 5 else "东区")


class Events:
    def __init__(self, db):
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE IF NOT EXISTS event
            (id INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT, t REAL, kind TEXT,
             zone TEXT, label TEXT, image TEXT)""")

    def add(self, now, kind, location, label, image):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.conn:
            cursor = self.conn.execute("INSERT INTO event(utc,t,kind,zone,label,image) VALUES (?,?,?,?,?,?)",
                                       (stamp, round(now, 2), kind, location, label, image))
            self.conn.execute("DELETE FROM event WHERE id <= ?", (cursor.lastrowid - 300,))
        return cursor.lastrowid

    def recent(self):
        return [dict(row) for row in self.conn.execute(
            "SELECT id,utc,t,kind,zone,label FROM event ORDER BY id DESC LIMIT 15")]

    def image(self, event_id):
        row = self.conn.execute("SELECT image FROM event WHERE id=?", (event_id,)).fetchone()
        return row[0] if row else None


class Rejected(ValueError):
    pass


class Rover:
    def __init__(self, events, pet=PET):
        self.events, self.pet = events, pet
        self.x, self.y = centre(DOCK)
        self.heading = self.now = self.distance = 0.0
        self.battery, self.mode, self.note = 80.0, "idle", "台架仿真已就绪"
        self.owner, self.expires, self.motion_until = None, 0.0, 0.0
        self.linear = self.angular = 0.0
        self.path, self.visits = deque(), deque()
        self.scanning, self.hits = 0.0, 0.0
        self.bumps = 0

    def still(self):
        self.linear = self.angular = self.motion_until = 0.0

    def brake(self, mode, note):
        self.still()
        self.path.clear()
        self.visits.clear()
        self.scanning = self.hits = 0.0
        self.mode, self.note = mode, note

    def set_path(self, goal):
        start = cell(self.x, self.y)
        path = route(start, goal)
        if path is None:
            self.brake("fault", "模拟地图上无可达路径")
            return False
        self.path = deque([centre(start)] + [centre(point) for point in path])
        return True

    def return_home(self, reason):
        self.brake("return", reason)
        self.set_path(DOCK)

    def sees_pet(self):
        if self.pet is None:
            return False
        px, py = centre(self.pet)
        dx, dy = px - self.x, py - self.y
        dist = math.hypot(dx, dy)
        if dist > CAMERA_RANGE or abs(angle_diff(math.atan2(dy, dx), self.heading)) > CAMERA_HALF_ANGLE:
            return False
        samples = max(2, math.ceil(dist * 25))
        return all(free(cell(self.x + dx * index / samples, self.y + dy * index / samples))
                   for index in range(samples + 1))

    def preview(self):
        pet_icon = ""
        if self.sees_pet():
            px, py = centre(self.pet)
            bearing = angle_diff(math.atan2(py - self.y, px - self.x), self.heading)
            offset = round(280 + 200 * math.tan(bearing) / math.tan(CAMERA_HALF_ANGLE))
            pet_icon = (f'<g transform="translate({offset},145)" fill="#e3af76">'
                        '<path d="M-34,-8 l-4,-38 26,17 Q0,-33 12,-28 l26,-18 -4,38 '
                        'Q44,29 0,33 Q-44,29 -34,-8Z"/><g fill="#172835">'
                        '<circle cx="-13" cy="0" r="3"/><circle cx="13" cy="0" r="3"/>'
                        '<path d="M-5,10 h10 l-5,5Z"/></g></g>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="560" height="260" viewBox="0 0 560 260">'
                '<rect width="560" height="260" fill="#203b49"/>'
                '<path d="M0,138 H560 V260 H0Z" fill="#2f4d56"/>'
                '<path d="M0,260 L220,138 M560,260 L340,138 M0,190 H560" stroke="#799395"/>'
                f'{pet_icon}<g fill="white" font-family="sans-serif" font-size="15">'
                '<text x="16" y="25">SIMULATED VIEW • NOT A CAMERA</text>'
                f'<text x="16" y="242">t={self.now:.1f}s · heading={math.degrees(self.heading)%360:.0f}°'
                f' · cat={"visible" if self.sees_pet() else "not in view"}</text></g></svg>')

    def capture(self, kind):
        label = "模拟猫 / 趴卧（场景预设）" if self.sees_pet() else "视野内未见模拟猫"
        location = self.pet if self.sees_pet() else cell(self.x, self.y)
        return self.events.add(self.now, kind, zone(location), label, self.preview())

    def command(self, client, action, value=None):
        if action == "stop":
            self.brake("stopped", "急停已锁定；需驾驶者显式复位")
            return
        if action == "claim":
            if self.owner not in (None, client) and self.now < self.expires:
                raise Rejected("另一设备持有驾驶权")
            if self.owner != client:
                self.still()
            self.owner, self.expires = client, self.now + LEASE_S
            return
        if self.owner != client or self.now >= self.expires:
            raise Rejected("请先取得驾驶权")
        self.expires = self.now + LEASE_S
        if action == "ping":
            return
        if action == "release":
            self.still()
            self.owner = None
            return
        if action == "capture":
            self.capture("manual")
            return
        if self.mode == "stopped":
            if action != "reset":
                raise Rejected("急停锁定中")
            self.brake("idle", "已复位；不会自动恢复上一个命令")
            return
        if self.mode == "depleted":
            raise Rejected("模拟电量耗尽；请重启实验")
        if action == "low":
            self.battery = min(self.battery, 23.0)
            self.return_home("注入低电测试：中止任务，返回模拟基座")
        elif action == "home":
            self.return_home("驾驶者请求返航")
        elif action == "search":
            if self.battery <= 25:
                raise Rejected("低电量禁止寻宠")
            self.brake("search", "按观察点巡查模拟房间")
            self.visits.extend(VISITS)
            self.set_path(self.visits.popleft())
        elif action == "drive":
            if value not in ("forward", "back", "left", "right", "neutral"):
                raise ValueError("无效方向")
            if value == "neutral":
                if self.mode == "manual":
                    self.brake("idle", "手动停车")
                return
            if self.battery <= 25:
                raise Rejected("返航电量保留；禁止遥控行走")
            self.brake("manual", "遥控模式")
            self.linear = {"forward": 0.9, "back": -0.55}.get(value, 0.0)
            self.angular = {"left": -1.7, "right": 1.7}.get(value, 0.0)
            self.motion_until = self.now + DEADMAN_S
        else:
            raise ValueError("未知命令")

    def move(self, nx, ny):
        if not clear_body(nx, ny):
            self.bumps += 1
            self.brake("idle", "模拟几何护栏触发停车")
            return False
        self.distance += math.hypot(nx - self.x, ny - self.y) * CELL_M
        self.x, self.y = nx, ny
        return True

    def tick(self, interval):
        if not math.isfinite(interval) or interval <= 0:
            return
        # Elapsed-clock deadlines are not scaled; delayed ticks never catch up motion.
        self.now += interval
        dt = min(interval, 0.15)
        if self.owner and self.now >= self.expires:
            self.owner = None
            self.still()
        if self.mode == "charging":
            self.battery = min(100.0, self.battery + dt * 1.5)
            return
        if self.mode in ("stopped", "fault", "depleted"):
            return
        if self.battery <= 0.0:
            self.brake("depleted", "模拟电量耗尽；未确认充电")
            return
        if self.battery <= 25 and self.mode != "return":
            self.return_home("低电自动返航")
        if self.mode == "manual":
            if self.now + 1e-9 >= self.motion_until:
                self.brake("idle", "遥控指令超时，已停车")
            else:
                self.heading += self.angular * dt
                self.move(self.x + math.cos(self.heading) * self.linear * dt,
                          self.y + math.sin(self.heading) * self.linear * dt)
        elif self.mode in ("search", "return"):
            if self.path:
                target_x, target_y = self.path[0]
                dx, dy = target_x - self.x, target_y - self.y
                distance = math.hypot(dx, dy)
                if distance < 1e-6:
                    self.path.popleft()
                else:
                    self.heading = math.atan2(dy, dx)
                    step = min(1.0, dt * 1.3 / distance)
                    self.move(self.x + step * dx, self.y + step * dy)
            elif self.mode == "return":
                if math.dist((self.x, self.y), centre(DOCK)) < 1e-5:
                    self.brake("charging", "仅模拟：已到基座中心，虚拟充电中")
                else:
                    self.brake("fault", "未到达基座中心")
            else:
                self.heading += math.pi / 4 * dt
                self.scanning += dt
                self.hits = self.hits + dt if self.sees_pet() else 0.0
                if self.hits >= 0.35:
                    self.capture("found")
                    self.brake("found", "已记录模拟猫的场景姿态，不代表真实 AI 识别")
                elif self.scanning >= 8:
                    self.scanning = self.hits = 0.0
                    if self.visits:
                        self.set_path(self.visits.popleft())
                    else:
                        self.brake("not_found", "可达观察点巡查结束：未发现，不代表家中没有宠物")
        self.battery = max(0.0, self.battery - dt *
                           (0.035 if self.mode in ("manual", "return", "search") else 0.006))

    def state(self, client):
        return {"simulation": True, "mode": self.mode, "note": self.note, "battery": round(self.battery, 2),
                "t": round(self.now, 2), "xy": [round(self.x, 3), round(self.y, 3)],
                "heading": round(self.heading, 4), "map": MAP, "dock": DOCK, "room": zone(cell(self.x, self.y)),
                "control": "mine" if self.owner == client else "busy" if self.owner else "free",
                "events": self.events.recent(), "bumps": self.bumps, "distance_m": round(self.distance, 3)}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bind, robot, token):
        super().__init__(bind, Handler)
        self.robot, self.token, self.lock = robot, token, threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "PetRoverSimulation/1"

    def log_message(self, fmt, *args):
        pass

    def send(self, status, mime, data):
        payload = data.encode("utf-8") if isinstance(data, str) else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def reply(self, status, data):
        self.send(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False))

    def auth(self):
        value = self.headers.get("Authorization", "")
        if not secrets.compare_digest(value.encode("utf-8"), ("Bearer " + self.server.token).encode()):
            self.reply(401, {"error": "缺少本次启动口令或口令已失效"})
            return None
        client = self.headers.get("X-Client", "")
        if not re.fullmatch("[a-f0-9]{32}", client):
            self.reply(400, {"error": "无效控制台编号"})
            return None
        return client

    def do_GET(self):
        if self.path == "/":
            self.send(200, "text/html; charset=utf-8", LIVE.read_bytes())
            return
        client = self.auth()
        if client is None:
            return
        robot = self.server.robot
        if self.path == "/api/state":
            with self.server.lock:
                state = robot.state(client)
            self.reply(200, state)
            return
        with self.server.lock:
            if self.path == "/api/preview":
                image = robot.preview()
            elif re.fullmatch(r"/api/events/[1-9][0-9]{0,9}", self.path):
                image = robot.events.image(int(self.path.rsplit("/", 1)[1]))
            else:
                image = None
        if image is None:
            self.reply(404, {"error": "资源不存在"})
        else:
            self.send(200, "image/svg+xml; charset=utf-8", image)

    def do_POST(self):
        client = self.auth()
        if client is None:
            return
        if self.path != "/api/command":
            self.reply(404, {"error": "资源不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= 2048:
                raise ValueError("请求长度不正确")
            data = json.loads(self.rfile.read(length).decode())
            if not isinstance(data, dict) or not isinstance(data.get("action"), str):
                raise ValueError("命令格式不正确")
            with self.server.lock:
                self.server.robot.command(client, data["action"], data.get("value"))
                state = self.server.robot.state(client)
        except Rejected as exc:
            self.reply(409, {"error": str(exc)})
            return
        except (ValueError, TypeError) as exc:
            self.reply(400, {"error": str(exc)})
            return
        self.reply(200, state)


def main():
    parser = argparse.ArgumentParser(description="宠物小车：无真实执行器的局域网仿真")
    parser.add_argument("--bind", default="127.0.0.1", help="本机回环或受信任局域网 IPv4")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=Path.home() / ".pet-rover" / "sim")
    parser.add_argument("--no-pet", action="store_true", help="运行无宠物场景")
    args = parser.parse_args()
    lan = [ipaddress.ip_network(block) for block in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    try:
        address = ipaddress.IPv4Address(args.bind)
        if not any(address in subnet for subnet in lan):
            raise ValueError()
    except ValueError:
        parser.error("仅允许回环或 RFC1918 局域网 IPv4；不要开放公网")
    args.data.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Events(args.data / "events.sqlite3")
    robot = Rover(store, None if args.no_pet else PET)
    token = secrets.token_urlsafe(24)
    server = Server((args.bind, args.port), robot, token)
    done = threading.Event()

    def clock_loop():
        last = time.monotonic()
        while not done.wait(0.05):
            now = time.monotonic()
            with server.lock:
                robot.tick(now - last)
            last = now

    clock = threading.Thread(target=clock_loop, daemon=True)
    clock.start()
    print("仅仿真，不连接车体。仅将此链接分享给受信任的同网家庭成员。", flush=True)
    print(f"http://{args.bind}:{server.server_port}/#key={token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        clock.join()
        server.server_close()


if __name__ == "__main__":
    main()
