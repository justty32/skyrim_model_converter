# 一鍵轉模型、貼圖與碰撞

← [README](README.md) · [CLI 契約](PROTOCOL.md)

```bash
python -m any2nif chair.glb output/Chair --package
```

輸出目錄裡會有 `meshes/any2nif/chair/chair.nif`、`textures/any2nif/chair/` 內的 DDS，以及 `converter-package.json`。這是 Data 內容，不含 ESP：可交給已有的 ModForge 資產打包流程，或由既有 plugin 的 Model 欄位引用。這裡尚未新增 ModForge 的 `importmesh` 指令。

來源 glTF／GLB 直接解析；OBJ、STL、PLY、DAE 等沿用 trimesh，FBX 使用專案內的 FBX2glTF。模型大小預設公尺，Y-up；公分來源加 `--unit cm`，Z-up 來源加 `--up-axis z`。`--asset-name chair_v2` 可指定輸出名稱，名稱會轉成 ASCII 小寫並排除路徑符號；空名稱與 Windows 保留名稱會拒絕。

超過 NIF 單一 shape 的 65,535 頂點限制時，會沿原本三角形順序自動切分；材質、法線、UV 與頂點色一起保留，不需要先手工拆模型。

## 碰撞怎麼選

| 選項 | 結果 |
|---|---|
| `--collision convex` | 整包預設。一個包住所有三角形的凸包；比盒子貼合外形，但填滿凹槽、門洞與內部空間。 |
| `--collision box` | 包住全部模型的盒狀碰撞，適合箱子、柱子。每軸至少厚 5 公分。 |
| `--collision none` | 不產生碰撞，適合只看不碰的展示物。 |
| `--collision hulls.json` | 使用自行準備的多凸包。JSON 已是 Y-up 公尺，不再套來源單位／軸向；適合需要保留空洞的手工拆分。 |

自動碰撞使用已套完 node transform、單位、縮放與軸向的模型，僅收三角形實際使用的頂點。平面凸包會沿法線前後各加厚 2.5 公分；點、線等無法形成外殼的輸入會報錯。碰撞保持 Havok 公尺，不套 render 的 70.03 倍。

## 貼圖與材質

每個材質會取得 `material_0000` 這類獨立名稱，來源的空名、同名或路徑字串不會造成互相覆蓋。glTF／GLB 內嵌、data URI、外部影像都會先讀取；指定卻缺失或壞掉的影像會報錯，整包不發布。

diffuse 的 RGB 顏色倍率會在線性色彩空間乘上原圖，再轉回 sRGB。沒有 diffuse 的材質會產生純色圖；沒有材質的 primitive 使用白色圖。Alpha 倍率保留給 NIF 材質，避免在貼圖和材質各乘一次。

diffuse 輸出 BC1／BC3；normal 使用 BC3 並翻轉綠色通道；specular 與 emissive 輸出 BC1。全部生成二次方尺寸與完整 mipmap。輸出後重新讀取 NIF 的 `BSShaderTextureSet`，逐一核對檔案存在、DDS 編碼、完整 mip 資料長度，並由 Pillow 解碼。

傳統 Skyrim 材質不是完整 PBR。Metallic／roughness 以既有公式近似，roughness 影像反相為 specular mask；`KHR_materials_specular` 有提供時優先採用。True PBR、材質的完全等價轉換與凹形碰撞自動拆分仍未實作。骨架、動畫、morph 與 sparse accessor 沿用靜態後端的拒絕規則。

目前只支援 TEXCOORD_0 與未變換的貼圖座標。非預設 UV 集、非 identity 的 `KHR_texture_transform` 或無法處理的 normal scale 會明確報錯；只靠 image-source extension、沒有一般 `texture.source` 的材質也會報錯。不要將 metadata 還在輸入裡，當成輸出已支援該功能。

## 重跑與失敗

整包先在輸出目錄旁邊的暫存處完成。全部檢查通過後才發布，因此轉換或貼圖失敗時，前一版成品保持完整。

既有目錄必須帶本工具的 manifest，且所有檔案與雜湊都相符，才能被新版整包替換；手動加檔、改檔、少檔或 symlink 都會拒絕覆蓋。輸出目錄也不能包含來源模型。需要保留手改版時，請改用另一個輸出目錄。

發布失敗會嘗試還原舊整包；若檔案系統連還原也失敗，錯誤訊息會指明舊備份保留的位置。新整包已發布、但清舊備份失敗時回成功並顯示備份位置，避免上游把成功當失敗重做。兩個程序不要同時寫同一個輸出目錄。

## Manifest

`converter-package.json` 使用 `schema: "model-converter-package/1"`。`mesh` 是相對 Data 目錄的 NIF 路徑；`collision` 是 `none`、`box`、`convex` 或 `json`；`textures` 記 NIF 實際引用的 texture 路徑；`sha256` 將每個產物的相對路徑對應 SHA-256，不含 manifest 自己。

Manifest 證明的是離線產物與檔案一致性，不是遊戲內驗收。回到有 Skyrim 的機器，仍要檢查模型方向／大小、顏色／透明／發光，以及碰撞是否能阻擋角色；公司 WSL 不會把這些標成已通過。

## 驗證入口

`tests/test_any2nif_package.py` 跑真 CLI、多種來源格式、模型讀回、所有 DDS 槽位與發布失敗回復；`tests/test_package_materials.py` 驗影像來源、命名、顏色與拒絕條件；`tests/test_any2nif_collision.py` 驗凸包、盒子、尺度、平面與大型模型。

```bash
.venv-wsl/bin/python -m pytest -q
```
