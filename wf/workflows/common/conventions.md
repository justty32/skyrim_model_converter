# conventions — 程式碼慣例（碰原始碼的工作流共用）

[common/README](README.md)｜[INDEX](../../INDEX.md)

碰原始碼的工作流（feature-dev / refactor / investigation）共用這套規矩：**寫碼時要遵守什麼**。哪個檔負責什麼領域、測試在哪 → [code-map](code-map.md)；真相層優先序也在 [code-map](code-map.md)。結構整理原則 → [STRUCTURE](../../STRUCTURE.md)。

## 慣例

| 項目 | 規矩 |
|------|------|
| 檔案拆分 | 單檔 >300 行照 [STRUCTURE](../../STRUCTURE.md) 檢視；查 `wc -l */*.py` |
| 命名 | snake_case；私有原語 `_` 前綴；測試 `tests/test_<套件>_<領域>.py` |
| 註解語言 | 沿用該檔既有語言；文件繁中 |
| breaking change | CLI（[PROTOCOL](../../../PROTOCOL.md)）是消費方契約：改前 grep 同層消費者 |
| 型別 / schema 同步 | NIF 欄位以 niftools `nif.xml` 為準；writer 要有獨立 bytes 解碼的測試 |
| 相依 | `requirements.txt`；新增先問 |
| 失敗保留舊成品 | 暫存驗證後才發布 |
