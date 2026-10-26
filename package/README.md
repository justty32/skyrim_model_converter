# 整包貼圖與材質

← [整包操作](../PACKAGE.md)｜[UV 重烘](texture-baking.md)

## 貼圖與材質

PLY 等 trimesh 來源的頂點顏色會保留到 NIF。來源按面指定顏色時，會先拆開共用頂點，避免相鄰面的顏色被平均混合；三角形位置與原本法線保留。沒有來源顏色時不額外加入預設灰色。這只保留顏色資料，透明混合仍由材質的 alpha 設定決定。NIF 的 VertexAttribute 與 SkyrimShaderPropertyFlags2 顏色標記皆使用 bit 5（0x20），依 [NifTools 格式定義](https://github.com/niftools/nifxml/blob/develop/nif.xml)。

每個材質會取得 `material_0000` 這類獨立名稱，來源的空名、同名或路徑字串不會造成互相覆蓋。glTF／GLB 內嵌、data URI、外部影像都會先讀取；指定卻缺失或壞掉的影像會報錯，整包不發布。

diffuse 的 RGB 顏色倍率會在線性色彩空間乘上原圖，再轉回 sRGB。沒有 diffuse 的材質會產生純色圖；沒有材質的 primitive 使用白色圖。Alpha 倍率保留給 NIF 材質，避免在貼圖和材質各乘一次。

`MASK` 使用 alpha test、關閉混合，保留大於等於門檻的像素；依 [NifTools AlphaFlags／TestFunction](https://github.com/niftools/nifxml/blob/develop/nif.xml) 寫出 0x1A00。門檻為 `ceil(clamp(alphaCutoff, 0, 1) × 255)`；cutoff 大於 1 時使用 NEVER 比較，整個材質透明，符合 [glTF cutoff 定義](https://raw.githubusercontent.com/KhronosGroup/glTF/main/specification/2.0/schema/material.schema.json)。NIF 的 8-bit 門檻、DDS 壓縮與遊戲濾波仍有量化誤差。使用 0x0201（混合開啟、ALWAYS 比較）的 MASK 模型需重轉。BLEND 與明確指定的原始 flags／threshold 維持既有行為。

diffuse 輸出 BC1／BC3；normal 會先將 `normalTexture.scale` 烘進法線的 X／Y 分量並重新正規化，再使用 BC3 並翻轉綠色通道；specular 與 emissive 輸出 BC1。全部生成二次方尺寸與完整 mipmap。輸出後重新讀取 NIF 的 `BSShaderTextureSet`，逐一核對檔案存在、DDS 編碼、完整 mip 資料長度，並由 Pillow 解碼。

傳統 Skyrim 材質不是完整 PBR。Metallic／roughness 以既有公式近似，roughness 影像反相為 specular mask；`KHR_materials_specular` 有提供時優先採用。True PBR、材質的完全等價轉換與凹形碰撞自動拆分仍未實作。骨架、動畫、morph 與 sparse accessor 沿用靜態後端的拒絕規則。

來源同時提供 `NORMAL` 與 `TANGENT` 時，`any2nif` 會保留切線方向及正反手性，連同 node transform、`--up-axis`、負縮放與大模型切分一起傳到 NIF。共用 UV 的直通路徑不因此重排 UV 或放大貼圖，避免丟失來源的凹凸方向。缺少 `NORMAL` 時忽略來源 `TANGENT`；生成法線仍沿用既有平滑近似。來源切線必須是與頂點數相符的 FLOAT VEC4、方向有限且非零、W 為 ±1，不能與 normal 平行；壞資料回 exit 2 並保留舊包。bitangent 的關係依 [glTF mesh 規格](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#meshes-overview)。缺來源 TANGENT 時也會依 UV 的正反方向生成切線，鏡射 UV 的直通貼圖保持原尺寸。鏡射接縫會複製頂點，讓同一個三角形的正反手性一致；重烘與最終 NIF 使用相同的逐角點切線，先生成再切分大型模型，保留跨分件的平滑方向。完全退化的 UV 仍使用固定備援方向。

法線強度支援 0（消除傾斜）、介於 0 與 1（減弱）、大於 1（加強）及負值（反轉 X／Y）；NaN／Infinity 會報錯。計算依 [glTF normalTexture.scale 定義](https://raw.githubusercontent.com/KhronosGroup/glTF/main/specification/2.0/schema/material.normalTextureInfo.schema.json)，在壓縮前烘焙，因此仍有 DDS 壓縮與濾波誤差。

整包支援替代 `TEXCOORD_n`，以及 `KHR_texture_transform` 的位移、旋轉、縮放與 `texCoord` 覆寫。各貼圖共用座標集與變換時，轉換器會把結果直接烘進 NIF 的單一 UV，原始模型不變。變換順序依 [Khronos 規格](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_texture_transform)：先縮放、再旋轉、最後位移。支援 float 與正規化 unsigned byte／short UV；缺少指定座標、非有限值或超出 NIF 半精度範圍會拒絕。

共用 UV 帶 normal 時，正值等比縮放與位移可直接烘進座標；旋轉、鏡射或不等比縮放會自動走下方的貼圖重烘，換算法線方向。normal 的 UV 零縮放會拒絕，因為無法確定完整切線方向。不同貼圖各用不同座標時，同樣改走貼圖重烘流程。這些處理只作用於 `--package`；單檔模式的後端仍讀 TEXCOORD_0。只靠 image-source extension、沒有一般 `texture.source` 的材質也仍報錯。


### 不同貼圖使用不同 UV

見 [UV 重烘規則](texture-baking.md)。
