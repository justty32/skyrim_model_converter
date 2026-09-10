# 一鍵轉模型、貼圖與碰撞

← [README](README.md) · [CLI 契約](PROTOCOL.md)

```bash
python -m any2nif chair.glb output/Chair --package
```

輸出目錄裡會有 `meshes/any2nif/chair/chair.nif`、`textures/any2nif/chair/` 內的 DDS，以及 `converter-package.json`。這是 Data 內容，不含 ESP：可交給已有的 ModForge 資產打包流程，或由既有 plugin 的 Model 欄位引用。這裡尚未新增 ModForge 的 `importmesh` 指令。

來源 glTF／GLB 直接解析；OBJ、STL、PLY、DAE 等沿用 trimesh，FBX 使用專案內的 FBX2glTF。模型大小預設公尺，Y-up；公分來源加 `--unit cm`，Z-up 來源加 `--up-axis z`。`--asset-name chair_v2` 可指定輸出名稱，名稱會轉成 ASCII 小寫並排除路徑符號；空名稱與 Windows 保留名稱會拒絕。

超過 NIF 單一 shape 的 65,535 頂點或三角形限制時，會沿原本三角形順序自動切分；材質、法線、UV 與頂點色一起保留，不需要先手工拆模型。

SheenChair 兩種解析度的成品重建與遊戲操作見 [回家驗收](HOME-VALIDATION.md)。

## 碰撞怎麼選

| 選項 | 結果 |
|---|---|
| `--collision convex` | 整包預設。一個包住所有三角形的凸包；比盒子貼合外形，但填滿凹槽、門洞與內部空間。 |
| `--collision convex-mesh` | 每個來源網格各自產生凸包，可保留分件之間的空隙。 |
| `--collision box` | 包住全部模型的盒狀碰撞，適合箱子、柱子。每軸至少厚 5 公分。 |
| `--collision none` | 不產生碰撞，適合只看不碰的展示物。 |
| `--collision hulls.json` | 使用自行準備的多凸包。JSON 已是 Y-up 公尺，不再套來源單位／軸向；適合需要保留空洞的手工拆分。 |

自動碰撞使用已套完 node transform、單位、縮放與軸向的模型，僅收三角形實際使用的頂點。平面凸包會沿法線前後各加厚 2.5 公分；點、線等無法形成外殼的輸入會報錯。碰撞保持 Havok 公尺，不套 render 的 70.03 倍。

`convex-mesh` 適合已分成桌面／桌腳、左右門柱／橫樑等網格的模型：

```bash
python -m any2nif doorway.glb output/Doorway --package --collision convex-mesh
```

分組以正規化後的每個 node × primitive 為準，同一網格的不同擺放也各自產生碰撞。超過 65,535 頂點或三角形而切出的 NIF shape 不會增加凸包數量。來源若把整扇門框合成單一 primitive，仍會填滿門洞；材質分區也可能將一個物件分成多個 primitive。這個選項沿用來源分件，不會自動將凹形網格拆成凸塊。任何分件退化或無法產生凸包時，整包失敗並保留舊成品。

## 貼圖與材質

每個材質會取得 `material_0000` 這類獨立名稱，來源的空名、同名或路徑字串不會造成互相覆蓋。glTF／GLB 內嵌、data URI、外部影像都會先讀取；指定卻缺失或壞掉的影像會報錯，整包不發布。

diffuse 的 RGB 顏色倍率會在線性色彩空間乘上原圖，再轉回 sRGB。沒有 diffuse 的材質會產生純色圖；沒有材質的 primitive 使用白色圖。Alpha 倍率保留給 NIF 材質，避免在貼圖和材質各乘一次。

diffuse 輸出 BC1／BC3；normal 會先將 `normalTexture.scale` 烘進法線的 X／Y 分量並重新正規化，再使用 BC3 並翻轉綠色通道；specular 與 emissive 輸出 BC1。全部生成二次方尺寸與完整 mipmap。輸出後重新讀取 NIF 的 `BSShaderTextureSet`，逐一核對檔案存在、DDS 編碼、完整 mip 資料長度，並由 Pillow 解碼。

傳統 Skyrim 材質不是完整 PBR。Metallic／roughness 以既有公式近似，roughness 影像反相為 specular mask；`KHR_materials_specular` 有提供時優先採用。True PBR、材質的完全等價轉換與凹形碰撞自動拆分仍未實作。骨架、動畫、morph 與 sparse accessor 沿用靜態後端的拒絕規則。

來源同時提供 `NORMAL` 與 `TANGENT` 時，`any2nif` 會保留切線方向及正反手性，連同 node transform、`--up-axis`、負縮放與大模型切分一起傳到 NIF。共用 UV 的直通路徑不因此重排 UV 或放大貼圖，避免丟失來源的凹凸方向。缺少 `NORMAL` 時忽略來源 `TANGENT`；生成法線仍沿用既有平滑近似。來源切線必須是與頂點數相符的 FLOAT VEC4、方向有限且非零、W 為 ±1，不能與 normal 平行；壞資料回 exit 2 並保留舊包。bitangent 的關係依 [glTF mesh 規格](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#meshes-overview)。缺來源 TANGENT 時也會依 UV 的正反方向生成切線，鏡射 UV 的直通貼圖保持原尺寸。鏡射接縫會複製頂點，讓同一個三角形的正反手性一致；重烘與最終 NIF 使用相同的逐角點切線，先生成再切分大型模型，保留跨分件的平滑方向。完全退化的 UV 仍使用固定備援方向。

法線強度支援 0（消除傾斜）、介於 0 與 1（減弱）、大於 1（加強）及負值（反轉 X／Y）；NaN／Infinity 會報錯。計算依 [glTF normalTexture.scale 定義](https://raw.githubusercontent.com/KhronosGroup/glTF/main/specification/2.0/schema/material.normalTextureInfo.schema.json)，在壓縮前烘焙，因此仍有 DDS 壓縮與濾波誤差。

整包支援替代 `TEXCOORD_n`，以及 `KHR_texture_transform` 的位移、旋轉、縮放與 `texCoord` 覆寫。各貼圖共用座標集與變換時，轉換器會把結果直接烘進 NIF 的單一 UV，原始模型不變。變換順序依 [Khronos 規格](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_texture_transform)：先縮放、再旋轉、最後位移。支援 float 與正規化 unsigned byte／short UV；缺少指定座標、非有限值或超出 NIF 半精度範圍會拒絕。

共用 UV 帶 normal 時，正值等比縮放與位移可直接烘進座標；旋轉、鏡射或不等比縮放會自動走下方的貼圖重烘，換算法線方向。normal 的 UV 零縮放會拒絕，因為無法確定完整切線方向。不同貼圖各用不同座標時，同樣改走貼圖重烘流程。這些新支援只作用於 `--package`；單檔模式的後端仍讀 TEXCOORD_0。只靠 image-source extension、沒有一般 `texture.source` 的材質也仍報錯。

### 不同貼圖使用不同 UV

`--package` 遇到各貼圖的座標集／變換不同、帶 AO 陰影圖、normal 的 UV 有旋轉／鏡射／不等比縮放，或 sampler 使用邊緣延伸／鏡射重複／nearest 時，會自動用 [xatlas](https://github.com/mworchel/xatlas-python) 排出沒有重疊的共用 UV，再把各張圖按原本 UV 重採樣。每個來源 primitive／擺放實例各有自己的材質與貼圖；UV 接縫增加頂點，但保持三角形的位置、順序、法線與頂點色。node 的位移／旋轉／縮放先套進幾何，避免不同擺放共用錯誤的法線烘焙。

預設每張烘焙圖是 1024 × 1024；想保留更多布紋細節可提高尺寸：

```bash
python -m any2nif SheenChair.glb output/SheenChair --package --collision convex-mesh --bake-size 2048
```

`--bake-size` 只供整包模式使用，可選 64、128、256、512、1024、2048、4096。較大尺寸增加時間、記憶體與成品大小；它不會增加來源圖本來沒有的細節，也不改共用 UV 直通流程的原圖大小。重烘是有限解析度近似，密集重複花紋與低階 mipmap 仍可能模糊或產生接縫。小到沒有像素中心、或 NIF 半精度 UV 壓縮後塌縮的面，會自動取得獨立小區塊，從自己的來源 UV 取色；最後檢查每個面都有像素覆蓋。若空間不足，會要求提高 `--bake-size` 並保留舊成品。

採樣支援 REPEAT、MIRRORED_REPEAT、CLAMP_TO_EDGE，以及 nearest／bilinear；共用 UV 的非預設 sampler 也會自動重烘，無效 sampler 索引／wrap／filter 會拒絕。RGB 色圖在線性空間取樣，normal、roughness、AO 等資料圖不做 sRGB 轉換。烘焙以來源最高解析度取樣，未模擬視角相關的來源 mip／anisotropic 濾波。Atlas 預留外圈供小區塊補救，未覆蓋區域補最近的邊緣顏色，再交給既有 DDS 完整 mipmap 流程。4096 的四槽合成模型完整試轉約 89 秒、峰值記憶體約 1.52 GiB（本次 WSL 實測，非所有模型的上限）。

AO 使用紅色通道與 `strength`，乘進線性 diffuse，之後再套 diffuse RGB factor；alpha 不乘 AO。這是傳統 Skyrim 的陰影近似，會讓直接光照也變暗，並非 glTF 的環境光遮蔽等價實作。normal 會先套強度，再按原 UV 與新 UV 的切線方向換算；有來源 tangent 時保留其方向與 handedness，再依 UV 變換換算；沒有來源 tangent 時，從 UV 的兩個方向求切線與正反手性，包含鏡射 UV；生成方式仍是平滑近似，並非 MikkTSpace。normal 變換需要可逆的 UV 縮放。超過 NIF 頂點／三角形上限時保留切分前已生成的切線方向。負 `--scale` 的鏡射由 NIF 切線正反手性處理。Sheen 與材質 variants 仍不轉成 Skyrim 對等效果，使用來源預設材質。

公開真實模型的固定版本、試轉結果與可重跑指令見 [REAL-ASSETS.md](REAL-ASSETS.md)。

## 重跑與失敗

整包先在輸出目錄旁邊的暫存處完成。全部檢查通過後才發布，因此轉換或貼圖失敗時，前一版成品保持完整。

既有目錄必須帶本工具的 manifest，且所有檔案與雜湊都相符，才能被新版整包替換；手動加檔、改檔、少檔或 symlink 都會拒絕覆蓋。輸出目錄也不能包含來源模型。需要保留手改版時，請改用另一個輸出目錄。

發布失敗會嘗試還原舊整包；若檔案系統連還原也失敗，錯誤訊息會指明舊備份保留的位置。新整包已發布、但清舊備份失敗時回成功並顯示備份位置，避免上游把成功當失敗重做。兩個程序不要同時寫同一個輸出目錄。

## Manifest

`converter-package.json` 使用 `schema: "model-converter-package/1"`。`mesh` 是相對 Data 目錄的 NIF 路徑；`collision` 是 `none`、`box`、`convex`、`convex-mesh` 或 `json`；`textures` 記 NIF 實際引用的 texture 路徑；`sha256` 將每個產物的相對路徑對應 SHA-256，不含 manifest 自己。

Manifest 證明的是離線產物與檔案一致性，不是遊戲內驗收。回到有 Skyrim 的機器，仍要檢查模型方向／大小、顏色／透明／發光，以及碰撞是否能阻擋角色；公司 WSL 不會把這些標成已通過。

## 驗證入口

`tests/test_any2nif_package.py` 跑真 CLI、多種來源格式、模型讀回、所有 DDS 槽位與發布失敗回復；`tests/test_package_materials.py` 驗影像來源、命名、顏色與拒絕條件；`tests/test_any2nif_collision.py` 驗凸包、盒子、分件空隙、NIF 多凸包引用、尺度、平面與大型模型。

```bash
.venv-wsl/bin/python -m pytest -q
```
