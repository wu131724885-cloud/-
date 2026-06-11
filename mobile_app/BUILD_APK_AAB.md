# Android Studio 打包 APK / AAB 完整指南

## 前置條件

1. **Android Studio 已安裝**（2022.1 以上）
2. **Android SDK**（在 Android Studio 首次啟動時會自動提示下載）
3. **Java/Kotlin 開發環境**（Android Studio 內建）

## 一、開啟 Android 專案

### 方式 1：直接用 npm 指令（最快）
在 `mobile_app` 資料夾執行：
```powershell
npm run cap:open:android
```

Android Studio 會自動開啟 `mobile_app/android` 專案。

### 方式 2：手動開啟
1. 開啟 Android Studio。
2. 選 **File** → **Open**。
3. 瀏覽到 `c:\Users\wu131\Desktop\專題程式\mobile_app\android`。
4. 點 **OK** 等待 Gradle 同步（首次可能 2~5 分鐘）。

## 二、選擇構建類型

等 Gradle 同步完成後（右下角提示完成），在 Android Studio 選單找 **Build** 選項：

```
Build → Select Build Variant
```

選擇適合的變體：
- **debugArm64** 或 **debugX86_64** = 測試版（自簽名，用於開發/測試）
- **releaseArm64** 或 **releaseX86_64** = 正式版（需手動簽名，用於發佈）

## 三、打包 APK（直接安裝到手機）

### 適合場景
- 快速測試
- 內部分享給團隊試用
- 不上架到 Google Play

### 操作步驟

1. **選菜單**：
   ```
   Build → Build Bundle(s) / APK(s) → Build APK(s)
   ```

2. **選 Release 版本**（如果要發佈）：
   - 如果是 **Debug 版本**：會自動用 Android SDK 的預設簽名，直接構建。
   - 如果是 **Release 版本**：需要設定簽名密鑰（見下方「簽名設定」）。

3. **等待構建**（通常 30 秒～2 分鐘）：
   - 右下角會顯示進度
   - 完成後會看到 `APK(s) generated successfully`

4. **找到 APK 檔**：
   ```
   mobile_app/android/app/build/outputs/apk/release/app-release.apk
   ```
   或
   ```
   mobile_app/android/app/build/outputs/apk/debug/app-debug.apk
   ```

5. **傳到手機並安裝**：
   - USB 連接手機 → 允許 USB 偵錯
   - 或通過檔案分享（Email、Google Drive、AirDrop）
   - 雙擊 APK 即可安裝（手機需允許安裝未知來源）

## 四、打包 AAB（Google Play 商店提交）

### 適合場景
- 上架到 Google Play 商店
- Google Play 會自動優化並簽名
- 支援更多設備組合（ARM、x86 等）

### 操作步驟

1. **選菜單**：
   ```
   Build → Build Bundle(s) / APK(s) → Build Bundle(s)
   ```

2. **選 Release 版本** 並設定簽名（見「簽名設定」）。

3. **等待構建**（通常 1~3 分鐘）。

4. **找到 AAB 檔**：
   ```
   mobile_app/android/app/build/outputs/bundle/release/app-release.aab
   ```

5. **上傳到 Google Play Console**：
   - 登入 Google Play Console
   - 建立應用 → 上傳 AAB 檔
   - Google Play 會自動生成各設備的 APK

## 五、簽名設定（Release 版本必須）

### 生成簽名金鑰（第一次）

1. **選菜單**：
   ```
   Build → Generate Signed Bundle / APK
   ```

2. **選 APK 或 AAB**（例如 APK）。

3. **新增金鑰庫**：
   ```
   Create new... → 設定以下參數：
   - Key store path: c:\Users\wu131\Desktop\專題程式\my.keystore
   - Password: [設定一個複雜密碼，例如: MyGarbage@2025]
   - Key alias: garbage_key
   - Key password: [同上或其他密碼]
   - Validity: 25 years (預設即可)
   - Certificate 部分填寫你的資訊（或留空）
   ```

4. **點 OK**。

### 之後再打包時

Android Studio 會記住金鑰庫路徑，只需輸入密碼即可。

## 六、常見問題

### Q：Debug 和 Release 有何區別？
- **Debug**：自簽名、可直接在 Android Studio 安裝、檔案較大。
- **Release**：需要自己的簽名金鑰、可上架商店、檔案較小。

### Q：簽名金鑰遺失了怎麼辦？
- 如果 keystore 檔案還在（`my.keystore`），用同樣密碼即可恢復。
- 如果遺失，只能產生新的 keystore，舊版本無法更新升級。

### Q：APK 太大怎麼辦？
- 可在 `android/app/build.gradle` 啟用 **minifyEnabled = true** 來縮小體積。
- 或只保留 ARM64 架構（移除 x86）。

### Q：手機上找不到 App 怎麼辦？
- 確認已允許安裝未知來源。
- 檢查手機 Android 版本是否符合（Capacitor 7 要求 Android 8+）。

## 七、快速參考

| 用途 | 指令 / 菜單 | 輸出位置 |
|------|-----------|--------|
| 快速測試 | `Build → Build APK(s)` (Debug) | `app/build/outputs/apk/debug/` |
| 分享給朋友 | `Build → Build APK(s)` (Release) | `app/build/outputs/apk/release/` |
| 上架商店 | `Build → Build Bundle(s)` (Release) | `app/build/outputs/bundle/release/` |

## 八、下一步

打包完成後：

1. **測試 Debug APK**：直接在手機安裝測試。
2. **準備 Release APK**：簽名後可分享給用戶。
3. **上架 AAB**：上傳到 Google Play Console，等審核。

有問題可查詢 [Capacitor Android Deployment 官方文檔](https://capacitorjs.com/docs/android)。
