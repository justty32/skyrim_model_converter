# gltf2nif — glTF 靜態 mesh → Skyrim SSE `.nif` 寫出器

← [model-converter README](../README.md)｜[PROTOCOL](../PROTOCOL.md)｜正向對照鏡子：[`nif2gltf/`](../nif2gltf/README.md)（本模組的欄位佈局以它的 parser 為權威）

model-converter 的**反向後端**：把 glTF 2.0 靜態 mesh 寫成 Skyrim Special Edition 的 `.nif`（`BSTriShape` 幾何 + `BSLightingShaderProperty` 材質 + 選配 `bhk` 碰撞）。直接服務 [darksouls-port](../../darksouls-port/plan.md) 的 `FLVER→glTF→NIF` 管線。

**第一道驗證＝round-trip**：寫出的 `.nif` 丟回 `nif2gltf` parser 讀，三角形/頂點/UV/貼圖路徑要對得回輸入 glTF。寫出器的每個位元組佈局都對過真實 vanilla SSE nif（一顆帶 `BSTriShape`+`BSLightingShaderProperty`+`bhkRigidBody` 的市售 mesh）與 `nif2gltf` reader。

## 用法

```
python -m gltf2nif <in.gltf> <out.nif> [--texprefix textures\dsport\m18] [--collision hulls.json] [--root-name "Scene Root"]
```

| 旗標 | 必填 | 語意 |
|---|---|---|
| `in.gltf` | ✅ | 來源 glTF/glb（靜態三角 mesh；一 primitive → 一 shape） |
| `out.nif` | ✅ | 目標 `.nif` |
| `--texprefix` | | 貼圖路徑前綴（預設 `textures\dsport`）。material 基名接在後面組成 slot 路徑 |
| `--collision` | | hulls JSON → `bhkConvexVerticesShape` 碰撞（見下） |
| `--root-name` | | 根 `NiNode` 名（預設 `Scene Root`） |

Exit code：`0` 成功／`1` 一般錯誤／`2` glTF 解析失敗。

依賴與 `nif2gltf` 同（`pygltflib` / `numpy`），**不加新依賴**（凸包半空間自寫暴力法，不引 scipy）。

## 座標與單位約定

glTF 是 **Y-up、公尺**；Skyrim 是 **Z-up、units（1 公尺 ≈ 70.03 units）**。這是 `nif2gltf` 正向轉換的逆：

```
glTF (x, y, z)  →  Skyrim (x, −z, y) × 70.03    # 幾何頂點（render mesh）
glTF (x, y, z)  →  Skyrim (x, −z, y)            # normal / 平面（方向，不乘尺度）
```

**Round-trip 語意**：`nif2gltf` 讀回時**不會把 ×70.03 除掉**，所以「寫出→讀回」會拿到 `70.03 × 原值`——軸向精確、只差一個已知的均勻尺度（測試就釘這個）。UV 走半精度（vanilla `BSVertexData` 就是 half2），round-trip 誤差 ~2e-3。

## 幾何：`BSTriShape` / `BSVertexData`

- 一 glTF primitive → 一 `BSTriShape`，全部掛在根 `NiNode` 下（shape 本身 transform 單位化，座標烘進頂點）。
- 頂點格式＝**vanilla 靜態的 full-precision 佈局**（stride 28）：

  | offset | 欄位 | 型別 |
  |---|---|---|
  | +0 | Vertex | float3（全精度位置） |
  | +12 | Bitangent X | float |
  | +16 | UV | half2 |
  | +20 | Normal (3) + Bitangent Y (1) | byte×4 |
  | +24 | Tangent (3) + Bitangent Z (1) | byte×4 |

  `BSVertexDesc` = `0x0001b000_00650407`（VertexDataSize=7、UV off=4、Normal off=5、Tangent off=6、attributes `VF_VERTEX|VF_UV|VF_NORMALS|VF_TANGENTS`=0x1B）。**刻意不設 `VF_FULLPREC`（0x400）旗標**——真實 vanilla 靜態也不設，靠 UV offset≥12 自描述判 float3（`nif2gltf` 就是這樣推的），這樣位元組與 vanilla 一致。
- `Data Size` = `stride × numVerts + numTris × 6`（含頂點與三角資料，對過 vanilla）。
- normals：glTF 有就帶、沒有就算面法線（area-weighted）。tangent frame 用 Lengyel 法從 UV 現算（掛了 normal map 需要切線基）。
- **限制**：SSE `BSTriShape` 的頂點/三角數是 16-bit，單一 shape 上限 65535 頂點；超過會報錯（請在上游切 mesh）。

## 材質：`BSLightingShaderProperty` + `BSShaderTextureSet`

貼圖路徑規則（material name = extractor 記的貼圖基名，副檔名 `.tga/.dds/.png` 會被剝掉）：

- slot0 diffuse = `<texprefix>\<基名>.dds`
- slot1 normal = `<texprefix>\<基名>_n.dds`——**僅當 glTF 同目錄存在對應 `_n.dds` 才填**（否則留空）。
- 其餘 slot 留空。DSR 的 `_s` spec map **已知限制：先忽略**（Skyrim specular 走 model-space 慣例不同，之後再處理）。

`BSLightingShaderProperty`（Default type，100 bytes）欄位選值表（值取自真實市售 SSE 不透明+normal map 靜態 mesh；拿不準者選最保守常見值）：

| offset | 欄位 | 值 | 備註 |
|---|---|---|---|
| +0 | Name | 0 | 無名 |
| +4 / +8 | (NiObjectNET 保留字) | `0xFFFFFFFF` / `0` | 逐位元組照抄 vanilla（一個 -1 ref + 一個 0，靜態不需其語意） |
| +12 | Controller | -1 | 無 |
| +16 | Shader Flags 1 | `0x82408009` | vanilla 靜態常見值 |
| +20 | Shader Flags 2 | `0x00008021` | 同上 |
| +24/+32 | UV Offset / Scale | (0,0) / (1,1) | |
| +40 | **Texture Set** | → texset block | `nif2gltf` 就從這個固定 offset 讀回 |
| +44/+56 | Emissive Color / Multiple | (0,0,0) / 1.0 | |
| +60 | Texture Clamp Mode | 3 | WRAP_S_WRAP_T |
| +64/+68 | Alpha / Refraction | 1.0 / 0 | |
| +72 | Glossiness | 80 | vanilla 建築靜態預設 |
| +76/+88 | Specular Color / Strength | (1,1,1) / 1.0 | |
| +92/+96 | Lighting Effect 1 / 2 | 0.3 / 2.0 | |

`BSShaderTextureSet`＝`uint count(=9)` + 9 條 sized-string（slot0 diffuse、slot1 normal，其餘空）。

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
- 單 shape ≤ 65535 頂點（SSE 16-bit 限制）。
- 凸包半空間為 O(V⁴) 暴力法，適用 V-HACD 級的小 hull（數十頂點）；共面 hull 會被退。
- shader flags / 材質數值是 vanilla 靜態通用預設，非逐 material 調校（正確性以「不透明+normal map 靜態」為準）。

## 測試

`.venv/bin/python -m pytest`（gltf2nif 28 綠：geometry 8、reader 4、writer 12、CLI 5，外加現有 nif2gltf 24）。主驗證＝writer→`nif2gltf` round-trip；另含座標釘點、凸包面數、CLI 契約、m0046 實件。
