# 真實模型離線試轉

← [整包說明](PACKAGE.md)

2026-09-10 公司 WSL 已跑以下來源與 production CLI。成功時會驗 NIF 三角形／位置／UV 讀回；整包本身另驗 DDS 解碼、完整 mipmap 與引用，並生成 `convex-mesh` 碰撞。這份紀錄不代表 Skyrim 畫面、材質等價或碰撞實機通過。

要比較 SheenChair 1024／2048，使用 [回家驗收](HOME-VALIDATION.md) 的專用打包命令；兩版路徑不同，可同時安裝。

## 重跑

在 model-converter 根目錄執行：

```bash
.venv-wsl/bin/python tools/smoke_real_assets.py --download
```

首次下載約 22 MB，來源與成品在忽略的 `backend/real-assets/`，不進版控。已有素材可省略 `--download`；腳本核對固定 SHA-256，既有不符檔案會失敗。來源 repo 固定在 `90d7ede14c7e280af263824604b427a1ca02cb66`，各 GLB 的雜湊在腳本 `ASSETS`。可帶這支腳本回家重新產包，無須攜帶公司絕對路徑。

| 案例 | 離線結果 |
|---|---|
| Lantern 原版 | 成功；3 shapes、5,394 三角形、4 DDS、3 凸包。 |
| Avocado 原版 | 成功；1 shape、682 三角形、3 DDS、1 凸包。 |
| LanternUV 衍生版 | 成功；原 Lantern 幾何／影像，改用 TEXCOORD_1 並套 2 倍 UV 縮放與 (0.25, 0.5) 位移；NIF UV 逐頂點核對通過。 |
| SheenChair 原版 | 成功；4 shapes、39,936 三角形、6 個實際 DDS 引用、4 凸包；跨 UV 重烘與 AO 合成。 |

Lantern／Avocado 的來源 primitive 含 TANGENT；2026-09-10 已補 any2nif 直通保留來源切線，兩個模型與下列 SheenChair／LanternUV 試轉再次通過。方向本身另以合成來源及 NIF 實際 T/B/N＋DDS 做回歸測試，沒有拿離線結果代替遊戲光影驗收。

LanternUV 是腳本建立的測試衍生檔，不是上游提供的版本。位置驗證先將 NIF render 單位除以 70.03，再對比來源 glTF；尺寸與方向仍須按用途在遊戲確認。SheenChair 的第二組 AO UV 與第一組顏色／法線 UV 已轉成每件獨立 atlas；sheen／材質 variants 不宣稱等價支援，使用來源預設材質。

## SheenChair 成品抽查

預設 1024 × 1024 烘焙，試轉腳本以每個面四個內部點，比較來源 glTF 的 UV／變換、線性 diffuse factor 與 AO strength，和 NIF 實際引用的 DDS。面積小於 8 像素平方的 UV 面不列入顏色抽查，避免把壓縮區塊與接縫誤差當成穩定採樣；所有面的三角形與位置仍逐角點核對。

| 材質 | RGB 樣本點 | 平均絕對誤差 | 第 90 百分位誤差 |
|---|---:|---:|---:|
| 布 | 35,572 | 0.0097 | 0.0281 |
| 木 | 26,200 | 0.0091 | 0.0209 |
| 金屬 | 14,268 | 0.0044 | 0.0093 |
| 標籤 | 512 | 0.0086 | 0.0204 |

RGB 範圍為 0～1；檢查門檻是平均誤差 < 0.025、第 90 百分位 < 0.06。法線／切線換算由合成模型測試驗方向，這張表只驗 diffuse，不代表遊戲打光或所有 PBR 效果一致。

真檔觸發了小面補救：xatlas 初次沒有配置布的 28 個頂點；經 NIF 半精度 UV／1024 採樣後，轉換器替布的 4,540 面、木的 451 面安排獨立小區塊。最後所有三角形都有自己的像素覆蓋，幾何不刪除；很小的區塊仍只有有限細節，低階 mipmap／遊戲畫面仍須驗收。

抽查器也做過反例驗證：在暫存副本把布的 diffuse 換成同尺寸、完整 mipmap 的全黑合法 DDS，檢查確實失敗（平均誤差 0.33435、第 90 百分位 0.88196）。原成品未修改，暫存副本已清除。

## 兩種解析度驗收包

2026-09-10 以 `tools/build_sheen_validation.py` 實跑兩版，完整成品在忽略的 `backend/home-validation/20260910-151908-610853/`。1024 花約 29 秒，Data 8.41 MB／ZIP 4.08 MB；2048 花約 53 秒，Data 22.77 MB／ZIP 8.69 MB。兩版皆 4 shapes、39,936 三角形、6 張實際引用 DDS、4 凸包；引用 DDS 尺寸逐張核對，ZIP CRC 與全部內容雜湊讀回通過，模型／貼圖路徑不同。

顏色抽查現按 DDS 的實際寬高計算像素面積。2048 的布／木／金屬／標籤分別抽查 77,608／33,856／15,360／512 點，平均誤差 0.0079／0.0059／0.0044／0.0063，第 90 百分位 0.0222／0.0132／0.0097／0.0130，全數低於原門檻。兩個解析度納入的面不同，這些數字不是相同樣本的畫質排名；仍需按 [回家驗收](HOME-VALIDATION.md) 比較實際布紋、法線與碰撞。ZIP 只含 Data 檔案，操作說明、來源署名與報告留在批次目錄。來源切線修正後再次實跑，最新批次是 `backend/home-validation/20260910-153248-865768/`，兩個 ZIP 的 SHA-256 與前一批完全相同。

## 來源與署名

[Lantern](https://github.com/KhronosGroup/glTF-Sample-Assets/blob/90d7ede14c7e280af263824604b427a1ca02cb66/Models/Lantern/README.md) 是 Microsoft／sbtron 的 CC0 木製路燈；上游另列 Frank Galligan 的 Draco 版本署名，本次取一般 GLB。

[Avocado](https://github.com/KhronosGroup/glTF-Sample-Assets/blob/90d7ede14c7e280af263824604b427a1ca02cb66/Models/Avocado/README.md) 是 Microsoft 的 CC0 手繪貼圖酪梨。

[SheenChair](https://github.com/KhronosGroup/glTF-Sample-Assets/blob/90d7ede14c7e280af263824604b427a1ca02cb66/Models/SheenChair/README.md) 是 Wayfair／Eric Chadwick 的 CC0 布椅，包含貼圖變換、第二組 UV、sheen 與 variants。
