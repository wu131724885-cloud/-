"""A7A 單機版雙體船控制主程式。

這個檔案整合了鏡頭串流、ncnn (Vulkan) YOLO 偵測、Arduino R4 序列通訊、
Flask 網頁監看，以及手動接管控制等功能。

目標是讓板子在沒有電腦常駐的情況下，也能獨立提供監看、導航與除錯介面。
"""

import atexit
import json
import os
import re
import socket
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from threading import Event, Lock, Thread

import requests
from flask import Flask, Response, jsonify, request, send_file

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

try:
    import serial
    from serial import SerialException
except ImportError:
    serial = None
    SerialException = Exception

try:
    import fcntl
except ImportError:
    fcntl = None


# === 執行期設定 ===
# 這一段集中管理影像、網頁、導航、WiFi 與 Serial 的主要參數。
# 大部分值都能透過環境變數覆寫，方便在 A7A 現場直接調校。

BASE_DIR = Path(__file__).resolve().parent

# OpenVINO INT8 模型路徑 (Ultralytics YOLO 格式)
YOLO_MODEL_PATH = str(BASE_DIR / "yolo26n_int8_openvino_model")

# COCO 80-class 類別名稱 (來源: metadata.yaml)
NCNN_CLASS_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake",
    "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
    "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]

# 影像與 ncnn 推論參數。
STREAM_URL = os.environ.get("STREAM_URL", "http://192.168.4.1/api/v1/stream")
CAMERA_DEVICE = (os.environ.get("CAMERA_DEVICE") or "").strip()
try:
    AUTO_CAMERA_SCAN_MAX_INDEX = max(1, int(os.environ.get("AUTO_CAMERA_SCAN_MAX_INDEX", "4")))
except (TypeError, ValueError):
    AUTO_CAMERA_SCAN_MAX_INDEX = 4
CONFIDENCE = float(os.environ.get("CONFIDENCE", "0.35"))
NMS_THRESHOLD = float(os.environ.get("NMS_THRESHOLD", "0.45"))
IMG_SIZE = 224
MAX_DETECTIONS = int(os.environ.get("MAX_DETECTIONS", "80"))
try:
    INFERENCE_MIN_INTERVAL_SECONDS = max(
        0.0,
        float(os.environ.get("INFERENCE_MIN_INTERVAL_SECONDS", "0.033")),
    )
except (TypeError, ValueError):
    INFERENCE_MIN_INTERVAL_SECONDS = 0.033
YOLO_TRACKING_ENABLED = os.environ.get("ENABLE_YOLO_TRACKING", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
WINDOW_NAME = "YOLO OpenVINO INT8"
UPSIDE_DOWN_BOTTLE_DETECTION_ENABLED = os.environ.get(
    "ENABLE_UPSIDE_DOWN_BOTTLE_DETECTION", "1"
).strip().lower() in {"1", "true", "yes", "on"}
SIDEWAYS_BOTTLE_DETECTION_ENABLED = os.environ.get(
    "ENABLE_SIDEWAYS_BOTTLE_DETECTION", "1"
).strip().lower() in {"1", "true", "yes", "on"}
try:
    UPSIDE_DOWN_BOTTLE_CONFIDENCE = float(os.environ.get("UPSIDE_DOWN_BOTTLE_CONFIDENCE", "0.22"))
except (TypeError, ValueError):
    UPSIDE_DOWN_BOTTLE_CONFIDENCE = 0.22
try:
    UPSIDE_DOWN_BOTTLE_MAX_DETECTIONS = max(1, int(os.environ.get("UPSIDE_DOWN_BOTTLE_MAX_DETECTIONS", "6")))
except (TypeError, ValueError):
    UPSIDE_DOWN_BOTTLE_MAX_DETECTIONS = 6
try:
    BOTTLE_ASSIST_CONFIDENCE = float(os.environ.get("BOTTLE_ASSIST_CONFIDENCE", "0.16"))
except (TypeError, ValueError):
    BOTTLE_ASSIST_CONFIDENCE = 0.16
try:
    SIDEWAYS_BOTTLE_CONFIDENCE = float(os.environ.get("SIDEWAYS_BOTTLE_CONFIDENCE", "0.16"))
except (TypeError, ValueError):
    SIDEWAYS_BOTTLE_CONFIDENCE = 0.16
try:
    # 目前 OpenVINO 匯出的 YOLO 模型為固定輸入大小；若補推論用更大的 imgsz，
    # 會在第二次 predict() 直接撞上 input tensor shape mismatch。
    BOTTLE_ASSIST_IMAGE_SIZE = int(os.environ.get("BOTTLE_ASSIST_IMAGE_SIZE", str(IMG_SIZE)))
except (TypeError, ValueError):
    BOTTLE_ASSIST_IMAGE_SIZE = IMG_SIZE

# 固定 shape 的 OpenVINO 匯出模型不能接受大於主模型輸入的 imgsz。
BOTTLE_ASSIST_IMAGE_SIZE = max(32, min(BOTTLE_ASSIST_IMAGE_SIZE, IMG_SIZE))
try:
    BOTTLE_ASSIST_MAX_DETECTIONS = max(1, int(os.environ.get("BOTTLE_ASSIST_MAX_DETECTIONS", "8")))
except (TypeError, ValueError):
    BOTTLE_ASSIST_MAX_DETECTIONS = 8
try:
    SIDEWAYS_BOTTLE_MAX_DETECTIONS = max(1, int(os.environ.get("SIDEWAYS_BOTTLE_MAX_DETECTIONS", "8")))
except (TypeError, ValueError):
    SIDEWAYS_BOTTLE_MAX_DETECTIONS = 8
try:
    BOTTLE_ASSIST_TRIGGER_CONFIDENCE = float(os.environ.get("BOTTLE_ASSIST_TRIGGER_CONFIDENCE", "0.60"))
except (TypeError, ValueError):
    BOTTLE_ASSIST_TRIGGER_CONFIDENCE = 0.60
try:
    BOTTLE_ASSIST_INTERVAL_SECONDS = max(
        0.0,
        float(os.environ.get("BOTTLE_ASSIST_INTERVAL_SECONDS", "0.10")),
    )
except (TypeError, ValueError):
    BOTTLE_ASSIST_INTERVAL_SECONDS = 0.10
try:
    BOTTLE_ASSIST_IOU_THRESHOLD = float(os.environ.get("BOTTLE_ASSIST_IOU_THRESHOLD", "0.45"))
except (TypeError, ValueError):
    BOTTLE_ASSIST_IOU_THRESHOLD = 0.45

# 網頁監看參數。
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = 8080
WEB_HTML_PATH = Path(__file__).parent / "船體控制.html"
try:
    WEB_STREAM_TARGET_FPS = max(1.0, float(os.environ.get("WEB_STREAM_TARGET_FPS", "30")))
except (TypeError, ValueError):
    WEB_STREAM_TARGET_FPS = 30.0
WEB_STREAM_FRAME_INTERVAL_SECONDS = 1.0 / WEB_STREAM_TARGET_FPS
try:
    WEB_STREAM_UPDATE_INTERVAL_SECONDS = max(
        WEB_STREAM_FRAME_INTERVAL_SECONDS,
        float(os.environ.get("WEB_STREAM_UPDATE_INTERVAL_SECONDS", "0.033")),
    )
except (TypeError, ValueError):
    WEB_STREAM_UPDATE_INTERVAL_SECONDS = WEB_STREAM_FRAME_INTERVAL_SECONDS
try:
    WEB_STREAM_CLIENT_INTERVAL_SECONDS = max(
        0.0,
        float(os.environ.get("WEB_STREAM_CLIENT_INTERVAL_SECONDS", str(WEB_STREAM_FRAME_INTERVAL_SECONDS))),
    )
except (TypeError, ValueError):
    WEB_STREAM_CLIENT_INTERVAL_SECONDS = WEB_STREAM_FRAME_INTERVAL_SECONDS
WEB_STREAM_MAX_WIDTH = int(os.environ.get("WEB_STREAM_MAX_WIDTH", "960"))
WEB_STREAM_JPEG_QUALITY = int(os.environ.get("WEB_STREAM_JPEG_QUALITY", "72"))
ENABLE_FFMPEG_COMPOSITOR = os.environ.get("ENABLE_FFMPEG_COMPOSITOR", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
ENABLE_LOCAL_PREVIEW = (os.environ.get("ENABLE_LOCAL_PREVIEW", "1").strip().lower() in {"1", "true", "yes", "on"})
AUTO_OPEN_BROWSER = (os.environ.get("AUTO_OPEN_BROWSER", "1").strip().lower() in {"1", "true", "yes", "on"})
try:
    AUTO_OPEN_BROWSER_DELAY_SECONDS = max(0.0, float(os.environ.get("AUTO_OPEN_BROWSER_DELAY_SECONDS", "1.0")))
except (TypeError, ValueError):
    AUTO_OPEN_BROWSER_DELAY_SECONDS = 1.0
CAMERA_WIDTH = int(os.environ.get("CAMERA_WIDTH", "640"))
CAMERA_HEIGHT = int(os.environ.get("CAMERA_HEIGHT", "480"))
CAMERA_FPS = int(os.environ.get("CAMERA_FPS", "30"))

# 自動導航與 WiFi 監看參數。
NAVIGATION_MODE_ENABLED = True
NAVIGATION_SEND_INTERVAL_SECONDS = 0.45
NAVIGATION_HARD_TURN_OFFSET = 0.38
NAVIGATION_OBSTACLE_DISTANCE_CM = 35
NAVIGATION_CAPTURE_DISTANCE_CM = 8
WIFI_INTERFACE = os.environ.get("WIFI_INTERFACE", "wlan0")
WIFI_SIGNAL_POLL_INTERVAL_SECONDS = float(os.environ.get("WIFI_SIGNAL_POLL_INTERVAL_SECONDS", "1.0"))
WIFI_SIGNAL_WEAK_DBM = int(os.environ.get("WIFI_SIGNAL_WEAK_DBM", "-58"))
WIFI_SIGNAL_RECOVER_DBM = int(os.environ.get("WIFI_SIGNAL_RECOVER_DBM", "-52"))
WIFI_AUTO_SWITCH_ENABLED = os.environ.get("WIFI_AUTO_SWITCH_ENABLED", "1").strip().lower() in {
    "1", "true", "yes", "on"
}
WIFI_SWITCH_CANDIDATES = [
    item.strip()
    for item in os.environ.get("WIFI_SWITCH_CANDIDATES", "").split(",")
    if item.strip()
]
try:
    WIFI_SWITCH_SCAN_INTERVAL_SECONDS = max(
        2.0,
        float(os.environ.get("WIFI_SWITCH_SCAN_INTERVAL_SECONDS", "5.0")),
    )
except (TypeError, ValueError):
    WIFI_SWITCH_SCAN_INTERVAL_SECONDS = 5.0
try:
    WIFI_SWITCH_COOLDOWN_SECONDS = max(
        5.0,
        float(os.environ.get("WIFI_SWITCH_COOLDOWN_SECONDS", "15.0")),
    )
except (TypeError, ValueError):
    WIFI_SWITCH_COOLDOWN_SECONDS = 15.0
try:
    WIFI_SWITCH_MIN_SIGNAL = max(1, min(100, int(os.environ.get("WIFI_SWITCH_MIN_SIGNAL", "35"))))
except (TypeError, ValueError):
    WIFI_SWITCH_MIN_SIGNAL = 35
try:
    WIFI_SWITCH_MIN_SIGNAL_GAIN = max(0, int(os.environ.get("WIFI_SWITCH_MIN_SIGNAL_GAIN", "8")))
except (TypeError, ValueError):
    WIFI_SWITCH_MIN_SIGNAL_GAIN = 8
try:
    WIFI_SWITCH_PROFILE_REFRESH_SECONDS = max(
        10.0,
        float(os.environ.get("WIFI_SWITCH_PROFILE_REFRESH_SECONDS", "30.0")),
    )
except (TypeError, ValueError):
    WIFI_SWITCH_PROFILE_REFRESH_SECONDS = 30.0
try:
    WIFI_SWITCH_NMCLI_TIMEOUT_SECONDS = max(
        3.0,
        float(os.environ.get("WIFI_SWITCH_NMCLI_TIMEOUT_SECONDS", "8.0")),
    )
except (TypeError, ValueError):
    WIFI_SWITCH_NMCLI_TIMEOUT_SECONDS = 8.0

# Serial、重連節流與控制模式常數。
SERIAL_PORT = os.environ.get("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD_RATE = int(os.environ.get("SERIAL_BAUD_RATE", "115200"))
SERIAL_TIMEOUT = float(os.environ.get("SERIAL_TIMEOUT", "0.1"))
SEND_INTERVAL_SECONDS = 0.3
SERIAL_RECONNECT_COOLDOWN_SECONDS = 2.0
SERIAL_ERROR_LOG_INTERVAL_SECONDS = 3.0
STREAM_RECONNECT_DELAY_SECONDS = 1.5
FRAME_GRABBER_LOG_INTERVAL_SECONDS = 3.0
INFERENCE_ERROR_LOG_INTERVAL_SECONDS = 3.0
INSTANCE_LOCK_PATH = Path(__file__).with_name(".main.py.lock")
CONTROL_MODE_AUTO = "AUTO"
CONTROL_MODE_MANUAL = "MANUAL"
MANUAL_ALLOWED_COMMANDS = {"FORWARD", "TURN_LEFT", "TURN_RIGHT", "STOP"}

_INSTANCE_LOCK_HANDLE = None

TARGET_CLASSES = {
    "bottle", "cup", "wine glass", "bowl", "fork", "knife", "spoon",
    "banana", "apple", "orange", "sandwich", "pizza", "donut", "cake",
    "broccoli", "carrot", "hot dog",
    "cell phone", "scissors", "toothbrush",
}
TARGET_CLASS_IDS = sorted(
    NCNN_CLASS_NAMES.index(class_name)
    for class_name in TARGET_CLASSES
    if class_name in NCNN_CLASS_NAMES
)
BOTTLE_CLASS_NAME = "bottle"
BOTTLE_CLASS_ID = NCNN_CLASS_NAMES.index(BOTTLE_CLASS_NAME) if BOTTLE_CLASS_NAME in NCNN_CLASS_NAMES else -1

# COCO 常用顏色
BOX_COLORS = {
    "person": (220, 220, 220), "bicycle": (0, 200, 0), "car": (0, 0, 200),
    "bottle": (0, 200, 255), "cup": (255, 200, 0), "wine glass": (255, 120, 0),
    "banana": (0, 255, 255), "apple": (0, 255, 0), "orange": (0, 128, 255),
    "bowl": (0, 160, 255), "fork": (220, 220, 220), "knife": (170, 170, 255),
    "spoon": (150, 210, 255), "sandwich": (80, 210, 180), "pizza": (0, 210, 255),
    "donut": (255, 80, 180), "cake": (255, 0, 180),
    "cell phone": (180, 180, 255), "remote": (255, 160, 80),
    "mouse": (180, 160, 255), "keyboard": (100, 100, 100),
    "book": (150, 100, 50), "tv": (0, 100, 200), "laptop": (0, 180, 100),
    "scissors": (255, 60, 120), "toothbrush": (120, 255, 200),
    "vase": (200, 100, 200), "clock": (200, 200, 0),
}


# === 共用 helper ===
def env_flag(name, default=False):
    """把環境變數解析成布林值。"""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def safe_int(value, default=999):
    """把外部資料安全轉成整數，失敗時回傳預設值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    """把外部資料安全轉成浮點數，失敗時回傳預設值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


R4_DEBUG_TELEMETRY_RE = re.compile(
    r"(?:【R4 運行中】)?"
    r"左前:(?P<FL>\d+)cm\s*\|\s*"
    r"右前:(?P<FR>\d+)cm\s*\|\s*"
    r"左側:(?P<SL>\d+)cm\s*\|\s*"
    r"右側:(?P<SR>\d+)cm\s*\|\s*"
    r"中央:(?P<C>\d+)cm\s*\|\|\s*"
    r"狀態:\s*(?P<ST>[A-Za-z0-9_]+)"
)


def parse_r4_telemetry_line(line):
    """把 R4 的 JSON 或除錯文字行轉成統一 telemetry 格式。"""
    text = str(line or "").strip()
    if not text:
        return None

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return {
            "FL": safe_int(data.get("FL", 999)),
            "FR": safe_int(data.get("FR", 999)),
            "SL": safe_int(data.get("SL", 999)),
            "SR": safe_int(data.get("SR", 999)),
            "C": safe_int(data.get("C", 999)),
            "ST": str(data.get("ST", "UNKNOWN") or "UNKNOWN"),
        }

    match = R4_DEBUG_TELEMETRY_RE.search(text)
    if not match:
        return None

    return {
        "FL": safe_int(match.group("FL"), 999),
        "FR": safe_int(match.group("FR"), 999),
        "SL": safe_int(match.group("SL"), 999),
        "SR": safe_int(match.group("SR"), 999),
        "C": safe_int(match.group("C"), 999),
        "ST": str(match.group("ST") or "UNKNOWN"),
    }


def get_primary_label(counts):
    """從 YOLO 類別統計中挑出目前數量最多的類別。"""
    if not counts:
        return None
    return max(counts, key=counts.get)


def get_lan_ipv4_candidates():
    """列出目前機器可對外提供服務的 LAN IPv4 位址。"""
    candidates = []

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            candidates.append(ip)
    except OSError:
        pass

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127.") and ip not in candidates:
                candidates.append(ip)
    except OSError:
        pass

    return candidates


def split_nmcli_fields(raw_line, delimiter=":"):
    """解析 nmcli -t 輸出的跳脫欄位。"""
    if raw_line is None:
        return []

    fields = []
    current = []
    escaped = False

    for char in raw_line:
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == delimiter:
            fields.append("".join(current))
            current = []
            continue

        current.append(char)

    if escaped:
        current.append("\\")

    fields.append("".join(current))
    return fields


# === 單實例保護 ===
def acquire_instance_lock():
    """避免 main.py 被重複啟動，造成 Serial 與 Web 埠同時搶占。"""
    global _INSTANCE_LOCK_HANDLE

    if fcntl is None:
        return True

    if _INSTANCE_LOCK_HANDLE is not None:
        return True

    handle = INSTANCE_LOCK_PATH.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print(f"偵測到另一個 main.py 已在執行，為避免重複占用 {SERIAL_PORT}，本次啟動已取消。")
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _INSTANCE_LOCK_HANDLE = handle
    return True


def release_instance_lock():
    """釋放單實例鎖，讓後續程序可以接手。"""
    global _INSTANCE_LOCK_HANDLE

    if _INSTANCE_LOCK_HANDLE is None:
        return

    try:
        if fcntl is not None:
            fcntl.flock(_INSTANCE_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass

    try:
        _INSTANCE_LOCK_HANDLE.close()
    finally:
        _INSTANCE_LOCK_HANDLE = None


atexit.register(release_instance_lock)


# === 串流讀取層 ===
class OpenCVStream:
    """用 OpenCV 直接讀取串流來源的簡單包裝。"""

    def __init__(self, url):
        if cv2 is None:
            raise RuntimeError("OpenCV 未安裝")
        self.mode = "opencv"
        # 本機 USB 攝影機用 V4L2 backend
        if isinstance(url, (int, str)) and str(url).startswith(("/dev/video", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
            backend = cv2.CAP_V4L2
        else:
            backend = cv2.CAP_ANY
        self.cap = cv2.VideoCapture(url, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"OpenCV 無法開啟: {url}")
        
        # 先要求相機走 MJPG，通常比未壓縮格式更容易穩住 640x480@30fps。
        try:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass

        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        # 設定解析度與 FPS
        if CAMERA_WIDTH > 0:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        if CAMERA_HEIGHT > 0:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        if CAMERA_FPS > 0:
            self.cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        
        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        print(f"OpenCV 相機已開啟: {actual_w}x{actual_h} @{actual_fps:.1f}fps")

    def read(self):
        return self.cap.read()

    def release(self):
        self.cap.release()


class MJPEGStream:
    """手動解析 HTTP MJPEG 串流，作為 OpenCV 失敗時的主要讀流方式。"""

    def __init__(self, url):
        if cv2 is None or np is None:
            raise RuntimeError("OpenCV 或 numpy 未安裝")
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
        """建立 HTTP 串流連線，並重設 JPEG 緩衝區。"""
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "multipart/x-mixed-replace,*/*",
        }
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
        """從位元組流中拼出完整 JPEG 並解碼成 OpenCV 影格。"""
        deadline = time.monotonic() + self.frame_wait_seconds

        while time.monotonic() < deadline:
            try:
                chunk = next(self.iterator)
            except StopIteration:
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

        return False, None

    def release(self):
        """關閉底層 HTTP 連線。"""
        if self.response is not None:
            self.response.close()


# === 背景抓流層 ===
class FrameGrabber:
    """在背景執行緒持續抓取最新影格，並在失敗時自動重連。"""

    def __init__(self, stream_factory, url, max_fails_before_reconnect=3, reconnect_delay_seconds=STREAM_RECONNECT_DELAY_SECONDS):
        self._factory = stream_factory
        self._url = url
        self._max_fails = max_fails_before_reconnect
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._stream = stream_factory(url)
        self.mode = self._stream.mode
        self._frame = None
        self._frame_sequence = 0
        self._lock = Lock()
        self._stop = False
        self._fail_count = 0
        self._last_log_message = None
        self._last_log_time = 0.0
        self._thread = Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def _log(self, message):
        """節流重複的抓流錯誤訊息。"""
        now = time.monotonic()
        if message == self._last_log_message and now - self._last_log_time < FRAME_GRABBER_LOG_INTERVAL_SECONDS:
            return

        print(message)
        self._last_log_message = message
        self._last_log_time = now

    def _grab_loop(self):
        """抓流主迴圈：成功就更新最新幀，連續失敗就重建串流。"""
        while not self._stop:
            try:
                ok, frame = self._stream.read()
            except Exception:
                ok, frame = False, None

            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._frame_sequence += 1
                    self._fail_count = 0
                continue

            with self._lock:
                self._fail_count += 1
                fails = self._fail_count
                if fails >= self._max_fails:
                    self._frame = None

            if fails < self._max_fails:
                continue

            self._log("FrameGrabber: 串流中斷，嘗試重連...")
            try:
                self._stream.release()
            except Exception:
                pass

            time.sleep(self._reconnect_delay_seconds)
            try:
                self._stream = self._factory(self._url)
                self.mode = self._stream.mode
                with self._lock:
                    self._fail_count = 0
                self._log("FrameGrabber: 重連成功")
            except Exception as error:
                self._log(f"FrameGrabber: 重連失敗 {error}")

    def read(self):
        """回傳目前最新一幀；若暫時沒有畫面則回傳 False。"""
        with self._lock:
            frame = self._frame
        return frame is not None, frame

    def read_with_sequence(self):
        """回傳最新影格與遞增序號，讓上層只在新影格到來時重算。"""
        with self._lock:
            frame = self._frame
            frame_sequence = self._frame_sequence
        return frame is not None, frame, frame_sequence

    def release(self):
        """停止背景抓流執行緒並釋放底層串流。"""
        self._stop = True
        self._thread.join(timeout=2)
        try:
            self._stream.release()
        except Exception:
            pass


# === R4 Serial 通訊層 ===
class SerialBridge:
    """管理 Arduino R4 的 Serial 連線、感測資料與控制命令。"""

    def __init__(self, port, baud_rate, timeout=0.1):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.connection = None
        self.status_text = "disconnected"
        self.latest_payload = {
            "FL": 999,
            "FR": 999,
            "SL": 999,
            "SR": 999,
            "C": 999,
            "ST": "UNKNOWN",
        }
        self.packet_count = 0
        self.last_packet_time = 0.0
        self.last_sent_command = None
        self.last_sent_time = 0.0
        self.next_connect_time = 0.0
        self.last_error_message = None
        self.last_error_log_time = 0.0

    def _log_serial_issue(self, prefix, error):
        """節流重複的 Serial 例外訊息。"""
        message = f"{prefix}: {error}"
        now = time.monotonic()
        if message == self.last_error_message and now - self.last_error_log_time < SERIAL_ERROR_LOG_INTERVAL_SECONDS:
            return

        print(message)
        self.last_error_message = message
        self.last_error_log_time = now

    def _mark_disconnected(self, prefix, error):
        """把連線標記為離線，並設定下一次允許重連的時間。"""
        self.close()
        self.status_text = f"offline {self.port}"
        self.next_connect_time = time.monotonic() + SERIAL_RECONNECT_COOLDOWN_SECONDS
        self._log_serial_issue(prefix, error)

    def is_connected(self):
        """回傳目前是否持有可用的 Serial 連線。"""
        return self.connection is not None

    def connect(self):
        """建立 Serial 連線；剛斷線時會先經過冷卻期再重試。"""
        if serial is None:
            self.status_text = "pyserial missing"
            print("未安裝 pyserial，Serial 功能已停用")
            return False

        if self.connection is not None:
            return True

        now = time.monotonic()
        if now < self.next_connect_time:
            self.status_text = f"reconnecting {self.port}"
            return False

        try:
            self.connection = serial.Serial(self.port, self.baud_rate, timeout=self.timeout)
            time.sleep(2)
            self.next_connect_time = 0.0
            self.status_text = f"online {self.port}"
            self.last_error_message = None
            print(f"Serial 已連線: {self.port} @ {self.baud_rate}")
            return True
        except SerialException as error:
            self.connection = None
            self.status_text = f"offline {self.port}"
            self.next_connect_time = now + SERIAL_RECONNECT_COOLDOWN_SECONDS
            self._log_serial_issue("Serial 連線失敗", error)
            return False

    def close(self):
        """關閉目前的 Serial 連線。"""
        if self.connection is not None:
            try:
                self.connection.close()
            finally:
                self.connection = None

    def get_status(self):
        """回傳前端可直接顯示的 Serial 狀態字串。"""
        if self.connection is None and time.monotonic() < self.next_connect_time:
            return f"reconnecting {self.port}"
        return self.status_text

    def get_latest_payload(self):
        """回傳最新一份成功解析的 R4 JSON 感測資料。"""
        return dict(self.latest_payload)

    def read_sensors(self):
        """讀取 Serial telemetry；沒有新資料時保留上一筆有效值。"""
        if self.connection is None and not self.connect():
            return self.get_latest_payload()

        try:
            while True:
                if self.connection.in_waiting <= 0:
                    break

                line = self.connection.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                payload = parse_r4_telemetry_line(line)
                if payload is None:
                    continue

                self.latest_payload = payload
                self.packet_count += 1
                self.last_packet_time = time.time()
                self.status_text = f"online {self.port}"
        except (OSError, SerialException) as error:
            self._mark_disconnected("Serial 讀取失敗", error)

        return self.get_latest_payload()

    def send_command(self, command):
        """送出控制命令給 R4，並避免極短時間內重複送相同命令。"""
        resolved = (command or "").strip().upper()
        if not resolved:
            return False

        now = time.monotonic()
        if resolved == self.last_sent_command and now - self.last_sent_time < SEND_INTERVAL_SECONDS:
            return False

        if self.connection is None and not self.connect():
            return False

        try:
            self.connection.write(f"{resolved}\n".encode("ascii"))
            self.last_sent_command = resolved
            self.last_sent_time = now
            self.status_text = f"sent {resolved}"
            return True
        except (OSError, SerialException) as error:
            self._mark_disconnected("Serial 傳送失敗", error)
            self.status_text = f"send failed {self.port}"
            return False


# === WiFi 監看與 telemetry 前處理 ===
class WiFiSignalMonitor:
    """從 Linux 的 /proc/net/wireless 輪詢 WiFi 訊號狀態。"""

    def __init__(self, interface="wlan0", poll_interval_seconds=1.0):
        self.interface = (interface or "").strip()
        self.poll_interval_seconds = max(0.2, float(poll_interval_seconds))
        self.latest_sample = {
            "available": False,
            "connected": False,
            "interface": self.interface or "N/A",
            "quality": None,
            "signal_dbm": None,
            "updated": 0.0,
            "message": "wifi unavailable",
            "source": "proc",
        }
        self.next_poll_time = 0.0

    def _read_proc_net_wireless(self):
        """解析核心提供的無線網卡狀態檔。"""
        try:
            lines = Path("/proc/net/wireless").read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as error:
            return {
                "available": False,
                "connected": False,
                "interface": self.interface or "N/A",
                "quality": None,
                "signal_dbm": None,
                "message": f"wifi unavailable: {error}",
                "source": "proc",
            }

        entries = []
        for raw_line in lines[2:]:
            if ":" not in raw_line:
                continue

            iface, payload = raw_line.split(":", 1)
            parts = payload.split()
            if len(parts) < 3:
                continue

            quality = int(round(safe_float(parts[1], -1.0)))
            signal_dbm = int(round(safe_float(parts[2], 0.0)))
            connected = quality > 0 and signal_dbm < 0
            entries.append(
                {
                    "available": True,
                    "connected": connected,
                    "interface": iface.strip(),
                    "quality": quality if quality >= 0 else None,
                    "signal_dbm": signal_dbm if connected else None,
                    "message": "wifi connected" if connected else "wifi disconnected",
                    "source": "proc",
                }
            )

        if not entries:
            return {
                "available": False,
                "connected": False,
                "interface": self.interface or "N/A",
                "quality": None,
                "signal_dbm": None,
                "message": "no wireless interface",
                "source": "proc",
            }

        if self.interface:
            for entry in entries:
                if entry["interface"] == self.interface:
                    return entry

        selected = dict(entries[0])
        self.interface = selected["interface"]
        return selected

    def poll(self, force=False):
        """按照輪詢週期更新 WiFi 狀態，避免每圈主迴圈都重讀系統檔。"""
        now = time.monotonic()
        if not force and now < self.next_poll_time:
            return dict(self.latest_sample)

        sample = self._read_proc_net_wireless()
        sample["updated"] = time.time()
        self.latest_sample = sample
        self.next_poll_time = now + self.poll_interval_seconds
        return dict(sample)


class WiFiAutoSwitcher:
    """在訊號弱或斷線時，透過 NetworkManager 切到較佳的已存 WiFi profile。"""

    def __init__(
        self,
        interface="wlan0",
        weak_dbm=-58,
        recover_dbm=-52,
        enabled=False,
        candidate_names=None,
        scan_interval_seconds=5.0,
        cooldown_seconds=15.0,
        min_candidate_signal=35,
        min_signal_gain=8,
        profile_refresh_seconds=30.0,
        nmcli_timeout_seconds=8.0,
    ):
        self.interface = (interface or "").strip()
        self.weak_dbm = int(weak_dbm)
        self.recover_dbm = max(int(recover_dbm), self.weak_dbm + 1)
        self.enabled = bool(enabled) and bool(self.interface)
        self.candidate_names = list(dict.fromkeys(item.strip() for item in (candidate_names or []) if item.strip()))
        self.scan_interval_seconds = max(2.0, float(scan_interval_seconds))
        self.cooldown_seconds = max(5.0, float(cooldown_seconds))
        self.min_candidate_signal = max(1, min(100, int(min_candidate_signal)))
        self.min_signal_gain = max(0, int(min_signal_gain))
        self.profile_refresh_seconds = max(10.0, float(profile_refresh_seconds))
        self.nmcli_timeout_seconds = max(3.0, float(nmcli_timeout_seconds))

        self.last_status = "wifi auto switch disabled" if not self.enabled else "wifi auto switch idle"
        self.last_error = ""
        self.active_connection = ""
        self.last_target_connection = ""
        self.cached_profiles = []
        self.cached_networks = []
        self.next_profile_refresh_time = 0.0
        self.next_scan_time = 0.0
        self.next_attempt_time = 0.0
        self.active_connection_refresh_seconds = max(1.0, min(self.scan_interval_seconds, 5.0))
        self.next_active_connection_refresh_time = 0.0

    def _build_report(self):
        if self.candidate_names:
            target_text = ", ".join(self.candidate_names)
        elif self.cached_profiles:
            target_text = ", ".join(profile["name"] for profile in self.cached_profiles)
        else:
            target_text = "auto"

        return {
            "WIFI_ACTIVE_CONNECTION": self.active_connection or "N/A",
            "WIFI_SWITCH_ENABLED": self.enabled,
            "WIFI_SWITCH_STATUS": self.last_status,
            "WIFI_SWITCH_TARGETS": target_text,
        }

    def _run_nmcli(self, args):
        command = ["nmcli", "-t"] + list(args)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.nmcli_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, "", str(error)

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if completed.returncode != 0:
            return False, stdout, stderr or stdout or f"nmcli exit {completed.returncode}"

        return True, stdout, ""

    def _get_profile_ssid(self, connection_name):
        ok, output, error = self._run_nmcli(["-g", "802-11-wireless.ssid", "connection", "show", connection_name])
        if not ok:
            self.last_error = error
            return connection_name

        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return lines[0] if lines else connection_name

    def _load_candidate_profiles(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_profile_refresh_time and self.cached_profiles:
            return [dict(item) for item in self.cached_profiles]

        ok, output, error = self._run_nmcli(["-f", "NAME,TYPE", "connection", "show"])
        if not ok:
            self.last_error = error
            self.last_status = f"wifi auto switch unavailable: {error}"
            return [dict(item) for item in self.cached_profiles]

        requested_names = set(self.candidate_names)
        profiles = []
        for raw_line in output.splitlines():
            fields = split_nmcli_fields(raw_line)
            if len(fields) < 2:
                continue

            connection_name = fields[0].strip()
            connection_type = fields[1].strip()
            if connection_type != "802-11-wireless" or not connection_name:
                continue
            if requested_names and connection_name not in requested_names:
                continue

            ssid = self._get_profile_ssid(connection_name)
            if not ssid:
                continue
            profiles.append({"name": connection_name, "ssid": ssid})

        self.cached_profiles = profiles
        self.next_profile_refresh_time = now + self.profile_refresh_seconds
        return [dict(item) for item in profiles]

    def _get_active_connection_name(self):
        ok, output, error = self._run_nmcli(["-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"])
        if not ok:
            self.last_error = error
            return self.active_connection

        for raw_line in output.splitlines():
            fields = split_nmcli_fields(raw_line)
            if len(fields) < 3:
                continue

            connection_name = fields[0].strip()
            device_name = fields[1].strip()
            connection_type = fields[2].strip()
            if device_name == self.interface and connection_type == "802-11-wireless":
                return connection_name

        return ""

    def _refresh_active_connection(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_active_connection_refresh_time:
            return self.active_connection

        active_connection = self._get_active_connection_name()
        self.next_active_connection_refresh_time = now + self.active_connection_refresh_seconds
        if active_connection:
            self.active_connection = active_connection
        return self.active_connection

    def _scan_visible_networks(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_scan_time and self.cached_networks:
            return [dict(item) for item in self.cached_networks]

        ok, output, error = self._run_nmcli(["-f", "IN-USE,SSID,SIGNAL", "device", "wifi", "list", "ifname", self.interface])
        if not ok:
            self.last_error = error
            self.last_status = f"wifi scan failed: {error}"
            return [dict(item) for item in self.cached_networks]

        networks_by_ssid = {}
        for raw_line in output.splitlines():
            fields = split_nmcli_fields(raw_line)
            if len(fields) < 3:
                continue

            in_use = fields[0].strip()
            ssid = fields[1].strip()
            signal = safe_int(fields[2].strip(), -1)
            if not ssid or signal < 0:
                continue

            existing = networks_by_ssid.get(ssid)
            if existing is not None and existing["signal"] >= signal:
                if in_use == "*":
                    existing["in_use"] = True
                continue

            networks_by_ssid[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "in_use": in_use == "*",
            }

        self.cached_networks = sorted(
            networks_by_ssid.values(),
            key=lambda item: item["signal"],
            reverse=True,
        )
        self.next_scan_time = now + self.scan_interval_seconds
        return [dict(item) for item in self.cached_networks]

    def evaluate(self, sample):
        self.active_connection = self._refresh_active_connection(force=not self.active_connection) or self.active_connection

        if not self.enabled:
            self.last_status = "wifi auto switch disabled"
            return self._build_report()

        current = dict(sample or {})
        connected = bool(current.get("connected"))
        signal_dbm = current.get("signal_dbm")

        if connected and signal_dbm is not None and signal_dbm >= self.recover_dbm:
            self.last_error = ""
            self.last_status = f"wifi stable on {self.active_connection or self.interface}"
            return self._build_report()

        if connected and signal_dbm is not None and signal_dbm > self.weak_dbm:
            self.last_status = f"wifi watching {self.active_connection or self.interface}"
            return self._build_report()

        now = time.monotonic()
        if now < self.next_attempt_time:
            remaining = max(0.0, self.next_attempt_time - now)
            self.last_status = f"wifi switch cooldown {remaining:.0f}s"
            return self._build_report()

        profiles = self._load_candidate_profiles()
        if not profiles:
            self.last_status = "wifi auto switch idle: no saved profile"
            return self._build_report()

        networks = self._scan_visible_networks(force=not connected)
        if not networks:
            self.last_status = "wifi auto switch idle: no visible wifi"
            return self._build_report()

        networks_by_ssid = {item["ssid"]: item for item in networks}
        candidates = []
        for profile in profiles:
            network = networks_by_ssid.get(profile["ssid"]) or networks_by_ssid.get(profile["name"])
            if network is None:
                continue
            candidates.append(
                {
                    "name": profile["name"],
                    "ssid": profile["ssid"],
                    "signal": network["signal"],
                    "in_use": network["in_use"],
                }
            )

        if not candidates:
            self.last_status = "wifi auto switch idle: no visible candidate"
            return self._build_report()

        candidates.sort(key=lambda item: item["signal"], reverse=True)
        best_candidate = candidates[0]
        current_candidate = None
        for candidate in candidates:
            if candidate["name"] == self.active_connection or candidate["in_use"]:
                current_candidate = candidate
                break

        if best_candidate["signal"] < self.min_candidate_signal:
            self.last_status = (
                f"wifi weak, best candidate {best_candidate['name']} only {best_candidate['signal']}%"
            )
            return self._build_report()

        if connected and best_candidate["name"] == self.active_connection:
            self.last_status = f"wifi weak on {self.active_connection}, current profile is still best"
            return self._build_report()

        if connected and current_candidate is not None:
            threshold = current_candidate["signal"] + self.min_signal_gain
            if best_candidate["signal"] < threshold:
                self.last_status = (
                    f"wifi weak on {self.active_connection or self.interface}, no better candidate"
                )
                return self._build_report()

        wait_seconds = max(1, min(10, int(round(self.nmcli_timeout_seconds))))
        ok, _, error = self._run_nmcli(
            [
                "--wait",
                str(wait_seconds),
                "connection",
                "up",
                "id",
                best_candidate["name"],
                "ifname",
                self.interface,
            ]
        )
        self.last_target_connection = best_candidate["name"]
        self.next_attempt_time = now + self.cooldown_seconds

        if not ok:
            self.last_error = error
            self.last_status = f"wifi switch failed {best_candidate['name']}: {error}"
            return self._build_report()

        self.active_connection = best_candidate["name"]
        self.last_error = ""
        self.last_status = f"wifi switched to {best_candidate['name']} ({best_candidate['signal']}%)"
        self.next_scan_time = 0.0
        self.next_active_connection_refresh_time = 0.0
        return self._build_report()


def build_wifi_telemetry(sample):
    """把 WiFi 原始資料整理成前端統一使用的 telemetry 欄位。"""
    current = dict(sample or {})
    signal_dbm = current.get("signal_dbm")
    quality = current.get("quality")
    interface = str(current.get("interface") or WIFI_INTERFACE or "N/A")

    if not current or not current.get("available"):
        status_text = "wifi unavailable"
    elif current.get("connected") and signal_dbm is not None:
        status_text = f"wifi ok {interface} {signal_dbm}dBm"
    else:
        status_text = f"wifi disconnected {interface}"

    return {
        "WIFI_IFACE": interface,
        "WIFI_DBM": signal_dbm if signal_dbm is not None else 999,
        "WIFI_QUALITY": quality if quality is not None else 999,
        "WIFI_MODE": status_text,
    }


def build_control_telemetry(control_state):
    """把控制模式與手動命令整理成 telemetry。"""
    current = dict(control_state or {})
    mode = str(current.get("mode") or CONTROL_MODE_AUTO).upper()
    manual_command = str(current.get("manual_command") or "STOP").upper()
    return {
        "CONTROL_MODE": mode,
        "MANUAL_COMMAND": manual_command,
    }


def format_status_text(base_status, control_state):
    """在手動模式下，把控制資訊附加到狀態列文字。"""
    current = dict(control_state or {})
    mode = str(current.get("mode") or CONTROL_MODE_AUTO).upper()
    manual_command = str(current.get("manual_command") or "STOP").upper()
    if mode == CONTROL_MODE_MANUAL:
        return f"manual {manual_command} | {base_status}"
    return base_status


def resolve_mcu_avoidance_state(sensor_payload, obstacle_distance_cm):
    """當 R4 已進入避障流程時，讓 Python 暫停覆蓋馬達命令。"""
    current = dict(sensor_payload or {})
    state = str(current.get("ST") or "").upper()
    if state.startswith("EVADE_"):
        return state

    threshold = max(1, safe_int(obstacle_distance_cm, 35))
    front_left = safe_int(current.get("FL"), 999)
    front_right = safe_int(current.get("FR"), 999)
    side_left = safe_int(current.get("SL"), 999)
    side_right = safe_int(current.get("SR"), 999)

    obstacle_left = 0 < front_left <= threshold
    obstacle_right = 0 < front_right <= threshold
    boxed_in = all(0 < distance <= threshold for distance in (front_left, front_right, side_left, side_right))

    if boxed_in:
        return "EVADE_BACKWARD"
    if obstacle_left and obstacle_right:
        return "EVADE_BOTH"
    if obstacle_left:
        return "EVADE_RIGHT"
    if obstacle_right:
        return "EVADE_LEFT"
    return ""


# === 控制模式與導航決策 ===
class NavigationController:
    """根據 YOLO 目標位置與中央距離，決定 AUTO 模式要送的命令。"""

    def __init__(
        self,
        hard_turn_offset=0.38,
        obstacle_distance_cm=15,
        capture_distance_cm=8,
        send_interval_seconds=0.45,
    ):
        self.hard_turn_offset = hard_turn_offset
        self.obstacle_distance_cm = obstacle_distance_cm
        self.capture_distance_cm = capture_distance_cm
        self.send_interval_seconds = send_interval_seconds
        self.last_command = None
        self.last_command_time = 0.0
        self.latest_command = "STARTING"

    def decide_command(self, counts, dist_center, center_x, dist_front_left=None, dist_front_right=None):
        """純計算目前應該採取的導航命令。"""
        has_target = get_primary_label(counts) is not None
        center_distance = safe_int(dist_center, 999)
        front_left_distance = safe_int(dist_front_left, 999)
        front_right_distance = safe_int(dist_front_right, 999)
        obstacle_left = 0 < front_left_distance <= self.obstacle_distance_cm
        obstacle_right = 0 < front_right_distance <= self.obstacle_distance_cm

        # 避障永遠優先於垃圾追蹤，避免 Python 持續送 FORWARD 壓過硬體保護。
        if obstacle_left or obstacle_right:
            if obstacle_left and obstacle_right:
                return "TURN_RIGHT" if front_left_distance < front_right_distance else "TURN_LEFT"
            if obstacle_left:
                return "TURN_RIGHT"
            return "TURN_LEFT"

        if has_target and 0 < center_distance <= self.capture_distance_cm:
            return "STOP"

        if has_target and center_x is not None:
            if center_x < 0.5 - self.hard_turn_offset:
                return "TURN_LEFT"
            if center_x > 0.5 + self.hard_turn_offset:
                return "TURN_RIGHT"

        return "FORWARD"

    def update(self, counts, dist_center, center_x, front_left_distance, front_right_distance, bridge):
        """在需要時把 AUTO 導航命令送到 SerialBridge。"""
        command = self.decide_command(
            counts,
            dist_center,
            center_x,
            dist_front_left=front_left_distance,
            dist_front_right=front_right_distance,
        )
        self.latest_command = command

        now = time.monotonic()
        if command == self.last_command and now - self.last_command_time < self.send_interval_seconds:
            return command

        bridge.send_command(command)
        self.last_command = command
        self.last_command_time = now
        return command


    # === 網頁監看與控制 API ===
class DetectionWebServer:
    """提供監看頁、/status、/stream、/snapshot 與 /control API。"""

    def __init__(
        self,
        html_path,
        host="0.0.0.0",
        port=8080,
        stream_client_interval_seconds=0.0,
        stream_max_width=960,
        stream_jpeg_quality=72,
    ):
        self.html_path = Path(html_path)
        self.host = host
        self.port = port
        self.stream_client_interval_seconds = stream_client_interval_seconds
        self.stream_max_width = stream_max_width
        self.stream_jpeg_quality = stream_jpeg_quality
        self.stream_frame_interval_seconds = WEB_STREAM_FRAME_INTERVAL_SECONDS

        self.latest_jpeg = None
        self._latest_jpeg_frame_identity = None
        self.latest_meta = {
            "fps": 0.0,
            "status": "starting",
            "arduino": "disconnected",
            "counts": {},
            "telemetry": build_bridge_telemetry(None, "STARTING"),
            "updated": time.time(),
        }
        self.control_state = {
            "mode": CONTROL_MODE_AUTO,
            "manual_command": "STOP",
            "updated": time.time(),
        }

        self.lock = Lock()
        self.app = Flask(__name__)
        self._register_routes()

    def _register_routes(self):
        """集中註冊 Flask 路由，讓前端入口與 API 更容易追蹤。"""
        @self.app.get("/")
        def index():
            # 首頁直接回傳監看控制頁。
            if self.html_path.exists():
                return send_file(self.html_path)
            return "HTML not found", 404

        @self.app.get("/status")
        def status():
            # 前端輪詢這個 API 取得最新遙測與控制狀態。
            with self.lock:
                return jsonify(self.latest_meta)

        @self.app.post("/control")
        def control():
            # 前端切換 AUTO / MANUAL 或送出手動命令都走這裡。
            payload = request.get_json(silent=True) or {}
            try:
                state = self.update_control_state(payload.get("mode"), payload.get("command"))
            except ValueError as error:
                return jsonify({"error": str(error)}), 400
            return jsonify(state)

        @self.app.get("/stream")
        def stream():
            # MJPEG 直播串流（<img> 直接顯示，自動刷新）
            def generate():
                while True:
                    with self.lock:
                        frame = self.latest_jpeg
                    if frame is not None:
                        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    time.sleep(max(self.stream_client_interval_seconds, self.stream_frame_interval_seconds))
            return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @self.app.get("/snapshot")
        def snapshot():
            # 單張快照，與 /stream 相同（保留相容性）
            with self.lock:
                frame = self.latest_jpeg
            if frame is None:
                return Response(status=503)
            return Response(
                frame,
                mimetype="image/jpeg",
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                },
            )

    # MJPEG 生成器已移除，改用 /stream 單幀 JPEG + canvas 輪播

    def _set_meta(self, fps, status_text, arduino_status, counts, telemetry):
        """原子化更新 /status 會回傳的狀態資料。"""
        with self.lock:
            self.latest_meta = {
                "fps": round(float(fps), 2),
                "status": status_text,
                "arduino": arduino_status,
                "counts": {key: int(value) for key, value in (counts or {}).items()},
                "telemetry": dict(telemetry or {}),
                "updated": time.time(),
                "backend": "openvino_int8",
            }

    def get_control_state(self):
        """回傳目前的 AUTO / MANUAL 狀態與手動命令。"""
        with self.lock:
            return dict(self.control_state)

    def update_control_state(self, mode=None, command=None):
        """驗證並更新控制狀態，供前端 /control 與主迴圈共用。"""
        requested_mode = None if mode is None else str(mode).strip().upper()
        requested_command = None if command is None else str(command).strip().upper()

        if requested_mode not in {None, CONTROL_MODE_AUTO, CONTROL_MODE_MANUAL}:
            raise ValueError(f"不支援的控制模式: {mode}")

        if requested_command not in {None, *MANUAL_ALLOWED_COMMANDS}:
            raise ValueError(f"不支援的控制命令: {command}")

        with self.lock:
            if requested_mode == CONTROL_MODE_AUTO:
                self.control_state["mode"] = CONTROL_MODE_AUTO
                self.control_state["manual_command"] = "STOP"
            elif requested_mode == CONTROL_MODE_MANUAL:
                self.control_state["mode"] = CONTROL_MODE_MANUAL
                self.control_state["manual_command"] = requested_command or "STOP"

            if requested_mode is None and requested_command is not None:
                self.control_state["manual_command"] = requested_command

            self.control_state["updated"] = time.time()
            return dict(self.control_state)

    def _encode_frame(self, frame):
        """把 OpenCV 影格縮放並壓成 JPEG，降低前端串流負擔。"""
        if cv2 is None or frame is None:
            return None

        web_frame = frame
        height, width = frame.shape[:2]
        if width > self.stream_max_width:
            scale = self.stream_max_width / float(width)
            web_frame = cv2.resize(
                frame,
                (int(width * scale), int(height * scale)),
                interpolation=cv2.INTER_AREA,
            )

        ok, encoded = cv2.imencode(
            ".jpg",
            web_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.stream_jpeg_quality],
        )
        if not ok:
            return None
        return encoded.tobytes()

    def update(self, frame, counts, fps, status_text, arduino_status, telemetry):
        """更新畫面與狀態，供有新影格時的正常流程使用。"""
        self.update_frame_only(frame)
        self._set_meta(fps, status_text, arduino_status, counts, telemetry)

    def update_frame_only(self, frame, fps=None):
        """只更新最新畫面，保留目前的狀態資料。"""
        frame_identity = id(frame)
        with self.lock:
            if frame_identity == self._latest_jpeg_frame_identity and self.latest_jpeg is not None:
                return False

        encoded = self._encode_frame(frame)
        if encoded is not None:
            with self.lock:
                self.latest_jpeg = encoded
                self._latest_jpeg_frame_identity = frame_identity
            return True
        return False

    def update_status_only(self, fps, status_text, arduino_status, counts, telemetry):
        """只更新狀態，不覆蓋前端目前看到的最後一張影像。"""
        self._set_meta(fps, status_text, arduino_status, counts, telemetry)

    def _select_port(self, start_port, max_tries=20):
        """在預設埠被占用時，往後搜尋可用埠號。"""
        for port in range(start_port, start_port + max_tries):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, port))
                return port
            except OSError:
                continue
            finally:
                sock.close()
        raise RuntimeError("找不到可用的 Flask 連接埠")

    def start(self):
        """在背景執行緒啟動 WSGI 伺服器，並列出本機與 LAN 可用網址。"""
        selected_port = self._select_port(self.port)
        if selected_port != self.port:
            print(f"連接埠 {self.port} 已被占用，改用 {selected_port}")
            self.port = selected_port

        def run():
            try:
                from waitress import serve  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("waitress 未安裝，無法啟動生產用 WSGI 伺服器") from exc

            serve(self.app, host=self.host, port=self.port)

        thread = Thread(target=run, daemon=True)
        thread.start()

        local_base = f"http://127.0.0.1:{self.port}"
        schedule_browser_open(f"{local_base}/")
        print(f"網頁監看已啟動: {local_base}")
        print(f"本機頁面: {local_base}/")
        print(f"本機串流: {local_base}/stream")
        print(f"本機快照: {local_base}/snapshot")

        lan_candidates = get_lan_ipv4_candidates()
        if lan_candidates:
            print("可給 Arduino/手機使用的網址:")
            for ip in lan_candidates:
                print(f"  頁面: http://{ip}:{self.port}/")
                print(f"  串流: http://{ip}:{self.port}/stream")
                print(f"  快照: http://{ip}:{self.port}/snapshot")


# === 遙測組裝、畫面標註與執行期工具 ===
def build_bridge_telemetry(bridge, py_cmd, extra=None):
    """把 R4、控制模式與 WiFi 狀態整合成前端用 telemetry。"""
    payload = bridge.get_latest_payload() if bridge is not None else {
        "FL": 999,
        "FR": 999,
        "SL": 999,
        "SR": 999,
        "C": 999,
        "ST": "UNKNOWN",
    }

    telemetry = {
        "FL": safe_int(payload.get("FL", 999)),
        "FR": safe_int(payload.get("FR", 999)),
        "SL": safe_int(payload.get("SL", 999)),
        "SR": safe_int(payload.get("SR", 999)),
        "C": safe_int(payload.get("C", 999)),
        "ST": str(payload.get("ST", "UNKNOWN") or "UNKNOWN"),
        "PY_CMD": str(py_cmd or "IDLE"),
        "CONTROL_MODE": CONTROL_MODE_AUTO,
        "MANUAL_COMMAND": "STOP",
        "WIFI_IFACE": WIFI_INTERFACE or "N/A",
        "WIFI_DBM": 999,
        "WIFI_QUALITY": 999,
        "WIFI_MODE": "wifi monitor starting",
        "WIFI_ACTIVE_CONNECTION": "N/A",
        "WIFI_SWITCH_ENABLED": WIFI_AUTO_SWITCH_ENABLED,
        "WIFI_SWITCH_STATUS": "wifi auto switch starting" if WIFI_AUTO_SWITCH_ENABLED else "wifi auto switch disabled",
        "WIFI_SWITCH_TARGETS": ", ".join(WIFI_SWITCH_CANDIDATES) if WIFI_SWITCH_CANDIDATES else "auto",
        "WIFI_WEAK_DBM": WIFI_SIGNAL_WEAK_DBM,
        "WIFI_RECOVER_DBM": max(WIFI_SIGNAL_RECOVER_DBM, WIFI_SIGNAL_WEAK_DBM + 1),
    }

    # extra 會覆蓋進目前迴圈最新的 WiFi 與控制模式狀態。
    if extra:
        telemetry.update(extra)

    return telemetry


def open_stream(url):
    """依來源型態開啟鏡頭：本機裝置走 OpenCV，網路串流走 MJPEG。"""
    # 判斷是否為本機裝置
    if isinstance(url, int) or (isinstance(url, str) and url.startswith("/dev/video")):
        try:
            stream = OpenCVStream(url)
            print(f"已使用 USB/UVC 模式開啟串流: {url}")
            return stream
        except Exception as error:
            raise RuntimeError(f"無法開啟 USB/UVC 攝影機來源 {url}: {error}") from error

    # 網路串流：優先 MJPEG，失敗回退 OpenCV
    try:
        stream = MJPEGStream(url)
        print("已使用 MJPEG 模式開啟串流")
        return stream
    except Exception:
        pass

    try:
        stream = OpenCVStream(url)
        print("已切換為 OpenCV 備援讀流")
        return stream
    except RuntimeError as error:
        raise RuntimeError(f"無法開啟鏡頭串流: {error}") from error


def _parse_camera_device_source(raw_source):
    """把 CAMERA_DEVICE 轉成 OpenCV 可接受的來源（例如 0 或 /dev/video0）。"""
    source = (raw_source or "").strip()
    if not source:
        return None
    if source.isdigit():
        return int(source)
    if source.startswith("/dev/video"):
        suffix = source.removeprefix("/dev/video")
        if suffix.isdigit():
            return int(suffix)
    return source


def _probe_opencv_source(candidate):
    """快速探測某個 OpenCV 來源是否可用。"""
    try:
        cap = cv2.VideoCapture(candidate)
        opened = cap.isOpened()
        cap.release()
        return opened
    except Exception:
        return False


def resolve_camera_source():
    """解析目前應使用的鏡頭來源。
    
    優先順序: CAMERA_DEVICE → 自動掃描 USB /dev/video* → STREAM_URL
    """
    # 1. 環境變數明確指定
    explicit = _parse_camera_device_source(CAMERA_DEVICE)
    if explicit is not None:
        return explicit, f"CAMERA_DEVICE={CAMERA_DEVICE}"

    # 2. 自動掃描本機 USB 攝影機
    for index in range(AUTO_CAMERA_SCAN_MAX_INDEX):
        device_path = f"/dev/video{index}"
        if _probe_opencv_source(device_path):
            src = _parse_camera_device_source(device_path)
            return src, f"auto-detected {device_path}"

    # 3. 退回網路串流
    return STREAM_URL, "default STREAM_URL"


def can_enable_local_preview():
    """判斷目前環境是否適合啟用 OpenCV 本機視窗預覽。"""
    if not ENABLE_LOCAL_PREVIEW:
        return False
    # 無桌面環境時，cv2.namedWindow 可能因 Qt/xcb 直接 abort 整個程序
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def can_auto_open_browser():
    """判斷目前環境是否適合自動開啟本機瀏覽器。"""
    if not AUTO_OPEN_BROWSER:
        return False
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def schedule_browser_open(url, delay_seconds=AUTO_OPEN_BROWSER_DELAY_SECONDS):
    """在背景延後開啟首頁，避免搶在 web server listen 前彈出瀏覽器。"""
    if not can_auto_open_browser():
        return False

    def open_later():
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            print(f"自動開啟瀏覽器失敗: {error}")

    Thread(target=open_later, daemon=True).start()
    return True


def draw_boxes(frame, detections):
    """在影像上畫出偵測框，並回傳類別統計與最佳目標中心位置。
    
    detections: list of dict, each has:
        - "class_id": int
        - "class_name": str
        - "confidence": float
        - "xyxy": np.array([x1, y1, x2, y2])
    """
    counts = defaultdict(int)
    best_center_x = None
    best_conf = -1.0
    img_w = frame.shape[1]

    for det in (detections or []):
        class_id = int(det["class_id"])
        confidence = float(det["confidence"])
        class_name = str(det["class_name"])
        track_id = safe_int(det.get("track_id"), -1)
        x1, y1, x2, y2 = map(int, det["xyxy"])

        # 只追蹤目標類別
        if class_name not in TARGET_CLASSES:
            continue

        counts[class_name] += 1
        color = BOX_COLORS.get(class_name, (0, 200, 255))

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        if track_id >= 0:
            label = f"#{track_id} {class_name} {confidence:.2f}"
        else:
            label = f"{class_name} {confidence:.2f}"
        cv2.putText(
            frame,
            label,
            (x1, max(y1 - 10, 25)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )

        # 保留信心度最高的目標中心，供導航邏輯判斷左右偏移。
        if confidence > best_conf and img_w > 0:
            best_conf = confidence
            best_center_x = ((x1 + x2) / 2.0) / img_w

    return frame, counts, best_center_x


def draw_info_panel(frame, counts, fps, status_text, arduino_status, telemetry):
    """在本機 OpenCV 預覽視窗上疊加即時資訊面板。"""
    if cv2 is None:
        return frame

    height, width = frame.shape[:2]
    panel_width = 360
    panel_height = 250
    x1, y1 = 10, 10
    x2, y2 = x1 + panel_width, y1 + panel_height

    cv2.rectangle(frame, (x1, y1), (x2, y2), (25, 25, 25), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 180), 2)

    lines = [
        "R4 + YOLO Monitor",
        f"FPS: {fps:.1f}",
        f"Status: {status_text}",
        f"Arduino: {arduino_status}",
        f"FL={telemetry['FL']} FR={telemetry['FR']} SL={telemetry['SL']}",
        f"SR={telemetry['SR']} C={telemetry['C']}",
        f"R4 ST: {telemetry['ST']}",
        f"PY CMD: {telemetry['PY_CMD']}",
    ]

    y = 38
    for index, text in enumerate(lines):
        font_scale = 0.7 if index == 0 else 0.55
        thickness = 2 if index == 0 else 1
        cv2.putText(frame, text, (25, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness)
        y += 28

    for label in sorted(TARGET_CLASSES):
        cv2.putText(
            frame,
            f"{label}: {counts.get(label, 0)}",
            (25, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (255, 255, 255),
            1,
        )
        y += 20

    cv2.putText(frame, "Press Q to quit", (width - 180, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return frame


# === 啟動流程與主迴圈 ===
def ensure_yolo_available():
    """確認 Ultralytics YOLO + OpenVINO 推論所需套件都已安裝。"""
    missing = []
    if YOLO is None:
        missing.append("ultralytics")
    if cv2 is None:
        missing.append("cv2")
    if np is None:
        missing.append("numpy")

    if missing:
        raise RuntimeError(f"缺少執行 YOLO 推論所需套件: {', '.join(missing)}")

    if not Path(YOLO_MODEL_PATH).exists():
        raise RuntimeError(f"找不到 OpenVINO 模型: {YOLO_MODEL_PATH}")


# === ffmpeg 合成器：軟體 MJPEG 編碼串流 ===
class FFmpegCompositor:
    """透過 ffmpeg 子程序將 OpenCV 已標框影格編碼為 MJPEG 串流。

    Python 負責：攝影機擷取、ncnn 推論、cv2 畫框
    ffmpeg 負責：高效 MJPEG 編碼、瀏覽器串流
    """

    def __init__(self, width=640, height=480, fps=30, quality=5):
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality
        self._proc = None
        self._latest_mjpeg = b""
        self._lock = Lock()
        self._reader_stop = Event()
        self._reader_thread = None

    def start(self):
        print(f"ffmpeg 合成器: MPJGEG stream ({self.width}x{self.height} @{self.fps}fps)")

        # ffmpeg stdin: raw BGR → stdout: MPJGEG (multipart JPEG stream)
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "pipe:0",
            "-c:v", "mjpeg",
            "-q:v", str(self.quality),
            "-f", "mpjpeg",
            "-avioflags", "direct",
            "-flush_packets", "1",
            "pipe:1",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._start_time = time.time()
        self._frame_count = 0
        self._reader_thread = Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

    def _read_output(self):
        """從 ffmpeg stdout 讀取 MPJGEG，取最後一個 JPEG 幀。"""
        buf = b""
        while not self._reader_stop.is_set():
            try:
                chunk = self._proc.stdout.read(8192)
                if not chunk:
                    break
                buf += chunk
                # 只保留最後一個完整 JPEG (以 FF D8 開頭 FF D9 結尾)
                last_end = buf.rfind(b"\xff\xd9")
                if last_end >= 0:
                    last_start = buf.rfind(b"\xff\xd8", 0, last_end)
                    if last_start >= 0:
                        jpeg = buf[last_start:last_end + 2]
                        if len(jpeg) > 500:  # valid JPEG
                            with self._lock:
                                self._latest_mjpeg = jpeg
                            self._frame_count += 1
                        buf = buf[last_end + 2:]
                if len(buf) > 500_000:
                    buf = buf[-200_000:]
            except Exception:
                break

    def _read_output(self):
        """從 ffmpeg stdout 讀取 image2pipe，提取最後一個完整 JPEG 幀。"""
        buf = b""
        while not self._reader_stop.is_set():
            try:
                chunk = self._proc.stdout.read(8192)
                if not chunk:
                    break
                buf += chunk
                # 只保留最後一個完整 JPEG
                last_end = buf.rfind(b"\xff\xd9")
                if last_end >= 0:
                    last_start = buf.rfind(b"\xff\xd8", 0, last_end)
                    if last_start >= 0:
                        with self._lock:
                            self._latest_mjpeg = buf[last_start:last_end + 2]
                        buf = buf[last_end + 2:]
                if len(buf) > 500_000:
                    buf = buf[-200_000:]
            except Exception:
                break

    def feed_frame(self, bgr_frame):
        """將 OpenCV BGR 影格（已含標框）寫入 ffmpeg stdin。"""
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write(bgr_frame.tobytes())
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def get_latest(self):
        """回傳最新的 MJPEG 資料。"""
        with self._lock:
            return self._latest_mjpeg

    def stop(self):
        self._reader_stop.set()
        if self._proc:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            self._proc.terminate()
            self._proc.wait(timeout=3)
            self._proc = None


# === YOLO (Ultralytics + OpenVINO) 全域模型 ===
_yolo_model = None
_yolo_predictor = None
_ffmpeg_compositor = None
_last_bottle_assist_time = 0.0

def _load_yolo_model():
    """一次性載入 Ultralytics YOLO 模型並快取 predictor，避免重複初始化。"""
    global _yolo_model, _yolo_predictor
    if _yolo_model is None:
        if YOLO is None:
            raise RuntimeError("ultralytics 未安裝")
        _yolo_model = YOLO(YOLO_MODEL_PATH, task="detect")
        # 預先觸發 predictor 初始化，避免第一次推論的延遲
        _yolo_predictor = _yolo_model.predictor
        print(f"YOLO OpenVINO 模型已載入: {YOLO_MODEL_PATH}")
    return _yolo_model


def _results_to_detections(results, use_track_ids=True, transform_xyxy=None):
    """把 Ultralytics result 轉成主程式使用的 detection dict。"""
    detections = []
    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            class_id = int(box.cls[0])
            xyxy = box.xyxy[0].cpu().numpy().copy()
            if transform_xyxy is not None:
                xyxy = transform_xyxy(xyxy)
            detections.append({
                "class_id": class_id,
                "class_name": NCNN_CLASS_NAMES[class_id] if class_id < len(NCNN_CLASS_NAMES) else f"class_{class_id}",
                "confidence": float(box.conf[0]),
                "xyxy": xyxy,
                "track_id": int(box.id[0]) if use_track_ids and box.id is not None else -1,
            })
    return detections


def _has_class_detection(detections, class_name):
    """檢查目前 detections 是否已包含指定類別。"""
    return any(str(det.get("class_name")) == class_name for det in detections)


def _get_class_detections(detections, class_name):
    """取出指定類別的 detections。"""
    return [det for det in detections if str(det.get("class_name")) == class_name]


def _get_best_class_confidence(detections, class_name):
    """取得指定類別目前最高信心度。"""
    class_detections = _get_class_detections(detections, class_name)
    if not class_detections:
        return -1.0
    return max(float(det.get("confidence", 0.0)) for det in class_detections)


def _compute_iou(box_a, box_b):
    """計算兩個 xyxy bbox 的 IoU。"""
    ax1, ay1, ax2, ay2 = [float(value) for value in box_a]
    bx1, by1, bx2, by2 = [float(value) for value in box_b]

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union_area = area_a + area_b - inter_area
    if union_area <= 0.0:
        return 0.0
    return inter_area / union_area


def _merge_bottle_assist_detections(base_detections, assist_detections):
    """把 bottle-only 補偵測合併回主 detections，避免重複框。"""
    if not assist_detections:
        return base_detections

    merged = list(base_detections)
    existing_bottles = _get_class_detections(merged, BOTTLE_CLASS_NAME)
    for assist_detection in assist_detections:
        assist_box = assist_detection.get("xyxy")
        is_duplicate = False
        for existing_detection in existing_bottles:
            existing_box = existing_detection.get("xyxy")
            if existing_box is None:
                continue
            if _compute_iou(existing_box, assist_box) >= BOTTLE_ASSIST_IOU_THRESHOLD:
                is_duplicate = True
                break

        if is_duplicate:
            continue

        merged.append(assist_detection)
        existing_bottles.append(assist_detection)

    return merged


def _rotate_xyxy_180(xyxy, frame_width, frame_height):
    """把 180 度旋轉畫面的 bbox 轉回原始座標系。"""
    transformed = xyxy.copy()
    transformed[0] = frame_width - xyxy[2]
    transformed[1] = frame_height - xyxy[3]
    transformed[2] = frame_width - xyxy[0]
    transformed[3] = frame_height - xyxy[1]
    return transformed


def _rotate_xyxy_90_cw(xyxy, frame_width, frame_height):
    """把順時針 90 度旋轉畫面的 bbox 轉回原始座標系。"""
    transformed = xyxy.copy()
    transformed[0] = xyxy[1]
    transformed[1] = frame_height - xyxy[2]
    transformed[2] = xyxy[3]
    transformed[3] = frame_height - xyxy[0]
    return transformed


def _rotate_xyxy_90_ccw(xyxy, frame_width, frame_height):
    """把逆時針 90 度旋轉畫面的 bbox 轉回原始座標系。"""
    transformed = xyxy.copy()
    transformed[0] = frame_width - xyxy[3]
    transformed[1] = xyxy[0]
    transformed[2] = frame_width - xyxy[1]
    transformed[3] = xyxy[2]
    return transformed


def _infer_upside_down_bottle(frame, model):
    """正常推論沒抓到 bottle 時，再用 180 度畫面補一次 bottle-only 推論。"""
    if (
        not UPSIDE_DOWN_BOTTLE_DETECTION_ENABLED
        or cv2 is None
        or BOTTLE_CLASS_ID < 0
    ):
        return []

    frame_height, frame_width = frame.shape[:2]
    rotated_frame = cv2.rotate(frame, cv2.ROTATE_180)
    results = model.predict(
        rotated_frame,
        imgsz=BOTTLE_ASSIST_IMAGE_SIZE,
        conf=min(UPSIDE_DOWN_BOTTLE_CONFIDENCE, BOTTLE_ASSIST_CONFIDENCE),
        iou=NMS_THRESHOLD,
        max_det=min(MAX_DETECTIONS, UPSIDE_DOWN_BOTTLE_MAX_DETECTIONS, BOTTLE_ASSIST_MAX_DETECTIONS),
        classes=[BOTTLE_CLASS_ID],
        verbose=False,
    )
    return _results_to_detections(
        results,
        use_track_ids=False,
        transform_xyxy=lambda xyxy: _rotate_xyxy_180(xyxy, frame_width, frame_height),
    )


def _infer_sensitive_bottle(frame, model):
    """用較低門檻與較大輸入尺寸補強正向 bottle 偵測。"""
    if BOTTLE_CLASS_ID < 0:
        return []

    results = model.predict(
        frame,
        imgsz=BOTTLE_ASSIST_IMAGE_SIZE,
        conf=BOTTLE_ASSIST_CONFIDENCE,
        iou=NMS_THRESHOLD,
        max_det=min(MAX_DETECTIONS, BOTTLE_ASSIST_MAX_DETECTIONS),
        classes=[BOTTLE_CLASS_ID],
        verbose=False,
    )
    return _results_to_detections(results, use_track_ids=False)


def _infer_sideways_bottle(frame, model):
    """對橫躺瓶子補做 90/270 度 bottle-only 推論。"""
    if (
        not SIDEWAYS_BOTTLE_DETECTION_ENABLED
        or cv2 is None
        or BOTTLE_CLASS_ID < 0
    ):
        return []

    frame_height, frame_width = frame.shape[:2]
    sideways_detections = []
    rotations = (
        (cv2.ROTATE_90_CLOCKWISE, lambda xyxy: _rotate_xyxy_90_cw(xyxy, frame_width, frame_height)),
        (cv2.ROTATE_90_COUNTERCLOCKWISE, lambda xyxy: _rotate_xyxy_90_ccw(xyxy, frame_width, frame_height)),
    )
    for rotation_code, transform_xyxy in rotations:
        rotated_frame = cv2.rotate(frame, rotation_code)
        results = model.predict(
            rotated_frame,
            imgsz=BOTTLE_ASSIST_IMAGE_SIZE,
            conf=min(SIDEWAYS_BOTTLE_CONFIDENCE, BOTTLE_ASSIST_CONFIDENCE),
            iou=NMS_THRESHOLD,
            max_det=min(MAX_DETECTIONS, SIDEWAYS_BOTTLE_MAX_DETECTIONS, BOTTLE_ASSIST_MAX_DETECTIONS),
            classes=[BOTTLE_CLASS_ID],
            verbose=False,
        )
        sideways_detections.extend(
            _results_to_detections(results, use_track_ids=False, transform_xyxy=transform_xyxy)
        )

    return sideways_detections


def _run_primary_inference(frame, model):
    """執行主 YOLO 推論；預設關閉 tracking 以減少 A7A 上的額外負擔。"""
    inference_kwargs = {
        "imgsz": IMG_SIZE,
        "conf": CONFIDENCE,
        "iou": NMS_THRESHOLD,
        "max_det": MAX_DETECTIONS,
        "classes": TARGET_CLASS_IDS or None,
        "verbose": False,
    }

    if YOLO_TRACKING_ENABLED:
        return model.track(frame, persist=True, **inference_kwargs), True

    return model.predict(frame, **inference_kwargs), False


def yolo_infer(frame):
    """使用 Ultralytics YOLO + OpenVINO INT8 推論（含追蹤器）。

    frame: numpy array, shape (H, W, 3), BGR uint8
    return: list of dict, each with class_id, class_name, confidence, xyxy, track_id
    """
    if YOLO is None:
        raise RuntimeError("ultralytics 未安裝")

    global _last_bottle_assist_time

    model = _load_yolo_model()
    results, use_track_ids = _run_primary_inference(frame, model)
    detections = _results_to_detections(results, use_track_ids=use_track_ids)

    best_bottle_confidence = _get_best_class_confidence(detections, BOTTLE_CLASS_NAME)
    now = time.monotonic()
    should_run_bottle_assist = (
        best_bottle_confidence < BOTTLE_ASSIST_TRIGGER_CONFIDENCE
        and (BOTTLE_ASSIST_INTERVAL_SECONDS <= 0.0 or now - _last_bottle_assist_time >= BOTTLE_ASSIST_INTERVAL_SECONDS)
    )
    if should_run_bottle_assist:
        assist_detections = _infer_sensitive_bottle(frame, model)
        assist_best_bottle_confidence = _get_best_class_confidence(assist_detections, BOTTLE_CLASS_NAME)
        if assist_best_bottle_confidence < BOTTLE_ASSIST_TRIGGER_CONFIDENCE:
            assist_detections.extend(_infer_upside_down_bottle(frame, model))
            assist_detections.extend(_infer_sideways_bottle(frame, model))
        detections = _merge_bottle_assist_detections(detections, assist_detections)
        _last_bottle_assist_time = now

    return detections


def initialize_yolo_runtime_components():
    """初始化 Serial、Flask 監看服務與影像擷取器。"""
    bridge = SerialBridge(SERIAL_PORT, SERIAL_BAUD_RATE, timeout=SERIAL_TIMEOUT)
    bridge.connect()
    print("已啟用 Serial JSON 感測器模式")

    # 先把網頁服務啟動起來，這樣就算鏡頭失敗，前端仍能看到 R4 狀態。
    web_server = DetectionWebServer(
        WEB_HTML_PATH,
        host=WEB_HOST,
        port=WEB_PORT,
        stream_client_interval_seconds=WEB_STREAM_CLIENT_INTERVAL_SECONDS,
        stream_max_width=WEB_STREAM_MAX_WIDTH,
        stream_jpeg_quality=WEB_STREAM_JPEG_QUALITY,
    )
    # 只印出網址，不在背景啟動 server（由 run_yolo_camera_mode 在主執行緒啟動）
    selected_port = web_server._select_port(web_server.port)
    if selected_port != web_server.port:
        print(f"連接埠 {web_server.port} 已被占用，改用 {selected_port}")
        web_server.port = selected_port

    local_base = f"http://127.0.0.1:{web_server.port}"
    print(f"網頁監看已啟動: {local_base}")
    print(f"本機頁面: {local_base}/")
    print(f"本機串流: {local_base}/stream")
    print(f"本機快照: {local_base}/snapshot")
    lan_candidates = get_lan_ipv4_candidates()
    if lan_candidates:
        print("可給 Arduino/手機使用的網址:")
        for ip in lan_candidates:
            print(f"  頁面: http://{ip}:{web_server.port}/")
            print(f"  串流: http://{ip}:{web_server.port}/stream")
            print(f"  快照: http://{ip}:{web_server.port}/snapshot")

    web_server.update_status_only(0.0, "starting", bridge.get_status(), {}, build_bridge_telemetry(bridge, "STARTING"))
    print(
        "影像輸出參數: "
        f"camera={CAMERA_WIDTH}x{CAMERA_HEIGHT}@{CAMERA_FPS} "
        f"infer-min-interval={INFERENCE_MIN_INTERVAL_SECONDS:.3f}s "
        f"tracking={'on' if YOLO_TRACKING_ENABLED else 'off'} "
        f"web-fps={WEB_STREAM_TARGET_FPS:.1f} "
        f"web-max-width={WEB_STREAM_MAX_WIDTH} "
        f"web-jpeg={WEB_STREAM_JPEG_QUALITY} "
        f"imgsz={IMG_SIZE}"
    )

    camera_source, source_hint = resolve_camera_source()
    print(f"連接鏡頭串流中... ({source_hint}: {camera_source})")
    try:
        frame_grabber = FrameGrabber(open_stream, camera_source)
    except Exception as error:
        frame_grabber = None
        print(f"鏡頭連線失敗，改為無影像監控模式: {error}")

    return bridge, web_server, frame_grabber


def run_serial_link_test():
    """只測試 R4 Serial 與前端狀態頁，不啟動鏡頭與 YOLO。"""
    bridge = SerialBridge(SERIAL_PORT, SERIAL_BAUD_RATE, timeout=SERIAL_TIMEOUT)
    bridge.connect()

    web_server = DetectionWebServer(
        WEB_HTML_PATH,
        host=WEB_HOST,
        port=WEB_PORT,
        stream_client_interval_seconds=WEB_STREAM_CLIENT_INTERVAL_SECONDS,
        stream_max_width=WEB_STREAM_MAX_WIDTH,
        stream_jpeg_quality=WEB_STREAM_JPEG_QUALITY,
    )
    web_server.start()

    print("已啟用 Serial JSON 感測器模式")
    bridge.send_command("STOP")
    last_print_time = 0.0

    try:
        while True:
            # 這個模式只驗證 Serial 資料鏈路，不做鏡頭與導航控制。
            sensors = bridge.read_sensors()
            telemetry = build_bridge_telemetry(bridge, "SERIAL_TEST")
            web_server.update_status_only(0.0, "serial link test", bridge.get_status(), {}, telemetry)

            now = time.monotonic()
            if now - last_print_time >= 1.0:
                print(
                    "[SerialTest] "
                    f"packets={bridge.packet_count} "
                    f"FL={sensors['FL']} FR={sensors['FR']} "
                    f"SL={sensors['SL']} SR={sensors['SR']} "
                    f"C={sensors['C']} ST={sensors['ST']}"
                )
                last_print_time = now

            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if bridge.is_connected():
            bridge.send_command("STOP")
        bridge.close()


def run_yolo_camera_mode():
    """完整主流程：YOLO (OpenVINO INT8)、R4、WiFi 監看與網頁控制都在此協作。"""
    ensure_yolo_available()
    bridge, web_server, frame_grabber = initialize_yolo_runtime_components()
    navigator = NavigationController(
        hard_turn_offset=NAVIGATION_HARD_TURN_OFFSET,
        obstacle_distance_cm=NAVIGATION_OBSTACLE_DISTANCE_CM,
        capture_distance_cm=NAVIGATION_CAPTURE_DISTANCE_CM,
        send_interval_seconds=NAVIGATION_SEND_INTERVAL_SECONDS,
    )
    wifi_monitor = WiFiSignalMonitor(WIFI_INTERFACE, WIFI_SIGNAL_POLL_INTERVAL_SECONDS)
    wifi_auto_switcher = WiFiAutoSwitcher(
        interface=WIFI_INTERFACE,
        weak_dbm=WIFI_SIGNAL_WEAK_DBM,
        recover_dbm=WIFI_SIGNAL_RECOVER_DBM,
        enabled=WIFI_AUTO_SWITCH_ENABLED,
        candidate_names=WIFI_SWITCH_CANDIDATES,
        scan_interval_seconds=WIFI_SWITCH_SCAN_INTERVAL_SECONDS,
        cooldown_seconds=WIFI_SWITCH_COOLDOWN_SECONDS,
        min_candidate_signal=WIFI_SWITCH_MIN_SIGNAL,
        min_signal_gain=WIFI_SWITCH_MIN_SIGNAL_GAIN,
        profile_refresh_seconds=WIFI_SWITCH_PROFILE_REFRESH_SECONDS,
        nmcli_timeout_seconds=WIFI_SWITCH_NMCLI_TIMEOUT_SECONDS,
    )
    print(
        "已啟用 WiFi 訊號監看: "
        f"iface={WIFI_INTERFACE or 'auto'} "
        f"warning<={WIFI_SIGNAL_WEAK_DBM}dBm "
        f"stable>={max(WIFI_SIGNAL_RECOVER_DBM, WIFI_SIGNAL_WEAK_DBM + 1)}dBm"
    )
    print(
        "WiFi 自動切網: "
        f"enabled={'yes' if WIFI_AUTO_SWITCH_ENABLED else 'no'} "
        f"targets={','.join(WIFI_SWITCH_CANDIDATES) if WIFI_SWITCH_CANDIDATES else 'all-saved-profiles'} "
        f"scan={WIFI_SWITCH_SCAN_INTERVAL_SECONDS:.1f}s "
        f"cooldown={WIFI_SWITCH_COOLDOWN_SECONDS:.1f}s"
    )

    preview_enabled = False
    if frame_grabber is not None and can_enable_local_preview():
        try:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
            preview_enabled = True
        except cv2.error:
            print("無法建立 OpenCV 視窗，改為僅網頁監控")
    elif frame_grabber is not None:
        print("偵測到無桌面顯示環境，已停用 OpenCV 視窗預覽（僅保留網頁監控）")

    # ffmpeg 路徑目前不提供給 Flask /stream 使用，預設關閉以避免額外佔用 CPU。
    global _ffmpeg_compositor
    if ENABLE_FFMPEG_COMPOSITOR:
        _ffmpeg_compositor = FFmpegCompositor(
            width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fps=int(WEB_STREAM_TARGET_FPS)
        )
        _ffmpeg_compositor.start()

    # 背景發佈：以目標 FPS 推送最新幀到 web_server，避免重複 JPEG 編碼同一張畫面。
    latest_display_frame = None
    display_lock = Lock()
    stream_publish_stop = Event()
    inference_stop = Event()

    def publish_stream_frames():
        """以目標 FPS 推送最新畫面；尚未完成推論時先退回 raw frame。"""
        last_frame_identity = None
        last_push_time = time.time()
        target_interval = WEB_STREAM_FRAME_INTERVAL_SECONDS
        while not stream_publish_stop.is_set():
            with display_lock:
                display_frame = latest_display_frame

            if display_frame is None and frame_grabber is not None:
                ok, raw_preview_frame = frame_grabber.read()
                if ok and raw_preview_frame is not None:
                    display_frame = raw_preview_frame.copy()

            now = time.time()
            if display_frame is not None:
                current_identity = id(display_frame)
                if current_identity != last_frame_identity or now - last_push_time >= target_interval:
                    # JPEG: 只有新影格才重編碼；若影格未變，沿用上一個 JPEG。
                    pushed = web_server.update_frame_only(display_frame)
                    # ffmpeg: 餵入合成器 (若有的話)
                    if pushed and _ffmpeg_compositor is not None:
                        _ffmpeg_compositor.feed_frame(display_frame)
                    if pushed:
                        last_frame_identity = current_identity
                    last_push_time = now
            elapsed = time.time() - now
            sleep_time = max(target_interval - elapsed, 0.001)
            time.sleep(sleep_time)

    # 背景推論執行緒
    def inference_loop():
        nonlocal latest_display_frame
        previous_time = time.time()
        last_web_update_time = 0.0
        last_detections = None
        last_infer_time = 0.0
        last_inference_error_log_time = 0.0
        last_processed_frame_sequence = -1
        preview_counter = 0  # 本地預覽節流計數器

        while not inference_stop.is_set():
            try:
                sensors = bridge.read_sensors()
                wifi_sample = wifi_monitor.poll()
                wifi_telemetry = build_wifi_telemetry(wifi_sample)
                wifi_telemetry.update(wifi_auto_switcher.evaluate(wifi_sample))
                control_state = web_server.get_control_state()
                control_telemetry = build_control_telemetry(control_state)
                manual_mode = control_telemetry["CONTROL_MODE"] == CONTROL_MODE_MANUAL
                manual_command = control_telemetry["MANUAL_COMMAND"]

                if manual_mode:
                    bridge.send_command(manual_command)

                runtime_extra = {}
                runtime_extra.update(wifi_telemetry)
                runtime_extra.update(control_telemetry)

                if frame_grabber is None:
                    telemetry = build_bridge_telemetry(bridge, "CAMERA_OFFLINE", runtime_extra)
                    web_server.update_status_only(0.0, "camera offline", bridge.get_status(), {}, telemetry)
                    time.sleep(0.05)
                    continue

                ok, raw_frame, raw_frame_sequence = frame_grabber.read_with_sequence()
                if not ok or raw_frame is None:
                    telemetry = build_bridge_telemetry(bridge, "WAITING_FRAME", runtime_extra)
                    web_server.update_status_only(0.0, "waiting for frame", bridge.get_status(), {}, telemetry)
                    time.sleep(0.002)
                    continue

                if raw_frame_sequence == last_processed_frame_sequence:
                    now_mono = time.monotonic()
                    if now_mono - last_web_update_time >= WEB_STREAM_UPDATE_INTERVAL_SECONDS:
                        telemetry = build_bridge_telemetry(bridge, "FRAME_WAIT", runtime_extra)
                        web_server.update_status_only(0.0, f"running ({frame_grabber.mode}, waiting new frame)", bridge.get_status(), {}, telemetry)
                        last_web_update_time = now_mono
                    time.sleep(min(WEB_STREAM_FRAME_INTERVAL_SECONDS, 0.005))
                    continue

                last_processed_frame_sequence = raw_frame_sequence

                frame = raw_frame
                now_infer = time.monotonic()
                should_infer = last_detections is None or now_infer - last_infer_time >= INFERENCE_MIN_INTERVAL_SECONDS

                if should_infer:
                    try:
                        detections = yolo_infer(frame)
                        last_detections = detections
                    except Exception as error:
                        with display_lock:
                            latest_display_frame = frame
                        if now_infer - last_inference_error_log_time >= INFERENCE_ERROR_LOG_INTERVAL_SECONDS:
                            print(f"YOLO 推論失敗: {error}")
                            last_inference_error_log_time = now_infer
                        now_mono = time.monotonic()
                        if now_mono - last_web_update_time >= WEB_STREAM_UPDATE_INTERVAL_SECONDS:
                            telemetry = build_bridge_telemetry(bridge, "INFER_ERROR", runtime_extra)
                            web_server.update_status_only(0.0, f"running ({frame_grabber.mode}, inference error)", bridge.get_status(), {}, telemetry)
                            last_web_update_time = now_mono
                        time.sleep(0.05)
                        continue
                    last_infer_time = now_infer

                if last_detections is None:
                    with display_lock:
                        latest_display_frame = frame
                    now_mono = time.monotonic()
                    if now_mono - last_web_update_time >= WEB_STREAM_UPDATE_INTERVAL_SECONDS:
                        telemetry = build_bridge_telemetry(bridge, "WARMING_UP", runtime_extra)
                        web_server.update_status_only(0.0, f"running ({frame_grabber.mode}, warming up)", bridge.get_status(), {}, telemetry)
                        last_web_update_time = now_mono
                    continue

                annotated_frame, counts, garbage_center_x = draw_boxes(frame.copy(), last_detections)

                py_command = manual_command if manual_mode else "IDLE"
                if not manual_mode and NAVIGATION_MODE_ENABLED:
                    mcu_avoidance_state = resolve_mcu_avoidance_state(
                        sensors,
                        NAVIGATION_OBSTACLE_DISTANCE_CM,
                    )
                    if mcu_avoidance_state:
                        navigator.last_command = None
                        py_command = mcu_avoidance_state
                    else:
                        py_command = navigator.update(
                            counts,
                            sensors.get("C"),
                            garbage_center_x,
                            sensors.get("FL"),
                            sensors.get("FR"),
                            bridge,
                        ) or navigator.latest_command

                current_time = time.time()
                fps = 1.0 / max(current_time - previous_time, 1e-6)
                previous_time = current_time
                telemetry = build_bridge_telemetry(bridge, py_command, runtime_extra)

                with display_lock:
                    latest_display_frame = annotated_frame if not preview_enabled else annotated_frame.copy()

                now_mono = time.monotonic()
                if now_mono - last_web_update_time >= WEB_STREAM_UPDATE_INTERVAL_SECONDS:
                    web_server.update_status_only(fps, f"running ({frame_grabber.mode}, openvino/int8)", bridge.get_status(), counts, telemetry)
                    last_web_update_time = now_mono

                if preview_enabled:
                    preview_counter += 1
                    if preview_counter % 3 == 0:  # 每 3 幀才渲染一次本地視窗，解放 CPU
                        try:
                            draw_info_panel(annotated_frame, counts, fps, "", bridge.get_status(), telemetry)
                            cv2.imshow(WINDOW_NAME, annotated_frame)
                            cv2.waitKey(1)
                        except cv2.error:
                            pass
            except Exception:
                time.sleep(0.1)

    # 啟動背景執行緒
    stream_publisher_thread = Thread(target=publish_stream_frames, daemon=True)
    stream_publisher_thread.start()
    infer_thread = Thread(target=inference_loop, daemon=True)
    infer_thread.start()

    try:
        print("啟動 Web 伺服器...")
        schedule_browser_open(f"http://127.0.0.1:{web_server.port}/")
        # Web 伺服器在主執行緒執行 (阻斷式)
        web_server.app.run(host=web_server.host, port=web_server.port, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        inference_stop.set()
        stream_publish_stop.set()
        stream_publisher_thread.join(timeout=2)
        # 停止 ffmpeg 合成器
        if _ffmpeg_compositor is not None:
            _ffmpeg_compositor.stop()
            _ffmpeg_compositor = None
        if bridge.is_connected():
            bridge.send_command("STOP")
        bridge.close()
        if frame_grabber is not None:
            frame_grabber.release()
        if preview_enabled:
            cv2.destroyAllWindows()


def main():
    """程式入口：先做單實例保護，再決定跑測試模式或完整模式。"""
    if not acquire_instance_lock():
        return

    try:
        if env_flag("SERIAL_LINK_TEST"):
            run_serial_link_test()
            return
        run_yolo_camera_mode()
    finally:
        release_instance_lock()


if __name__ == "__main__":
    main()