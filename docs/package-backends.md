# 整包與貼圖後端

← [架構與後端導覽](README.md)

## 實作（`any2nif/` — 正向入口）

所有來源先正規化成 glTF/GLB，再交給既有 `gltf2nif` 寫 SSE `.nif`；完整 CLI 契約見 [PROTOCOL.md](../PROTOCOL.md#正向入口any2nif)。

<!-- wf-nav -->
| 模組 | 職責 |
|---|---|
| `any2nif/cli.py` | CLI 編排：單位／軸向、貼圖、碰撞、PBR 材質與 NIF 寫出；exit 0/1/2/3。 |
| `any2nif/source_resources.py` | 追蹤 OBJ／DAE／ZAE 外部資源讀取並驗證影像，將 trimesh 吞掉的缺檔／解碼錯誤改成明確失敗；`tests/test_trimesh_resources.py` 驗真來源與舊整包保護。 |
| `any2nif/normalize.py` | 依副檔名分派：glTF/GLB 直通，OBJ/STL/PLY/DAE/ZAE/OFF/DXF/XYZ 走 trimesh，FBX 走 FBX2glTF。 |
| `any2nif/trimesh_backend.py`、`fbx_backend.py` | 將非 glTF 來源正規化成 GLB，保留可用的 mesh／material 資訊；trimesh 路徑保留場景節點擺放，`tests/test_trimesh_instances.py` 驗真 DAE 的父子變換與整包碰撞；`tests/test_trimesh_colors.py` 驗真 PLY 頂點／面顏色到 NIF 的保留。 |
| `any2nif/collision.py` | `none`／自動 box、convex、convex-mesh／既有 hulls JSON；`tests/test_any2nif_collision.py` 驗真 CLI 碰撞尺度、平面加厚與大型 mesh。 |
| `any2nif/mesh_split.py` | 依 NIF 每 shape 的 65,535 頂點／三角形上限自動切分，保留材質／頂點資料與三角形順序；`tests/test_any2nif_mesh_split.py` 驗大型 mesh 真 NIF 讀回與資料拒絕；`tests/test_triangle_limit.py` 驗共用頂點但面數超限的完整讀回，碰撞 suite 另跑真整包不丟面且不增加凸包。 |
| `any2nif/package.py` | 完整 Data 目錄暫存、DDS 格式／引用驗證、manifest、發布／回復；`tests/test_any2nif_package.py` 跨格式真 CLI、所有貼圖槽與失敗保留驗證。 |
| `any2nif/package_materials.py` | 自包含 glTF、安全獨立材質名、缺圖拒絕、diffuse RGB 烘焙；`tests/test_package_materials.py` 驗外部／GLB／data URI 影像與材質限制。`tests/test_package_alpha_mask.py` 直接讀整包 NIF／DDS，驗透明裁切位元、8-bit 門檻邊界與 alpha 倍率儲存。 |
| `any2nif/package_uv.py` | 整包的替代 UV 與共用貼圖變換烘焙、單 UV／法線切線限制；`tests/test_package_uv.py` 讀回 NIF 驗座標與失敗保護。 |
| `any2nif/package_bake.py`、`bake_geometry.py` | 跨 UV 圖片重烘、AO 合成、逐實例／primitive 分離與頂點重映射；`tests/test_package_bake.py`、`test_bake_instances.py`、`test_package_normal_frames.py`、`test_package_sampler.py` 驗像素、法線、採樣設定、幾何與失敗保護。 |
| `any2nif/uv_atlas.py` | xatlas 排版、像素覆蓋與邊緣補色；`tests/test_uv_atlas.py` 驗幾何保留、重疊與細面。 |
| `any2nif/texture_sampling.py`、`normal_bake.py` | glTF wrap／濾波／sRGB 採樣及法線切線換算；`tests/test_texture_sampling.py`、`test_normal_bake.py` 驗解析值與方向。 |
| `tools/build_sheen_validation.py` | SheenChair 1024／2048 獨立路徑的驗收包、實際成品與 ZIP 讀回；操作見 [HOME-VALIDATION.md](../HOME-VALIDATION.md)。 |
| `tools/smoke_real_assets.py` | 固定來源與 SHA-256 的公開模型下載／離線試轉，含真實網格 UV 衍生案例與 SheenChair DDS 顏色抽查；`tests/test_real_asset_validation.py` 驗抽查器；操作與結果見 [REAL-ASSETS.md](../REAL-ASSETS.md)。 |
| `any2nif/transform.py` | 將來源單位換算成公尺，並把來源 Z-up 正規化成 glTF Y-up；`tests/test_any2nif_transform.py` 驗無效縮放拒絕與負縮放的法線／繞向。 |
| `any2nif/textures.py` | glTF 圖像轉 Skyrim diffuse／normal／specular／emissive `.dds` 槽位；烘焙法線強度。 |

## 實作（`tex2dds/` — 貼圖編碼）

純 Python 將 Pillow 可解碼影像寫成 Skyrim 可用 BC1/BC3 DDS；完整 CLI 契約見 [PROTOCOL.md](../PROTOCOL.md#貼圖編碼tex2dds)。

<!-- wf-nav -->
| 模組 | 職責 |
|---|---|
| `tex2dds/cli.py` | 單檔轉換 CLI 與 exit 0/1/2。 |
| `tex2dds/pipeline.py` | 解碼、pow2 resize、normal green flip、mipmap chain 與格式選擇。 |
| `tex2dds/bcn.py` | 純 Python BC1／BC3 block encoder。 |
| `tex2dds/dds.py` | 寫入 DXT1／DXT5 DDS header 與 mip payload。 |

