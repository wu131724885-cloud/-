import socket
import time
from pathlib import Path
from threading import Lock, Thread

import cv2
from flask import Flask, Response, jsonify, send_file


# 這個模組專門負責網頁端 API 與串流，不放偵測與硬體控制邏輯。
class DetectionWebServer:
    def __init__(
        self,
        html_path,
        host="0.0.0.0",
        port=8080,
        stream_client_interval_seconds=0.005,
        stream_max_width=960,
        stream_jpeg_quality=72,
    ):
        self.html_path = Path(html_path)
        self.host = host
        self.port = port
        self.stream_client_interval_seconds = stream_client_interval_seconds
        self.stream_max_width = stream_max_width
        self.stream_jpeg_quality = stream_jpeg_quality

        self.latest_jpeg = None
        self.latest_meta = {
            "fps": 0.0,
            "status": "starting",
            "arduino": "disconnected",
            "counts": {},
            "updated": time.time(),
        }

        self.lock = Lock()
        self.app = Flask(__name__)
        self._register_routes()

    def _register_routes(self):
        # 首頁：回傳監看畫面。
        @self.app.get("/")
        def index():
            if self.html_path.exists():
                return send_file(self.html_path)
            return "index.html not found", 404

        # 狀態：給前端輪詢 FPS、計數與 Arduino 狀態。
        @self.app.get("/status")
        def status():
            with self.lock:
                return jsonify(self.latest_meta)

        # 即時影像：使用 multipart MJPEG 連續推送。
        @self.app.get("/stream")
        def stream():
            return Response(
                self._stream_generator(),
                mimetype="multipart/x-mixed-replace; boundary=frame",
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                },
            )

        # 單張快照：當 MJPEG 不穩定時可作為備援。
        @self.app.get("/snapshot")
        def snapshot():
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

    def _stream_generator(self):
        # 持續送出最新 JPEG；若尚未有影像就短暫等待。
        while True:
            with self.lock:
                frame = self.latest_jpeg

            if frame is None:
                time.sleep(0.05)
                continue

            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(self.stream_client_interval_seconds)

    def update(self, frame, counts, fps, status_text, arduino_status):
        # 限制網頁端解析度，降低編碼負擔與網路傳輸量。
        web_frame = frame
        height, width = frame.shape[:2]
        if width > self.stream_max_width:
            scale = self.stream_max_width / float(width)
            web_frame = cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)

        ok, encoded = cv2.imencode(
            ".jpg",
            web_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.stream_jpeg_quality],
        )
        if not ok:
            return

        with self.lock:
            self.latest_jpeg = encoded.tobytes()
            self.latest_meta = {
                "fps": round(float(fps), 2),
                "status": status_text,
                "arduino": arduino_status,
                "counts": {k: int(v) for k, v in counts.items()},
                "updated": time.time(),
            }

    def start(self):
        # 以背景執行緒啟動 Flask，避免阻塞主偵測流程。
        def run():
            self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False, threaded=True)

        thread = Thread(target=run, daemon=True)
        thread.start()
        local_base = f"http://127.0.0.1:{self.port}"
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
        else:
            print("未偵測到可用區網 IP，請手動用 ipconfig 查詢並組成 http://<你的IP>:8080/stream")


def get_lan_ipv4_candidates():
    # 收集本機可對外連線的 IPv4，方便顯示給手機/Arduino 連線。
    candidates = []

    # 方式 1: 透過預設路由推測目前主要對外網卡 IP。
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            candidates.append(ip)
    except OSError:
        pass

    # 方式 2: 補上主機名稱解析到的本機 IPv4。
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127.") and ip not in candidates:
                candidates.append(ip)
    except OSError:
        pass

    return candidates
