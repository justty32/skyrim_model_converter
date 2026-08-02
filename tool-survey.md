# 外部工具與格式調查（MVP 後選型參考）

← [README](README.md)

> MVP 已改自寫純 Python 後端（`nif2gltf/`，見 README「實作」節），**不再依賴外部 NIF 工具**。本檔保留作 **MVP 後**（紋理/蒙皮/正向全矩陣）的後端選型參考與格式踩坑備忘。

## 工具盤點（2026-06-17，Explore agent 核對 repo 既有 finding）

> ⚠️ **幻覺黑名單（勿規劃）**：`amPerl/nif`（Rust `nif2gltf` CLI）**不存在**；`SkyMeshGLTF` 來源存疑。兩者 Gemini 捏造，[nif-gltf finding banner](../ModForge/sub_projs/gemini-research/worldspace-editor/nif-gltf-conversion.md) 已標。

| 工具 | 方向 | 平台 | 型態 | 確認度 |
|---|---|---|---|---|
| **NifSkope（fo76utils fork）** | nif↔glTF | **Linux + Win** | GUI（CLI 批量未證） | ✅ 真實，最新 2025-12；**Linux 唯一原生 nif→glTF 選項** |
| **PyNifly**（+ Blender glTF 匯出） | nif↔glTF（雙向最高品質） | **Win-only**（native `NiflyDLL`） | Blender addon | ✅ 真實，但鎖 Windows（2026 仍 Win-only） |
| **Outfit Studio** | glTF→nif（蒙皮） | Linux 可編譯 / Wine | GUI | ✅ 真實，批量未證 |
| **ck-cmd** | **FBX**↔nif（**非 glTF**） | Win / Wine | CLI | ✅ 真實，但**不直接吃 glTF**（要 FBX 中轉） |
| **Compressonator** | dds 編碼 | Linux/Win/Mac | CLI | ✅ 真實（model-porting 已選定） |

**現況關鍵缺口**：外部工具裡**沒有任何已驗證的「批量 nif→glTF」pipeline**（PyNifly+Blender 卡 Win-only；NifSkope fork 批量未證）。→ 即本 repo 改自寫的動機。

## 跨平台架構難點（決定工具長相）

- **PyNifly 鎖 Windows**（native DLL）→ 蒙皮/高保真路線無法在 Manjaro 原生跑。
- **NifSkope fo76utils fork 跨平台** → 靜態 mesh 的 nif→glTF 可在 Linux 原生。
- 因此工具大概率走 **dual-backend**（沿用 model-porting 的「靜態原生 Linux / 蒙皮 reboot Windows」決策），用環境變數或設定切後端——與 ModForge `MODFORGE_TTS_BIN` 那種協議掛法同精神。

## 紋理 round-trip 注意（lossy，兩向都要處理）

- **glTF 不帶 dds** → nif→glTF 時 dds 自動轉 PNG/JPEG；glTF→nif 時要重新編回 dds（BCn + mipmap，Compressonator）。
- **法線約定**：Skyrim = DirectX(Y−)、glTF/Godot = OpenGL(Y+) → 轉換要 **Flip Y**（往返各翻一次）。
- **`_n` 貼圖**：alpha = glossiness；Godot 要分離的 roughness（= 1 − gloss，需 Invert）。
- **純預覽代理**：只要形狀的話可**完全跳過紋理**，Godot 用平色 proxy——worldspace editor 物件擺放的 MVP 走這條最省。
