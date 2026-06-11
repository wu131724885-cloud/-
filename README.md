# A7A 雙體船 AI 控制系統

本專案為 Radxa Cubie A7A（Allwinner A733）開發板設計的單機智慧雙體船控制系統，整合了影像串流、AI 物件偵測、Arduino 馬達控制、WiFi 監看 / 自動切網與網頁即時監控。

---

## 目錄
- [系統架構總覽](#系統架構總覽)
- [功能特色](#功能特色)
- [目標硬體與軟體環境](#目標硬體與軟體環境)
- [主要組件說明](#主要組件說明)
- [啟動與操作方式](#啟動與操作方式)
- [資料流與控制流](#資料流與控制流)
- [常見問題與排查](#常見問題與排查)

---

## 系統架構總覽

```
[IP Camera/MJPEG] → [main.py] ←→ [Arduino R4]
  │                │
  │                └─ [WiFi 監看 / 自動切網]
  │                │
  └─ [Flask + HTML 前端] ←→ [瀏覽器/手機]
```
- 影像串流 → AI 偵測 → 控制命令 → 馬達/感測器
- 前端頁面可即時監看、手動/自動切換、WiFi 警示

---

## 功能特色
- **雙推論後端**：預設使用 Ultralytics YOLOv8 `.pt` 模型，也可透過 `MODEL_NAME` 切回 TFLite 模型。
- **TFLite NPU 加速**：若指定 `.tflite` 模型，可採用 tflite-runtime + TIM-VX delegate 把推論下放 NPU。
- **單一主程式**：所有後端功能集中於 main.py，易於維護。
- **即時網頁監控**：Flask + HTML5 前端，支援 AUTO/MANUAL 切換與即時畫面。
- **Arduino 馬達/感測器控制**：USB Serial 雙向通訊，支援自動導航與手動接管。
- **WiFi 健康監看與自動切網**：自動偵測訊號品質，在弱訊號或斷線時透過 NetworkManager 切到較佳的已存 WiFi profile，前端即時顯示警示與切網狀態。
- **高容錯設計**：鏡頭、Serial、推論任一失效，其他功能可繼續運作。

---

## 目標硬體與軟體環境
- **開發板**：Radxa Cubie A7A（Allwinner A733, VIP9000 NPU）
- **作業系統**：Tina Linux / Ubuntu
- **AI SDK**：ZIFENG278/ai-sdk（含 libvx_delegate.so）
- **Python 3.7+**
- **主要依賴**：
  - ultralytics
  - tflite-runtime
  - numpy
  - opencv-python
  - flask
  - requests
  - pyserial

---

## 主要組件說明

### 1. main.py
- **AI 推論**：支援 Ultralytics YOLOv8 `.pt` 與 TFLite YOLO 風格模型；目前預設模型是 `yolov8n.pt`。
- **影像串流**：支援 MJPEG/RTSP，背景執行緒穩定抓流。
- **Serial 通訊**：與 Arduino R4 雙向資料交換，支援自動導航與手動命令。
- **Web 服務**：Flask 提供 /、/status、/control、/stream、/snapshot 等 API。
- **WiFi 監看 / 自動切網**：定期讀取 /proc/net/wireless，必要時透過 nmcli 切到較佳的已存 WiFi profile，前端即時顯示警示與切網狀態。
- **單實例保護**：避免多重啟動造成資源衝突。

### 2. 船體控制.html
- **前端操作面板**：即時畫面、遙測資訊、WiFi 警示、AUTO/MANUAL 切換、手動方向鍵。
- **高容錯互動**：按住移動、放開即停止，避免命令殘留。

### 3. wifiDATA/wifiDATA.ino
- **Arduino R4 韌體**：馬達 PWM 控制、超音波感測、JSON 資料回傳。

---

## 啟動與操作方式

### 1. 安裝依賴
```bash
pip install ultralytics tflite-runtime numpy opencv-python flask requests pyserial
```

### 2. 設定 NPU 驅動路徑
```bash
export LD_LIBRARY_PATH=/home/radxa/ai-sdk:$LD_LIBRARY_PATH
```

### 3. 啟動主程式
- **完整模式**：
  ```bash
  python3 main.py
  ```
- **若要切回舊 TFLite 模型**：
  ```bash
  MODEL_NAME=best_float16.tflite python3 main.py
  ```
- **純 Serial 測試**：
  ```bash
  SERIAL_LINK_TEST=1 python3 main.py
  ```

### 4. 前端監看
- 使用瀏覽器開啟 http://[A7A_IP]:8080/

### 5. WiFi 自動切網設定
- 預設會啟用 `WIFI_AUTO_SWITCH_ENABLED=1`，在訊號弱於 `WIFI_SIGNAL_WEAK_DBM` 或斷線時評估切網。
- 若要限制候選 profile，可設定 `WIFI_SWITCH_CANDIDATES=11111111,tiffany590919` 這種逗號分隔清單。
- `WIFI_SWITCH_MIN_SIGNAL` 控制候選網路最低信號百分比，`WIFI_SWITCH_MIN_SIGNAL_GAIN` 控制切換前至少要比目前網路好多少。
- `WIFI_SWITCH_SCAN_INTERVAL_SECONDS` 與 `WIFI_SWITCH_COOLDOWN_SECONDS` 可調整掃描頻率與切換冷卻時間。
- 這個功能依賴 NetworkManager / `nmcli` 與已儲存的 WiFi profile；若系統未使用 NetworkManager，主程式會退回監看模式。

---

## 資料流與控制流

1. **影像流**：FrameGrabber → TFLite NPU 推論 → 畫面標註 → 前端串流
2. **AI 推論**：預設為 Ultralytics YOLOv8 `.pt` 模型；若指定 `.tflite` 則走 TFLite 解碼 → NMS → 控制決策
3. **Serial 通訊**：Python ↔ Arduino R4，命令/感測資料雙向流動
4. **WiFi 監看 / 切網**：/proc/net/wireless + nmcli → build_wifi_telemetry / WiFiAutoSwitcher → 前端警示與切網狀態
5. **Web API**：/status、/control、/stream、/snapshot 提供即時互動
6. **控制模式**：AUTO（AI自動導航）/ MANUAL（前端手動接管）

---

## 常見問題與排查

- **相機 offline**：鏡頭串流失敗，請檢查 STREAM_URL 與網路連線。
- **/control 404**：後端未重啟，請重新啟動 main.py。
- **Serial 讀取失敗**：檢查 /dev/ttyACM0 權限與連線狀態。
- **NPU delegate 載入失敗**：確認 libvx_delegate.so 路徑與 LD_LIBRARY_PATH。
- **推論無結果**：先確認目前是 `.pt` 還是 `.tflite` 模型，再檢查對應的模型檔與 label/類別設定。

---

## 一句話總結

本專案即為 A7A 雙體船的 AI 控制中樞，單一主程式整合影像、AI、硬體、網頁與手動接管，支援 Ultralytics 與 TFLite 雙後端，適合嵌入式智慧船體應用。
