# 手機 App 打包（Android / iOS）

這個資料夾是獨立的手機殼專案（Capacitor）。

它不會改動你的 Python 偵測核心，只是在手機內嵌顯示你現有的網頁：`http://<電腦IP>:8080/`。

## 1. 前置需求

- Node.js 18+
- Android Studio（Android）
- Xcode（iOS，僅 macOS）

## 2. 安裝套件

在 `mobile_app` 目錄執行：

```powershell
npm install
```

## 3. 建立原生專案

```powershell
npm run cap:add:android
npm run cap:add:ios
```

如果只要 Android，可只執行第一行。

## 4. 同步 Web 檔案

```powershell
npm run cap:sync
```

## 5. 開啟 IDE 打包

Android:

```powershell
npm run cap:open:android
```

iOS:

```powershell
npm run cap:open:ios
```

## 6. 手機端使用

1. 先在電腦啟動 Python 程式：
   ```powershell
   python main.py
   ```
2. 確認終端機顯示 `http://<電腦IP>:8080/`。
3. 手機與電腦在同一個 Wi-Fi。
4. App 首頁輸入該網址後按「連線」。

## 注意

- 若 Android 連不上，先確認手機能在瀏覽器開 `http://<電腦IP>:8080/`。
- 防火牆需允許 Python 監聽 8080。
- 這個殼 App 只顯示你本地服務，不會把 YOLO 推論搬到手機端。
