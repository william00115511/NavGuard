"""NavGuard vs Google Maps 安全性驗證子專案（離線批次評估，不掛進正式 API）。

方法論見 backend/evaluation/README.md：兩條路線一律套用 app/engine/safety.py
與 app/engine/metrics.py 既有、未修改的評分函式，避免另外寫一套算分邏輯
導致比較失去意義。
"""
