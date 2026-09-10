# model-converter ↔ 消費者 協議（CLI 契約草案）

← [README](README.md)

**狀態（2026-09-10）**：已實作 `nif2gltf`、`gltf2nif`、`any2nif` 與 `tex2dds`。本檔定義呼叫方看到的 CLI 契約；後端留在獨立 repo。真實模型與遊戲碰撞驗收、反向貼圖、蒙皮等未完項目見 [README 的 Open](README.md#open)。

## 定位（照 [skyrim-voicegen](../skyrim-voicegen/README.md) 的掛法）

model-converter 是**黑盒 exec**，不整合進 ModForge / Godot editor。掛勾＝環境變數 **`MODFORGE_NIF2GLTF_BIN`**，指向一支 wrapper（在自己的 venv / 環境內跑選定後端）。呼叫方只給 args、只收 glTF；轉換器只看 args、不認得呼叫方。**互不整合。**

Godot worldspace editor 的 production `ModelFetch` 已直接遵守這個掛勾：env hook
存在時 executable 直接收到下述 protocol args；未設定時才採可預期的同層 checkout
fallback（`model-converter/.venv/.../python -m nif2gltf`）。明示但不存在的 hook 是設定錯誤，
不得靜默換 backend。

```
driver  ──(--in foo.nif --out foo.gltf [--flat])──►  nif2gltf  ──► writes foo.gltf (+ .bin/貼圖)
```

`driver` 可以是 Godot worldspace editor 的前置腳本、ModForge、或人工。這個掛勾的職責是一個 `.nif` → 一個 `.gltf`；正向模型與貼圖轉換另由下方 `gltf2nif`、`any2nif`、`tex2dds` 契約承接。

## MVP 範圍（鎖定，對齊 README）

靜態 mesh、Linux 原生、**`--flat` 跳紋理用平色**，輸出餵 [worldspace editor](../godot-worldspace-editor/README.md) 當物件代理（目前是彩色方塊 placeholder，換成真實 glTF）。蒙皮與紋理 round-trip 仍未完成；反向（glTF→nif）已由下方 `gltf2nif` 實作。

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
- consumer 必須把 `.gltf` 與同 stem `.bin` 視為同一次發布；nonzero 或任一缺漏皆失敗，
  不得沿用同 output key 的 stale/partial 檔。

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

## Live consumer contract

同層 `godot-worldspace-editor/tests/test_model_fetch_contract.py` 會用本 repo 的 synthetic
NIF fixture 經 production CLI 寫 `.gltf + .bin`，再交給 Godot 4.6 production
`ModelFetch._load_gltf()`。它驗 Node3D、mesh/primitive/vertices、非對稱軸向與尺度，並驗
missing bin、bad glTF、converter nonzero fail closed；整個 Godot project 在 temp copy 執行。

```powershell
$env:GODOT_BIN = 'C:\path\to\Godot_v4.6-stable_win64_console.exe'
python ..\godot-worldspace-editor\tests\test_model_fetch_contract.py
```

## 反向命令：glTF → NIF（`gltf2nif`，2026-07-05）

nif→glTF 的鏡像方向，供 [darksouls-port](../darksouls-port/plan.md) 的資產移植管線消費。**dumb 工具**：一個 glTF → 一個 `.nif`，不認呼叫方、不讀 ESM。參考後端＝本 repo 的 `gltf2nif` Python 模組（欄位表與選值見 [gltf2nif/README.md](gltf2nif/README.md)）。

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

## 正向入口：`any2nif`

```text
usage: any2nif [-h] [--textures-out DIR] [--texprefix TEXPREFIX] [--scale SCALE] [--unit UNIT] [--up-axis {y,z}] [--collision COLLISION] [--root-name ROOT_NAME] [--fbx2gltf FBX2GLTF] [--no-materials] [--keep-intermediate DIR] in_path out_path
```

| 旗標 | 必填 | 語意 |
|---|---|---|
| `in_path` | ✅ | 來源模型：`.dae/.dxf/.fbx/.glb/.gltf/.obj/.off/.ply/.stl/.xyz/.zae`。glTF/GLB 直通，其餘先正規化成 GLB。 |
| `out_path` | ✅ | 目標 SSE `.nif`。 |
| `--textures-out DIR` | | 將來源貼圖寫成 BC1/BC3 + mipmaps 的 `.dds` 到 DIR。 |
| `--texprefix TEXPREFIX` | | 寫入 NIF 的遊戲內貼圖路徑前綴；預設 `textures\any2nif`。 |
| `--scale SCALE` | | 額外套用的均勻座標縮放；與 `--unit` 相乘。 |
| `--unit UNIT` | | 來源單位；`m`（預設）、`cm`、`mm`、`in`、`ft`，先換算成公尺。 |
| `--up-axis {y,z}` | | 來源檔的 up axis；預設 `y`，`z` 會轉成 glTF Y-up。 |
| `--collision COLLISION` | | `none`（單檔預設無碰撞）、`box`（盒狀）、`convex`（外形凸包），或既有 hulls JSON 路徑；整包預設 `convex`。 |
| `--root-name ROOT_NAME` | | 根 `NiNode` 名；預設 `Scene Root`。 |
| `--fbx2gltf FBX2GLTF` | | FBX 輸入專用的 FBX2glTF binary 路徑。 |
| `--no-materials` | | 忽略來源 PBR 材質值，使用 `gltf2nif` 靜態預設值。 |
| `--keep-intermediate DIR` | | 將正規化的 glTF/GLB 留在 DIR，不使用即棄暫存目錄。 |

| exit code | 意義 |
|---|---|
| 0 | 成功，`out_path` 已寫。 |
| 1 | 一般錯誤（來源不存在、單位／相依／貼圖／碰撞／寫檔失敗）。 |
| 2 | 來源格式或 glTF 解析失敗；`argparse` 命令列用法錯誤亦回傳 2。 |
| 3 | 來源含 skin、morph 或動畫，靜態後端拒絕。 |

**輸出保證**：成功時產出 Skyrim SSE `.nif`；來源先統一為 glTF Y-up／公尺，再由 `gltf2nif` 轉成 Skyrim Z-up units。每個 glTF primitive 通常對應一個 `BSTriShape`；超過 65,535 頂點時自動分成多個 shape，保留原三角形與各頂點資料；指定 `--textures-out` 時另寫來源可解出的 diffuse／normal／specular／emissive DDS，指定 `--keep-intermediate` 時保留正規化結果。

### 自動盒狀碰撞

```bash
python -m any2nif crate.glb crate.nif --collision box
```

`box` 將所有 mesh 中三角形用到的頂點合併計算邊界，使用套完 node transform、`--unit`、`--scale` 與 `--up-axis` 的座標。產出一個盒狀 `bhkConvexVerticesShape`，碰撞頂點保持 Havok 公尺，僅旋轉到 Z-up，不套 render 的 70.03 倍。任一軸不足 5 公分時，向兩側等量補到 5 公分，避免平面物件沒有碰撞厚度。它會填滿模型內部空洞，適合箱子等簡單物件；若要沿模型外形包覆可用 `convex`：SciPy 計算一個外殼，不填盒子多出來的角，但仍會填凹洞。平面沿法線加厚 5 公分，點／線退化會拒絕；凹形自動拆分仍未完成。

JSON 路徑沿用原契約：內容已是 Y-up 公尺，**不再**套來源單位或軸向。檔名若剛好是 `box`、`convex` 或 `none`，加上 `./` 表明是路徑。自動模式遇到空模型或非有限座標會失敗（exit 1）。

`tests/test_any2nif_collision.py` 以真 CLI 產出 NIF，再讀取其中的碰撞頂點與 render mesh，檢查單位／軸向一致；遊戲中能否站立仍需實機驗收。

### 整包模式

`any2nif <source> <output-directory> --package [--asset-name NAME]` 將 `out_path` 解讀為完整 Data 目錄。模型／貼圖位置由同一個名稱計算；不接受另外的 `--textures-out`、`--texprefix`、`--keep-intermediate`。來源尺度／軸向與 collision JSON 保持前述契約。名稱會正規化成 ASCII 小寫，Windows 保留名稱拒絕。

`--package` 的材質處理、發布與 `model-converter-package/1` manifest 契約見 [PACKAGE.md](PACKAGE.md)；單檔模式不能使用 `--asset-name`。

## 貼圖編碼：`tex2dds`

```text
usage: tex2dds [-h] [--format {auto,bc1,bc3}] [--no-mipmaps] [--normal-map] [--resize {pow2,none}] in_path out_path
```

| 旗標 | 必填 | 語意 |
|---|---|---|
| `in_path` | ✅ | 來源影像：PNG/JPG/JPEG/TGA/BMP/DDS。 |
| `out_path` | ✅ | 目標 `.dds`。 |
| `--format {auto,bc1,bc3}` | | 預設 `auto`：有實際透明度選 BC3，否則選 BC1。 |
| `--no-mipmaps` | | 只寫 base level；預設產生直到 1×1 的完整 mip chain。 |
| `--normal-map` | | Skyrim `_n`：翻轉綠色通道到 DirectX 慣例、保留 alpha 作 glossiness，並強制 BC3。 |
| `--resize {pow2,none}` | | 預設 `pow2`，縮放到最近的 2 次方；`none` 保留來源尺寸。 |

| exit code | 意義 |
|---|---|
| 0 | 成功，`out_path` 已寫。 |
| 1 | 一般錯誤（命令列用法、來源不存在、選項或寫檔失敗）。 |
| 2 | Pillow 無法解析來源影像。 |

**輸出保證**：成功時產出 FourCC `DXT1`（BC1）或 `DXT5`（BC3）的 DDS；預設為 pow2 尺寸與完整 mip chain。小於 4×4 的 block 以邊緣像素補齊，DDS header 仍保留該 mip 的真實尺寸。

## 與 ModForge `package` 的關係

nif→glTF 是**反向**（純預覽代理）；glTF→nif（`gltf2nif`，上節）是**移植方向**，產出的 `.nif`+`.dds` 進 ModForge spec 的 `assets/`，由 `package`（`StaticSpec.Model`）打包。正向（一般外部→nif）決策真相在 [model-porting/](../ModForge/workflows/idea/asset-pipelines/model-porting/README.md)。
