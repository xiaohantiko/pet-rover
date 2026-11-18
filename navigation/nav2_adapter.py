"""Default is a local mock. ROS actions are available for an isolated simulation only."""
import argparse
import json
import math
import os
import signal
import time

try:
    from .mission import Grid, Inputs, Mission, demonstration
except ImportError:
    from mission import Grid, Inputs, Mission, demonstration


def number_stamp(stamp):
    return stamp.sec + stamp.nanosec/1e9


def yaw(orientation):
    return math.atan2(2*(orientation.w*orientation.z + orientation.x*orientation.y),
                      1 - 2*(orientation.y**2 + orientation.z**2))


def ros_run(args):
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
    from rclpy.signals import SignalHandlerOptions
    from action_msgs.msg import GoalStatus
    from geometry_msgs.msg import PoseWithCovarianceStamped
    from nav_msgs.msg import OccupancyGrid
    from rosgraph_msgs.msg import Clock
    from sensor_msgs.msg import BatteryState
    from std_msgs.msg import Bool

    drive_sim = args.mode == "nav2-sim"
    if drive_sim:
        from nav2_msgs.action import NavigateToPose, DockRobot
    state = Inputs()
    policy = Mission(args.point,args.dock_id)
    stamp, stop = None, False
    rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
    node = Node("pet_mission_adapter",namespace="/pet_sim")
    static = QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,
                        reliability=ReliabilityPolicy.RELIABLE)

    def clock(msg):
        nonlocal stamp
        if stamp is not None and number_stamp(msg.clock) < number_stamp(stamp):
            state.abort = True
        if stamp is None or number_stamp(msg.clock) > number_stamp(stamp):
            state.clock_at = time.monotonic()
        stamp = msg.clock

    def aligned(header):
        return stamp is not None and -.25 <= number_stamp(stamp)-number_stamp(header.stamp) <= 5

    def map_data(msg):
        info = msg.info
        ori = info.origin.orientation
        if (msg.header.frame_id != "map" or abs(ori.x)+abs(ori.y)>.01 or
                abs(ori.z**2+ori.w**2-1)>.02):
            state.grid = None
            return
        state.grid = Grid(info.width,info.height,info.resolution,info.origin.position.x,info.origin.position.y,
                          yaw(ori),msg.data)

    def pose(msg):
        coords = msg.pose.pose.position
        cov = [msg.pose.covariance[index] for index in (0,7,35)]
        if (msg.header.frame_id == "map" and aligned(msg.header)
                and all(math.isfinite(value) and 0<=value<=limit for value,limit in zip(cov,(.25,.25,.25)))):
            state.xy,state.pose_at = (coords.x,coords.y),time.monotonic()
        else:
            state.pose_at = -math.inf

    def battery(msg):
        if not msg.present or not aligned(msg.header):
            state.battery_at = -math.inf
            return
        state.fraction,state.battery_at = msg.percentage,time.monotonic()
        state.charging = (msg.power_supply_status == BatteryState.POWER_SUPPLY_STATUS_CHARGING
                          and math.isfinite(msg.current) and msg.current > 0)

    def pet(msg):
        state.found = bool(msg.data)

    def abort(msg):
        state.abort = state.abort or bool(msg.data)

    node.create_subscription(Clock,"/clock",clock,qos_profile_sensor_data)
    node.create_subscription(OccupancyGrid,"map",map_data,static)
    node.create_subscription(PoseWithCovarianceStamped,"amcl_pose",pose,static)
    node.create_subscription(BatteryState,"battery_state",battery,qos_profile_sensor_data)
    node.create_subscription(Bool,"confirmed_pet",pet,10)
    node.create_subscription(Bool,"abort_mission",abort,10)
    clients = {}
    if drive_sim:
        clients = {"navigate":ActionClient(node,NavigateToPose,"navigate_to_pose"),
                   "dock":ActionClient(node,DockRobot,"dock_robot")}
    handle, waiting, cancel_wanted = None, False, False

    def on_end(future):
        nonlocal handle,waiting,cancel_wanted
        try:
            wrapped = future.result()
            status = "canceled" if wrapped.status == GoalStatus.STATUS_CANCELED else "failed"
            if (wrapped.status == GoalStatus.STATUS_SUCCEEDED and
                    not getattr(wrapped.result,"error_code",0) and getattr(wrapped.result,"success",True)):
                status = "ok"
            policy.result(status,time.monotonic())
        except Exception as exc:
            policy.result("failed",time.monotonic())
            node.get_logger().error(f"结果读取失败：{exc}")
        finally:
            handle,waiting,cancel_wanted = None,False,False

    def request_cancel():
        nonlocal cancel_wanted
        cancel_wanted = True
        if handle:
            handle.cancel_goal_async()

    def accepted(future):
        nonlocal handle,waiting
        try:
            handle = future.result()
            if not handle.accepted:
                handle,waiting = None,False
                policy.result("failed",time.monotonic())
                return
            handle.get_result_async().add_done_callback(on_end)
            if cancel_wanted:
                request_cancel()
        except Exception as exc:
            handle,waiting = None,False
            policy.result("failed",time.monotonic())
            node.get_logger().error(f"目标提交失败：{exc}")

    def act(effect):
        nonlocal waiting,cancel_wanted
        kind,value = effect
        if kind=="cancel":
            request_cancel()
            return
        if waiting or not clients[kind].server_is_ready():
            policy.result("failed",time.monotonic())
            return
        if kind=="dock":
            goal = DockRobot.Goal()
            goal.use_dock_id,goal.dock_id = True,value
            goal.navigate_to_staging_pose,goal.max_staging_time = True,60.0
        else:
            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id,goal.pose.header.stamp = "map",stamp
            goal.pose.pose.position.x,goal.pose.pose.position.y = value[:2]
            goal.pose.pose.orientation.z = math.sin(value[2]/2)
            goal.pose.pose.orientation.w = math.cos(value[2]/2)
        waiting,cancel_wanted = True, False
        clients[kind].send_goal_async(goal).add_done_callback(accepted)

    def interrupt(_sig,_frame):
        nonlocal stop
        stop=True

    prior = signal.signal(signal.SIGINT,interrupt)
    previous, previous_log = None, -math.inf
    try:
        while rclpy.ok():
            rclpy.spin_once(node,timeout_sec=.1)
            now=time.monotonic()
            if drive_sim:
                state.abort = state.abort or stop
                effect=policy.tick(state,now)
                if effect:
                    act(effect)
                if policy.mode != previous:
                    print(policy.mode,policy.note,flush=True)
                    previous=policy.mode
                if policy.mode in policy.terminal:
                    break
            else:
                if stop:
                    break
                if now-previous_log>=2:
                    print(json.dumps({"read_only":True,"map_received":state.grid is not None,
                                      "ready":bool(state.ready(now)),"pose_valid":now-state.pose_at<=5,
                                      "battery_valid":0<state.fraction<=1 and now-state.battery_at<=5,
                                      "clock_advancing":now-state.clock_at<=5}),flush=True)
                    previous_log=now
    finally:
        if waiting and rclpy.ok():
            request_cancel()
            end=time.monotonic()+2
            while waiting and rclpy.ok() and time.monotonic()<end:
                rclpy.spin_once(node,timeout_sec=.1)
            if waiting:
                print("无法确认动作已停止，请停止仿真后端。",flush=True)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        signal.signal(signal.SIGINT,prior)
    return 1 if drive_sim and policy.mode=="fault" else 0


def main():
    parser=argparse.ArgumentParser(description="台架任务适配器；默认 mock；禁止与真实车桥接")
    parser.add_argument("mode",nargs="?",default="demo",choices=("demo","observe","nav2-sim"))
    parser.add_argument("--point",type=float,nargs=3,action="append",default=[],metavar=("X","Y","YAW_RAD"))
    parser.add_argument("--dock-id",default="synthetic_dock")
    parser.add_argument("--ack-isolated-sim",action="store_true")
    args=parser.parse_args()
    if args.mode=="demo":
        print(json.dumps(demonstration(),indent=2))
        return 0
    if args.mode=="nav2-sim" and not (args.ack_isolated_sim and args.point and
            os.environ.get("ROS_LOCALHOST_ONLY")=="1" and os.environ.get("ROS_DOMAIN_ID")=="79"):
        parser.error("动作模式仅限独立模拟器：须 --ack-isolated-sim、--point、ROS_LOCALHOST_ONLY=1、ROS_DOMAIN_ID=79")
    try:
        return ros_run(args)
    except ImportError as exc:
        parser.exit(2,f"缺少已 source 的 ROS 2 Jazzy/相关接口：{exc}。可不带参数运行 demo。\n")


if __name__ == "__main__":
    raise SystemExit(main())
