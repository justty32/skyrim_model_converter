# INDEX — model-converter 專案地圖

model-converter = **以 Skyrim `.nif`（含 `.dds`）為中心的模型格式雙向轉換工具：`nif2gltf`、`gltf2nif`、`any2nif`（FBX／OBJ／glTF 等 → 完整 Data 目錄）、`tex2dds`；靠 CLI 被 ModForge、darksouls-port、godot-worldspace-editor 消費**。本檔只描述**頂層**：每列一句話＋連結；目錄內部複雜就放它自己的 README / INDEX。

## Repo 佈局

| 路徑 | 內容 |
|------|------|
| [`nif2gltf/`](../nif2gltf/) | 反向：Skyrim NIF 靜態 mesh → glTF（自寫 parser，說明在根 README「實作」節）|
| [`gltf2nif/`](../gltf2nif/README.md) | glTF 靜態 mesh → SSE NIF（含材質、碰撞）|
| [`any2nif/`](../any2nif/) | 正向整包入口：glTF／GLB／OBJ／FBX… → 完整 Data 目錄（規則見 [PACKAGE.md](../PACKAGE.md)）|
| [`tex2dds/`](../tex2dds/) | 貼圖 → BC1／BC3 DDS（含 mipmap）|
| [`tools/`](../tools/) | `fetch_fbx2gltf.sh`（下載 FBX2glTF 到 gitignore 的 `tools/bin/`）、`smoke_real_assets.py`、`build_sheen_validation.py` |
| [`tests/`](../tests/) | pytest suite 與 fixtures |
| 根 `*.md` | [PROTOCOL](../PROTOCOL.md)（CLI 契約）、[PACKAGE](../PACKAGE.md)、[AUDIT](../AUDIT.md)、[EFFECT-SHADER-CTD](../EFFECT-SHADER-CTD.md)、[HOME-VALIDATION](../HOME-VALIDATION.md)、[REAL-ASSETS](../REAL-ASSETS.md)、[tool-survey](../tool-survey.md) |
| `.venv/`、`.venv-wsl/`、`backend/` | 本機 venv 與驗收包輸出（gitignore）|
| `wf/` | 工作流（派發見 [WORKFLOWS.md](WORKFLOWS.md)；共享區 [workflows/common/](workflows/common/README.md)）|
| `.claude/commands/` | slash 指令適配層（可選）。Claude Code 為例：只讀專案根的這層，非侵入式佈局也留在根；沒有 slash 機制的工具忽略本目錄，直接跑 `wf/tools/wf-lint.sh` |
| `wf/tools/` | kernel 工具：`wf-lint.sh`（檢查）、`tabledb.py`（資料檔 CRUD／連結）、`find_big_lists.py`、`fix_moved_links.py`；資料檔契約見 [workflows/common/data-files.md](workflows/common/data-files.md) |
<!-- wf-insert:INDEX -->

## 頂層文件

| 檔案 | 角色 |
|------|------|
| [WORKFLOWS.md](WORKFLOWS.md) | 派發器：意圖 → 工作流入口 |
| [STRUCTURE.md](STRUCTURE.md) | 結構整理參考（被動）：分層、膨脹即拆、四級成長、archive、工作流形式 |
| [SESSION-LOG.md](../SESSION-LOG.md) | 進度（在 repo 根，不在 `wf/`；依日期段落記錄現役狀態）|
| [WAIT_USER.md](../../../WAIT_USER.md) | 等使用者親自做 / 驗證的事，**以母 repo 那份為準**，本 repo 不另開 |
