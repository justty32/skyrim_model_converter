# dev-env — 開發環境、指令、外部工具

[WORKFLOWS](../WORKFLOWS.md)｜[INDEX](../INDEX.md)

這台機器上要能開發需要什麼、怎麼裝、跑什麼指令；外部工具設定與 env var 也收在這裡。

**何時用**：fresh clone、換機器、裝不起來、忘了指令、要加一個外部工具或環境變數。
**何時不用**：驗證 / 測試怎麼跑 → [testing](testing.md)；程式碼慣例 → [common/conventions](common/conventions.md)。

## Done when

- 照「流程（fresh clone 後）」走完，`.venv/bin/python -m pytest -q` 回傳 0。
- 下面三張表沒有空欄；要使用者親自做的（帳號、授權、金鑰）在 [WAIT_USER](../../../../WAIT_USER.md) 各佔一行。

## 流程（fresh clone 後）

1. 照同層佈局 clone 在 `~/repo/moddings/skyrim/projects/`（跨 repo 連結與 consumer 測試以同層為前提）。
2. `python -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`（WSL 可用 `uv venv .venv-wsl`，見根 README）。
3. 要測 FBX：`./tools/fetch_fbx2gltf.sh`，或設 `MODEL_CONVERTER_FBX2GLTF`。
4. 跑一次 `.venv/bin/python -m pytest -q` 確認。

## 指令表

| 做什麼 | 指令 | 備註 |
|--------|------|------|
| 安裝依賴 | `.venv/bin/python -m pip install -r requirements.txt` | `requirements.txt` 變動時 |
| build | 無 | 純 Python |
| 跑起來 | `python -m any2nif <in> <outdir> --package`、`python -m gltf2nif <in.gltf> <out.nif>`、`python -m nif2gltf --in … --out … --flat` | 參數見 [PROTOCOL](../../PROTOCOL.md) |
| lint / format | 無 | 沒有設定 lint |

驗證與測試指令不列這裡——連同「誰跑」一起在 [testing](testing.md)。

## 跨機 / 離線差異

| 環境 | 能跑 | 跑不了的 → 怎麼辦 |
|------|------|------------------|
| 主力機（家中 Linux，`.venv`）| 全部離線 suite；實機驗收 | — |
| 公司 WSL（`.venv-wsl`）| 全部離線 suite | 遊戲內外觀／站立驗收 → 母 repo WAIT_USER |

## 外部工具與 env var

| 名稱 | 用途 | 怎麼取得 / 設定 |
|------|------|----------------|
| FBX2glTF | FBX 匯入 | `./tools/fetch_fbx2gltf.sh` 下載到 gitignore 的 `tools/bin/` |
| `MODEL_CONVERTER_FBX2GLTF` | 指定 FBX2glTF binary | 未設時用 `tools/bin/` 那份 |

需要帳號、付費、授權才能取得的：守鐵律 2（授權來源），並在 [WAIT_USER](../../../../WAIT_USER.md) 記一行。

## 交接

- 環境就緒要開工 → [feature-dev](feature-dev/README.md)；先確認驗證跑得動 → [testing](testing.md)。
- 同一個裝機坑第二次撞到 → [common/gotchas](common/gotchas.md)。
