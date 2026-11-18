"""Single-flight task policy, independent of ROS or motor drivers."""
from dataclasses import dataclass
import math


@dataclass
class Grid:
    width: int
    height: int
    metres: float
    ox: float
    oy: float
    yaw: float
    cells: object

    def permits(self, x, y):
        if not all(math.isfinite(v) for v in (x,y,self.metres,self.ox,self.oy,self.yaw)) or self.metres <= 0:
            return False
        dx, dy = x - self.ox, y - self.oy
        cosine, sine = math.cos(self.yaw), math.sin(self.yaw)
        col = math.floor((dx*cosine + dy*sine)/self.metres)
        row = math.floor((-dx*sine + dy*cosine)/self.metres)
        return (len(self.cells) == self.width*self.height and 0 <= col < self.width and
                0 <= row < self.height and 0 <= self.cells[row*self.width+col] < 50)


@dataclass
class Inputs:
    grid: object = None
    xy: tuple = (float("nan"),float("nan"))
    pose_at: float = -math.inf
    battery_at: float = -math.inf
    clock_at: float = -math.inf
    fraction: float = float("nan")
    charging: bool = False
    found: bool = False
    abort: bool = False

    def ready(self, now):
        return (self.grid and self.grid.permits(*self.xy) and 0 < self.fraction <= 1 and
                all(0 <= now-mark <= 5 for mark in (self.pose_at,self.battery_at,self.clock_at)))


class Mission:
    terminal = {"found","not_found","aborted","fault","charging"}

    def __init__(self, points, dock_id):
        self.points, self.dock_id = list(points), dock_id
        self.mode, self.active, self.after = "wait_inputs", None, None
        self.deadline, self.entered = None, None
        self.charge_first, self.note = None, ""

    def fail(self, reason):
        self.mode, self.note = "fault", reason
        return None

    def dispatch(self, kind, payload, now):
        self.mode = "to_point" if kind == "navigate" else "to_dock"
        self.active, self.deadline = kind, now + 120
        return kind, payload

    def interrupt(self, target, now):
        self.after = target
        if self.active:
            self.mode, self.deadline = "cancel_pending", now+5
            return "cancel", None
        self.mode = target
        return None

    def tick(self, sensed, now):
        if self.mode in self.terminal:
            return None
        if self.mode == "cancel_pending":
            if sensed.abort:
                self.after = "aborted"
            elif not sensed.ready(now):
                self.after = "fault"
            if now >= self.deadline:
                return self.fail("未收到目标终态；无法确认机器人已停止，不再发新目标")
            return None
        if sensed.abort:
            return self.interrupt("aborted", now)
        if self.mode == "wait_inputs":
            if self.entered is None:
                self.entered=now
            if not sensed.ready(now):
                if now-self.entered >= 30:
                    return self.fail("启动后未收到有效地图、定位、时钟和电池")
                return None
            self.mode = "choose"
        if not sensed.ready(now):
            self.note = "姿态、电池或时钟失效/位置越界；不继续"
            return self.interrupt("fault",now)
        if self.active and now >= self.deadline:
            self.note = "任务超时；取消后停止"
            return self.interrupt("fault",now)
        if self.mode not in ("to_dock","need_dock","verify") and sensed.fraction <= .25:
            if self.active:
                return self.interrupt("need_dock",now)
            self.mode = "need_dock"
        if self.mode == "need_dock":
            return self.dispatch("dock",self.dock_id,now)
        if self.mode not in ("to_dock","verify") and sensed.found:
            return self.interrupt("found",now)
        if self.mode == "watch" and now >= self.deadline:
            self.mode = "choose"
        if self.mode == "choose":
            if not self.points:
                self.mode = "not_found"
                return None
            point = self.points.pop(0)
            if len(point) != 3 or not all(math.isfinite(v) for v in point) or not sensed.grid.permits(*point[:2]):
                return self.fail("观察点不在已知可通行栅格中")
            return self.dispatch("navigate",point,now)
        if self.mode == "verify":
            if now >= self.deadline:
                return self.fail("模拟对接动作已结束，但没有新鲜且持续的充电状态")
            if not sensed.charging or sensed.battery_at <= self.entered:
                self.charge_first = None
            elif self.charge_first is None:
                self.charge_first = sensed.battery_at
            elif sensed.battery_at - self.charge_first >= 2:
                self.mode = "charging"
        return None

    def result(self, status, now):
        if self.active is None or self.mode in self.terminal:
            return
        self.active = None
        if self.mode == "cancel_pending":
            if status in ("canceled","ok"):
                self.mode = self.after
            else:
                self.fail("取消期间动作失败；不发送新目标")
            return
        if status != "ok":
            self.fail("动作被拒或失败")
        elif self.mode == "to_dock":
            self.mode,self.entered,self.deadline = "verify",now,now+12
        else:
            self.mode,self.deadline = "watch",now+3


def demonstration():
    inputs = Inputs(grid=Grid(3,2,1,0,0,0,[0]*6),xy=(.5,.5),fraction=.8)
    policy = Mission([(1.5,.5,0)],"synthetic_dock")
    trace = []
    for now in range(10):
        inputs.clock_at = inputs.pose_at = inputs.battery_at = now
        if now == 1:
            inputs.fraction = .23
        if now == 2:
            policy.result("canceled",now)
        if now == 4:
            policy.result("ok",now)
        if now >= 5:
            inputs.charging = True
        event=policy.tick(inputs,now)
        trace.append({"model_s":now,"mode":policy.mode,"intent":event})
        if policy.mode in policy.terminal:
            break
    return trace
