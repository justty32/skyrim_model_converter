# 一鍵轉模型、貼圖與碰撞

← [README](README.md) · [CLI 契約](PROTOCOL.md)

```bash
python -m any2nif chair.glb output/Chair --package
```

輸出目錄裡會有 `meshes/any2nif/chair/chair.nif`、`textures/any2nif/chair/` 內的 DDS，以及 `converter-package.json`。這是 Data 內容，不含 ESP：可交給已有的 ModForge 資產打包流程，或由既有 plugin 的 Model 欄位引用。這裡尚未新增 ModForge 的 `importmesh` 指令。

來源 glTF／GLB 直接解析；OBJ、STL、PLY、DAE 等沿用 trimesh，FBX 使用專案內的 FBX2glTF。trimesh 場景匯入會保留各零件的累積位移／旋轉／縮放與重複擺放，包含 DAE 的父子節點；模型與自動碰撞沿用同一擺放。模型大小預設公尺，Y-up；公分來源加 `--unit cm`，Z-up 來源加 `--up-axis z`。`--asset-name chair_v2` 可指定輸出名稱，名稱會轉成 ASCII 小寫並排除路徑符號；空名稱與 Windows 保留名稱會拒絕。

OBJ／DAE／ZAE 匯入會檢查載入器實際要求的外部／壓縮包內資源；材質檔讀不到、貼圖缺失或無法解碼時會報錯，指出資源名稱並保留舊整包，不再悄悄換成白色。原本沒有材質／貼圖的來源仍可轉換。這不新增 MTL 貼圖指令支援；未被 trimesh 解析的貼圖槽仍沿用後端限制。

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

見 [整包貼圖與材質](package/README.md)：頂點色、命名、alpha、DDS、切線與 UV 契約。

### 不同貼圖使用不同 UV

見 [UV 重烘規則](package/texture-baking.md)：觸發條件、尺寸、採樣與近似限制。

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
