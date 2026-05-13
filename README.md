# YOLO 垃圾辨識展示

這個專案用 Python 讀取 IP Camera 串流，並使用 YOLO 做即時物件辨識展示。

目前已加入 Arduino 序列整合，Python 偵測到指定物件後會透過 USB 序列埠送出命令給 Arduino。

## 需求

- Python 3.10+
- 可連線到鏡頭串流網址 `http://192.168.4.1/api/v1/stream`

## 安裝

```powershell
pip install -r requirements.txt
```

## 執行

```powershell
python main.py
```

## Arduino 整合

1. 將 [arduino_garbage_bridge/arduino_garbage_bridge.ino](c:/Users/wu131/Desktop/專題程式/arduino_garbage_bridge/arduino_garbage_bridge.ino) 燒進 Arduino。
2. 用 Arduino IDE 的序列監控確認鮑率是 `9600`。
3. 在 [main.py](c:/Users/wu131/Desktop/專題程式/main.py) 修改 `SERIAL_PORT`，例如 Windows 常見是 `COM3`、`COM4`。
4. 啟動 Python 程式後，偵測結果穩定連續出現 5 幀才會送一次指令，避免抖動連發。

### 偵測到目標才抓距離（新增）

`main.py` 目前已支援「觸發式抓距離」：

- 當畫面偵測到目標類別（例如 `bottle`、`banana`）時，才呼叫 Arduino 的 `/api/sensor` 抓距離。
- 預設由 `ARDUINO_SENSOR_FETCH_ON_DETECTION_ONLY = True` 啟用。
- 觸發最小間隔可用 `ARDUINO_SENSOR_TRIGGER_MIN_INTERVAL_SECONDS` 調整（避免過度頻繁請求）。

如果你想恢復固定輪詢模式，將 `ARDUINO_SENSOR_FETCH_ON_DETECTION_ONLY` 改為 `False` 即可。

### 命令 URL 自動推導（新增）

`main.py` 已加入自動推導命令 API：

- 只要設定 `ARDUINO_SENSOR_API_URL`（例如 `http://192.168.0.88/api/sensor`）
- 程式會自動推導 `http://192.168.0.88/api/command/`
- 由 `ARDUINO_AUTO_DERIVE_COMMAND_API_URL = True` 控制（預設開啟）

這樣你只要維護一個 Arduino IP，能降低 `Arduino Wi-Fi 指令失敗` 的設定錯誤機率。

### 垃圾前進 / 牆壁轉向（新增）

已加入導航模式（`main.py`）：

- `NAVIGATION_MODE_ENABLED = True`：啟用後，Python 不再送分類字串，改送導航命令。
- 偵測到垃圾目標類別：送 `FORWARD`
- 沒偵測到垃圾且距離小於等於 `NAVIGATION_WALL_DISTANCE_CM`：送 `TURN_RIGHT`

Arduino 韌體（`wifiDATA/wifiDATA.ino`）也已加入命令：

- `FORWARD`
- `TURN_RIGHT`
- `TURN_LEFT`
- `STOP`
- `AUTO`（回到自動避障）

注意：此功能需要重新燒錄 `wifiDATA/wifiDATA.ino` 到 Arduino，才能接收新命令。

Arduino 會接收以下文字命令：

- `BOTTLE`
- `CUP`
- `WINE_GLASS`
- `BANANA`
- `APPLE`
- `ORANGE`
- `NONE`

範例 Arduino 程式目前用 LED 示範：

- 可回收類：亮 `D8`
- 水果類：亮 `D9`
- 無偵測或待機：亮板載 LED

如果你要控制舵機、馬達或分類閘門，可以直接把 `handleCommand()` 裡面的 LED 邏輯換成你的硬體控制程式。

## 操作

- 將瓶子、杯子、水果等物件放到鏡頭前
- 按 `Q` 離開視窗

## 備註

目前使用的是通用 YOLO 模型 `yolov8n.pt`，目的是先確認：

1. 鏡頭串流正常
2. Python 可正常讀取畫面
3. YOLO 推論流程正常
4. 畫面顯示與計數正常

這還不是自訂垃圾分類模型。等展示版跑穩後，再進一步改成自己的垃圾資料集模型。
