import time
from collections import defaultdict
from pathlib import Path
from queue import Queue, Empty
from threading import Lock, Thread
from urllib.parse import urlparse

import cv2
import numpy as np
import requests
from ultralytics import YOLO
from web_app import DetectionWebServer

# 主程式：負責影像讀取、YOLO 推論、Arduino 控制與 UI 更新。

# 嘗試載入序列通訊模組，若未安裝則停用 Arduino 功能。
try:
    import serial
    from serial import SerialException
except ImportError:
    serial = None
    SerialException = Exception

# 影像串流與 YOLO 推論的基本設定。
STREAM_URL = "http://192.168.4.1/api/v1/stream"
MODEL_NAME = "yolov8s.pt"
CONFIDENCE = 0.4
IMG_SIZE = 320          # 較小推論尺寸提升速度；若需要更高精準度改為 416 或 640
IOU_THRESHOLD = 0.45
MAX_DETECTIONS = 80
INFERENCE_MIN_INTERVAL_SECONDS = 0.045
WINDOW_NAME = "YOLO Garbage Demo"
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080
WEB_HTML_PATH = Path(__file__).parent / "wifiDATA" / "index.html"
WEB_STREAM_UPDATE_INTERVAL_SECONDS = 0.08
WEB_STREAM_CLIENT_INTERVAL_SECONDS = 0.005
WEB_STREAM_MAX_WIDTH = 960
WEB_STREAM_JPEG_QUALITY = 72
# Arduino Wi-Fi API (由 wifiDATA.ino 提供)；請改成你的 Arduino IP。
ARDUINO_SENSOR_API_URL = "http://192.168.0.33/api/sensor"
ARDUINO_COMMAND_API_BASE_URL = "http://192.168.0.33/api/command/"
ARDUINO_AUTO_DERIVE_COMMAND_API_URL = True
ARDUINO_WIFI_COMMAND_CONNECT_TIMEOUT_SECONDS = 0.8
ARDUINO_WIFI_COMMAND_READ_TIMEOUT_SECONDS = 1.2
ARDUINO_WIFI_COMMAND_RETRY_COUNT = 2
ARDUINO_SENSOR_POLL_SECONDS = 1.0
ARDUINO_SENSOR_PRINT_ENABLED = True
ARDUINO_SENSOR_PRINT_INTERVAL_SECONDS = 1.0
ARDUINO_SENSOR_FETCH_ON_DETECTION_ONLY = True
ARDUINO_SENSOR_TRIGGER_MIN_INTERVAL_SECONDS = 0.35

# 導航模式：偵測到垃圾就前進，無垃圾且前方過近視為牆壁則轉向。
NAVIGATION_MODE_ENABLED = True
NAVIGATION_WALL_DISTANCE_CM = 30
NAVIGATION_SEND_INTERVAL_SECONDS = 0.45
# 垃圾置中死區 (0.0–0.5)：中央區間內直走，超出則轉向追蹤。
NAVIGATION_STEER_DEADZONE = 0.22
# 船體晃動時使用粗對準：只有目標非常偏才轉向，其餘優先前進。
NAVIGATION_HARD_TURN_OFFSET = 0.38
# 就算短暫掉框，仍保留「近期看過垃圾」狀態，避免來回停轉。
NAVIGATION_TARGET_MEMORY_SECONDS = 0.9
# 連續偵測到 N 幀才認定有垃圾，避免單幀誤觸。
NAVIGATION_GARBAGE_STABLE_FRAMES = 3
# 前方距離小於此值時判定已抵達垃圾，送 STOP。
NAVIGATION_APPROACH_STOP_CM = 8
# 距離資料超過此秒數視為過期，不可用來判定 STOP。
NAVIGATION_DISTANCE_STALE_SECONDS = 1.0

# Arduino 序列通訊設定，需依實際裝置修改 COM 埠。
SERIAL_PORT = "COM3"
SERIAL_BAUD_RATE = 115200
SERIAL_TIMEOUT = 1
SEND_INTERVAL_SECONDS = 1.0
STABLE_FRAME_THRESHOLD = 5

# 只處理這些指定類別，其餘偵測結果直接忽略。
TARGET_CLASSES = {
    "bottle",
    "cup",
    "wine glass",
    "banana",
    "apple",
    "orange",
}

# 每一種物件在畫面上的外框顏色。
BOX_COLORS = {
    "bottle": (0, 200, 255),
    "cup": (255, 200, 0),
    "wine glass": (255, 120, 0),
    "banana": (0, 255, 255),
    "apple": (0, 255, 0),
    "orange": (0, 128, 255),
}

# Python 端物件名稱對應到 Arduino 端要接收的命令字串。
ARDUINO_COMMANDS = {
    "bottle": "BOTTLE",
    "cup": "CUP",
    "wine glass": "WINE_GLASS",
    "banana": "BANANA",
    "apple": "APPLE",
    "orange": "ORANGE",
}


# OpenCV 直接讀取串流的備援類別。
class OpenCVStream:
    def __init__(self, url):
        self.mode = "opencv"
        self.cap = cv2.VideoCapture(url)
        if not self.cap.isOpened():
            raise RuntimeError("OpenCV 無法直接開啟串流")

    def read(self):
        # 維持與 MJPEGStream 相同的 read() 介面。
        return self.cap.read()

    def release(self):
        self.cap.release()


# 針對 multipart MJPEG 串流自行拆封包與解碼影像。
class MJPEGStream:
    def __init__(self, url):
        self.mode = "mjpeg"
        self.url = url
        self.response = None
        self.iterator = None
        self.buffer = b""
        self.connect_timeout = 5
        self.read_timeout = 60
        self.frame_wait_seconds = 20
        self._connect()

    def _connect(self):
        # 某些 IP Camera 需要明確的 Accept header 才會回傳 MJPEG。
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "multipart/x-mixed-replace,*/*"}
        self.response = requests.get(
            self.url,
            stream=True,
            timeout=(self.connect_timeout, self.read_timeout),
            headers=headers,
        )
        self.response.raise_for_status()
        self.iterator = self.response.iter_content(chunk_size=8192)
        self.buffer = b""

    def read(self):
        # 在限定時間內持續累積位元組，直到組出一張完整 JPEG。
        deadline = time.monotonic() + self.frame_wait_seconds

        while time.monotonic() < deadline:
            try:
                chunk = next(self.iterator)
            except StopIteration:
                # Some cameras close and reopen multipart streams periodically.
                self.release()
                self._connect()
                continue
            except requests.RequestException:
                self.release()
                try:
                    self._connect()
                    continue
                except requests.RequestException:
                    return False, None

            if not chunk:
                continue

            self.buffer += chunk

            start = self.buffer.find(b"\xff\xd8")
            if start == -1:
                # 緩衝區過大時保留尾端，避免記憶體持續增加。
                if len(self.buffer) > 2_000_000:
                    self.buffer = self.buffer[-4096:]
                continue

            end = self.buffer.find(b"\xff\xd9", start + 2)
            if end == -1:
                if start > 0:
                    self.buffer = self.buffer[start:]
                continue

            jpg = self.buffer[start : end + 2]
            self.buffer = self.buffer[end + 2 :]
            frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                return True, frame

        # 超時仍沒收到完整影像時，回傳失敗讓主流程處理重連。
        return False, None

    def release(self):
        if self.response is not None:
            self.response.close()


# 在背景執行緒持續抓幀，讓主迴圈不再因等待網路而阻塞 YOLO 推論。
class FrameGrabber:
    def __init__(self, stream_factory, url, max_fails_before_reconnect=3):
        self._factory = stream_factory
        self._url = url
        self._max_fails = max_fails_before_reconnect
        self._stream = stream_factory(url)
        self.mode = self._stream.mode
        self._frame = None
        self._lock = Lock()
        self._stop = False
        self._fail_count = 0
        self._thread = Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def _grab_loop(self):
        while not self._stop:
            try:
                ok, frame = self._stream.read()
            except Exception:
                ok, frame = False, None

            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._fail_count = 0
            else:
                with self._lock:
                    self._fail_count += 1
                    fails = self._fail_count

                if fails >= self._max_fails:
                    print("FrameGrabber: 串流中斷，嘗試重連...")
                    try:
                        self._stream.release()
                    except Exception:
                        pass
                    time.sleep(1)
                    try:
                        self._stream = self._factory(self._url)
                        self.mode = self._stream.mode
                        with self._lock:
                            self._fail_count = 0
                        print("FrameGrabber: 重連成功")
                    except Exception as reconnect_error:
                        print(f"FrameGrabber: 重連失敗 {reconnect_error}")

    def read(self):
        with self._lock:
            frame = self._frame
        return frame is not None, frame

    def release(self):
        self._stop = True
        self._thread.join(timeout=2)
        try:
            self._stream.release()
        except Exception:
            pass


# 管理 Python 與 Arduino 之間的序列通訊與命令去抖動。
class ArduinoBridge:
    def __init__(self, port, baud_rate, timeout=1, command_api_base_url=""):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.command_api_base_url = (command_api_base_url or "").rstrip("/") + "/" if command_api_base_url else ""
        self.use_wifi_command = bool(self.command_api_base_url)
        self.connection = None
        self.http_session = requests.Session()
        self.command_queue = Queue(maxsize=1)
        self.stop_worker = False
        self.command_worker = None
        self.status_text = "disabled"
        self.last_sent_command = None
        self.last_sent_time = 0.0
        self.pending_label = None
        self.pending_count = 0
        self.wifi_connect_timeout = max(float(ARDUINO_WIFI_COMMAND_CONNECT_TIMEOUT_SECONDS), 0.2)
        self.wifi_read_timeout = max(float(ARDUINO_WIFI_COMMAND_READ_TIMEOUT_SECONDS), 0.2)
        self.wifi_retry_count = max(int(ARDUINO_WIFI_COMMAND_RETRY_COUNT), 1)

    def _send_wifi_command(self, command):
        url = f"{self.command_api_base_url}{command}"
        last_error = None

        for attempt in range(1, self.wifi_retry_count + 1):
            try:
                response = self.http_session.get(
                    url,
                    timeout=(self.wifi_connect_timeout, self.wifi_read_timeout),
                )
                response.raise_for_status()
                return True, None, url, attempt
            except requests.RequestException as error:
                last_error = error

        return False, last_error, url, self.wifi_retry_count

    def _wifi_command_loop(self):
        while not self.stop_worker:
            try:
                command = self.command_queue.get(timeout=0.2)
            except Empty:
                continue

            ok, error, url, used_attempts = self._send_wifi_command(command)
            if ok:
                self.status_text = f"sent {command} (wifi)"
                print(f"已送出 Arduino Wi-Fi 指令: {command}")
            else:
                self.status_text = "wifi command offline"
                print(f"Arduino Wi-Fi 指令失敗: url={url} retries={used_attempts} error={error}")

    def connect(self):
        if self.use_wifi_command:
            self.status_text = "wifi command mode"
            print(f"Arduino Wi-Fi 命令模式: {self.command_api_base_url}")
            self.stop_worker = False
            self.command_worker = Thread(target=self._wifi_command_loop, daemon=True)
            self.command_worker.start()
            return True

        # 若環境沒有 pyserial，就保留偵測流程但跳過 Arduino。
        if serial is None:
            self.status_text = "pyserial missing"
            print("未安裝 pyserial，Arduino 通訊已停用")
            return False

        try:
            # 新連線後暫停 2 秒，讓 Arduino 完成重置開機。
            self.connection = serial.Serial(self.port, self.baud_rate, timeout=self.timeout)
            time.sleep(2)
            self.status_text = f"connected {self.port}"
            print(f"Arduino 已連線: {self.port} @ {self.baud_rate}")
            return True
        except SerialException as error:
            self.connection = None
            self.status_text = f"offline {self.port}"
            print(f"Arduino 連線失敗: {error}")
            return False

    def close(self):
        self.stop_worker = True
        if self.command_worker is not None:
            self.command_worker.join(timeout=0.5)

        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.http_session.close()

    def get_status(self):
        return self.status_text

    def poll(self):
        # 非阻塞讀取 Arduino 回覆，方便在主畫面顯示目前狀態。
        if self.use_wifi_command:
            return

        if self.connection is None:
            return

        try:
            if self.connection.in_waiting:
                message = self.connection.readline().decode("utf-8", errors="ignore").strip()
                if message:
                    print(f"Arduino: {message}")
        except SerialException:
            self.close()
            self.status_text = f"offline {self.port}"

    def update_detection(self, counts):
        # 只取目前畫面中數量最多的類別，避免一次送出多個命令。
        label = get_primary_label(counts)

        # 沒有偵測到目標物件時，不主動送出待機命令。
        if label is None:
            self.pending_label = None
            self.pending_count = 0
            return

        if label == self.pending_label:
            self.pending_count += 1
        else:
            self.pending_label = label
            self.pending_count = 1

        # 連續多幀都看到同一類別時才發送，降低偵測跳動造成的誤動作。
        if self.pending_count < STABLE_FRAME_THRESHOLD:
            return

        self.send_command(label)

    def _send_resolved_command(self, command):
        if command is None:
            return

        now = time.monotonic()

        # 相同命令在短時間內不重複傳送，避免命令洗版。
        if command == self.last_sent_command and now - self.last_sent_time < SEND_INTERVAL_SECONDS:
            return

        if self.use_wifi_command:
            self.last_sent_command = command
            self.last_sent_time = now
            if self.command_queue.full():
                try:
                    self.command_queue.get_nowait()
                except Empty:
                    pass
            self.command_queue.put_nowait(command)
            return

        if self.connection is None and not self.connect():
            return

        try:
            self.connection.write(f"{command}\n".encode("ascii"))
            self.last_sent_command = command
            self.last_sent_time = now
            self.status_text = f"sent {command}"
            print(f"已送出 Arduino 指令: {command}")
        except SerialException as error:
            self.close()
            self.status_text = f"send failed {self.port}"
            print(f"Arduino 傳送失敗: {error}")

    def send_command(self, label):
        # 只在有有效類別時送出對應命令。
        command = ARDUINO_COMMANDS.get(label)
        self._send_resolved_command(command)

    def send_raw_command(self, command):
        # 導航模式可直接送 FORWARD / TURN_RIGHT / STOP 等原始命令。
        resolved = (command or "").strip().upper()
        if not resolved:
            return
        self._send_resolved_command(resolved)


class ArduinoSensorClient:
    def __init__(
        self,
        api_url,
        poll_seconds=0.5,
        print_enabled=False,
        print_interval_seconds=1.0,
        fetch_on_detection_only=False,
        trigger_min_interval_seconds=0.35,
    ):
        self.api_url = (api_url or "").strip()
        self.poll_seconds = max(float(poll_seconds), 0.2)
        self.next_poll_time = 0.0
        self.distance_cm = None
        self.motor_state = "unknown"
        self.status_text = "disabled"
        self.session = requests.Session()
        self.lock = Lock()
        self.stop_worker = False
        self.worker = None
        self.print_enabled = bool(print_enabled)
        self.print_interval_seconds = max(float(print_interval_seconds), 0.2)
        self.last_print_time = 0.0
        self.last_print_signature = None
        self.last_print_status = self.status_text
        self.fetch_on_detection_only = bool(fetch_on_detection_only)
        self.trigger_min_interval_seconds = max(float(trigger_min_interval_seconds), 0.2)
        self.last_trigger_time = 0.0
        self.last_trigger_label = None
        self.last_update_time = 0.0

        if self.api_url:
            if self.fetch_on_detection_only:
                self.status_text = "waiting trigger"
            else:
                self.status_text = "connecting"

    def _fetch_once(self):
        response = self.session.get(self.api_url, timeout=(0.15, 0.25))
        response.raise_for_status()
        payload = response.json()

        distance_raw = payload.get("distance_cm")
        with self.lock:
            if distance_raw is not None:
                self.distance_cm = int(distance_raw)
            self.motor_state = str(payload.get("motor_state", "unknown"))
            self.status_text = "online"
            self.last_update_time = time.monotonic()

            distance_cm = self.distance_cm
            motor_state = self.motor_state
            status_text = self.status_text

        if self.print_enabled:
            now = time.monotonic()
            signature = (distance_cm, motor_state, status_text)
            should_print = (
                signature != self.last_print_signature
                and now - self.last_print_time >= self.print_interval_seconds
            )
            if should_print:
                print(f"[Sensor] distance_cm={distance_cm} motor_state={motor_state} status={status_text}")
                self.last_print_signature = signature
                self.last_print_status = status_text
                self.last_print_time = now

    def _poll_loop(self):
        while not self.stop_worker:
            try:
                self._fetch_once()
            except Exception:
                with self.lock:
                    self.status_text = "offline"

                if self.print_enabled and self.last_print_status != "offline":
                    print("[Sensor] status=offline")
                    self.last_print_status = "offline"

            time.sleep(self.poll_seconds)

    def start(self):
        if not self.api_url:
            return

        # 僅在「偵測觸發模式」關閉時啟動背景固定輪詢。
        if self.fetch_on_detection_only:
            return

        self.stop_worker = False
        self.worker = Thread(target=self._poll_loop, daemon=True)
        self.worker.start()

    def poll(self):
        # 改為背景執行，主迴圈不再阻塞。
        return

    def update_with_detection(self, counts):
        if not self.api_url or not self.fetch_on_detection_only:
            return

        label = get_primary_label(counts)
        if label is None:
            return

        now = time.monotonic()
        if label == self.last_trigger_label and now - self.last_trigger_time < self.trigger_min_interval_seconds:
            return

        try:
            self._fetch_once()
            self.last_trigger_time = now
            self.last_trigger_label = label
        except Exception:
            with self.lock:
                self.status_text = "offline"

            if self.print_enabled and self.last_print_status != "offline":
                print("[Sensor] status=offline")
                self.last_print_status = "offline"

    def close(self):
        self.stop_worker = True
        if self.worker is not None:
            self.worker.join(timeout=0.5)
        self.session.close()

    def get_overlay_text(self):
        with self.lock:
            distance_cm = self.distance_cm
            motor_state = self.motor_state
            status_text = self.status_text

        if distance_cm is None:
            distance_text = "N/A"
        else:
            distance_text = f"{distance_cm} cm"

        return distance_text, motor_state, status_text

    def get_latest_distance_cm(self):
        with self.lock:
            return self.distance_cm

    def get_latest_distance_info(self):
        with self.lock:
            distance_cm = self.distance_cm
            last_update_time = self.last_update_time

        if last_update_time <= 0:
            return distance_cm, None

        return distance_cm, max(0.0, time.monotonic() - last_update_time)


class NavigationController:
    def __init__(
        self,
        wall_distance_cm=30,
        send_interval_seconds=0.45,
        steer_deadzone=0.22,
        hard_turn_offset=0.38,
        target_memory_seconds=0.9,
        garbage_stable_frames=3,
        approach_stop_cm=12,
        distance_stale_seconds=1.0,
    ):
        self.wall_distance_cm = max(int(wall_distance_cm), 5)
        self.send_interval_seconds = max(float(send_interval_seconds), 0.1)
        self.steer_deadzone = max(float(steer_deadzone), 0.05)
        self.hard_turn_offset = max(float(hard_turn_offset), self.steer_deadzone)
        self.target_memory_seconds = max(float(target_memory_seconds), 0.1)
        self.garbage_stable_frames = max(int(garbage_stable_frames), 1)
        self.approach_stop_cm = max(int(approach_stop_cm), 1)
        self.distance_stale_seconds = max(float(distance_stale_seconds), 0.2)
        self.last_command = None
        self.last_command_time = 0.0
        self._garbage_frame_count = 0
        self._last_garbage_seen_time = 0.0

    def decide_command(self, counts, distance_cm, distance_age_seconds, center_x):
        now = time.monotonic()
        has_garbage = get_primary_label(counts) is not None

        distance_valid = (
            distance_cm is not None
            and distance_cm > 0
            and distance_cm < 900
            and distance_age_seconds is not None
            and distance_age_seconds <= self.distance_stale_seconds
        )

        if has_garbage:
            self._garbage_frame_count += 1
            self._last_garbage_seen_time = now
        else:
            self._garbage_frame_count = 0

        recent_target = (now - self._last_garbage_seen_time) <= self.target_memory_seconds

        # 連續幀數不足且近期也沒看到垃圾時，改用避障邏輯。
        if self._garbage_frame_count < self.garbage_stable_frames and not recent_target:
            if distance_valid and distance_cm <= self.wall_distance_cm:
                return "TURN_RIGHT"
            return None

        # 已確認有垃圾 ─────────────────────────────────────────────
        # 距離夠近，視為到位，停車。
        if distance_valid and distance_cm <= self.approach_stop_cm:
            return "STOP"

        # 船用粗對準策略：只有目標非常偏才轉向，其餘情況優先前進。
        if center_x is not None:
            if center_x < 0.5 - self.hard_turn_offset:
                return "TURN_LEFT"
            if center_x > 0.5 + self.hard_turn_offset:
                return "TURN_RIGHT"

        return "FORWARD"

    def update(self, counts, distance_cm, distance_age_seconds, center_x, arduino_bridge):
        command = self.decide_command(counts, distance_cm, distance_age_seconds, center_x)
        if command is None:
            return

        now = time.monotonic()
        if command == self.last_command and now - self.last_command_time < self.send_interval_seconds:
            return

        arduino_bridge.send_raw_command(command)
        self.last_command = command
        self.last_command_time = now
        center_text = f"{center_x:.2f}" if center_x is not None else "N/A"
        age_text = f"{distance_age_seconds:.2f}s" if distance_age_seconds is not None else "N/A"
        print(f"[Nav] {command}  dist={distance_cm}cm  age={age_text}  cx={center_text}")


def open_stream(url):
    # 先優先使用較穩定的 MJPEG 讀法，失敗才回退到 OpenCV 內建串流。
    try:
        stream = MJPEGStream(url)
        print("已使用 MJPEG 模式開啟串流")
        return stream
    except requests.RequestException:
        pass

    try:
        stream = OpenCVStream(url)
        print("已切換為 OpenCV 備援讀流")
        return stream
    except RuntimeError as error:
        raise RuntimeError(f"無法開啟鏡頭串流: {error}") from error


def get_primary_label(counts):
    # 從目前各類別計數中挑出數量最多者作為主類別。
    if not counts:
        return None

    return max(counts, key=counts.get)


def resolve_target_class_ids(model_names):
    # 先把目標類別名稱轉成 YOLO class id，推論時可直接過濾加速。
    target_ids = []
    for class_id, class_name in model_names.items():
        if class_name in TARGET_CLASSES:
            target_ids.append(int(class_id))
    return sorted(target_ids)


def derive_command_api_base_url(sensor_api_url):
    # 從 /api/sensor 反推出 /api/command/，避免兩個 URL 手動填錯不同 IP。
    if not sensor_api_url:
        return ""

    parsed = urlparse(sensor_api_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""

    return f"{parsed.scheme}://{parsed.netloc}/api/command/"


def draw_info_panel(frame, counts, fps, status_text, arduino_status, sensor_distance, sensor_motor, sensor_status):
    # 在畫面左上角繪製狀態資訊面板。
    height, width = frame.shape[:2]

    panel_width = 320
    panel_height = 300
    x1, y1 = 10, 10
    x2, y2 = x1 + panel_width, y1 + panel_height

    cv2.rectangle(frame, (x1, y1), (x2, y2), (25, 25, 25), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 180), 2)

    cv2.putText(frame, "Garbage Detection Demo", (25, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    cv2.putText(frame, f"FPS: {fps:.1f}", (25, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
    cv2.putText(frame, f"Status: {status_text}", (25, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 180), 2)
    cv2.putText(frame, f"Arduino: {arduino_status}", (25, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (120, 220, 255), 2)
    cv2.putText(frame, f"Sensor API: {sensor_status}", (25, 152), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 210, 130), 2)
    cv2.putText(frame, f"Distance: {sensor_distance}", (25, 174), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
    cv2.putText(frame, f"Motor: {sensor_motor}", (25, 196), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)

    total = 0
    y = 224
    for label in sorted(TARGET_CLASSES):
        # 逐項顯示當前畫面中各類別的數量。
        count = counts.get(label, 0)
        total += count
        cv2.putText(frame, f"{label}: {count}", (25, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        y += 22

    cv2.putText(frame, f"Visible total: {total}", (25, y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 180), 2)
    cv2.putText(frame, "Press Q to quit", (width - 190, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

    return frame


def draw_boxes(frame, result):
    # 根據 YOLO 結果畫框，並統計指定類別在畫面中的數量。
    # 同時回傳信心度最高的垃圾框中心 X 比例 (0.0 左 ~ 1.0 右)，供導航轉向用。
    counts = defaultdict(int)
    best_center_x = None
    best_conf = -1.0

    if result.boxes is None:
        return frame, counts, best_center_x

    names = result.names
    img_w = frame.shape[1]

    for box in result.boxes:
        # 讀取每個框的類別、信心值與座標資訊。
        class_id = int(box.cls[0])
        confidence = float(box.conf[0])
        class_name = names[class_id]

        # 只保留專案關心的垃圾分類物件。
        if class_name not in TARGET_CLASSES:
            continue

        counts[class_name] += 1

        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
        color = BOX_COLORS.get(class_name, (0, 200, 255))

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            f"{class_name} {confidence:.2f}",
            (x1, max(y1 - 10, 25)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )

        # 記錄信心度最高的框中心，用於導航轉向。
        if confidence > best_conf and img_w > 0:
            best_conf = confidence
            best_center_x = ((x1 + x2) / 2.0) / img_w

    return frame, counts, best_center_x


def main():
    # 載入模型後，初始化影像串流與 Arduino 連線。
    print("載入 YOLO 模型中...")
    model = YOLO(MODEL_NAME)
    target_class_ids = resolve_target_class_ids(model.names)
    if target_class_ids:
        print(f"YOLO 類別過濾已啟用: {target_class_ids}")
    else:
        print("警告: 找不到 TARGET_CLASSES 對應的 class id，將回退為全類別推論")

    # 啟動時先做一次暖機，降低第一幀推論卡頓。
    try:
        warmup = np.zeros((IMG_SIZE, IMG_SIZE, 3), dtype=np.uint8)
        model(
            warmup,
            imgsz=IMG_SIZE,
            conf=CONFIDENCE,
            iou=IOU_THRESHOLD,
            max_det=MAX_DETECTIONS,
            classes=target_class_ids if target_class_ids else None,
            verbose=False,
        )
    except Exception as warmup_error:
        print(f"YOLO 暖機失敗，將繼續執行: {warmup_error}")

    print("連接鏡頭串流中...")
    cap = FrameGrabber(open_stream, STREAM_URL)

    command_api_base_url = ARDUINO_COMMAND_API_BASE_URL
    if ARDUINO_AUTO_DERIVE_COMMAND_API_URL:
        derived = derive_command_api_base_url(ARDUINO_SENSOR_API_URL)
        if derived:
            if command_api_base_url and command_api_base_url.rstrip("/") != derived.rstrip("/"):
                print(f"偵測到命令 URL 與感測器 URL 不同，已改用自動推導: {derived}")
            command_api_base_url = derived

    arduino = ArduinoBridge(
        SERIAL_PORT,
        SERIAL_BAUD_RATE,
        timeout=SERIAL_TIMEOUT,
        command_api_base_url=command_api_base_url,
    )
    arduino.connect()
    sensor_client = ArduinoSensorClient(
        ARDUINO_SENSOR_API_URL,
        poll_seconds=ARDUINO_SENSOR_POLL_SECONDS,
        print_enabled=ARDUINO_SENSOR_PRINT_ENABLED,
        print_interval_seconds=ARDUINO_SENSOR_PRINT_INTERVAL_SECONDS,
        fetch_on_detection_only=ARDUINO_SENSOR_FETCH_ON_DETECTION_ONLY and not NAVIGATION_MODE_ENABLED,
        trigger_min_interval_seconds=ARDUINO_SENSOR_TRIGGER_MIN_INTERVAL_SECONDS,
    )
    navigator = NavigationController(
        wall_distance_cm=NAVIGATION_WALL_DISTANCE_CM,
        send_interval_seconds=NAVIGATION_SEND_INTERVAL_SECONDS,
        steer_deadzone=NAVIGATION_STEER_DEADZONE,
        hard_turn_offset=NAVIGATION_HARD_TURN_OFFSET,
        target_memory_seconds=NAVIGATION_TARGET_MEMORY_SECONDS,
        garbage_stable_frames=NAVIGATION_GARBAGE_STABLE_FRAMES,
        approach_stop_cm=NAVIGATION_APPROACH_STOP_CM,
        distance_stale_seconds=NAVIGATION_DISTANCE_STALE_SECONDS,
    )
    sensor_client.start()
    if ARDUINO_SENSOR_API_URL:
        print(f"Arduino 感測器 API: {ARDUINO_SENSOR_API_URL}")
    else:
        print("未設定 Arduino 感測器 API，將略過距離資料抓取")
    web_server = DetectionWebServer(
        WEB_HTML_PATH,
        host=WEB_HOST,
        port=WEB_PORT,
        stream_client_interval_seconds=WEB_STREAM_CLIENT_INTERVAL_SECONDS,
        stream_max_width=WEB_STREAM_MAX_WIDTH,
        stream_jpeg_quality=WEB_STREAM_JPEG_QUALITY,
    )
    web_server.start()

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    previous_time = time.time()
    last_web_update_time = 0.0
    status_text = "waiting for frame..."
    last_result = None
    last_infer_time = 0.0

    while True:
        # 背景執行緒已持續抓幀，這裡只取最新一張，不阻塞推論。
        ret, frame = cap.read()

        if not ret or frame is None:
            # 尚未收到第一幀或串流暫時中斷，等 FrameGrabber 自行重連。
            status_text = "waiting for frame..."
            time.sleep(0.01)
            continue

        # 對當前畫面執行 YOLO 推論；在短時間內重用前次結果以提升流暢度。
        now_infer = time.monotonic()
        should_infer = (
            last_result is None
            or now_infer - last_infer_time >= INFERENCE_MIN_INTERVAL_SECONDS
        )
        if should_infer:
            results = model(
                frame,
                imgsz=IMG_SIZE,
                conf=CONFIDENCE,
                iou=IOU_THRESHOLD,
                max_det=MAX_DETECTIONS,
                classes=target_class_ids if target_class_ids else None,
                verbose=False,
            )
            last_result = results[0]
            last_infer_time = now_infer

        result = last_result
        if result is None:
            continue

        # 繪製框線後，把主類別同步送到 Arduino。
        frame, counts, garbage_center_x = draw_boxes(frame, result)
        if not NAVIGATION_MODE_ENABLED:
            arduino.update_detection(counts)
        arduino.poll()
        sensor_client.update_with_detection(counts)
        sensor_client.poll()
        sensor_distance, sensor_motor, sensor_status = sensor_client.get_overlay_text()
        latest_distance_cm, latest_distance_age_seconds = sensor_client.get_latest_distance_info()
        if NAVIGATION_MODE_ENABLED:
            navigator.update(counts, latest_distance_cm, latest_distance_age_seconds, garbage_center_x, arduino)

        # 以相鄰兩幀時間差估算即時 FPS。
        current_time = time.time()
        fps = 1.0 / max(current_time - previous_time, 1e-6)
        previous_time = current_time
        status_text = f"running ({cap.mode})"

        # 網頁串流限頻更新，避免每幀 JPEG 編碼拉低 YOLO FPS。
        now_mono = time.monotonic()
        if now_mono - last_web_update_time >= WEB_STREAM_UPDATE_INTERVAL_SECONDS:
            web_server.update(frame, counts, fps, status_text, arduino.get_status())
            last_web_update_time = now_mono

        # 顯示標註後的畫面，按 Q 結束程式。
        draw_info_panel(
            frame,
            counts,
            fps,
            status_text,
            arduino.get_status(),
            sensor_distance,
            sensor_motor,
            sensor_status,
        )
        cv2.imshow(WINDOW_NAME, frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break

    cap.release()
    sensor_client.close()
    arduino.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    # 直接執行此檔案時，啟動完整辨識流程。
    main()
