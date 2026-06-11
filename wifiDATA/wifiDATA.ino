// =================================================================
// 雙體船 5感測器 + 雙馬達差速控制 (Arduino UNO R4 WiFi 頂配最穩腳位版)
// =================================================================

// 【通訊模式開關】
// true : 序列埠監控器看中文除錯訊息 
// false: 純乾淨 JSON 輸出，專門給 Python 讀取（自動導航時務必改為 false）
const bool DEBUG_MODE = true; 

// --- 【頂配完美優化】雙 BTN7960 馬達腳位配置 (完全避開 Timer 與 DAC 衝突) ---
const int RPWM_L = 2;   // 左馬達前進 (硬體 PWM)
const int LPWM_L = 3;   // 左馬達後退 (硬體 PWM)
const int RPWM_R = 5;   // 右馬達前進 (標準 PWM)
const int LPWM_R = 6;   // 右馬達後退 (標準 PWM)

// --- 【頂配完美優化】5 顆超音波腳位 (完全移開馬達 PWM 引腳，留空 D0, D1) ---
const int tFL = 10, eFL = 11;    // 左前鋒 (D10, D11)
const int tFR = 12, eFR = 13;    // 右前鋒 (D12, D13)
const int tSL = 7,  eSL = 8;     // 左側衛 (D7, D8)
const int tSR = A2, eSR = A3;    // 右側衛 (A2, A3 當數位腳)
const int tC  = A0, eC  = A1;    // 中央嘴巴 (A0, A1 當數位腳，徹底避開 PWM 衝突)

// --- 系統參數設定 ---
const int motorSpeed = 255;     // 巡航速度 (0~255)
const int turnHighSpeed = 255;  // 轉向外側馬達速度 (0~255)
const int wallDist = 35;        // 自動避障觸發距離 (35cm)
unsigned long lastJsonTime = 0; // 傳輸計時器

// 運行狀態變數
long dFL = 999, dFR = 999, dSL = 999, dSR = 999, dC = 999;
String motorState = "STOP";
unsigned long overrideUntil = 0; // 強制逃脫計時器

void setup() {
  // R4 WiFi 核心原廠標準高速率
  Serial.begin(115200); 
  delay(500); // 讓 R4 WiFi 序列埠晶片穩定準備
  
  // 馬達腳位初始化
  pinMode(RPWM_L, OUTPUT); pinMode(LPWM_L, OUTPUT);
  pinMode(RPWM_R, OUTPUT); pinMode(LPWM_R, OUTPUT);
  
  // 超音波腳位初始化
  pinMode(tFL, OUTPUT); pinMode(eFL, INPUT);
  pinMode(tFR, OUTPUT); pinMode(eFR, INPUT);
  pinMode(tSL, OUTPUT); pinMode(eSL, INPUT);
  pinMode(tSR, OUTPUT); pinMode(eSR, INPUT);
  pinMode(tC, OUTPUT);  pinMode(eC, INPUT);
  
  stopMotors();
  if (DEBUG_MODE) {
    Serial.println("\r\n--- 雙體船 R4 WiFi 頂配完美版安全啟動 ---");
  }
}

void loop() {
  // 1. 讀取所有感測器距離
  updateSensors();

  // 2. 處理 Python 指令 (使用非阻塞式單字元累積，絕不卡死)
  if (Serial.available() > 0) {
    char inChar = (char)Serial.read();
    static String cmd = "";
    if (inChar == '\n') {
      cmd.trim();
      // 在死胡同倒退逃脫期間，除非 Python 下達 STOP，否則拒絕其他干擾指令
      bool escapeLocked = (millis() <= overrideUntil) && (motorState == "EVADE_BACKWARD");
      if (!escapeLocked || cmd == "STOP") {
        executePythonCommand(cmd);
      }
      cmd = ""; // 清空快取
    } else if (inChar != '\r') {
      cmd += inChar;
    }
  }

  // 3. MCU 硬體自動防撞與「死胡同逃脫」核心機制
  if (millis() > overrideUntil) {
    
    // 【死胡同逃脫：第一步】四面楚歌 -> 強制巨力倒車
    if (dFL <= wallDist && dFR <= wallDist && dSL <= wallDist && dSR <= wallDist && motorState != "EVADE_BACKWARD") {
      moveBackward(); 
      motorState = "EVADE_BACKWARD";
      overrideUntil = millis() + 1500; // 強制倒車 1.5 秒
    }
    // 【死胡同逃脫：第二步】倒車完畢 -> 原地大迴轉甩開死角
    else if (motorState == "EVADE_BACKWARD") {
      turnLeft(); 
      motorState = "EVADE_SPIN";
      overrideUntil = millis() + 1000; // 強制原地旋轉 1 秒
    }
    // 【避障 A】正前方牆壁阻擋 -> 向左大轉彎
    else if (dFL <= wallDist && dFR <= wallDist) {
      turnLeft(); 
      motorState = "EVADE_BOTH";
    }
    // 【避障 B】左前方太近 -> 向右差速避讓
    else if (dFL <= wallDist) {
      turnRight(); 
      motorState = "EVADE_RIGHT";
    } 
    // 【避障 C】右前方太近 -> 向左差速避讓
    else if (dFR <= wallDist) {
      turnLeft();  
      motorState = "EVADE_LEFT";
    }
    // 【狀態恢復】警報解除，前方與側邊皆安全 -> 恢復自動巡航
    else if (motorState == "EVADE_RIGHT" || motorState == "EVADE_LEFT" || motorState == "EVADE_BOTH" || motorState == "EVADE_SPIN") {
      moveForward();             
      motorState = "AUTO_CRUISE"; 
    }
    // 【初始啟動】開機或被 Python 叫停後重新出發
    else if (motorState == "STOP" || motorState == "PY_CAPTURE_STOP") {
      moveForward();
      motorState = "AUTO_CRUISE";
    }
  }

  // 4. 定時數據輸出 (間隔 250ms，給 Python 留出充裕的接收緩衝)
  if (millis() - lastJsonTime > 250) {
    if (DEBUG_MODE) {
      Serial.print("【R4 運行中】左前:"); Serial.print(dFL); Serial.print("cm | ");
      Serial.print("右前:"); Serial.print(dFR); Serial.print("cm | ");
      Serial.print("左側:"); Serial.print(dSL); Serial.print("cm | ");
      Serial.print("右側:"); Serial.print(dSR); Serial.print("cm | ");
      Serial.print("中央:"); Serial.print(dC);  Serial.print("cm || 狀態: ");
      Serial.println(motorState); 
    } 
    else {
      // 標準純淨 JSON 格式，Python 端 loads 絕對不會報錯
      Serial.print("{\"FL\":"); Serial.print(dFL);
      Serial.print(",\"FR\":"); Serial.print(dFR);
      Serial.print(",\"SL\":"); Serial.print(dSL);
      Serial.print(",\"SR\":"); Serial.print(dSR);
      Serial.print(",\"C\":");  Serial.print(dC);
      Serial.print(",\"ST\":\""); Serial.print(motorState);
      Serial.println("\"}");
    }
    lastJsonTime = millis();
  }
}

// --- 強固型測距副程式 (10ms 超時保護，殘響過濾，絕不卡死程式) ---
long getDist(int trig, int echo) {
  digitalWrite(trig, LOW);  delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10);
  digitalWrite(trig, LOW);
  
  long duration = pulseIn(echo, HIGH, 10000); // 10ms 超時限制 (約 1.7 米內有效防卡死)
  if (duration == 0) return 999;
  
  long distance = duration * 0.034 / 2;
  if (distance <= 2 || distance > 400) return 999; 
  return distance;
}

void updateSensors() {
  dFL = getDist(tFL, eFL); delay(5); // 留 5ms 避開殘響交叉干擾
  dFR = getDist(tFR, eFR); delay(5);
  dSL = getDist(tSL, eSL); delay(5);
  dSR = getDist(tSR, eSR); delay(5);
  dC  = getDist(tC, eC);
}

// --- 接收並解析 Python 指令 ---
void executePythonCommand(String cmd) {
  if (cmd == "STOP") {
    stopMotors();
    motorState = "PY_CAPTURE_STOP";
    overrideUntil = millis() + 2000; // 聽從 Python 叫停時，強制原地安全鎖定 2 秒
  } else if (cmd == "FORWARD") {
    moveForward();
    motorState = "PY_FORWARD";
    overrideUntil = 0;
  } else if (cmd == "TURN_LEFT") {
    turnLeft();
    motorState = "PY_TURN_LEFT";
    overrideUntil = 0;
  } else if (cmd == "TURN_RIGHT") {
    turnRight();
    motorState = "PY_TURN_RIGHT";
    overrideUntil = 0;
  }
}

// --- BTN7960 工業級大電流馬達控制邏輯 ---
void stopMotors() {
  analogWrite(RPWM_L, 0); analogWrite(LPWM_L, 0);
  analogWrite(RPWM_R, 0); analogWrite(LPWM_R, 0);
}

void moveForward() {
  analogWrite(LPWM_L, 0); analogWrite(LPWM_R, 0); 
  analogWrite(RPWM_L, motorSpeed); 
  analogWrite(RPWM_R, motorSpeed);
}

void moveBackward() {
  analogWrite(RPWM_L, 0); analogWrite(RPWM_R, 0); 
  analogWrite(LPWM_L, motorSpeed); 
  analogWrite(LPWM_R, motorSpeed);
}

void turnRight() {
  // 左馬達全速推進，右馬達斷電煞車 -> 快速向右差速修正
  analogWrite(LPWM_L, 0); analogWrite(RPWM_R, 0); analogWrite(LPWM_R, 0);
  analogWrite(RPWM_L, turnHighSpeed); 
}

void turnLeft() {
  // 右馬達全速推進，左馬達斷電煞車 -> 快速向左差速修正
  analogWrite(RPWM_L, 0); analogWrite(LPWM_L, 0); analogWrite(LPWM_R, 0);
  analogWrite(RPWM_R, turnHighSpeed); 
}