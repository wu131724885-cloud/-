#include "WiFiS3.h"

// --- Wi-Fi 設定 ---
char ssid[] = "tiffany590919";    // 請替換成你的 Wi-Fi SSID
char pass[] = "tiffany590919";    // 請替換成你的 Wi-Fi 密碼
int status = WL_IDLE_STATUS;
WiFiServer server(80);         // 建立網頁伺服器，使用 80 port

// Python 偵測串流位址: 要填「與 Arduino/手機 同一個網段」的電腦 IP。
// 你目前家用網段是 192.168.0.x，所以這裡用 192.168.0.17。
char pyStreamUrl[] = "http://192.168.0.17:8080/stream";

// --- 腳位定義 ---
const int trigPin = 8;
const int echoPin = 9;
const int EN_PIN = 7;
const int RPWM = 5;
const int LPWM = 6;

// --- 參數設定 ---
const int stopDistance = 20;
const int motorSpeed = 150;
const int turnSpeed = 150;
const unsigned long remoteOverrideMs = 1200;

// 全域變數儲存最新狀態
long currentDistance = 0;
String motorState = "停止";
String lastPythonCommand = "NONE";
unsigned long remoteOverrideUntil = 0;

void setMotorStop() {
  analogWrite(RPWM, 0);
  analogWrite(LPWM, 0);
}

void setMotorForward() {
  analogWrite(RPWM, motorSpeed);
  analogWrite(LPWM, 0);
}

void setMotorTurnRight() {
  analogWrite(RPWM, 0);
  analogWrite(LPWM, turnSpeed);
}

void setMotorTurnLeft() {
  analogWrite(RPWM, turnSpeed);
  analogWrite(LPWM, 0);
}

String parseRequestPath(const String& requestLine) {
  // 從 HTTP request line 取出路徑，例如 GET /api/sensor HTTP/1.1。
  int firstSpace = requestLine.indexOf(' ');
  if (firstSpace < 0) return "/";

  int secondSpace = requestLine.indexOf(' ', firstSpace + 1);
  if (secondSpace < 0) return "/";

  String path = requestLine.substring(firstSpace + 1, secondSpace);
  if (path.length() == 0) return "/";
  return path;
}

void sendSensorApiResponse(WiFiClient &client) {
  // 提供給 Python/前端輪詢的輕量 JSON API。
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: application/json; charset=utf-8");
  client.println("Cache-Control: no-store");
  client.println("Connection: close");
  client.println();
  client.print("{\"distance_cm\":");
  client.print(currentDistance);
  client.print(",\"motor_state\":\"");
  client.print(motorState);
  client.print("\",\"last_python_command\":\"");
  client.print(lastPythonCommand);
  client.println("\"}");
}

void applyPythonCommand(const String& command) {
  String cmd = command;
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0) return;

  lastPythonCommand = cmd;
  unsigned long now = millis();

  // 遠端指令會短暫覆蓋自動避障，確保命令有實際動作時間。
  if (cmd == "FORWARD") {
    setMotorForward();
    motorState = "遠端前進";
    remoteOverrideUntil = now + remoteOverrideMs;
    return;
  }

  if (cmd == "TURN_RIGHT") {
    setMotorTurnRight();
    motorState = "遠端右轉";
    remoteOverrideUntil = now + remoteOverrideMs;
    return;
  }

  if (cmd == "TURN_LEFT") {
    setMotorTurnLeft();
    motorState = "遠端左轉";
    remoteOverrideUntil = now + remoteOverrideMs;
    return;
  }

  if (cmd == "STOP") {
    setMotorStop();
    motorState = "遠端停止";
    remoteOverrideUntil = now + remoteOverrideMs;
    return;
  }

  if (cmd == "AUTO") {
    remoteOverrideUntil = 0;
    motorState = "自動模式";
  }
}

void sendCommandApiResponse(WiFiClient &client, const String& path) {
  // 將 URL 中的命令段取出並套用，例如 /api/command/STOP。
  const String prefix = "/api/command/";
  String cmd = path.substring(prefix.length());
  applyPythonCommand(cmd);

  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: application/json; charset=utf-8");
  client.println("Cache-Control: no-store");
  client.println("Connection: close");
  client.println();
  client.print("{\"ok\":true,\"received\":\"");
  client.print(lastPythonCommand);
  client.println("\"}");
}

void sendDashboardResponse(WiFiClient &client) {
  // 傳送標準 HTTP 回應標頭
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: text/html; charset=utf-8"); // 支援中文
  client.println("Connection: close");
  client.println();
  
  // 傳送 HTML 網頁內容
  client.println("<!DOCTYPE HTML>");
  client.println("<html>");
  client.println("<head><title>R4 WiFi 狀態監控</title></head>");
  client.println("<body style='font-family: Arial; text-align: center; margin-top: 50px;'>");
  client.println("<h2>Arduino 車輛即時狀態監控</h2>");

  client.println("<p style='font-size: 14px; color: #555;'>下方為 Python YOLO 即時偵測串流</p>");
  client.print("<p style='font-size: 13px; color: #777;'>串流網址: ");
  client.print(pyStreamUrl);
  client.println("</p>");
  client.print("<img src='");
  client.print(pyStreamUrl);
  client.println("' id='streamImg' alt='YOLO stream' style='width:min(92vw,900px); border:2px solid #333; border-radius:10px;' />");
  client.println("<p id='streamMsg' style='font-size:13px; color:#b00;'></p>");
  client.print("<p><a href='");
  client.print(pyStreamUrl);
  client.println("' target='_blank'>新分頁直接開啟串流</a></p>");
  
  // --- 已加上 ID 的數據顯示區塊 ---
  client.println("<p style='font-size: 24px;'>前方距離: <strong id='distVal' style='color: blue;'>讀取中...</strong></p>");
  client.println("<p style='font-size: 24px;'>馬達狀態: <strong id='motorVal' style='color: gray;'>讀取中...</strong></p>");
  client.println("<p style='font-size: 20px;'>Python 命令: <strong id='cmdVal' style='color: purple;'>讀取中...</strong></p>");

  client.print("<script>");
  
  // 原有的影像串流處理邏輯
  client.print("(function(){");
  client.print("var streamUrl='");
  client.print(pyStreamUrl);
  client.print("';");
  client.print("var snapshotUrl=streamUrl.replace('/stream','/snapshot');");
  client.print("var img=document.getElementById('streamImg');");
  client.print("var msg=document.getElementById('streamMsg');");
  client.print("var fallbackStarted=false;");
  client.print("function startSnapshotFallback(){");
  client.print("if(fallbackStarted){return;} fallbackStarted=true;");
  client.print("msg.textContent='MJPEG 失敗，已切換快照模式';");
  client.print("setInterval(function(){ img.src=snapshotUrl+'?t='+Date.now(); },150);");
  client.print("}");
  client.print("img.onerror=startSnapshotFallback;");
  client.print("setTimeout(function(){ if(!img.complete || img.naturalWidth===0){ startSnapshotFallback(); } },3000);");
  client.print("})();");

  // --- AJAX 自動輪詢邏輯 (每 1000 毫秒更新一次) ---
  client.println("setInterval(function() {");
  client.println("  fetch('/api/sensor')");
  client.println("    .then(response => response.json())");
  client.println("    .then(data => {");
  client.println("      document.getElementById('distVal').innerText = data.distance_cm + ' cm';");
  client.println("      var motorEl = document.getElementById('motorVal');");
  client.println("      motorEl.innerText = data.motor_state;");
  client.println("      if (data.motor_state.indexOf('煞停') !== -1) {");
  client.println("        motorEl.style.color = 'red';");
  client.println("      } else {");
  client.println("        motorEl.style.color = 'green';");
  client.println("      }");
  client.println("      document.getElementById('cmdVal').innerText = data.last_python_command;");
  client.println("    }).catch(e => console.log('讀取資料失敗', e));");
  client.println("}, 1000);"); 
  
  client.print("</script>");
  client.println("</body>");
  client.println("</html>");
}

void setup() {
  Serial.begin(115200);
  
  // 腳位初始化
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  pinMode(EN_PIN, OUTPUT);
  pinMode(RPWM, OUTPUT);
  pinMode(LPWM, OUTPUT);
  digitalWrite(EN_PIN, HIGH);

  // 檢查 Wi-Fi 模組
  if (WiFi.status() == WL_NO_MODULE) {
    Serial.println("通訊失敗：找不到 Wi-Fi 模組！");
    while (true); // 停在此處
  }

  // 嘗試連線到 Wi-Fi 網路
  Serial.print("嘗試連線至 WPA SSID: ");
  Serial.println(ssid);
  while (status != WL_CONNECTED) {
    status = WiFi.begin(ssid, pass);
    delay(5000);
  }
  
  server.begin(); // 啟動伺服器
  printWifiStatus(); // 印出 IP 位址
}

void loop() {
  // --- 1. 更新感測器與馬達狀態 ---
  updateSensorAndMotor();

  // --- 2. 處理 Wi-Fi 網頁客戶端請求 ---
  WiFiClient client = server.available();
  if (client) {
    String currentLine = "";
    String requestLine = "";
    bool gotRequestLine = false;

    while (client.connected()) {
      if (client.available()) {
        char c = client.read();

        if (c == '\n') {
          currentLine.trim();

          if (!gotRequestLine && currentLine.length() > 0) {
            requestLine = currentLine;
            gotRequestLine = true;
          }

          // HTTP 標頭結束：空行
          if (currentLine.length() == 0) {
            String path = parseRequestPath(requestLine);
            
            // --- 新增：攔截瀏覽器的 favicon 請求，防止 Arduino 卡死 ---
            if (path == "/favicon.ico") {
              client.println("HTTP/1.1 404 Not Found");
              client.println("Connection: close");
              client.println();
            } 
            // 簡易路由：感測器 API、命令 API、其餘走監控頁
            else if (path == "/api/sensor") {
              sendSensorApiResponse(client);
            } else if (path.startsWith("/api/command/")) {
              sendCommandApiResponse(client, path);
            } else {
              sendDashboardResponse(client);
            }
            break;
          }

          currentLine = "";
          continue;
        }

        if (c != '\r') {
          currentLine += c;
        }
      }
    }
    // 給予瀏覽器一點時間接收數據
    delay(1);
    client.stop(); // 斷開連線
  }
}

// 獨立出來的感測與控制函式
void updateSensorAndMotor() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  
  long duration = pulseIn(echoPin, HIGH, 30000); // 加上 timeout 避免卡死
  
  if (duration == 0) {
    currentDistance = 999; // 超出範圍的防呆設定
  } else {
    currentDistance = duration * 0.034 / 2;
  }

  // 遠端模式生效期間，保留遠端控制，不覆寫馬達輸出。
  if (millis() < remoteOverrideUntil) {
    return;
  }

  // 馬達邏輯控制
  if (currentDistance > 0 && currentDistance <= stopDistance) {
    setMotorStop();
    motorState = "煞停 (障礙物警告)";
  } else {
    setMotorForward();
    motorState = "正轉 (安全)";
  }
}

// 印出連線資訊
void printWifiStatus() {
  Serial.print("成功連線至 SSID: ");
  Serial.println(WiFi.SSID());
  IPAddress ip = WiFi.localIP();
  Serial.print("請在瀏覽器輸入此 IP 位址來監看即時數據: ");
  Serial.println(ip);
}