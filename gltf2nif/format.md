# gltf2nif 座標、幾何與材質格式

← [gltf2nif 使用入口](README.md)

## 座標與單位約定

glTF 是 **Y-up、公尺**；Skyrim 是 **Z-up、units（1 公尺 ≈ 70.03 units）**。這是 `nif2gltf` 正向轉換的逆：

```
glTF (x, y, z)  →  Skyrim (x, −z, y) × 70.03    # 幾何頂點（render mesh）
glTF (x, y, z)  →  Skyrim (x, −z, y)            # normal / 平面（方向，不乘尺度）
```

**Round-trip 語意**：`nif2gltf` 讀回時**不會把 ×70.03 除掉**，所以「寫出→讀回」會拿到 `70.03 × 原值`——軸向精確、只差一個已知的均勻尺度（測試就釘這個）。UV 走半精度（vanilla `BSVertexData` 就是 half2），round-trip 誤差 ~2e-3。

## 幾何：`BSTriShape` / `BSVertexData`

- 一 glTF primitive → 一 `BSTriShape`，全部掛在根 `NiNode` 下（shape 本身 transform 單位化，座標烘進頂點）。
- 只讀 active scene 的 node hierarchy；TRS／matrix（含 parent transform）烘進頂點，mesh instance 各自輸出，normal 走 inverse-transpose，鏡射 transform 自動反轉 winding。
- primitive 僅接受 TRIANGLES；支援核心 glTF 的 normalized UBYTE/USHORT TEXCOORD accessor，壞 attribute／index／sparse accessor 明確拒絕。
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
- normals：glTF 有就帶、沒有就算面法線（area-weighted）。裸命令預設仍用 Lengyel 法從 UV 現算 tangent frame。`any2nif` 透過 `read_gltf(..., preserve_tangents=True)` 啟用來源 TANGENT，存進 `Mesh.tangents`；[tangents.py](tangents.py) 處理驗證、node transform 與 handedness，writer 保留該方向。無來源切線時，any2nif 設 `Mesh.uv_handedness=True`，由 `prepare_tangent_frames` 拆開相反 UV 手性的共用頂點並生成 TANGENT，再做大型 mesh 切分。reader 與 Mesh 的選項預設皆 False，沒有 authored tangents／頂點色且未啟用 UV handedness 的Mesh 維持相同輸出；替代 UV／atlas 預處理仍只由整包入口負責。
- 頂點色使用 32-byte stride、offset 28 的 RGBA，VertexAttribute.Vertex_Colors 與 SLSF2_Vertex_Colors 都是 bit 5（0x20），依 [nif.xml](https://github.com/niftools/nifxml/blob/develop/nif.xml)。無頂點色的裸命令位元組基準維持不變。
- **限制**：SSE `BSTriShape` 的頂點/三角數是 16-bit，單一 shape 上限各為 65535 頂點與三角形；超過會報錯（請在上游切 mesh）。

## 材質：`BSLightingShaderProperty` + `BSShaderTextureSet`

貼圖路徑規則（material name = extractor 記的貼圖基名，副檔名 `.tga/.dds/.png` 會被剝掉）：

- slot0 diffuse = `<texprefix>\<基名>.dds`
- slot1 normal = `<texprefix>\<基名>_n.dds`——**僅當 glTF 同目錄存在對應 `_n.dds` 才填**（否則留空）。
- 其餘 slot 留空。DSR 的 `_s` spec map **已知限制：先忽略**（Skyrim specular 走 model-space 慣例不同，之後再處理）。

`--materials` 的最小格式如下。陣列必須恰好覆蓋每個來源 mesh，`mesh` 名不可重複；同一
`group` 的 primitive 會合成一個 shape，`skip: true` 則完全不寫出。路徑覆寫是完整 NIF 路徑。

```json
{"version": 1, "materials": [{"mesh": "wall_mesh0", "group": "stone", "diffuse_texture_name": "textures\\port\\stone.dds", "normal_texture_name": "textures\\port\\stone_n.dds", "double_sided": false, "alpha_mode": "MASK", "alpha_flags_override": 4844, "alpha_threshold_override": 128}]}
```

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
