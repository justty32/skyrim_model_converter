# Asset converter 續行

Done when: 一般模型、貼圖與碰撞能用一條命令轉成完整 Data 目錄；模型引用的 DDS 全數存在且可解碼；失敗保留既有成品；glTF/GLB、OBJ、FBX 等路徑有離線驗證，CLI／測試／文件對齊。遊戲內外觀與站立驗收另行記錄，不用離線測試冒充。

## 現役狀態

2026-09-10 分件碰撞已完成：`--collision convex-mesh` 依正規化後的 node × primitive 各自建立凸包。離線驗證分件門框保留空隙、重複擺放與尺度軸向、多凸包 NIF 引用，以及 65,538 頂點完整整包的渲染切分不改碰撞分組。單一 primitive 的凹洞仍不會自動拆分。

2026-09-10 續行已完成：`normalTexture.scale` 改為烘進法線貼圖，支援歸零、減弱、加強與負值；非有限值仍拒絕。整包測試實際讀回 NIF 引用的 DDS，確認共用 diffuse 不受影響、重跑一致、壞設定保留既有整包。

2026-09-10 公司 WSL：使用者選定的一鍵一般模型流程已完成離線實作，使用方式見 [PACKAGE.md](PACKAGE.md)。實機外觀／尺寸方向／碰撞站立仍待回家驗收，記在母 repo 的 [WAIT_USER](../../wait-user/feature-runtime.md#asset-converter-一鍵靜態模型整包2026-09-10)。

本次獨立 WSL 環境在 `.venv-wsl`，由既有 uv 建立；FBX2glTF 在忽略的 `tools/bin/`。Windows `.venv` 保留。使用者已授權專案內安裝依賴與 push。

驗證：`.venv-wsl/bin/python -m pytest -q --disable-warnings` → **310 passed、零 skipped**；包含真 FBX2glTF、跨格式完整 Data 目錄、所有 DDS 槽位、65535 頂點切分、負縮放、錯誤資料與發布／回復。仍有既有套件的 deprecation warnings。

跨 repo darksouls-port live contract 另行嘗試，但其 `initial_state.py` import 缺少 soulstruct，未能開始執行；本輪未改 darksouls-port 或裸 `gltf2nif` writer，不能把本 repo 測試當成該跨 repo 測試已通過。

## 後續方向

True PBR、反向貼圖、蒙皮／動畫、凹形碰撞自動拆分與 ModForge spec 的黑盒接線仍是另外的工作。本輪不宣稱這些已完成；舊 idea 的入口已改標目前實作與歷史方案。
