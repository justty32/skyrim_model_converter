# Model Converter — 模型格式互轉工具

← CLI 契約草案：[PROTOCOL.md](PROTOCOL.md)

> **獨立 repo**（2026-08-02 自 ModForge `sub_projs/model-converter` 抽出，未帶舊 commit 歷史）。文中 `../ModForge/…`、`../godot-worldspace-editor/…` 這類連結，前提是各 repo **同層 clone 在同一個父目錄下**（本機為 `~/repo/moddings/skyrim/projects/`）。

**一句話**：以 Skyrim **`.nif`（含 `.dds` 紋理）** 為中心，做與 **Godot 可用格式（glTF）** 及 **各種常用模型格式（FBX / OBJ / glTF）** 的**雙向**互轉工具；正向路已由 `any2nif` + `tex2dds` 實作。定位是 ModForge 生態的**基石工具**——不整合進 ModForge，靠協議/CLI 被消費。

**為什麼開這個工具**：兩個消費需求撞在一起，且現有家底都是半套——
- **worldspace 物件編輯**（[godot-worldspace-editor](../godot-worldspace-editor/README.md)）要把 vanilla `.nif` → glTF 丟進 Godot 當視覺代理（**反向**，純預覽）。
- **model-porting**（[workflows/idea/asset-pipelines/model-porting](../ModForge/workflows/idea/asset-pipelines/model-porting/README.md)）已把**正向**（外部 FBX/OBJ/glTF → `.nif`）規劃得很深，但**只有正向**。
- 兩者其實是同一把工具的兩個方向。與其各做各的，不如收斂成一個轉換器，正向沿用 model-porting 的決策、反向補上。

---

## 關係定位（不重造輪子）

| 既有資產 | 方向 | 角色 |
|---|---|---|
| [model-porting/](../ModForge/workflows/idea/asset-pipelines/model-porting/README.md)（01–10） | **正向** 外部→nif | 正向 deep-dive 的真相在那裡；本工具正向部分**沿用**其工具決策（NifTools 靜態 / PyNifly 蒙皮 / Compressonator dds），不複製內容 |
| [gemini-research/.../nif-gltf-conversion.md](../ModForge/sub_projs/gemini-research/worldspace-editor/nif-gltf-conversion.md) | **反向** nif→glTF | 反向工具調查的**唯一**現有來源（已帶幻覺更正 banner）；結論濃縮進下方工具表，原稿留存 |
| [godot-worldspace-editor](../godot-worldspace-editor/README.md) | 消費者 | nif→glTF 代理是它物件編輯的前置依賴 |
| ModForge `package`（`StaticSpec.Model`） | 消費者 | 正向產出的 `.nif`+`.dds` 由它打包進 Meshes/Textures 樹 |

---

## 外部工具與格式調查

MVP 已改自寫純 Python 後端（見下「實作」節），不再依賴外部 NIF 工具。外部工具盤點（NifSkope fork / PyNifly / Outfit Studio / ck-cmd / Compressonator + 幻覺黑名單）、跨平台 dual-backend 架構難點、紋理 round-trip 踩坑 → 全留 [tool-survey.md](tool-survey.md) 作 **MVP 後**選型參考。

---

## Scope

**MVP（已鎖，2026-06-17）：vanilla `.nif` → glTF 批量轉換**——靜態 mesh、Linux 原生、**跳紋理用平色 proxy**，輸出餵 [worldspace editor](../godot-worldspace-editor/README.md) 當物件代理。
一鎚解三件事：① 填上「無已驗證批量 nif→glTF」缺口；② 讓 worldspace 物件編輯能往前走；③ 避開 PyNifly 的 Windows 鎖（靜態走 NifSkope fo76utils fork 即可，Linux 原生）。

**完整目標（MVP 後）**：`.nif`+紋理 ↔ glTF/Godot ↔ FBX/OBJ 全矩陣雙向，含紋理重映射與蒙皮（蒙皮走 Windows/PyNifly 後端）。

## 實作（`nif2gltf/` — 自寫載體，2026-06-17 離線）

原本 MVP 第一關卡在「NifSkope fork 有沒有 CLI」。改走 README 自留的後路——**自寫靜態 NIF mesh parser**，因此不再依賴任何外部 NIF 工具，Linux/Win 原生跑。

| 模組 | 職責 |
|---|---|
| `nif2gltf/nif_reader.py` | 手寫 Skyrim NIF（20.2.0.7 / user 12）靜態 mesh 解析的**入口與裝配**：標頭解析、NiNode 樹 transform 組合、Skyrim(Z-up)→glTF(Y-up)、含 skin/動畫→拒（exit 3）、`read_nif` 主流程。**Block Size 給每塊邊界 offset，單塊解析漂移不會骨牌**。公開 API（`read_nif`/`NifError`/`SkinnedNifError`）由此 re-export。 |
| `nif2gltf/_binreader.py` | 二進位讀取原語：`NifError`/`SkinnedNifError` 例外 + `_Reader`（byte cursor、scalar/vec/mat 解碼）。 |
| `nif2gltf/_blocks.py` | 各 block 型別解碼：**LE** NiTriShape/NiTriStrips→NiTriShapeData/NiTriStripsData（全 float）；**SSE** BSTriShape/BSDynamic/BSSubIndex（BSVertexData，走 BSVertexDesc offset 表 + Full_Precision 旗標自描述解碼）；NiNode、反三角條（de-strip）、半精度解碼。 |
| `nif2gltf/gltf_writer.py` | `Mesh` IR → glTF 2.0（`.gltf`+`.bin`，pygltflib）。POSITION/NORMAL/TEXCOORD_0 + 單一平色 material（`--flat`）。 |
| `nif2gltf/geometry.py` | `Mesh` 中介表示 + Skyrim→glTF 軸轉。 |
| `nif2gltf/cli.py` | 照 [PROTOCOL.md](PROTOCOL.md)：`--in/--out/--flat`，batch `--manifest/--outdir`，exit 0/1/2/3。 |

**格式來源**：niftools/nifxml `nif.xml`（逐欄查證，非憑記憶；reference 檔 gitignore）。
**跑**：`python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`，然後 `python -m nif2gltf --in foo.nif --out foo.gltf --flat`。
**測**：`.venv/Scripts/python -m pytest`（**202 passed**，2026-09-05 實跑）。
跨 repo live consumer 測試在同層
`../godot-worldspace-editor/tests/test_model_fetch_contract.py`：production CLI 的 synthetic
NIF `.gltf + .bin` 會由 Godot 4.6 production `ModelFetch._load_gltf()` 真正載入，並驗
mesh/primitive/vertices/軸向尺度與 fail-closed；詳 [PROTOCOL](PROTOCOL.md#live-consumer-contract)。
⚠️ **離線限制**：合成 fixture 只證「reader 讀回它照 nif.xml 編的東西」，**未對真實 vanilla `.nif` 逐 byte 驗**（離線無遊戲素材）——SSE offset 解碼尤其需真檔確認，列 WAIT_USER。

## 實作（`gltf2nif/` — 反向後端，2026-07-05）

反向缺口（glTF 靜態 mesh → SSE `.nif`）已補：見 **[gltf2nif/README.md](gltf2nif/README.md)**。鏡射 `nif2gltf` 的結構，以它的 parser 為佈局權威（寫出→讀回 round-trip 是主驗證），並對真實 vanilla SSE nif 逐位元組核過欄位。

```
python -m gltf2nif <in.gltf> <out.nif> [--texprefix textures\dsport\m18] [--collision hulls.json]
```

- **幾何** `BSTriShape`（full-precision 佈局 stride 28，座標 glTF Y-up 公尺 → Skyrim Z-up ×70.03）
- **材質** `BSLightingShaderProperty`+`BSShaderTextureSet`（material 基名 → `<texprefix>\<基名>.dds` + 探測到的 `_n` normal map）
- **碰撞** `--collision` hulls JSON → `bhkCollisionObject→bhkRigidBody→bhkListShape→bhkConvexVerticesShape`（Havok 公尺、不乘 70；STATIC/STONE/MOTION_FIXED）
- 服務 [darksouls-port](../ModForge/sub_projs/darksouls-port/plan.md) 的 `FLVER→glTF→NIF` 管線；m0046B1A18 實件已跑（5 shape / 1684 tri / 64 KB，round-trip 位置誤差 ~1.7e-6 m）。

## 實作（`any2nif/` — 正向入口）

所有來源先正規化成 glTF/GLB，再交給既有 `gltf2nif` 寫 SSE `.nif`；完整 CLI 契約見 [PROTOCOL.md](PROTOCOL.md#正向入口any2nif)。

| 模組 | 職責 |
|---|---|
| `any2nif/cli.py` | CLI 編排：單位／軸向、貼圖、碰撞、PBR 材質與 NIF 寫出；exit 0/1/2/3。 |
| `any2nif/normalize.py` | 依副檔名分派：glTF/GLB 直通，OBJ/STL/PLY/DAE/ZAE/OFF/DXF/XYZ 走 trimesh，FBX 走 FBX2glTF。 |
| `any2nif/trimesh_backend.py`、`fbx_backend.py` | 將非 glTF 來源正規化成 GLB，保留可用的 mesh／material 資訊。 |
| `any2nif/transform.py` | 將來源單位換算成公尺，並把來源 Z-up 正規化成 glTF Y-up。 |
| `any2nif/textures.py` | glTF 圖像轉 Skyrim diffuse／normal／specular `.dds` 槽位。 |

## 實作（`tex2dds/` — 貼圖編碼）

純 Python 將 Pillow 可解碼影像寫成 Skyrim 可用 BC1/BC3 DDS；完整 CLI 契約見 [PROTOCOL.md](PROTOCOL.md#貼圖編碼tex2dds)。

| 模組 | 職責 |
|---|---|
| `tex2dds/cli.py` | 單檔轉換 CLI 與 exit 0/1/2。 |
| `tex2dds/pipeline.py` | 解碼、pow2 resize、normal green flip、mipmap chain 與格式選擇。 |
| `tex2dds/bcn.py` | 純 Python BC1／BC3 block encoder。 |
| `tex2dds/dds.py` | 寫入 DXT1／DXT5 DDS header 與 mip payload。 |

## Open

- **反向產出實機驗證**（**待主力機**）：`gltf2nif` 輸出的 `.nif`（含碰撞）進遊戲測試 cell，確認看得到、站得上去。離線 round-trip + 對 vanilla byte 核已過，剩實機 acceptance。
- **對真實 vanilla `.nif` 驗證載體**（MVP 收尾，**待主力機**）：跑 `nif2gltf` 轉真實 vanilla mesh（LE 與 SSE 各取樣），確認 glTF 進 Godot/Blender 形狀對；SSE 半精度 offset 解碼是最需驗的點。見 WAIT_USER。
- ~~**批量 nif→glTF 的可行載體**~~ ✅ 自寫 `nif2gltf`（上節），不再卡 NifSkope。
- ~~**協議形狀**~~ ✅ 草案 2026-06-17 [PROTOCOL.md](PROTOCOL.md)：掛勾 `MODFORGE_NIF2GLTF_BIN`（黑盒 exec）、單檔 `--in/--out/--flat`、批量 `manifest.json`、exit code。**參考後端＝本 repo 的 `nif2gltf`**（wrapper 呼 `python -m nif2gltf`）；契約 backend-agnostic，要換後端不動契約。
- ~~**與 model-porting 的邊界**~~ ✅ 選型細節留在 model-porting／[AUDIT.md](AUDIT.md)，本 repo 收斂可執行的正反向工具與 CLI 契約。
- **`.3ds`**：trimesh 不支援；assimp 需要 sudo 安裝，本輪無法採用。
- **蒙皮／骨架**：`gltf2nif` 明確拒絕帶 skin／動畫的輸入，現階段只支援靜態 mesh。
- **BC7**：未實作；目前 `tex2dds` 的 BC1／BC3 已足以覆蓋本輪需求。
