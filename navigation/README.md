# ROS 2 / Nav2 任务适配器：先只读，再用独立仿真

此目录把“巡查地点→看到宠物中止→低电取消巡查→对接→确认充电”写成可测的状态机，并提供 ROS 2 Jazzy 的适配代码。不是自带机器人、地图或驱动的 Nav2 部署，不连接网页遥控、固件或真实电池。**已执行的是无 ROS 的 mock 测试；没有运行 ROS 图、Gazebo、真车或真实充电。**

## 1. 无 ROS 即可验证

在公开仓库目录运行：

```sh
python3 navigation/nav2_adapter.py
python3 -m unittest discover -s tests -p test_navigation.py -v
```

默认离线合成台架。首条意图为 `navigate`；注入低电后变成 `cancel`，收到原目标终态才产生 `dock`。模拟对接动作成功本身不是“正在充电”，必须有动作之后的新电池报文、充电状态及连续至少 2 秒的有效证据；未确认超时进入故障。输出秒数为脚本推演，不等于 Nav2 路径时长。

## 2. 只读观察集成

以下均在另行搭建的 Ubuntu/ROS 2 Jazzy 测试系统上运行。本仓库没有附带 Gazebo 世界、雷达驱动、URDF/TF、底盘控制器、AMCL/地图服务器、Nav2 配置、充电插件或实物对齐标定。装好与发行版匹配的 ROS/Nav2 后，依 **本机** 安装路径加载 `setup.bash`。先使用无实车桥接的单机仿真，所有仿真进程统一设置：

```sh
export ROS_DOMAIN_ID=79
export ROS_LOCALHOST_ONLY=1
source /opt/ros/jazzy/setup.bash
ros2 topic list
ros2 action list -t
python3 navigation/nav2_adapter.py observe
```

观察模式只订阅以下业务话题，不构造导航/对接 ActionClient、不发布速度或初始位姿。观察模式需要 `/clock` 前进才能显示 ready（专为模拟时钟），而非真实车时钟探针。检查生命周期节点已 active；在 RViz 的**虚构地图**上完成初始定位；检查数据确在 `/pet_sim` 命名空间，不在默认根话题。适配器不替你启动 Nav2。

| 输入 | 类型与要求 |
|---|---|
| `/clock` | `rosgraph_msgs/msg/Clock`，仿真时间前进；倒退锁止本次任务 |
| `/pet_sim/map` | `nav_msgs/msg/OccupancyGrid`，静态持久 QoS，frame=`map`，平面地图。未知、越界及占据值 ≥50 不作为观察点 |
| `/pet_sim/amcl_pose` | `geometry_msgs/msg/PoseWithCovarianceStamped`，与地图相同坐标系；作为入门门槛，x/y/yaw 方差均须 ≤0.25（单位分别是 m²、m²、rad²）；不是精度保证 |
| `/pet_sim/battery_state` | `sensor_msgs/msg/BatteryState`，present；percentage 是 0～1，不是 0～100；只有“charging 且有限正充电电流”可参与确认 |
| `/pet_sim/confirmed_pet` | `std_msgs/msg/Bool`，由外部感知/模拟器提供**已确认**发现，不是 AI 检测本身；适配器不会从图片猜姿态 |
| `/pet_sim/abort_mission` | `std_msgs/msg/Bool`，收到 true 锁止任务，不因后来的 false 擅自恢复 |

姿态和电池报文带与 `/clock` 一致的时间戳；时间或接收新鲜度超过 5 秒会拒绝或取消任务。仿真暂停或 AMCL 只在发生位移时发位姿可能造成保守取消；应提供可靠定位状态或改进适配，不要用重复旧时间戳绕过检查。静态地图使用 reliable + transient-local；电池/clock 使用 sensor-data QoS。先用 `ros2 topic info -v <话题>` 核对两端。

## 3. 隔离模拟器发送目标（未在此环境运行）

**不要在有真车驱动的同一 DDS domain 或有桥接的局域网运行。** `ROS_LOCALHOST_ONLY` 和单独 domain 只是防误连，不是安全隔离。同机真实的驱动也必须断开。Nav2 自带的演示未必提供模拟充电站或电池插件；必须先构建并验证仿真地图、障碍代价地图、定位、两个动作服务器、充电传感器与模拟基座数据库。

```sh
ros2 interface show nav2_msgs/action/NavigateToPose
ros2 interface show nav2_msgs/action/DockRobot
ros2 action info /pet_sim/navigate_to_pose
ros2 action info /pet_sim/dock_robot
python3 navigation/nav2_adapter.py nav2-sim --ack-isolated-sim --point 1.5 0.5 0 --dock-id synthetic_dock
```

示例点只是虚构米制坐标/弧度航向；改成自己模拟地图中宽敞的观察点，可重复 `--point X Y YAW_RAD`。基座 ID 必须来自你仿真中的 docking server 数据库；适配器按 ID 请求服务器先导航到对接前位置。坐标栅格预检不能证明足够轮廓间隙或可达性；静态/局部代价地图、机器人半径、防跌落等是下层职责。动作定义依据 [Jazzy NavigateToPose 源码](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_msgs/action/NavigateToPose.action)、[Jazzy DockRobot 源码](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_msgs/action/DockRobot.action)。个别系统提供旧动作包，不要猜测类型，先用 `ros2 action list -t` 核对。

模拟验收顺序：先巡查一个点；到点停留观察 3 秒，无确认则换下一个；路径失败则故障而非跳过。随后保持输入有效，模拟产生 `confirmed_pet=true` 或发送中止；若仍有在途目标，应观察 cancel → 原动作终态 → 任务结束。再开一轮，在移动中将**模拟**电池降到 23%，必须是 cancel → 终态 → dock，不允许两个并行动作；充电消息停发或电流不正不得宣称充电。没有找到只表示这些观察点未确认，不是“家里没宠物”。

在独立模拟器测试任务中止：`ros2 topic pub --once /pet_sim/abort_mission std_msgs/msg/Bool '{data: true}'`。程序 Ctrl+C 也会请求取消，但**动作取消不是硬件急停**。超过 5 秒没有可靠终态，适配器停发新目标并报告无法确认；操作者应停止模拟器后端。实车迁移需要全套设备驱动、状态适配、指令仲裁、独立看门狗与现场验收，不应直接把本程序接上轮子。
