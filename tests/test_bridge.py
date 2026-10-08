"""Protocol fixtures, parameter behavior and real UDP/WebSocket integration.

The two independent fixtures reproduce the documented GodotARKit / PyLiveLinkFace
layouts. They are synthetic data, not a recording from the user's phone.
"""
from __future__ import annotations

import json
import math
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path

from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed

from bridge import BLENDSHAPES, Bridge, FaceFrame, Mapper, PacketError, Settings, Storage, decode_packet


def packet(values=None, legacy=False, subject="iPhone 测试", device="$12345678-1234-1234-1234-123456789abc"):
    curves = [0.0] * 61
    for name, value in (values or {}).items():
        curves[BLENDSHAPES.index(name)] = value
    name = subject.encode("utf-8")
    uid = device.encode("utf-8")
    if legacy:
        prefix = struct.pack("<I", 6) + uid
    else:
        prefix = b"\x06" + struct.pack("!I", len(uid)) + uid
    return (prefix + struct.pack("!I", len(name)) + name + struct.pack("!IfIIB", 24, 0.25, 60, 1, 61)
            + struct.pack("!61f", *curves))


def frame(values=None):
    curves = dict.fromkeys(BLENDSHAPES, 0.0)
    curves.update(values or {})
    return FaceFrame("test", "test", curves, "test", 1)


def until(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true within timeout")


class ProtocolTests(unittest.TestCase):
    def test_modern_unicode_and_curve_order(self):
        decoded = decode_packet(packet({"JawOpen": 0.7, "HeadYaw": -0.3, "RightEyeRoll": 0.9}))
        self.assertEqual(decoded.subject, "iPhone 测试")
        self.assertEqual(decoded.layout, "ARKit v6")
        self.assertEqual(decoded.frame_number, 24)
        self.assertAlmostEqual(decoded.values["JawOpen"], 0.7, places=5)
        self.assertAlmostEqual(decoded.values["HeadYaw"], -0.3, places=5)
        self.assertAlmostEqual(decoded.values["RightEyeRoll"], 0.9, places=5)

    def test_legacy_unicode(self):
        decoded = decode_packet(packet({"EyeBlinkRight": 1.0}, legacy=True))
        self.assertEqual(decoded.subject, "iPhone 测试")
        self.assertEqual(decoded.layout, "ARKit v6")
        self.assertEqual(decoded.values["EyeBlinkRight"], 1.0)
        self.assertEqual(packet(legacy=True), packet(device="12345678-1234-1234-1234-123456789abc"))

    def test_all_truncated_prefixes_rejected(self):
        for legacy in (False, True):
            encoded = packet(legacy=legacy)
            for length in range(len(encoded)):
                with self.assertRaises(PacketError):
                    decode_packet(encoded[:length])

    def test_wrong_version_count_length_and_trailing_bytes_rejected(self):
        encoded = packet()
        candidates = [b"\x07" + encoded[1:], encoded + b"\x00", b"\x06\xff\xff\xff\xff" + encoded[5:]]
        changed = bytearray(encoded)
        changed[-245] = 60
        candidates.append(bytes(changed))
        for data in candidates:
            with self.assertRaises(PacketError):
                decode_packet(data)

    def test_nan_and_infinity_rejected(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaises(PacketError):
                decode_packet(packet({"JawOpen": value}))

    def test_format_error_explains_the_failed_field(self):
        encoded = packet()
        changed = bytearray(encoded)
        changed[-245] = 60
        with self.assertRaisesRegex(PacketError, "通道数量.*60"):
            decode_packet(bytes(changed))


class MappingTests(unittest.TestCase):
    def test_wink_and_mouth(self):
        result = Mapper().map(frame({"EyeBlinkLeft": 1, "JawOpen": 0.5}), Settings(smoothing_ms=0), 1)
        self.assertEqual(result["EyeOpenLeft"], 0)
        self.assertEqual(result["EyeOpenRight"], 1)
        self.assertEqual(result["MouthOpen"], 0.75)

    def test_head_zero_degrees_reverse_and_clip(self):
        settings = Settings(smoothing_ms=0, head_zero=(0.2, 0, 0), invert_y=True)
        result = Mapper().map(frame({"HeadYaw": 0.2 + math.radians(10), "HeadPitch": math.radians(20), "HeadRoll": 5}), settings, 1)
        self.assertAlmostEqual(result["FaceAngleX"], 10)
        self.assertAlmostEqual(result["FaceAngleY"], -20)
        self.assertEqual(result["FaceAngleZ"], 30)

    def test_gaze_side_conventions_and_eye_swap(self):
        f = frame({"EyeLookOutLeft": 0.4, "EyeLookInRight": 0.4, "EyeBlinkLeft": 1})
        result = Mapper().map(f, Settings(smoothing_ms=0), 1)
        self.assertEqual(result["EyeLeftX"], 0.4)
        self.assertEqual(result["EyeRightX"], 0.4)
        swapped = Mapper().map(f, Settings(swap_eyes=True, invert_gaze=True), 1)
        self.assertEqual(swapped["EyeOpenRight"], 0)
        self.assertEqual(swapped["EyeOpenLeft"], 1)
        self.assertEqual(swapped["EyeLeftX"], -0.4)

    def test_smoothing_is_time_based_and_blinks_remain_fast(self):
        results = []
        for rate in (30, 60):
            mapper = Mapper()
            settings = Settings(smoothing_ms=100)
            mapper.map(frame(), settings, 0)
            for i in range(1, rate + 1):
                result = mapper.map(frame({"HeadYaw": math.radians(20)}), settings, i / rate)
            results.append(result["FaceAngleX"])
        self.assertAlmostEqual(results[0], results[1], places=8)
        mapper = Mapper()
        mapper.map(frame(), Settings(smoothing_ms=250), 0)
        result = mapper.map(frame({"EyeBlinkLeft": 1}), Settings(smoothing_ms=250), 0.03)
        self.assertLess(result["EyeOpenLeft"], 0.03)

    def test_extreme_blendshapes_cannot_escape_ranges(self):
        result = Mapper().map(frame({k: 10000 for k in BLENDSHAPES}), Settings(), 1)
        self.assertTrue(all(math.isfinite(value) for value in result.values()))
        self.assertTrue(0 <= result["MouthOpen"] <= 1)
        self.assertTrue(0 <= result["BrowLeftY"] <= 1)
        self.assertTrue(-30 <= result["FaceAngleX"] <= 30)


class OrientationTests(unittest.TestCase):
    def test_left_landscape_restores_yaw_pitch_and_keeps_face_local_gaze(self):
        encoded = frame({"HeadYaw": math.radians(-8), "HeadPitch": math.radians(12),
                         "HeadRoll": math.radians(95), "EyeLookOutLeft": 0.3,
                         "EyeLookDownLeft": 0.2, "EyeBlinkRight": 1, "JawOpen": 0.4})
        settings = Settings(smoothing_ms=0, phone_orientation="landscape_left", head_zero=(0, 0, math.pi / 2))
        result = Mapper().map(encoded, settings, 1)
        self.assertAlmostEqual(result["FaceAngleX"], 12)
        self.assertAlmostEqual(result["FaceAngleY"], 8)
        self.assertAlmostEqual(result["FaceAngleZ"], 5)
        self.assertAlmostEqual(result["EyeLeftX"], 0.3)
        self.assertAlmostEqual(result["EyeLeftY"], -0.2)
        self.assertEqual(result["EyeOpenRight"], 0)
        self.assertAlmostEqual(result["MouthOpen"], 0.6)

    def test_right_landscape_restores_yaw_pitch_before_gain_and_axis_inversion(self):
        encoded = frame({"HeadYaw": math.radians(8), "HeadPitch": math.radians(-12),
                         "HeadRoll": math.radians(-85)})
        settings = Settings(smoothing_ms=0, phone_orientation="landscape_right", head_gain=2,
                            head_zero=(0, 0, -math.pi / 2), invert_x=True, invert_y=True, invert_z=True)
        result = Mapper().map(encoded, settings, 1)
        self.assertAlmostEqual(result["FaceAngleX"], -24)
        self.assertAlmostEqual(result["FaceAngleY"], -16)
        self.assertAlmostEqual(result["FaceAngleZ"], -10)

    def test_upside_down_handles_the_roll_boundary_without_a_full_turn_jump(self):
        result = Mapper().map(frame({"HeadYaw": math.radians(-12), "HeadPitch": math.radians(-8),
                                     "HeadRoll": math.radians(-179)}),
                              Settings(smoothing_ms=0, phone_orientation="upside_down",
                                       head_zero=(0, 0, math.radians(179))), 1)
        self.assertAlmostEqual(result["FaceAngleX"], 12)
        self.assertAlmostEqual(result["FaceAngleY"], 8)
        self.assertAlmostEqual(result["FaceAngleZ"], 2)

    def test_all_orientations_preserve_calibration_and_eye_side_options(self):
        with tempfile.TemporaryDirectory() as directory:
            neutral = frame({"HeadYaw": 0.2, "HeadPitch": 0.3, "HeadRoll": 1.5,
                             "EyeLookOutLeft": 0.3, "EyeLookDownLeft": 0.2,
                             "EyeLookInRight": 0.4, "EyeLookUpRight": 0.1, "EyeBlinkLeft": 1})
            for orientation in ("portrait", "landscape_left", "landscape_right", "upside_down"):
                engine = Bridge(Settings(smoothing_ms=0, phone_orientation=orientation,
                                         swap_eyes=True, invert_gaze=True), Storage(Path(directory)))
                engine.latest = neutral, time.monotonic()
                engine.calibrate()
                mapped, _ = engine._mapped(time.monotonic())
                for key in ("FaceAngleX", "FaceAngleY", "FaceAngleZ", "EyeLeftX", "EyeLeftY", "EyeRightX", "EyeRightY"):
                    self.assertAlmostEqual(mapped[key], 0)
                self.assertEqual(mapped["EyeOpenRight"], 0)

    def test_orientation_change_resets_smoothing_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Bridge(Settings(smoothing_ms=250), Storage(Path(directory)))
            now = time.monotonic()
            engine.latest = frame({"HeadYaw": math.radians(-8), "HeadPitch": math.radians(12)}), now
            engine._mapped(now)
            engine.update_settings(replace(engine.settings, phone_orientation="landscape_left"))
            mapped, _ = engine._mapped(now + 0.001)
            self.assertAlmostEqual(mapped["FaceAngleX"], 12)
            self.assertAlmostEqual(mapped["FaceAngleY"], 8)

    def test_orientation_persists_and_legacy_settings_use_no_compensation(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            (storage.directory / "settings.json").write_text("{}", encoding="utf-8")
            self.assertEqual(storage.load_settings().phone_orientation, "portrait")
            storage.save_settings(Settings(phone_orientation="landscape_left"))
            self.assertEqual(storage.load_settings().phone_orientation, "landscape_left")

    def test_unknown_orientation_is_rejected(self):
        for orientation in ("auto", "invalid", None, 90):
            with self.assertRaises(ValueError):
                Settings(phone_orientation=orientation).validate()


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.directory.name))
        self.neutral = frame({
            "HeadYaw": 0.2, "HeadPitch": -0.1, "HeadRoll": 0.05,
            "EyeLookOutLeft": 0.35, "EyeLookDownLeft": 0.4,
            "EyeLookInRight": 0.25, "EyeLookDownRight": 0.2,
            "EyeBlinkLeft": 0.4, "EyeBlinkRight": 0.2,
        })

    def tearDown(self):
        self.directory.cleanup()

    def test_calibration_centers_head_and_both_eyes_without_changing_blinks(self):
        engine = Bridge(Settings(smoothing_ms=0, eye_gain=2, swap_eyes=True, invert_gaze=True), self.storage)
        now = time.monotonic()
        engine.latest = self.neutral, now
        before, _ = engine._mapped(now)
        engine.calibrate()
        after, valid = engine._mapped(time.monotonic())
        self.assertTrue(valid)
        for key in ("FaceAngleX", "FaceAngleY", "FaceAngleZ", "EyeLeftX", "EyeLeftY", "EyeRightX", "EyeRightY"):
            self.assertAlmostEqual(after[key], 0)
        for key in ("EyeOpenLeft", "EyeOpenRight"):
            self.assertEqual(after[key], before[key])

    def test_eye_movement_remains_relative_to_the_saved_neutral_pose(self):
        engine = Bridge(Settings(smoothing_ms=0), self.storage)
        engine.latest = self.neutral, time.monotonic()
        engine.calibrate()
        moved = dict(self.neutral.values)
        moved.update({"EyeLookOutLeft": 0.45, "EyeLookUpLeft": 0.2,
                      "EyeLookInRight": 0.35, "EyeLookUpRight": 0.1,
                      "EyeBlinkLeft": 1, "EyeBlinkRight": 0, "HeadYaw": 0.3})
        engine.latest = frame(moved), time.monotonic()
        after, _ = engine._mapped(time.monotonic())
        self.assertAlmostEqual(after["EyeLeftX"], 0.1)
        self.assertAlmostEqual(after["EyeLeftY"], 0.2)
        self.assertAlmostEqual(after["EyeRightX"], 0.1)
        self.assertAlmostEqual(after["EyeRightY"], 0.1)
        self.assertEqual(after["EyeOpenLeft"], 0)
        self.assertEqual(after["EyeOpenRight"], 1)
        self.assertAlmostEqual(after["FaceAngleX"], math.degrees(0.1))
        engine.update_settings(replace(engine.settings, eye_gain=2, swap_eyes=True, invert_gaze=True))
        after, _ = engine._mapped(time.monotonic())
        self.assertAlmostEqual(after["EyeLeftX"], -0.2)
        self.assertAlmostEqual(after["EyeLeftY"], 0.2)
        self.assertAlmostEqual(after["EyeRightX"], -0.2)
        self.assertAlmostEqual(after["EyeRightY"], 0.4)

    def test_calibration_discards_previous_smoothing_offsets(self):
        engine = Bridge(Settings(smoothing_ms=200), self.storage)
        now = time.monotonic()
        engine.latest = self.neutral, now
        engine._mapped(now)
        engine.calibrate()
        after, _ = engine._mapped(now + 0.001)
        self.assertEqual(after["EyeLeftX"], 0)
        self.assertEqual(after["EyeLeftY"], 0)

    def test_calibration_requires_a_recent_phone_frame(self):
        engine = Bridge(Settings(), self.storage)
        for latest in (None, (self.neutral, time.monotonic() - 2)):
            engine.latest = latest
            with self.assertRaises(ValueError):
                engine.calibrate()
        self.assertEqual(engine.settings, Settings())


class StorageTests(unittest.TestCase):
    def test_gaze_zero_persists_and_old_settings_default_to_uncalibrated(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            (storage.directory / "settings.json").write_text(
                json.dumps({"head_zero": [0.1, 0.2, 0.3]}), encoding="utf-8")
            loaded = storage.load_settings()
            self.assertEqual(tuple(loaded.head_zero), (0.1, 0.2, 0.3))
            self.assertEqual(tuple(loaded.gaze_zero), (0, 0, 0, 0))
            calibrated = replace(loaded, gaze_zero=(0.2, -0.3, 0.4, -0.5))
            storage.save_settings(calibrated)
            self.assertEqual(tuple(storage.load_settings().gaze_zero), calibrated.gaze_zero)
            for invalid in ((0, 0), (0, 0, math.nan, 0), (0, 0, 0, math.inf)):
                with self.assertRaises(ValueError):
                    replace(calibrated, gaze_zero=invalid).validate()

    def test_default_storage_works_when_localappdata_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "portable-tool"
            program.mkdir()
            denied = Path(directory) / "denied-appdata"
            original_mkdir = Path.mkdir

            def restricted_mkdir(path, *args, **kwargs):
                if path.is_relative_to(denied):
                    raise PermissionError(5, "Access denied", str(path))
                return original_mkdir(path, *args, **kwargs)

            with patch.dict(os.environ, {"LOCALAPPDATA": str(denied)}), \
                    patch("bridge.__file__", str(program / "bridge.py")), \
                    patch.object(Path, "mkdir", restricted_mkdir):
                try:
                    storage = Storage()
                except OSError as exc:
                    self.fail(f"默认设置目录不应依赖无法访问的 AppData：{exc}")
                self.assertEqual(storage.directory, program / "data")
                storage.save_settings(Settings(head_gain=1.7))
                storage.save_token(8001, "local-test-token")
                self.assertEqual(storage.load_settings().head_gain, 1.7)
                self.assertEqual(storage.load_token(8001), "local-test-token")

    def test_settings_and_endpoint_tokens_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            settings = Settings(head_gain=2, demo=True, head_zero=(0.1, 0.2, 0.3))
            storage.save_settings(settings)
            loaded = storage.load_settings()
            self.assertEqual(loaded.head_gain, 2)
            self.assertEqual(tuple(loaded.head_zero), settings.head_zero)
            self.assertFalse(loaded.demo)
            storage.save_token(8001, "test-token")
            self.assertEqual(storage.load_token(8001), "test-token")
            self.assertIsNone(storage.load_token(8002))
            storage.save_token(8001, None)
            self.assertIsNone(storage.load_token(8001))

    def test_invalid_settings_rejected(self):
        for settings in (Settings(udp_port=0), Settings(fps=1), Settings(smoothing_ms=math.nan), Settings(head_zero=(math.inf, 0, 0))):
            with self.assertRaises(ValueError):
                settings.validate()


class MockVTS:
    def __init__(self, deny=False, pending=False, disconnect_once=False, reject_empty=False):
        self.deny, self.pending, self.disconnect_once = deny, pending, disconnect_once
        self.reject_empty = reject_empty
        self.requests = []
        self.injections = []
        self.lock = threading.Lock()
        self.server = serve(self.handle, "127.0.0.1", 0, compression=None)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.thread.join(timeout=3)

    def handle(self, ws):
        try:
            self.exchange(ws)
        except ConnectionClosed:
            pass

    def exchange(self, ws):
        authenticated = False
        for raw in ws:
            request = json.loads(raw)
            kind, data = request["messageType"], request["data"]
            with self.lock:
                self.requests.append(request)
            response_type = kind.removesuffix("Request") + "Response"
            payload = {}
            if kind == "AuthenticationTokenRequest":
                if self.pending:
                    continue
                if self.deny:
                    response_type, payload = "APIError", {"errorID": 50, "message": "denied by user"}
                else:
                    payload = {"authenticationToken": "test-token"}
            elif kind == "AuthenticationRequest":
                authenticated = data.get("authenticationToken") == "test-token"
                payload = {"authenticated": authenticated}
            elif not authenticated:
                response_type, payload = "APIError", {"errorID": 50, "message": "not authenticated"}
            elif kind == "InputParameterListRequest":
                payload = {"defaultParameters": [{"name": key, "min": low, "max": high} for key, low, high in (
                    ("FaceAngleX", -30, 30), ("FaceAngleY", -30, 30), ("FaceAngleZ", -30, 30),
                    ("EyeOpenLeft", 0, 1), ("EyeOpenRight", 0, 1), ("MouthOpen", 0, 1))]}
            elif kind == "InjectParameterDataRequest":
                with self.lock:
                    self.injections.append(data)
                    disconnect = self.disconnect_once
                    self.disconnect_once = False
                if disconnect:
                    ws.close()
                    return
                if self.reject_empty and not data["parameterValues"]:
                    response_type, payload = "APIError", {"errorID": 450, "message": "no parameter data"}
            # An unrelated event must not be mistaken for the response.
            ws.send(json.dumps({"messageType": "ModelLoadedEvent", "requestID": "unrelated", "data": {}}))
            ws.send(json.dumps({"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0",
                                "messageType": response_type, "requestID": request["requestID"], "data": payload}))

    def count(self, kind):
        with self.lock:
            return sum(r["messageType"] == kind for r in self.requests)

    def has_injection(self, predicate):
        with self.lock:
            return any(predicate(data) for data in self.injections)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.directory.name))
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.stop()
            for thread in engine.threads:
                thread.join(timeout=3)
            self.assertFalse(engine.running)
        self.directory.cleanup()

    def engine(self, mock, demo=True):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(("127.0.0.1", 0))
            port = udp.getsockname()[1]
        engine = Bridge(Settings(udp_port=port, vts_port=mock.port, demo=demo, fps=60), self.storage)
        self.engines.append(engine)
        return engine

    def test_udp_to_vts_and_stale_release(self):
        with MockVTS() as mock:
            engine = self.engine(mock, demo=False)
            engine.start()
            until(lambda: engine.snapshot().connected)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"invalid", ("127.0.0.1", engine.settings.udp_port))
                sender.sendto(packet({"EyeBlinkLeft": 1, "JawOpen": 0.4, "HeadYaw": math.radians(10)}), ("127.0.0.1", engine.settings.udp_port))
            until(lambda: mock.has_injection(lambda d: d["faceFound"]))
            self.assertEqual(engine.snapshot().received, 1)
            self.assertEqual(engine.snapshot().invalid, 1)
            with mock.lock:
                sent = next(d for d in mock.injections if d["faceFound"])
            values = {p["id"]: p["value"] for p in sent["parameterValues"]}
            self.assertEqual(values["EyeOpenLeft"], 0)
            self.assertAlmostEqual(values["MouthOpen"], 0.6, places=5)
            self.assertAlmostEqual(values["FaceAngleX"], 10, places=5)
            self.assertNotIn("TongueOut", values)
            self.assertEqual(sent["mode"], "set")
            until(lambda: engine.snapshot().values == {}, seconds=3)
            until(lambda: mock.has_injection(lambda d: not d["faceFound"] and d["parameterValues"] == []))
            self.assertIn("中断", engine.snapshot().phone)
            engine.stop()
            until(lambda: not engine.running)

    def test_invalid_phone_data_is_reported_as_received_without_driving_vts(self):
        with MockVTS() as mock:
            engine = self.engine(mock, demo=False)
            engine.start()
            until(lambda: engine.snapshot().connected)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"invalid", ("127.0.0.1", engine.settings.udp_port))
            until(lambda: engine.snapshot().invalid == 1)
            state = engine.snapshot()
            self.assertIn("已收到", state.phone)
            self.assertIn("无法解析", state.phone)
            self.assertEqual(state.source, "127.0.0.1")
            self.assertEqual(state.received, 0)
            self.assertEqual(state.sent, 0)
            self.assertEqual(state.values, {})
            self.assertFalse(mock.has_injection(lambda data: data["faceFound"]))
            engine.stop()
            until(lambda: not engine.running)

    def test_saved_authorization_does_not_prompt_again(self):
        with MockVTS() as mock:
            engine = self.engine(mock)
            for _ in range(2):
                engine.start()
                until(lambda: engine.snapshot().sent > 2)
                engine.stop()
                until(lambda: not engine.running)
            self.assertEqual(mock.count("AuthenticationTokenRequest"), 1)

    def test_empty_injection_rejection_keeps_connection_and_releases_parameters(self):
        with MockVTS(reject_empty=True) as mock:
            engine = self.engine(mock, demo=False)
            engine.start()
            until(lambda: mock.count("APIStateRequest") >= 1)
            self.assertTrue(engine.snapshot().connected)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(packet({"JawOpen": 0.5}), ("127.0.0.1", engine.settings.udp_port))
            until(lambda: engine.snapshot().sent >= 1)
            until(lambda: engine.snapshot().values == {} and engine.snapshot().tx_fps == 0, seconds=3)
            self.assertTrue(engine.snapshot().connected)
            self.assertEqual(engine.snapshot().tx_fps, 0)
            with mock.lock:
                invalid_injections = [d for d in mock.injections if not d["parameterValues"]]
            self.assertEqual(len(invalid_injections), 1)
            engine.stop()
            until(lambda: not engine.running)

    def test_invalid_token_renews_once(self):
        with MockVTS() as mock:
            self.storage.save_token(mock.port, "expired")
            engine = self.engine(mock)
            engine.start()
            until(lambda: engine.snapshot().sent > 2)
            self.assertEqual(mock.count("AuthenticationTokenRequest"), 1)
            self.assertEqual(self.storage.load_token(mock.port), "test-token")
            engine.stop()
            until(lambda: not engine.running)

    def test_denial_does_not_repeat_authorization(self):
        with MockVTS(deny=True) as mock:
            engine = self.engine(mock, demo=False)
            engine.start()
            until(lambda: "被拒绝" in engine.snapshot().vts)
            time.sleep(0.3)
            self.assertEqual(mock.count("AuthenticationTokenRequest"), 1)
            self.assertEqual(mock.count("InjectParameterDataRequest"), 0)
            engine.stop()
            until(lambda: not engine.running)

    def test_stop_while_authorization_popup_pending(self):
        with MockVTS(pending=True) as mock:
            engine = self.engine(mock)
            engine.start()
            until(lambda: mock.count("AuthenticationTokenRequest") == 1)
            engine.stop()
            until(lambda: not engine.running, seconds=2)

    def test_reconnect_reuses_token(self):
        with MockVTS(disconnect_once=True) as mock:
            engine = self.engine(mock)
            engine.start()
            until(lambda: engine.snapshot().sent > 2, seconds=6)
            self.assertEqual(mock.count("AuthenticationTokenRequest"), 1)
            self.assertGreaterEqual(mock.count("AuthenticationRequest"), 2)
            engine.stop()
            until(lambda: not engine.running)

    def test_udp_port_conflict_is_reported(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(("0.0.0.0", 0))
            engine = Bridge(Settings(udp_port=occupied.getsockname()[1]), self.storage)
            self.engines.append(engine)
            with self.assertRaises(OSError):
                engine.start()


if __name__ == "__main__":
    unittest.main()
