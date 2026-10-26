# model-converter — AI agent 專案備忘

model-converter = **以 Skyrim `.nif`（含 `.dds`）為中心的模型格式雙向轉換工具：`nif2gltf`、`gltf2nif`、`any2nif`（FBX／OBJ／glTF 等 → 完整 Data 目錄）、`tex2dds`；靠 CLI 被 ModForge、darksouls-port、godot-worldspace-editor 消費**。人類向說明在根 [README.md](README.md)。本檔是最頂層路由器，只指向下一層；細節不寫這裡（分層原則見 [STRUCTURE.md](wf/STRUCTURE.md)）。

## 開場與入口

- 每個 session 先讀根 [SESSION-LOG.md](SESSION-LOG.md) 開頭的 `Done when` 與「現役狀態」段（本 repo 的進度記在根，不在 `wf/`）；等使用者的項記在母 repo [WAIT_USER.md](../../WAIT_USER.md)，本 repo 不另開。
- **碰原始碼前**：慣例與 code map → [workflows/common/conventions.md](wf/workflows/common/conventions.md)、[workflows/common/code-map.md](wf/workflows/common/code-map.md)；環境與指令 → [workflows/dev-env.md](wf/workflows/dev-env.md)。
<!-- wf-insert:AGENTS -->
- **要你動手做事** → [WORKFLOWS.md](wf/WORKFLOWS.md) 依意圖派發，再讀該工作流入口檔。
- **想看專案結構** → [INDEX.md](wf/INDEX.md)。
- 使用者偏好與邊界 → [workflows/common/user.md](wf/workflows/common/user.md)。

## 鐵律（always-on，隨時適用）

1. 重構 / 整理**不改原意**：開發＝行為不變且驗證綠燈（`.venv/bin/python -m pytest -q`）；非開發＝原意不變，照 `Done when:` 驗收。
2. **不可逆或對外的動作**（push、刪除、對外送出、動 DB）要有**授權來源**：使用者當場確認，或他親自登記在清單裡（routines / schedule）。都沒有就先問。
3. **條列走資料檔、導航留 md**：給 AI 消化的表／清單 >1 KB 存 `.json`／`.csv`（契約 `wf-table/1`，見 [data-files](wf/workflows/common/data-files.md)），用 `wf/tools/tabledb.py` 讀寫、不整份讀進 context；給人點的導航連結留 md。
4. **具體流程**在各工作流入口檔，不在頂層。
5. **HEAD 被釘選**：`../darksouls-port/p3/converter-pin.json` 釘本 repo 的 commit＋原始碼 sha256，DSPort P3 gate 比對主 checkout 的 `HEAD`。**主 checkout 不 commit、不切分支**；改動在 `git worktree` 的分支施工並推遠端，等下次重釘 pin 時一起併 `main`。
6. CLI 契約在 [PROTOCOL.md](PROTOCOL.md)：改 exit code／參數要同步消費方（ModForge、darksouls-port、godot-worldspace-editor）。

<!-- wf-kernel v0.6+368468a (2026-10-26 導入) -->
