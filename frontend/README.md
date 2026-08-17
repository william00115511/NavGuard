# Safeway Flutter App

夜間步行安全導航的對話式 mobile client。Flutter 只顯示後端計算結果；不含 Gemini、路網、風險資料或任何 server-side API key。

## 設定

Maps SDK key 是平台受限的 mobile key，不是 secret server key。

```bash
# Android
cp android/local.properties.example android/local.properties
# 編輯 MAPS_API_KEY，並保留自己的 sdk.dir

# iOS
cp ios/Flutter/Local.xcconfig.example ios/Flutter/Local.xcconfig
# 編輯 GOOGLE_MAPS_API_KEY
```

在 Google Cloud Console 將 Android key 限制為此 app 的 package + SHA-1、只允許 Maps SDK for Android；iOS key 限制為 bundle ID `com.safeway.safewayFrontend`、只允許 Maps SDK for iOS。

後端 URL 是公開設定，可由 Dart define 提供：

```bash
flutter pub get
flutter run --dart-define=API_BASE_URL=https://YOUR_CLOUD_RUN_URL
```

未提供 `API_BASE_URL` 時，app 使用本機示範 response，方便獨立驗證 UI；真實 API contract 請參考根目錄 `AGENTS.md` 的 `/api/session` 和 `/api/chat`。

## 行為

- 啟動時取得位置並建立 session。
- 使用者以自然語言傳訊；前端顯示 `collecting_info`、`route_ready` 或 `error` 的 `reply_text`。
- `route_ready` 顯示推薦安全路線與最快路線、後端 metrics／warnings、動態事件摘要。
- 「在 Google Maps 開啟」會先提示實際導航路徑可能略有不同。
