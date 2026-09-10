# 真實模型離線試轉

← [整包說明](PACKAGE.md)

2026-09-10 公司 WSL 已跑以下來源與 production CLI。成功時會驗 NIF 三角形／位置／UV 讀回；整包本身另驗 DDS 解碼、完整 mipmap 與引用，並生成 `convex-mesh` 碰撞。這份紀錄不代表 Skyrim 畫面、材質等價或碰撞實機通過。

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
| SheenChair 原版 | 預期 exit 2；材質各槽的 UV 不同，沒有發布整包。 |

LanternUV 是腳本建立的測試衍生檔，不是上游提供的版本。位置驗證先將 NIF render 單位除以 70.03，再對比來源 glTF；尺寸與方向仍須按用途在遊戲確認。SheenChair 另有 sheen／材質 variants，這次不宣稱已支援；它的第二組 AO UV 與其他槽的第一組 UV 顯示了單 UV 輸出的剩餘限制。

## 來源與署名

[Lantern](https://github.com/KhronosGroup/glTF-Sample-Assets/blob/90d7ede14c7e280af263824604b427a1ca02cb66/Models/Lantern/README.md) 是 Microsoft／sbtron 的 CC0 木製路燈；上游另列 Frank Galligan 的 Draco 版本署名，本次取一般 GLB。

[Avocado](https://github.com/KhronosGroup/glTF-Sample-Assets/blob/90d7ede14c7e280af263824604b427a1ca02cb66/Models/Avocado/README.md) 是 Microsoft 的 CC0 手繪貼圖酪梨。

[SheenChair](https://github.com/KhronosGroup/glTF-Sample-Assets/blob/90d7ede14c7e280af263824604b427a1ca02cb66/Models/SheenChair/README.md) 是 Wayfair／Eric Chadwick 的 CC0 布椅，包含貼圖變換、第二組 UV、sheen 與 variants。
