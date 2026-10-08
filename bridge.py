"""Live Link (ARKit) UDP receiver and VTube Studio API bridge.

Protocol references:
https://github.com/Jules-NC/GodotARKit/blob/main/arkit_packet.gd
https://github.com/JimWest/PyLiveLinkFace/blob/main/pylivelinkface/pylivelinkface.py
https://github.com/DenchiSoft/VTubeStudio#authentication
https://github.com/DenchiSoft/VTubeStudio#feeding-in-data-for-default-or-custom-parameters
"""
from __future__ import annotations

import json
import math
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from websockets.sync.client import connect

BLENDSHAPES = (
    "EyeBlinkLeft EyeLookDownLeft EyeLookInLeft EyeLookOutLeft EyeLookUpLeft "
    "EyeSquintLeft EyeWideLeft EyeBlinkRight EyeLookDownRight EyeLookInRight "
    "EyeLookOutRight EyeLookUpRight EyeSquintRight EyeWideRight JawForward "
    "JawLeft JawRight JawOpen MouthClose MouthFunnel MouthPucker MouthLeft "
    "MouthRight MouthSmileLeft MouthSmileRight MouthFrownLeft MouthFrownRight "
    "MouthDimpleLeft MouthDimpleRight MouthStretchLeft MouthStretchRight "
    "MouthRollLower MouthRollUpper MouthShrugLower MouthShrugUpper "
    "MouthPressLeft MouthPressRight MouthLowerDownLeft MouthLowerDownRight "
    "MouthUpperUpLeft MouthUpperUpRight BrowDownLeft BrowDownRight BrowInnerUp "
    "BrowOuterUpLeft BrowOuterUpRight CheekPuff CheekSquintLeft CheekSquintRight "
    "NoseSneerLeft NoseSneerRight TongueOut HeadYaw HeadPitch HeadRoll "
    "LeftEyeYaw LeftEyePitch LeftEyeRoll RightEyeYaw RightEyePitch RightEyeRoll"
).split()
PLUGIN_NAME = "LiveLink Face to VTS"
PLUGIN_DEVELOPER = "Local Face Bridge"


class PacketError(ValueError):
    pass


@dataclass(frozen=True)
class FaceFrame:
    device: str
    subject: str
    values: dict[str, float]
    layout: str
    frame_number: int


def decode_packet(data: bytes) -> FaceFrame:
    """Read the v6 network layout; never search for floats heuristically.

    PyLiveLinkFace's LE uint32(6) + '$' + 36-byte UUID header is byte-for-byte
    identical to uint8(6) + BE uint32(36) + UUID. One parser handles both.
    """
    if not data or data[0] != 6:
        version = data[0] if data else "空"
        raise PacketError(f"不支持的数据版本 {version}，请选 Live Link（ARKit）模式")

    def string_at(offset: int) -> tuple[str, int]:
        if offset + 4 > len(data):
            raise PacketError("字符串长度缺失")
        length = struct.unpack_from("!I", data, offset)[0]
        offset += 4
        if not 1 <= length <= 1024 or offset + length > len(data):
            raise PacketError("字符串长度无效")
        return data[offset:offset + length].decode("utf-8").rstrip("\x00"), offset + length

    def finish(device: str, subject: str, offset: int, layout: str) -> FaceFrame:
        if len(data) != offset + 17 + 61 * 4:
            raise PacketError(f"数据长度不符：收到 {len(data)} 字节，预期 {offset + 17 + 61 * 4}")
        frame_number, subframe, fps, denominator, count = struct.unpack_from("!IfIIB", data, offset)
        if count != 61:
            raise PacketError(f"通道数量不符：收到 {count}，预期 61")
        if fps == 0 or denominator == 0 or not math.isfinite(subframe):
            raise PacketError("帧率或子帧数据无效")
        values = struct.unpack_from("!61f", data, offset + 17)
        if not all(math.isfinite(value) for value in values):
            raise PacketError("表情数据含有非有限数值")
        return FaceFrame(device, subject, dict(zip(BLENDSHAPES, values)), layout, frame_number)

    try:
        device, offset = string_at(1)
        subject, offset = string_at(offset)
        return finish(device, subject, offset, "ARKit v6")
    except (UnicodeDecodeError, struct.error) as exc:
        raise PacketError("ARKit 帧头或名称编码无效") from exc


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def eye_direction(values: dict[str, float], side: str) -> tuple[float, float]:
    """Physical eye direction before calibration, gain, inversion, or eye swap."""
    shape = lambda key: clamp(values.get(key, 0.0))
    x = shape(f"EyeLookOut{side}") - shape(f"EyeLookIn{side}")
    if side == "Right":
        x = -x
    y = shape(f"EyeLookUp{side}") - shape(f"EyeLookDown{side}")
    return x, y


@dataclass(frozen=True)
class Settings:
    udp_port: int = 11111
    vts_port: int = 8001
    fps: int = 60
    smoothing_ms: float = 100.0
    blink_smoothing_ms: float = 20.0
    head_gain: float = 1.0
    mouth_gain: float = 1.5
    blink_gain: float = 1.2
    eye_gain: float = 1.0
    phone_orientation: str = "portrait"
    invert_x: bool = False
    invert_y: bool = False
    invert_z: bool = False
    swap_eyes: bool = False
    invert_gaze: bool = False
    demo: bool = False
    head_zero: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gaze_zero: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    blink_zero: tuple[float, float] = (0.0, 0.0)

    def validate(self) -> None:
        if self.phone_orientation not in ("portrait", "landscape_left", "landscape_right", "upside_down"):
            raise ValueError("手机方向必须是竖屏、镜头在左、镜头在右或倒置竖屏")
        for port in (self.udp_port, self.vts_port):
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("端口必须是 1–65535 的整数")
        if type(self.fps) is not int or not 15 <= self.fps <= 120:
            raise ValueError("发送帧率必须是 15–120 的整数")
        limits = {"smoothing_ms": (0, 250), "blink_smoothing_ms": (0, 120), "head_gain": (0.1, 3),
                  "mouth_gain": (0.1, 4), "blink_gain": (0.1, 3), "eye_gain": (0.1, 3)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} 超出允许范围")
        if len(self.head_zero) != 3 or not all(math.isfinite(v) for v in self.head_zero):
            raise ValueError("头部校准值无效")
        if len(self.gaze_zero) != 4 or not all(math.isfinite(v) for v in self.gaze_zero):
            raise ValueError("视线校准值无效")
        if len(self.blink_zero) != 2 or not all(isinstance(v, (float, int)) and math.isfinite(v) and 0 <= v < 0.9
                                              for v in self.blink_zero):
            raise ValueError("眼睛开合校准值无效")


class Storage:
    def __init__(self, directory: Path | None = None):
        self.directory = directory if directory is not None else Path(__file__).resolve().parent / "data"
        self.directory.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> Settings:
        path = self.directory / "settings.json"
        if not path.exists():
            return Settings()
        fields = Settings.__dataclass_fields__
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = Settings(**{k: v for k, v in data.items() if k in fields})
        settings.validate()
        # Demo is always an explicit choice each time the app is opened.
        return replace(settings, demo=False)

    def _write(self, name: str, data: dict) -> None:
        path = self.directory / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def save_settings(self, settings: Settings) -> None:
        settings.validate()
        self._write("settings.json", asdict(settings))

    def load_token(self, port: int) -> str | None:
        try:
            return json.loads((self.directory / "tokens.json").read_text(encoding="utf-8")).get(str(port))
        except (FileNotFoundError, ValueError):
            return None

    def save_token(self, port: int, token: str | None) -> None:
        try:
            tokens = json.loads((self.directory / "tokens.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            tokens = {}
        if token:
            tokens[str(port)] = token
        else:
            tokens.pop(str(port), None)
        self._write("tokens.json", tokens)


class Mapper:
    def __init__(self):
        self.previous: dict[str, float] = {}
        self.last_time: float | None = None

    def reset(self) -> None:
        self.previous.clear()
        self.last_time = None

    def map(self, frame: FaceFrame, settings: Settings, now: float) -> dict[str, float]:
        v = frame.values
        shape = lambda key: clamp(v.get(key, 0.0))
        left_smile, right_smile = shape("MouthSmileLeft"), shape("MouthSmileRight")
        left_brow = clamp(0.5 + 0.5 * (shape("BrowInnerUp") + shape("BrowOuterUpLeft") - shape("BrowDownLeft")))
        right_brow = clamp(0.5 + 0.5 * (shape("BrowInnerUp") + shape("BrowOuterUpRight") - shape("BrowDownRight")))
        yaw, pitch, roll = (v.get(key, 0.0) - settings.head_zero[i]
                            for i, key in enumerate(("HeadYaw", "HeadPitch", "HeadRoll")))
        if settings.phone_orientation == "landscape_left":
            yaw, pitch = pitch, -yaw
        elif settings.phone_orientation == "landscape_right":
            yaw, pitch = -pitch, yaw
        elif settings.phone_orientation == "upside_down":
            yaw, pitch = -yaw, -pitch
        if settings.phone_orientation != "portrait":
            # Sideways/upside-down poses can cross the -pi/pi roll boundary.
            roll = math.remainder(roll, 2 * math.pi)
        result = {}
        for axis, value, inverse in zip("XYZ", (yaw, pitch, roll),
                                       (settings.invert_x, settings.invert_y, settings.invert_z)):
            angle = math.degrees(value) * settings.head_gain
            result[f"FaceAngle{axis}"] = clamp(-angle if inverse else angle, -30.0, 30.0)
        result.update({
            "MouthOpen": clamp((shape("JawOpen") - 0.3 * shape("MouthClose")) * settings.mouth_gain),
            "MouthSmile": clamp((left_smile + right_smile - shape("MouthFrownLeft") - shape("MouthFrownRight")) / 2, -1, 1),
            "MouthX": clamp(shape("MouthRight") - shape("MouthLeft"), -1, 1),
            "Brows": (left_brow + right_brow) / 2,
            "BrowLeftY": left_brow, "BrowRightY": right_brow,
            "CheekPuff": shape("CheekPuff"), "TongueOut": shape("TongueOut"),
            "EyeSmileLeft": shape("CheekSquintLeft"), "EyeSmileRight": shape("CheekSquintRight"),
        })
        for target_side in ("Left", "Right"):
            side = ("Right" if target_side == "Left" else "Left") if settings.swap_eyes else target_side
            baseline = settings.blink_zero[0 if side == "Left" else 1]
            # Normalize the remaining range so a calibrated eye can still close fully.
            blink = clamp((shape(f"EyeBlink{side}") - baseline) / (1.0 - baseline))
            result[f"EyeOpen{target_side}"] = clamp(1.0 - blink * settings.blink_gain + 0.2 * shape(f"EyeWide{side}"))
            gaze_x, gaze_y = eye_direction(v, side)
            offset = 0 if side == "Left" else 2
            gaze_x -= settings.gaze_zero[offset]
            gaze_y -= settings.gaze_zero[offset + 1]
            if settings.invert_gaze:
                gaze_x = -gaze_x
            result[f"Eye{target_side}X"] = clamp(gaze_x * settings.eye_gain, -1, 1)
            result[f"Eye{target_side}Y"] = clamp(gaze_y * settings.eye_gain, -1, 1)
        dt = max(0.0, now - self.last_time) if self.last_time is not None else None
        self.last_time = now
        for key, value in result.items():
            # Blinks have an independent time constant; lips retain a short cap.
            tau = settings.smoothing_ms / 1000
            if key.startswith("EyeOpen"):
                tau = settings.blink_smoothing_ms / 1000
            elif key.startswith("Mouth"):
                tau = min(tau, 0.025)
            if dt is not None and tau > 0 and key in self.previous:
                alpha = 1.0 - math.exp(-dt / tau)
                result[key] = self.previous[key] + alpha * (value - self.previous[key])
        self.previous = result.copy()
        return result


def demo_frame(now: float) -> FaceFrame:
    values = dict.fromkeys(BLENDSHAPES, 0.0)
    values.update({"HeadYaw": 0.3 * math.sin(now), "HeadPitch": 0.15 * math.sin(now * 0.7),
                   "HeadRoll": 0.15 * math.sin(now * 0.5), "JawOpen": 0.3 * (1 + math.sin(now * 3)),
                   "MouthSmileLeft": 0.4, "MouthSmileRight": 0.4,
                   "EyeBlinkLeft": 1.0 if now % 3 < 0.13 else 0.0,
                   "EyeBlinkRight": 1.0 if now % 3 < 0.13 else 0.0,
                   "BrowInnerUp": 0.3 * (1 + math.sin(now * 1.5))})
    return FaceFrame("demo", "模拟数据（非手机）", values, "demo", int(now * 60))


class APIError(RuntimeError):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"VTS 错误 {code}：{message}")


class AuthDenied(RuntimeError):
    pass


class Cancelled(Exception):
    pass


class VTSClient:
    def __init__(self, ws, stop: threading.Event):
        self.ws, self.stop = ws, stop
        self.counter = 0

    def request(self, message_type: str, data: dict | None = None, timeout: float = 3.0) -> dict:
        # Every response is consumed, so no per-frame WebSocket backlog accumulates.
        self.counter += 1
        request_id = f"llf-{self.counter}"
        self.ws.send(json.dumps({"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0",
                                 "requestID": request_id, "messageType": message_type, "data": data or {}}))
        deadline = time.monotonic() + timeout
        while not self.stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("VTS 响应超时；若正在授权，请停止后重试")
            try:
                response = json.loads(self.ws.recv(timeout=min(0.2, remaining)))
            except TimeoutError:
                continue
            if response.get("requestID") != request_id:
                continue
            if response.get("messageType") == "APIError":
                detail = response.get("data", {})
                raise APIError(detail.get("errorID", -1), detail.get("message", "未知错误"))
            expected = message_type.removesuffix("Request") + "Response"
            if response.get("messageType") != expected:
                raise RuntimeError(f"VTS 返回非预期消息：{response.get('messageType')}")
            return response.get("data", {})
        raise Cancelled()

    def authenticate(self, storage: Storage, port: int, status) -> None:
        identity = {"pluginName": PLUGIN_NAME, "pluginDeveloper": PLUGIN_DEVELOPER}
        token = storage.load_token(port)
        if token:
            try:
                data = self.request("AuthenticationRequest", {**identity, "authenticationToken": token})
                if data.get("authenticated"):
                    return
            except APIError as exc:
                if exc.code != 50:
                    raise
            storage.save_token(port, None)
        status("请到 VTube Studio 点击「允许」", False)
        try:
            data = self.request("AuthenticationTokenRequest", identity, timeout=120)
        except APIError as exc:
            if exc.code == 50:
                raise AuthDenied("VTS 授权被拒绝，请停止后重新开始连接") from exc
            raise
        token = data.get("authenticationToken")
        if not isinstance(token, str) or not token:
            raise AuthDenied("VTS 未返回授权令牌，请停止后重新连接")
        data = self.request("AuthenticationRequest", {**identity, "authenticationToken": token})
        if not data.get("authenticated"):
            raise AuthDenied("VTS 授权失败，请停止后重新连接")
        storage.save_token(port, token)


@dataclass
class Snapshot:
    phone: str = "尚未开始"
    vts: str = "尚未连接"
    connected: bool = False
    source: str = "—"
    layout: str = "—"
    received: int = 0
    invalid: int = 0
    sent: int = 0
    rx_fps: float = 0.0
    tx_fps: float = 0.0
    last_received: float = 0.0
    last_packet: float = 0.0
    values: dict[str, float] = field(default_factory=dict)


class Bridge:
    def __init__(self, settings: Settings, storage: Storage):
        settings.validate()
        self.settings, self.storage = settings, storage
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.state = Snapshot()
        self.logs: deque[str] = deque(maxlen=300)
        self.latest: tuple[FaceFrame, float] | None = None
        self.threads: list[threading.Thread] = []
        self.udp: socket.socket | None = None
        self.mapper = Mapper()
        self.reset_mapper = False
        self.selected_source: tuple[str, str, str] | None = None

    @property
    def running(self) -> bool:
        return any(thread.is_alive() for thread in self.threads)

    def log(self, message: str) -> None:
        with self.lock:
            self.logs.append(f"{time.strftime('%H:%M:%S')}  {message}")

    def snapshot(self) -> Snapshot:
        with self.lock:
            state = replace(self.state, values=self.state.values.copy())
            if not self.stop_event.is_set() and state.last_packet and time.monotonic() - state.last_packet > 1:
                state.phone = "手机数据已中断，等待恢复"
            return state

    def update_settings(self, settings: Settings) -> None:
        settings.validate()
        with self.lock:
            if settings.phone_orientation != self.settings.phone_orientation:
                self.reset_mapper = True
                self.log("手机方向补偿已更改；摆好手机后请点击「校准」")
            self.settings = settings

    def calibrate(self) -> Settings:
        with self.lock:
            if self.latest is None or time.monotonic() - self.latest[1] > 1:
                raise ValueError("还没有实时手机数据，请先连接手机")
            v = self.latest[0].values
            blink_zero = tuple(clamp(v.get(f"EyeBlink{side}", 0.0)) for side in ("Left", "Right"))
            if any(value >= 0.9 for value in blink_zero):
                raise ValueError("请自然睁开双眼、看向正中后再点击校准")
            self.settings = replace(self.settings,
                                    head_zero=tuple(v[k] for k in ("HeadYaw", "HeadPitch", "HeadRoll")),
                                    gaze_zero=(*eye_direction(v, "Left"), *eye_direction(v, "Right")),
                                    blink_zero=blink_zero)
            self.reset_mapper = True
            settings = self.settings
        self.log("已校准头部、视线与眼睛开合；坐姿或手机位置改变时可重新校准")
        return settings

    def start(self) -> None:
        if self.running:
            raise ValueError("上一次连接正在退出，请稍等")
        self.settings.validate()
        udp = None
        if not self.settings.demo:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    udp.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                udp.bind(("0.0.0.0", self.settings.udp_port))
                udp.settimeout(0.2)
            except OSError:
                udp.close()
                raise
        self.udp = udp
        self.stop_event.clear()
        self.mapper.reset()
        self.latest = None
        self.selected_source = None
        self.state = Snapshot(phone="模拟数据（非手机）" if self.settings.demo else "等待手机发送 ARKit 数据")
        self.threads = []
        if udp:
            self.threads.append(threading.Thread(target=self._receive, args=(udp,), daemon=True))
        self.threads.append(threading.Thread(target=self._send, daemon=True))
        for thread in self.threads:
            thread.start()
        self.log(f"{'模拟模式' if self.settings.demo else '监听 UDP ' + str(self.settings.udp_port)}；VTS 端口 {self.settings.vts_port}")

    def stop(self) -> None:
        self.stop_event.set()
        if self.udp:
            self.udp.close()
            self.udp = None
        with self.lock:
            self.state.connected = False
            self.state.phone = "已停止"
            self.state.vts = "已停止；模型参数将在约 1 秒后释放"

    def _receive(self, udp: socket.socket) -> None:
        tick, count = time.monotonic(), 0
        last_error = 0.0
        while not self.stop_event.is_set():
            try:
                data, addr = udp.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.stop_event.is_set():
                    self.log(f"UDP 接收失败：{exc}")
                    self.stop_event.set()
                break
            now = time.monotonic()
            with self.lock:
                self.state.last_packet = now
            try:
                frame = decode_packet(data)
            except PacketError as exc:
                with self.lock:
                    self.state.invalid += 1
                    if self.latest is None or now - self.latest[1] > 1:
                        self.state.source = addr[0]
                        self.state.phone = f"已收到手机数据，但无法解析：{exc}"
                if now - last_error > 5:
                    self.log(str(exc))
                    last_error = now
                continue
            source = (addr[0], frame.device, frame.subject)
            with self.lock:
                # Lock to one performer until it stops streaming for two seconds.
                if self.selected_source != source:
                    if self.latest and now - self.latest[1] < 2:
                        continue
                    self.selected_source = source
                    self.reset_mapper = True
                    self.log(f"收到 {frame.subject}（{addr[0]}），{frame.layout}")
                self.latest = frame, now
                self.state.received += 1
                self.state.last_received = now
                self.state.source = f"{frame.subject} · {addr[0]}"
                self.state.layout = frame.layout
                self.state.phone = "手机数据正常"
                count += 1
                if now - tick >= 1:
                    self.state.rx_fps = count / (now - tick)
                    tick, count = now, 0

    def _status(self, text: str, connected: bool) -> None:
        with self.lock:
            if self.state.vts != text:
                self.log(text)
            self.state.vts, self.state.connected = text, connected

    def _mapped(self, now: float) -> tuple[dict[str, float], bool]:
        with self.lock:
            settings, latest = self.settings, self.latest
            if self.reset_mapper:
                self.mapper.reset()
                self.reset_mapper = False
        if settings.demo:
            latest = demo_frame(now), now
            with self.lock:
                self.state.source, self.state.layout = "模拟数据（非手机）", "demo"
        valid = latest is not None and now - latest[1] <= 1.0
        if valid:
            values = self.mapper.map(latest[0], settings, now)
        else:
            values = {}
            self.mapper.reset()
        with self.lock:
            self.state.values = values
        return values, valid

    def _send(self) -> None:
        port = self.settings.vts_port
        fatal = False
        while not self.stop_event.is_set() and not fatal:
            try:
                self._status("正在连接 VTube Studio…", False)
                # No system proxy for the loopback connection.
                # https://websockets.readthedocs.io/en/16.0/reference/sync/client.html
                with connect(f"ws://127.0.0.1:{port}", proxy=None, open_timeout=2,
                             close_timeout=0.5, compression=None, max_size=2**20) as ws:
                    client = VTSClient(ws, self.stop_event)
                    client.authenticate(self.storage, port, self._status)
                    parameters = client.request("InputParameterListRequest")
                    ranges = {p["name"]: (float(p["min"]), float(p["max"]))
                              for p in parameters.get("defaultParameters", [])}
                    if not ranges:
                        raise RuntimeError("VTS 没有返回默认参数列表")
                    self.mapper.reset()
                    self._status("VTS 已连接，等待面捕数据", True)
                    tick, count, deadline = time.monotonic(), 0, time.monotonic()
                    idle_injection_supported = True
                    last_idle_request = 0.0
                    while not self.stop_event.is_set():
                        now = time.monotonic()
                        values, valid = self._mapped(now)
                        payload = [{"id": key, "value": clamp(value, *ranges[key])}
                                   for key, value in values.items() if key in ranges]
                        if valid and not payload:
                            raise RuntimeError("没有可用的 VTS 默认面捕参数")
                        if valid:
                            client.request("InjectParameterDataRequest", {
                                "faceFound": True, "mode": "set", "parameterValues": payload})
                            count += 1
                            with self.lock:
                                self.state.sent += 1
                                if now - tick >= 1:
                                    self.state.tx_fps = count / (now - tick)
                                    tick, count = now, 0
                        else:
                            # Stop updating values so parameter ownership expires after 1s.
                            # VTS error 450 can mean this version rejects empty injections.
                            # https://github.com/DenchiSoft/VTubeStudio/blob/master/Files/ErrorID.cs
                            if now - last_idle_request >= 0.5:
                                if idle_injection_supported:
                                    try:
                                        client.request("InjectParameterDataRequest", {
                                            "faceFound": False, "mode": "set", "parameterValues": []})
                                    except APIError as exc:
                                        if exc.code != 450:
                                            raise
                                        idle_injection_supported = False
                                if not idle_injection_supported:
                                    client.request("APIStateRequest")
                                last_idle_request = now
                            with self.lock:
                                self.state.tx_fps = 0.0
                            tick, count = now, 0
                        status = "VTS 已连接 · 模拟数据" if self.settings.demo else (
                            "VTS 已连接 · 正在驱动模型" if valid else "VTS 已连接 · 等待手机数据")
                        self._status(status, True)
                        deadline = max(deadline + 1 / self.settings.fps, time.monotonic())
                        self.stop_event.wait(max(0, deadline - time.monotonic()))
            except Cancelled:
                break
            except AuthDenied as exc:
                self._status(str(exc), False)
                fatal = True
            except APIError as exc:
                if exc.code == 50:
                    self._status("VTS 已撤销授权，请停止后重新连接", False)
                    fatal = True
                else:
                    self._status(f"{exc}；3 秒后重试（检查其他面捕插件）", False)
            except Exception as exc:
                if not self.stop_event.is_set():
                    self._status(f"VTS 未连通：{exc}；3 秒后重试", False)
            if not fatal and not self.stop_event.is_set():
                # Keep the local data preview active while VTS is unavailable.
                until = time.monotonic() + 3
                while not self.stop_event.is_set() and time.monotonic() < until:
                    self._mapped(time.monotonic())
                    self.stop_event.wait(1 / self.settings.fps)
        if self.stop_event.is_set():
            self._status("已停止；模型参数将在约 1 秒后释放", False)
