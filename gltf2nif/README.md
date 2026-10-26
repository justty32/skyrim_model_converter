# gltf2nif — glTF 靜態 mesh → Skyrim SSE `.nif` 寫出器

← [model-converter README](../README.md)｜[PROTOCOL](../PROTOCOL.md)｜正向對照鏡子：[`nif2gltf/`](../nif2gltf/)（模組職責表見 [README §實作](../README.md)）

model-converter 的**反向後端**：把 glTF 2.0 靜態 mesh 寫成 Skyrim Special Edition 的 `.nif`（`BSTriShape` 幾何 + `BSLightingShaderProperty` 材質 + 選配 `bhk` 碰撞）。直接服務 [darksouls-port](../../darksouls-port/plan.md) 的 `FLVER→glTF→NIF` 管線。

**第一道驗證＝round-trip**：寫出的 `.nif` 丟回 `nif2gltf` parser 讀，三角形/頂點/UV/貼圖路徑要對得回輸入 glTF。這可驗可讀性，不能單獨排除 writer 與測試共享錯誤 offset；靜態 lighting/collision 佈局另對過真實 vanilla SSE NIF，格式特定欄位以固定版本 NifTools schema 與獨立 bytes 解碼核對。effect prefix 的格式證據見 [EFFECT-SHADER-CTD.md](../EFFECT-SHADER-CTD.md)。

## 用法

```
python -m gltf2nif <in.gltf> <out.nif> [--texprefix textures\dsport\m18] [--collision hulls.json] [--materials materials.json] [--root-name "Scene Root"]
```

| 旗標 | 必填 | 語意 |
|---|---|---|
| `in.gltf` | ✅ | 來源 glTF/glb（靜態三角 mesh；一 primitive → 一 shape） |
| `out.nif` | ✅ | 目標 `.nif` |
| `--texprefix` | | 貼圖路徑前綴（預設 `textures\dsport`）。material 基名接在後面組成 slot 路徑 |
| `--collision` | | hulls JSON → `bhkConvexVerticesShape` 碰撞（見下） |
| `--materials` | | version 1 的 named-mesh JSON；逐 mesh 覆寫 diffuse／normal、alpha、雙面／effect，並可依 `group` 合併或以 `skip` 移除 primitive |
| `--root-name` | | 根 `NiNode` 名（預設 `Scene Root`） |

Exit code：`0` 成功／`1` 一般錯誤／`2` glTF 解析失敗。

依賴與 `nif2gltf` 同（`pygltflib` / `numpy`），**不加新依賴**（凸包半空間自寫暴力法，不引 scipy）。

## 座標與單位約定

見 [座標、幾何與材質格式](format.md)。

## 幾何：`BSTriShape` / `BSVertexData`

見 [座標、幾何與材質格式](format.md)。

## 材質：`BSLightingShaderProperty` + `BSShaderTextureSet`

見 [座標、幾何與材質格式](format.md)。

## 碰撞（`--collision`）

hulls JSON 格式（座標＝**公尺、DS 原生 Y-up**，與 glTF 同系）：

```json
{"hulls": [ {"vertices": [[x,y,z], ...]}, {"vertices": [...]} ]}
```

生出：根 `NiNode` 上 `bhkCollisionObject → bhkRigidBody → bhkListShape → bhkConvexVerticesShape × N`。

- **尺度**：bhk 世界內部用 Havok 公尺（Skyrim units ÷ ~70），DS 本來就公尺 → hull 頂點**只做 Y-up→Z-up 軸變換、不乘 70**（渲染 mesh 才乘）。
- 每個 `bhkConvexVerticesShape`：`vertices`（Vector4，w=0）+ `normals`（Vector4，`(nx,ny,nz, d)`，`d = −n·v`，即凸包各面的半空間平面）。半空間由自寫暴力凸包法算（對每組頂點三元組取面、驗所有點同側、去重）；共面/零體積的 hull 會被判退。
- `bhkRigidBody`（`bhkRigidBodyCInfo2010`，250 bytes，逐欄對過 vanilla）靜態常規值：

  | 欄位 | 值（enum 來自 nif.xml） |
  |---|---|
  | Havok Filter Layer | `SKYL_STATIC` = 1 |
  | Material（list & convex shape） | `SKY_HAV_MAT_STONE` = 3741512247 |
  | Motion System | `MO_SYS_FIXED` = 7 |
  | Quality Type | `MO_QUAL_INVALID` = 0 |
  | Deactivator / Solver Deactivation | `DEACTIVATOR_NEVER`=1 / `SOLVER_DEACTIVATION_OFF`=1 |
  | Collision Response | `RESPONSE_SIMPLE_CONTACT` = 1 |
  | Broad Phase Type | `BROAD_PHASE_ENTITY` = 1 |
  | Mass | 0（immovable） |
  | Linear/Angular Damping | 0.1 / 0.05 |
  | Friction / Restitution | 0.5 / 0.4 |
  | Max Lin/Ang Velocity | 104.4 / 31.57 |
  | Penetration Depth | 0.15 |
  | Convex Radius（shell） | 0.05 |
  | `bhkWorldObjCInfoProperty` | Data=0, Size=0, CapacityAndFlags=`0x80000000` |
  | `bhkCollisionObject` Flags | `0x0081`（SYNC_ON_UPDATE） |

## 已知限制

- `_s`（DSR specular）貼圖忽略。
- 蒙皮/動畫 mesh 不支援（本後端只做靜態；上游應保證無 skin）。
- 單 shape 的頂點數與三角形數各 ≤ 65535（SSE 16-bit 限制）。
- 凸包半空間為 O(V⁴) 暴力法，適用 V-HACD 級的小 hull（數十頂點）；共面 hull 會被退。
- shader flags / 材質數值是 vanilla 靜態通用預設，非逐 material 調校（正確性以「不透明+normal map 靜態」為準）。

## 測試

`.venv/bin/python -m pytest -q`。主驗證＝writer→`nif2gltf` round-trip；另含座標釘點、凸包面數、CLI 契約、m0046 實件。
