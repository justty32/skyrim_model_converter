# AUDIT — 正向管線（常見格式 → Skyrim `.nif` + `.dds`）選型

← [README](README.md) ｜ 2026-09-05 15:45–16:00，`lead-mc`。本檔是 `any2nif` / `tex2dds` 開工前的實裝驗證與選型結論。

## 1. 現況 baseline（實跑，非推論）

| 項 | 結果 |
|---|---|
| repo HEAD | 開工時 detached `2c09125`；已 `git checkout main` + `git pull --ff-only` 快轉到 `2c09125` |
| `.venv` | **是壞的**：`pyvenv.cfg` 的 `command` 還指 `/home/lorkhan/repo/ModForge/sub_projs/model-converter/.venv`，`bin/pip` 這類帶 shebang 的腳本 exit 127。但 `bin/python` 是指向 `/usr/bin/python3` 的 symlink，`sys.prefix` 由目錄推導 → **`.venv/bin/python -m pip` 可用**，不必重建 |
| 既有測試 | `.venv/bin/python -m pytest -q` → **68 passed in 0.31s** ✅ |
| darksouls-port contract test | `projects/darksouls-port/venv/bin/python -m unittest tests.test_model_converter_contract` → **Ran 2 tests, OK** ✅（該 repo 有自己的 `venv/`，需要 `soulstruct`；用 model-converter 的 venv 跑會 collection error，這不是回歸） |
| venv 既有套件 | numpy 2.4.6、scipy 1.18.0、pygltflib 1.16.5、pytest 9.1.0、**trimesh 5.0.0**、**vhacdx 0.0.10**、constrata、zstandard。（工作記憶說「pip 沒有 trimesh」是**錯的**，實測已裝） |

## 2. `gltf2nif` 支援矩陣（讀碼＋實跑合成 fixture）

| 能力 | 現況 | 依據 |
|---|---|---|
| `.gltf` + 外部 `.bin` | ✅ | `_buffer_bytes` 讀 `uri` 相對路徑 |
| **`.glb`** | ✅ **早就支援**（先前假設「只吃 .gltf」不成立） | `GLTF2().load()` 依副檔名分派；`_buffer_bytes` 對 `uri is None` 走 `gltf.binary_blob()`。實跑 trimesh 產的 `box.glb` → 8 verts / 12 tris / UV 有 |
| `data:` base64 內嵌 buffer | ✅ | `_buffer_bytes` 有 `data:` 分支 |
| 多 primitive / 多 material | ✅ 一 primitive → 一 `BSTriShape` | `read_gltf` 逐 primitive |
| node transform（TRS/matrix、階層、負行列式翻面） | ✅ | `_scene_instances` + `_transform_geometry` |
| interleaved `byteStride`、accessor `byteOffset`、normalized 整數 UV | ✅ | `_read_accessor` |
| skin / morph target / animation | ❌ 明確拒絕（`GltfError`） | 設計如此 |
| sparse accessor | ❌ 拒絕 | |
| **貼圖影像** | ❌ **完全沒處理**：只取 material 的 **名字**當貼圖基名，再 `probe_normal_map` 看旁邊有沒有 `<基名>_n.dds`。glTF 內嵌／外部 image 一律被忽略 | `_material_basename`、`probe_normal_map` |
| **PBR 材質參數** | ❌ 完全沒讀：`baseColorFactor`／`metallicRoughness`／`emissive`／`alphaMode`／`doubleSided` 全部丟掉，`BSLightingShaderProperty` 是寫死常數（`_LSP_SHADER_FLAGS1/2`、gloss 80、spec 1.0、雙面永遠開） | `_build_lsp` 無參數 |
| `NiAlphaProperty` | ❌ 從不產生，`BSTriShape` 的 Alpha Property 永遠 `-1` | `_build_bstrishape` |
| 頂點上限 | 65535（SSE `BSTriShape` 16-bit）→ 超過就 `ValueError`，**不自動切分** | `build_nif` |

**結論**：缺口不是 GLB，是 **①非 glTF 的輸入格式 ②貼圖影像 → `.dds` ③PBR → BSLightingShaderProperty**。

## 3. 外部工具三條路——各實裝一次

沿用交接書要求，**三條都真的裝來跑過**，不是查文件。

| 路 | 指令 | 結果 |
|---|---|---|
| **A. 系統套件 `assimp`** | `pacman -Ss '^assimp$'` → `extra/assimp 6.0.5-1` 存在；`pacman -Q assimp` → not found；`sudo -n true` → **`sudo: a password is required`** | ❌ **走不通**。裝系統套件要使用者輸密碼，今天不打擾；且 COMMON 第 1 條精神是不動系統狀態。pip 的 `impasse` / `pyassimp` 都只是 ctypes binding，**仍需要系統 `libassimp.so`** → 同樣死。`assimp-py` 只有 sdist（1.1.0），要本地編譯 assimp C++ 源碼，成本／風險不成比例 |
| **B. `FBX2glTF` 二進位** | `curl -L .../v0.9.7/FBX2glTF-linux-x64`（12.8 MB 靜態連結）→ `chmod +x` → `./FBX2glTF-linux-x64 --version` → **`FBX2glTF version 0.9.7` / exit 0** | ✅ **可用**。2019 年的 build 但靜態連結，在 Manjaro 6.18 跑得起來。**選這條做 FBX** |
| **C. `blender --background`** | `which blender` → 空；`pacman -Q blender` → not found；裝需 sudo | ❌ 走不通，同 A |

補測（貼圖側）：

| 工具 | 結果 |
|---|---|
| `texconv` | ❌ 不在本機。`wine` 有（`/usr/bin/wine`），理論上可跑 texconv.exe，但要多一個 Windows 相依＋wine prefix，且**離線測不了** → 不選 |
| ImageMagick `magick` / `convert` | ✅ 在本機。能寫 DDS（DXT1/DXT5），但 mipmap／BC 品質不可控，且多一個外部相依 → **只當備援，不當主路** |
| `Pillow` | ✅ pip 裝得起來（12.3.0，有 cp314 wheel）。**讀** PNG/JPG/TGA/BMP/DDS 都行；**寫** DDS 支援有限 → 拿它當**輸入解碼**與**獨立驗證**（Pillow 讀得回我們寫的 DDS＝第三方交叉驗證） |
| `imagecodecs` | ✅ pip 有 cp312-abi3 wheel（28 MB）。但它**沒有 BC1/BC3 編碼器**（只有 JPEG/PNG/JPEG2000 那類）→ 不選 |
| `Compressonator` / `nvcompress` | ❌ 都不在本機，要下載外部二進位 → 不選 |

## 4. 選型結論

1. **貼圖 `tex2dds`＝自寫純 Python BC1/BC3 編碼器**（numpy 向量化）＋自寫 DDS 標頭＋自寫 mipmap chain。
   理由：零外部二進位相依、離線可測、輸出可被 Pillow 的 DDS reader 獨立讀回驗證（交叉驗證不是自說自話）。`--codec magick` 留成備援旗標。
2. **模型 `any2nif`＝「一律先正規化成 glTF，再走既有 `gltf2nif`」**。
   理由：`gltf2nif` 的 NIF 位元組佈局已對真實 vanilla SSE nif 核過、有 42 個測試護著，正向路重用它是唯一不重造輪子的走法；也讓 `lead-dsp` 的材質需求自動受益。
   - **OBJ / STL / PLY / DAE / OFF / DXF / XYZ / ZAE** → `trimesh` 5.0.0 → 暫存 `.glb` →`read_gltf`。（`trimesh.available_formats()` 實測：`bz2, dae, dxf, glb, gltf, obj, off, ply, stl, stl_ascii, tar.bz2, tar.gz, xyz, zae, zip`，DAE 需 `pycollada`，已裝 0.9.3）
   - **GLTF / GLB** → 直接進 `read_gltf`，**不經 trimesh**（避免無謂的 round-trip 失真）
   - **FBX** → `FBX2glTF-linux-x64` → `.glb` → `read_gltf`
   - **3DS** → ❌ 不做：trimesh 不支援、assimp 走不通。要做得自寫 chunk parser，本輪不值得（見 REPORT「沒做的」）
3. **材質**：在 `gltf2nif` 加 `MaterialSpec`（additive、預設 `None`＝行為與位元組**完全不變**），把 glTF PBR / OBJ MTL 映到 `BSLightingShaderProperty` ＋ 需要時產 `NiAlphaProperty`。

## 5. 對外契約（本輪新增，不動舊的）

```
python -m any2nif <in.{obj,glb,gltf,fbx,dae,stl,ply,off,dxf}> <out.nif>
      [--textures-out DIR] [--texprefix textures\x\y] [--scale F] [--up-axis y|z]
      [--collision none|hulls.json] [--fbx2gltf PATH] [--no-materials] [--keep-intermediate DIR]

python -m tex2dds <in.{png,jpg,tga,bmp,dds}> <out.dds>
      [--format auto|bc1|bc3] [--no-mipmaps] [--normal-map] [--resize pow2|none]
```
Exit code 沿用 `PROTOCOL.md`：`0` 成功／`1` 一般錯誤／`2` 來源解析失敗／`3` 含 skin/動畫（靜態後端拒絕）。
**`nif2gltf` 與 `gltf2nif` 的 CLI、旗標、輸出位元組一律不動**——`darksouls-port/tests/test_model_converter_contract.py` 收工前會再跑一次證明。
