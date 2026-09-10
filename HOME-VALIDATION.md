# SheenChair 回家驗收

這份包用同一張椅子比較 1024／2048 烘焙。兩版各有模型、DDS 與分件凸包碰撞，路徑不同，可以並排放。它們是靜態擺設，沒有坐下互動；不含 ESP，需先在測試 plugin 建立模型記錄。

## 取得成品

2026-09-10 最新可攜帶批次是 `backend/home-validation/20260910-160024-009333/`。請取這一批的 `sheenchair_1024.zip`（約 4.08 MB）與 `sheenchair_2048.zip`（約 8.69 MB），連同說明、署名、報告與 `SHA256SUMS` 一起帶走；較早批次缺少最後的法線接縫修正。

公司已產出的整個 `backend/home-validation/` 批次資料夾可直接帶回家；其中兩個 ZIP 各自是 Data 內容，解開後最上層是 `meshes`、`textures`、`converter-package.json`。素材和 ZIP 沒有進 Git，**只 pull 不會拿到成品**。也可在家用專案自己的 Python 環境重建：

```bash
uv venv .venv-wsl
uv pip install --python .venv-wsl/bin/python -r requirements.txt
.venv-wsl/bin/python tools/build_sheen_validation.py --download
```

在 model-converter 根目錄執行。已有環境可省略前兩行；已有固定版本 SheenChair 可省略 `--download`。這個 GLB 流程不需要 FBX2glTF。腳本最後印出成品位置；每次使用新批次資料夾，報告記錄 ZIP 雜湊及離線檢查結果。整個批次資料夾一起帶走，保留來源署名與報告。回家後在批次資料夾執行 `sha256sum -c SHA256SUMS`，兩個 ZIP 都應顯示 `OK`，再開始安裝。

## 放進遊戲

先將兩個 ZIP 在 MO2 裝成兩個獨立 mod，例如 `AssetTest-SheenChair-1024` 與 `AssetTest-SheenChair-2048`，確認兩個 mod 的根目錄直接看得到 `meshes` 和 `textures`。本次公司端沒有安裝或啟用它們；回家操作使用現役 `modpack-main`。

用 Creation Kit 或既有 plugin 編輯流程，在一個專用測試 ESP 新增兩筆 **Static（STAT）**。不要改原版椅子的記錄。EditorID 與 Model 路徑如下；Model 路徑從 `meshes` 裡面開始：

| EditorID | Model |
|---|---|
| `ACSheenChair1024` | `any2nif\sheenchair_1024\sheenchair_1024.nif` |
| `ACSheenChair2048` | `any2nif\sheenchair_2048\sheenchair_2048.nif` |

將兩筆擺在有地板、有照明的測試 cell，左右留一點距離；兩筆都用相同旋轉與 scale 1。儲存測試 ESP 並在 MO2 啟用，再載入遊戲進該測試 cell。只安裝 ZIP 不會自動生出椅子。使用 ModForge 時同樣以 `statics[].model` 引用上表路徑，並將兩包的 meshes／textures 合併交給既有資產打包流程；目前沒有新增 `importmesh` 指令。

## 看什麼

先在同一盞光下近看椅背、坐墊與木架，再拉遠比較。1024 放左、2048 放右，截圖保留兩版位置。留意布紋比例是否一致、木紋是否錯位、法線凹凸有沒有反向、細縫是否出現黑線或明顯跳色；遠處也要看一次，因為縮小貼圖後可能才出現接縫。2048 不保證每一處都更好，兩版都需看。

繞一圈確認正面、背面與底面都存在，大小合理、椅腳朝下。固定來源的外框約寬 0.827、高 0.686、深 0.570 公尺（公司以來源頂點實算）；兩版都應保持這個比例。再走向椅背與坐墊，測試是否擋住角色、能否站上坐墊。分件凸包沿用來源材質／網格分組，椅腳間隙可能仍被填滿；若有看不見的牆，記下位置。椅子不含 Furniture 動作，不能用「可否坐下」判定成功。

請記錄遊戲版本、使用的批次資料夾、兩版是否通過，以及問題截圖（近看、遠看、碰撞位置）。兩版都正常後，再選一版作日常使用。這次只驗一般靜態模型；傳統 Skyrim 材質是近似，Sheen、variants、完整 PBR 不列為等價效果。

來源：Wayfair／Eric Chadwick，CC0；固定版本與署名 URL 隨包附上。離線顏色抽查只涵蓋可穩定取樣的面內點，不能代替上述遊戲檢查。
