# model-converter ↔ 消費者 協議（CLI 契約草案）

← [README](README.md)

**狀態：契約已實作（2026-06-17）。** 契約形狀已定，且**參考後端已自寫**＝本 repo 的 `nif2gltf` Python 模組（LE+SSE 靜態 mesh、`--flat`、batch manifest，23 測綠）；不再依賴 NifSkope。本檔定義「呼叫方看到什麼」，與用哪個後端解耦——後端換掉，契約不動。**唯一未竟＝對真實 vanilla `.nif` 的 byte 驗證**（離線無素材，見 README Open / WAIT_USER）。

## 定位（照 [skyrim-voicegen](../ModForge/sub_projs/skyrim-voicegen/README.md) 的掛法）

model-converter 是**黑盒 exec**，不整合進 ModForge / Godot editor。掛勾＝環境變數 **`MODFORGE_NIF2GLTF_BIN`**，指向一支 wrapper（在自己的 venv / 環境內跑選定後端）。呼叫方只給 args、只收 glTF；轉換器只看 args、不認得呼叫方。**互不整合。**

```
driver  ──(--in foo.nif --out foo.gltf [--flat])──►  nif2gltf  ──► writes foo.gltf (+ .bin/貼圖)
```

`driver` 可以是 Godot worldspace editor 的前置腳本、ModForge、或人工。轉換器的職責**只有一件**：一個 `.nif` → 一個 `.gltf`。

## MVP 範圍（鎖定，對齊 README）

靜態 mesh、Linux 原生、**`--flat` 跳紋理用平色**，輸出餵 [worldspace editor](../godot-worldspace-editor/README.md) 當物件代理（目前是彩色方塊 placeholder，換成真實 glTF）。蒙皮 / 紋理 round-trip / 反向（glTF→nif）皆 **MVP 後**。

## CLI 契約

### 單檔模式（MVP 必須）

```
nif2gltf --in <path.nif> --out <path.gltf> [--flat | --textures <root>] [--master <name>.esm]
```

| 旗標 | 必填 | 語意 |
|---|---|---|
| `--in` | ✅ | 來源 `.nif`（靜態 mesh；含 skin 的 MVP 可拒絕並 exit 3） |
| `--out` | ✅ | 目標 `.gltf` 路徑；同名 `.bin` / 貼圖寫同夾（呼叫方保證夾可寫） |
| `--flat` | （與 `--textures` 二選一，MVP 預設） | 跳過紋理，輸出單一平色材質（最省，worldspace 代理夠用） |
| `--textures <root>` | | 貼圖搜尋根（解 NIF 內 `textures\...` 相對路徑）；MVP 不實作 |
| `--master <name>.esm` | | 純標註用途（log / 來源追蹤），不影響幾何 |

### 批量模式（MVP 後，先佔位）

```
nif2gltf --manifest <manifest.json> --outdir <dir>
```

`manifest.json`：呼叫方已解析好的工作清單（**解析 FormID→nif 路徑是呼叫方的事**，轉換器不讀 ESM）：

```json
{
  "version": 1,
  "items": [
    { "in": "meshes/clutter/common/rock01.nif", "out": "rock01.gltf", "flat": true },
    { "in": "meshes/plants/treepineforest01.nif", "out": "treepineforest01.gltf", "flat": true }
  ]
}
```

> **為什麼批量靠 manifest 而非 glob**：FormID → MODL nif 路徑要讀 ESM（STAT/TREE 的 `MODL`），那是 ModForge / `gamedata` 的職責，不該塞進轉換器。轉換器保持 dumb：清單進、glTF 出。Godot палette 端再把 `base ref ↔ glTF 檔` 對回去（消費者關切，不在本契約）。

## 輸出保證

- 成功 → `--out` 路徑存在且為合法 glTF 2.0（`.gltf` + 旁邊 `.bin`，或自包含 `.glb`——**待定：MVP 先 `.gltf`+`.bin`**）。
- 幾何：座標軸對 Godot（Y-up、公尺）；**法線約定** Skyrim DirectX(Y−) → glTF/Godot OpenGL(Y+) 需 **Flip Y**（見 README「紋理 round-trip」）。
- `--flat`：單一 `StandardMaterial`，無貼圖引用。

## Exit code

| code | 意義 |
|---|---|
| 0 | 成功，`--out` 已寫 |
| 1 | 一般錯誤（args 缺、來源讀不到、後端失敗） |
| 2 | 來源 NIF 解析失敗（格式不認得 / 壓縮 / 版本不支援） |
| 3 | 含 skin/動畫，MVP 靜態後端拒絕（呼叫方應改走 Windows/PyNifly 後端） |

## 環境（不進 repo，照 voicegen 慣例）

- `MODFORGE_NIF2GLTF_BIN` — wrapper 路徑（呼叫方 export）。
- **參考 wrapper**：一行殼呼 `python -m nif2gltf "$@"`（在本 repo 的 `.venv` 內）。venv / 後端工具是內政，gitignore 留本機。
- ✅ **後端已自寫**（取代原「待證 NifSkope」）：`nif2gltf` 純 Python 靜態 NIF mesh parser，照本契約輸出 `.gltf`+`.bin`，不需任何外部 NIF 工具。MVP 後的紋理/蒙皮/正向才可能再掛 PyNifly 等 Windows 後端。

## 反向命令：glTF → NIF（`gltf2nif`，2026-07-05）

nif→glTF 的鏡像方向，供 [darksouls-port](../ModForge/sub_projs/darksouls-port/plan.md) 的資產移植管線消費。**dumb 工具**：一個 glTF → 一個 `.nif`，不認呼叫方、不讀 ESM。參考後端＝本 repo 的 `gltf2nif` Python 模組（欄位表與選值見 [gltf2nif/README.md](gltf2nif/README.md)）。

```
gltf2nif <in.gltf> <out.nif> [--texprefix <textures\prefix>] [--collision <hulls.json>] [--root-name <name>]
```

| 旗標 | 必填 | 語意 |
|---|---|---|
| `in.gltf` | ✅ | 來源 glTF/glb（靜態三角 mesh；一 primitive→一 `BSTriShape`） |
| `out.nif` | ✅ | 目標 SSE `.nif`（20.2.0.7 / user 12 / BSVersion 100） |
| `--texprefix` | | 貼圖路徑前綴（預設 `textures\dsport`）；material 基名 → slot0 `<prefix>\<基名>.dds`、slot1 `<基名>_n.dds`（探測到才填） |
| `--collision` | | hulls JSON（公尺 / DS Y-up）→ `bhkConvexVerticesShape` 串（不乘 70；STATIC/STONE/MOTION_FIXED） |
| `--root-name` | | 根 `NiNode` 名 |

**座標約定**：glTF Y-up 公尺 → Skyrim Z-up units，`(x,y,z)→(x,−z,y)×70.03`（幾何）；碰撞 hull 只軸變換不乘尺度（bhk 內部＝Havok 公尺）。

**Exit code**：`0` 成功／`1` 一般錯誤（args、寫檔、碰撞解析）／`2` glTF 解析失敗。

**驗證保證**：輸出可被本 repo 的 `nif2gltf` parser 讀回，三角形/頂點座標（誤差容忍內）/UV/貼圖路徑與輸入一致；每個位元組佈局對過真實 vanilla SSE nif。契約 backend-agnostic。

## 與 ModForge `package` 的關係

nif→glTF 是**反向**（純預覽代理）；glTF→nif（`gltf2nif`，上節）是**移植方向**，產出的 `.nif`+`.dds` 進 ModForge spec 的 `assets/`，由 `package`（`StaticSpec.Model`）打包。正向（一般外部→nif）決策真相在 [model-porting/](../ModForge/workflows/idea/asset-pipelines/model-porting/README.md)。
