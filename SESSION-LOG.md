# Asset converter 續行

只列 open；已完成的看 git log 與專題文件（[PACKAGE](PACKAGE.md)、[MODFORGE-PACKAGE](MODFORGE-PACKAGE.md)、[REAL-ASSETS](REAL-ASSETS.md)、[EFFECT-SHADER-CTD](EFFECT-SHADER-CTD.md)）。

## 主動推進

- 使用者 2026-11-05 裁示恢復主動推進 → 下場開線：先讀 README／本檔定第一個切片。

## 待回家實機驗收

離線測試不冒充遊戲驗收；細項與步驟在母 repo [WAIT_USER asset-converter](../../wait-user/feature-runtime/asset-converter.md)。

- SheenChair 一鍵整包：布紋／木紋比例、法線方向、接縫、碰撞站立。最新批次 `backend/home-validation/20260910-160024-009333/`（ZIP 不進 Git，攜帶整批或照 [HOME-VALIDATION](HOME-VALIDATION.md) 重建）。
- 兩個 DS 光柱：用 effect shader 修正版重產，進原 cell 驗收不 CTD、可見；之後才決定恢復 DS 管線的兩筆 `skip`。
- 頂點色與 MASK 裁切：舊彩色／MASK NIF 需重轉後看顏色與挖空。
- ModForge 單模型切片（`modforge_package`）：未部署，外觀／尺寸方向／碰撞站立未驗。

## 後續方向（未開工）

True PBR、反向貼圖、蒙皮／動畫、單 primitive 凹形碰撞自動拆分、通用 ModForge `modelSource`／`importmesh` 接線。

## 環境

WSL 用 `.venv-wsl`（`.venv-wsl/bin/python -m pytest -q`），FBX2glTF 在忽略的 `tools/bin/`；Windows 用 `.venv`。
