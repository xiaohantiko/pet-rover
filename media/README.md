# 用真实视频做一本本地宠物日记

这是独立离线工具：处理你主动选择的**本地视频**，不打开相机、不发到云端、不驱动车轮。控制台里的模拟猫与这里的真实检测是两条不同路径。先用短视频；帧时间按帧数和帧率换算，优先使用恒定帧率素材。

1. 安装 Python 3.9–3.12 和带 `libx264` 的 FFmpeg；确认 `ffmpeg -version`。
2. 从公开仓库根目录，在 macOS/Linux 执行：

   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install -r media/requirements.txt
   python media/get_weights.py weights
   python media/pet_diary.py /你的目录/视频.mp4 weights captures/第一次 --room 客厅
   ```

3. 打开新目录中的 `diary.json`。每个事件对应未经画框遮挡的 JPG 和无声 MP4；零事件也会生成 JSON。再次运行须换一个尚不存在的输出目录，不覆盖相册。如果中途异常，保留报错供诊断，部分输出不算成功。
4. 建议先将相机固定、原地停车再采集。约每秒取两帧，同类别连续出现至少三次才记录，最多按15秒分段；选帧兼顾检测分数与目标区域清晰度。保留事件前后各约2秒（受素材边界限制）。同一帧只保留分数最高的猫或狗，不能区分个体；房间来自手填，不是视觉定位。姿态、睡眠、进食及健康均不推断。
5. 若漏检，先检查是否过暗、太小或被挡；可用 `--threshold 0.5` 与默认 0.6 对比，观察误报。分数不是准确率。修改模型后用自己的**获准使用**正负样本重做评测，家庭样本不要进 Git。

模型取自 [MobileNet-SSD 原项目](https://github.com/chuanqi305/MobileNet-SSD)，下载脚本固定提交和两份 SHA-256。公开库不分发权重或训练素材，使用者需核对上游使用条件。移除权重或输出仅影响本工具，不影响仿真。

**摄像头→短视频→事件**：先在同一电脑运行 `python3 camera_preview.py --source pattern`，另开终端执行 `python3 media/observe.py captures/observation.mp4 --seconds 8`，按提示只粘贴当次地址 `#key=` 后面的口令，即可从本机预览服务以4帧/秒留存8秒无声观察。用上面的日记命令处理生成的 MP4。测试图没有宠物，预期零事件；经在场人员同意，才将预览源改为 USB 或 Pi 相机。工具不对外提供相册、不发送运动指令，也不自动启动录音。
