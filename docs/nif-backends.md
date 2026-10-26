# NIF 雙向後端

← [架構與後端導覽](README.md)

## 實作（`nif2gltf/` — 自寫載體，2026-06-17 離線）

**自寫靜態 NIF mesh parser**，Linux/Win 原生跑，不依賴外部 NIF 工具，也不以「NifSkope fork 有沒有 CLI」為前提。

<!-- wf-nav -->
| 模組 | 職責 |
|---|---|
| `nif2gltf/nif_reader.py` | 手寫 Skyrim NIF（20.2.0.7 / user 12）靜態 mesh 解析的**入口與裝配**：標頭解析、NiNode 樹 transform 組合、Skyrim(Z-up)→glTF(Y-up)、含 skin/動畫→拒（exit 3）、`read_nif` 主流程。**Block Size 給每塊邊界 offset，單塊解析漂移不會骨牌**。公開 API（`read_nif`/`NifError`/`SkinnedNifError`）由此 re-export。 |
| `nif2gltf/_binreader.py` | 二進位讀取原語：`NifError`/`SkinnedNifError` 例外 + `_Reader`（byte cursor、scalar/vec/mat 解碼）。 |
| `nif2gltf/_blocks.py` | 各 block 型別解碼：**LE** NiTriShape/NiTriStrips→NiTriShapeData/NiTriStripsData（全 float）；**SSE** BSTriShape/BSDynamic/BSSubIndex（BSVertexData，走 BSVertexDesc offset 表 + Full_Precision 旗標自描述解碼）；NiNode、反三角條（de-strip）、半精度解碼。 |
| `nif2gltf/gltf_writer.py` | `Mesh` IR → glTF 2.0（`.gltf`+`.bin`，pygltflib）。POSITION/NORMAL/TEXCOORD_0 + 單一平色 material（`--flat`）。 |
| `nif2gltf/geometry.py` | `Mesh` 中介表示 + Skyrim→glTF 軸轉。 |
| `nif2gltf/cli.py` | 照 [PROTOCOL.md](../PROTOCOL.md)：`--in/--out/--flat`，batch `--manifest/--outdir`，exit 0/1/2/3。 |

**格式來源**：niftools/nifxml `nif.xml`（逐欄查證，非憑記憶；reference 檔 gitignore）。
**跑**：`python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`，然後 `python -m nif2gltf --in foo.nif --out foo.gltf --flat`。
**測**：Linux 用 `.venv/bin/python -m pytest -q`；Windows 用 `.venv/Scripts/python -m pytest`；WSL 獨立環境用 `.venv-wsl/bin/python -m pytest -q`。
跨 repo live consumer 測試在同層
`../godot-worldspace-editor/tests/test_model_fetch_contract.py`：production CLI 的 synthetic
NIF `.gltf + .bin` 會由 Godot 4.6 production `ModelFetch._load_gltf()` 真正載入，並驗
mesh/primitive/vertices/軸向尺度與 fail-closed；詳 [PROTOCOL](../PROTOCOL.md#live-consumer-contract)。
**驗證範圍**：合成 fixture 驗解析與寫出契約；SSE 真實石頭／松樹已有 [Godot GUI 歷史驗收](../../ModForge/wait_todo/worldspace-editor.md)，LE 真檔與正向整包實機仍待驗。

## 實作（`gltf2nif/` — 反向後端，2026-07-05）

glTF 靜態 mesh → SSE `.nif`：見 **[gltf2nif/README.md](../gltf2nif/README.md)**。鏡射 `nif2gltf` 的結構，以 round-trip 驗證幾何與可讀性；但 writer/reader 可能共享錯誤假設，格式欄位另以固定版本 NifTools schema、獨立 bytes 解碼與可得的真實 NIF 取樣核對。

```
python -m gltf2nif <in.gltf> <out.nif> [--texprefix textures\dsport\m18] [--collision hulls.json]
```

- **幾何** `BSTriShape`（full-precision 佈局 stride 28，座標 glTF Y-up 公尺 → Skyrim Z-up ×70.03）
- `gltf2nif/tangents.py` 驗證並保留來源切線方向與 handedness；`read_gltf(..., preserve_tangents=True)` 供 any2nif 啟用，同檔 `prepare_tangent_frames` 將生成切線的鏡射接縫拆開並固定正反手性，先生成再切分大型模型；裸 gltf2nif 預設仍使用舊算法。`tests/test_package_normal_frames.py` 讀實際 NIF T/B/N 與 DDS 驗光影方向，`tests/test_any2nif_tangents.py` 驗壞資料保護、legacy byte 契約及大型切分；`tests/test_generated_tangent_frames.py`／`test_generated_tangent_split.py` 驗無來源切線時的鏡射 UV、atlas 與切分；`tests/test_tangent_seams.py` 驗共用頂點的鏡射接縫與實際 normal DDS 方向。
- **材質** `BSLightingShaderProperty`+`BSShaderTextureSet`（material 基名 → `<texprefix>\<基名>.dds` + 探測到的 `_n` normal map）；`--materials` 可用 named-mesh JSON 精確覆寫貼圖／alpha／雙面／effect，並合併或跳過 primitive。effect 序列化 CTD 的格式根因與離線驗證見 [EFFECT-SHADER-CTD.md](../EFFECT-SHADER-CTD.md)。
- **碰撞** `--collision` hulls JSON → `bhkCollisionObject→bhkRigidBody→bhkListShape→bhkConvexVerticesShape`（Havok 公尺、不乘 70；STATIC/STONE/MOTION_FIXED）
- 服務 [darksouls-port](../../darksouls-port/plan.md) 的 `FLVER→glTF→NIF` 管線。
