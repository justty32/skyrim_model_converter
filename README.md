# Model Converter — 模型格式互轉工具

← CLI 契約：[PROTOCOL.md](PROTOCOL.md)

`MODFORGE_NIF2GLTF_BIN` 供黑盒 exec；單檔 `--in/--out/--flat`、批量 `manifest.json` 與 exit code 依契約。參考 wrapper 呼 `python -m nif2gltf`；契約 backend-agnostic，更換後端不改契約。

> **獨立 repo**。文中 `../ModForge/…`、`../godot-worldspace-editor/…` 這類連結，前提是各 repo **同層 clone 在同一個父目錄下**（本機為 `~/repo/moddings/skyrim/projects/`）。

**一句話**：以 Skyrim **`.nif`（含 `.dds` 紋理）** 為中心，做與 **Godot 可用格式（glTF）** 及 **各種常用模型格式（FBX / OBJ / glTF）** 的**雙向**互轉工具；正向路已由 `any2nif` + `tex2dds` 實作。定位是 ModForge 生態的**基石工具**——不整合進 ModForge，靠協議/CLI 被消費。

**消費方向**：vanilla `.nif` → glTF 供 [worldspace 物件編輯](../godot-worldspace-editor/README.md) 作視覺代理；外部 FBX/OBJ/glTF → `.nif` 供 [model-porting](../ModForge/workflows/idea/asset-pipelines/model-porting/README.md)，正向決策沿用該工作流。

## 一條命令轉成完整資產目錄

在專案環境已裝好依賴後：

```bash
python -m any2nif crate.glb output/Crate --package
```

`output/Crate` 是可交給 ModForge 或放進 mod 的 Data 內容：模型在 `meshes/any2nif/crate/crate.nif`，DDS 在 `textures/any2nif/crate/`，`converter-package.json` 記錄檔案與雜湊。預設自動產生凸包碰撞；已分件模型可用 `--collision convex-mesh` 保留零件間空隙；箱子可改 `--collision box`，純展示物可用 `--collision none`。來源是公分或 Z-up 時加 `--unit cm --up-axis z`；想自己取輸出名稱則加 `--asset-name my_crate`。

整包模式會補沒有名字的材質、分開同名材質、烘入 diffuse 顏色，並檢查模型引用的每張 DDS 都存在、符合 BC1/BC3 與完整 mipmap。先在暫存目錄轉好並驗證才發布；失敗保留前一版。重跑可替換本工具產生且未被手動修改的整包，其他目錄或手改成品會拒絕覆蓋。

凸包會填滿模型的凹洞；它不是建築室內的精細碰撞。Skyrim 傳統材質是 PBR 的近似，True PBR 與蒙皮不在這條流程內。完整規則與目前不支援的材質設定見 [PACKAGE.md](PACKAGE.md)。離線測試不能代替遊戲中的外觀與站立驗收。

WSL 可用本機已有的 `uv` 建獨立環境：

```bash
uv venv .venv-wsl
uv pip install --python .venv-wsl/bin/python -r requirements.txt
.venv-wsl/bin/python -m any2nif crate.glb output/Crate --package
```

FBX 另跑 `./tools/fetch_fbx2gltf.sh`，會下載至本專案忽略的 `tools/bin/`；其他格式不需要它。原有直接輸出 `.nif` 的用法仍可使用。

---

## 關係定位（不重造輪子）

見 [docs/README.md](docs/README.md)。

## 外部工具與格式調查

見 [docs/README.md](docs/README.md)。

## Scope

見 [docs/README.md](docs/README.md)。

## 實作（`nif2gltf/` — 自寫載體，2026-06-17 離線）

見 [docs/nif-backends.md](docs/nif-backends.md)。

## 實作（`gltf2nif/` — 反向後端，2026-07-05）

見 [docs/nif-backends.md](docs/nif-backends.md)。

## 實作（`any2nif/` — 正向入口）

見 [docs/package-backends.md](docs/package-backends.md)。

## 實作（`tex2dds/` — 貼圖編碼）

見 [docs/package-backends.md](docs/package-backends.md)。

## Open

待續項目見 [SESSION-LOG.md](SESSION-LOG.md)。一般靜態物件可加 `--collision box|convex|convex-mesh` 自動產生碰撞，或以 `--package` 一次轉完整模型與 DDS；詳細規則見 [PACKAGE.md](PACKAGE.md)。凹形碰撞自動拆分尚未完成。

- **反向產出實機驗證**（**待主力機**）：`gltf2nif` 輸出的 `.nif`（含碰撞）進遊戲測試 cell，確認看得到、站得上去。離線 round-trip + 對 vanilla byte 核已過，剩實機 acceptance。
- **LE 真檔驗證**（待主力機）：LE 格式仍缺真檔取樣；[SSE 石頭／松樹既有驗收紀錄](../ModForge/wait_todo/worldspace-editor.md)不能代替正向整包或碰撞實機驗收。
- **`.3ds`**：trimesh 不支援；assimp 需要 sudo 安裝，目前未採用。
- **蒙皮／骨架**：`gltf2nif` 明確拒絕帶 skin／動畫的輸入，現階段只支援靜態 mesh。
- **BC7**：未實作；目前 `tex2dds` 的 BC1／BC3 覆蓋目前需求。
