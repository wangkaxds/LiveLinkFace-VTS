# Live Link Face → VTube Studio

**免费、无广告的开源面捕转换工具，用 iPhone 的 Live Link Face 驱动你的 Live2D。**

手机发送 ARKit 面捕数据，本工具通过 VTube Studio 的公开插件 API 把它转换为模型输入。支持 Windows，手机端使用 iPhone / iPad 的 **Live Link（ARKit）** 模式，运行时无需安装或启动 Unreal Engine。

An MIT-licensed Windows bridge from Live Link Face's iOS ARKit UDP stream to the VTube Studio Public API. No ads, accounts, cloud relay, or usage-time limit in the bridge.

## 为什么使用它

- **免费、无广告**：转换工具没有订阅、付费解锁或使用时长限制。手机端 [Live Link Face](https://apps.apple.com/us/app/live-link-face/id1495370836) 也可免费下载。
- **无需虚幻引擎**：直接接入 VTube Studio，不用创建 UE 项目或运行引擎。
- **本地传输**：手机在局域网内发送数据，工具只连接本机 VTS；无需注册本工具账号或使用云端转发。
- **动作可调**：头部与视线归零、幅度、平滑程度和方向反转，可按坐姿和模型调整。
- **横屏面捕适配**：支持镜头在左、镜头在右和倒置摆放的手动方向补偿，方便使用发送方向未校正的旧版 Live Link Face。
- **状态可见**：实时数值、收发帧率、连接日志和模拟动作，方便检查连接问题。
- **源码公开**：可以查看实现、修改动作映射并贡献改进。

## 下载和启动

1. 安装 [Python 3.11](https://www.python.org/downloads/)，保留安装器默认的 tkinter 和 Python Launcher 组件。
2. 下载 [源码 ZIP](https://github.com/wangkaxds/LiveLinkFace-VTS/archive/refs/heads/main.zip)，或点击仓库 **Code → Download ZIP**，完整解压。
3. 双击 **启动面捕.bat**。首次启动会创建工具自己的 Python 环境并安装依赖，需要联网；以后直接双击即可。

保留 BAT 旁边的源码和 `requirements.txt`。BAT 会进入工具自身目录，从快捷方式启动也能找到文件。首次安装好依赖后，运行时只需要手机和电脑处于同一个局域网。

手机需支持 Live Link Face 的 ARKit 面捕。电脑需要安装 VTube Studio 并准备好 Live2D 模型；本项目提供的是面捕转换工具。

## 第一次使用

1. 打开 VTube Studio，载入模型。在设置中打开 **允许插件 API 访问 / Allow Plugin API access**，默认端口为 `8001`。
2. 手机与电脑在同一个局域网。打开 Live Link Face，Capture Mode 选择 **Live Link（ARKit）**，开启 **Stream Head Rotation**。
3. 手机的 Live Link 设置中选择 Add Target，填本工具显示的电脑局域网 IP，端口填 `11111`；回到主画面打开 LIVE 发送。多网卡时，选与手机在同一网段的 IP。
4. 点击本工具的 **开始连接**。VTS 弹出 `LiveLink Face to VTS` 授权请求时，点击 **允许**。
5. 手机和 VTS 两项都正常后，正对手机坐好并目视正中，点击 **头部与视线归零**，根据模型调整幅度。当前头部姿势和左右眼视线都会成为正中位置；眨眼开合不受归零影响。

手机目标 IP 以工具显示的电脑局域网地址为准；有多张网卡时，选择与手机位于同一网段的地址。

## 横放手机（旧版横屏面捕适配）

1. 把手机横着放，保持 ARKit / LIVE 发送开启。
2. 在工具「动作响应 → 手机方向」中，按**面向手机屏幕时**的前置镜头位置选择：镜头在左选「横屏 · 镜头在左」，镜头在右选「横屏 · 镜头在右」。镜头朝下时选「倒置竖屏」。
3. 手机摆好、目视正中后，点击「头部与视线归零」，再试左右转头、抬头低头和歪头。

方向补偿将头部左右/上下转动轴转换为人物的方向，并处理倒置时倾斜角度跨越边界的问题。眨眼、嘴巴和眼球视线系数保持按脸部方向处理，视线归零照常生效。选项可以在连接期间修改，会保存到本地设置；改变方向后应再次归零。

如果手机发送的数据方向本身已正确，保持「竖屏 / 不补偿」。本工具使用手动选择，不根据手机界面是否旋转来自动切换方向。

## 已有功能

- 实时数值、连接状态、收发帧率与连接日志。
- 头部三轴旋转、左右眼开合、左右眼视线、张嘴、微笑、嘴巴左右、眉毛、鼓腮和舌头输入。
- 头部与视线归零、平滑、嘴巴/眨眼/视线幅度、头部各轴反向、左右眼交换。
- VTS 断线重连；授权保存；授权拒绝后不会反复弹出授权请求。
- 手机超过一秒没有有效帧时停止覆盖表情参数，让 VTS 释放参数控制。
- 模拟动作模式，可先测试 VTS 和模型映射。模拟数据会明确标出来源，重开程序默认关闭。

使用 VTS 已有的默认输入参数。只发送 VTS 实际返回的参数，并按它的取值范围裁剪；没有创建专用 VBridger 参数。模型最终能表现什么动作，取决于模型本身的绑定。

## 排查

| 现象 | 检查 |
| --- | --- |
| 等待手机数据 | IP/端口、同一局域网、LIVE 发送、ARKit 模式、iOS 设置中的本地网络权限 |
| 已收到手机数据，但无法解析 | 数据已到达电脑；查看具体错误，首先确认 Capture Mode 为 Live Link（ARKit） |
| UDP 11111 无法使用 | 可能被 Unreal Engine 占用；工具与手机都改为 `11112` |
| 防火墙阻止接收 | Windows 提示时允许此应用接收私人网络的数据；只放行选用的 UDP 端口 |
| VTS 未连通 | 开启插件 API，确认端口；无需开启 VTS 自带摄像头面捕 |
| 授权被拒绝/撤销 | 点停止，再开始，在 VTS 中允许插件 |
| 数值在动，模型不动 | 在模型设置中检查 FaceAngleX/Y/Z、EyeOpenLeft/Right、MouthOpen 等输入映射；关闭其他正在覆盖相同参数的插件 |
| 头部方向或幅度不对 | 在调节页调整幅度与反向；实际头部单位及正负方向需要手机实测确认 |
| 眼睛嘴巴数值在动，头部三轴一直为 0 | 手机开启 Stream Head Rotation 后再转头测试 |
| 平视时眼球偏向一边 | 目视希望作为正中的位置，点击「头部与视线归零」；移动手机后可以再次归零 |
| 横放手机后转头变成点头，或人物一直歪着 | 在「手机方向」选择镜头在左/在右，再点击「头部与视线归零」 |

设置：工具目录下的 `data/settings.json`。授权：同目录的 `tokens.json`。默认目录按源码位置确定，从其他目录启动也使用同一位置。它们保存在电脑本地，程序不上传面捕数据；WebSocket 只连接 `127.0.0.1`，UDP 从局域网接收。

## 协议来源与限制

- [Epic：iOS 面捕设置、网络目标与头部旋转](https://dev.epicgames.com/documentation/en-us/unreal-engine/recording-face-animation-on-ios-device-in-unreal-engine)
- [Epic 官方 App Store 页面：Live Link（ARKit）捕捉模式](https://apps.apple.com/us/app/live-link-face/id1495370836)
- [GodotARKit 原作者的 ARKit v6 解包实现](https://github.com/Jules-NC/GodotARKit/blob/main/arkit_packet.gd)
- [PyLiveLinkFace 原作者的编码实现与通道顺序](https://github.com/JimWest/PyLiveLinkFace/blob/main/pylivelinkface/pylivelinkface.py)
- [VTS：授权](https://github.com/DenchiSoft/VTubeStudio#authentication)、[默认输入查询](https://github.com/DenchiSoft/VTubeStudio#requesting-list-of-available-tracking-parameters)、[参数注入](https://github.com/DenchiSoft/VTubeStudio#feeding-in-data-for-default-or-custom-parameters)
- [websockets 16.0：同步客户端](https://websockets.readthedocs.io/en/16.0/reference/sync/client.html)

v6 数据格式按公开参考实现独立编写；Epic 的上述用户文档并未直接公布二进制帧结构。PyLiveLinkFace 的旧版头部编码与标准 v6 帧在字节上兼容，测试覆盖两种独立构造方式。默认将 headYaw/headPitch/headRoll 按弧度转换为角度，这个比例和轴正负需要真机校准。

本版仅支持 **Live Link（ARKit）v6，61 通道**，不支持 **MetaHuman Animator** 模式。没有修改 VTS 水印或收费功能。此工具自行接收外部面捕数据，再调用公开 API。

已用本机 iPhone 实测 ARKit UDP 接收与真实 VTS 参数注入：手机约 60 帧/秒，眨眼、嘴巴和头部三轴数值随动作变化。开启 Stream Head Rotation 后，实测左右、上下和倾斜都有变化。35 项自动测试覆盖协议、状态提示、授权、参数注入、头部与视线校准、横屏方向补偿、旧设置兼容和断流释放。具体模型的动作映射仍需按模型本身的绑定确认。

横屏「镜头在左」已实测手机数据接收与归零，归零后倾斜数值不再因横放手机而持续达到上限。「镜头在右」与倒置摆放的方向转换由自动测试验证。

## 开发与自检

Python 3.11，Tk 8.6，websockets 16.0。依赖在项目内 `.venv` 安装。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
.\.venv\Scripts\python.exe -m unittest tests.test_bridge -v
```

BAT 入口也支持自检：

```bat
启动面捕.bat --self-test --report source-self-test.json
启动面捕.bat --smoke-test --report source-gui-test.json
```

自检会临时创建本机随机端口的模拟 UDP / WebSocket 服务，不连接真实 VTS，不需要手机。支持 `--smoke-test --report gui-test.json` 检查 GUI 创建。`--settings-dir` 可指定独立设置目录。

## 贡献与许可证

欢迎提交 [Issue](https://github.com/wangkaxds/LiveLinkFace-VTS/issues) 或 Pull Request。报告连接问题时，请注明 Windows / Python / Live Link Face / VTS 的版本、捕捉模式和界面中的错误提示。请勿提交 `data/tokens.json`、个人配置或真实手机数据包。

本项目使用 [MIT 许可证](LICENSE)。第三方组件和协议参考见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。这是独立社区工具，未与 Epic Games、VTube Studio 或 Live2D 官方建立关联。
