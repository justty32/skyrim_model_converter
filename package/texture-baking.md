# 不同貼圖使用不同 UV

← [整包貼圖與材質](README.md)｜[整包操作](../PACKAGE.md)


`--package` 遇到各貼圖的座標集／變換不同、帶 AO 陰影圖、normal 的 UV 有旋轉／鏡射／不等比縮放，或 sampler 使用邊緣延伸／鏡射重複／nearest 時，會自動用 [xatlas](https://github.com/mworchel/xatlas-python) 排出沒有重疊的共用 UV，再把各張圖按原本 UV 重採樣。每個來源 primitive／擺放實例各有自己的材質與貼圖；UV 接縫增加頂點，但保持三角形的位置、順序、法線與頂點色。node 的位移／旋轉／縮放先套進幾何，避免不同擺放共用錯誤的法線烘焙。

預設每張烘焙圖是 1024 × 1024；想保留更多布紋細節可提高尺寸：

```bash
python -m any2nif SheenChair.glb output/SheenChair --package --collision convex-mesh --bake-size 2048
```

`--bake-size` 只供整包模式使用，可選 64、128、256、512、1024、2048、4096。較大尺寸增加時間、記憶體與成品大小；它不會增加來源圖本來沒有的細節，也不改共用 UV 直通流程的原圖大小。重烘是有限解析度近似，密集重複花紋與低階 mipmap 仍可能模糊或產生接縫。小到沒有像素中心、或 NIF 半精度 UV 壓縮後塌縮的面，會自動取得獨立小區塊，從自己的來源 UV 取色；最後檢查每個面都有像素覆蓋。若空間不足，會要求提高 `--bake-size` 並保留舊成品。

採樣支援 REPEAT、MIRRORED_REPEAT、CLAMP_TO_EDGE，以及 nearest／bilinear；共用 UV 的非預設 sampler 也會自動重烘，無效 sampler 索引／wrap／filter 會拒絕。RGB 色圖在線性空間取樣，normal、roughness、AO 等資料圖不做 sRGB 轉換。烘焙以來源最高解析度取樣，未模擬視角相關的來源 mip／anisotropic 濾波。Atlas 預留外圈供小區塊補救，未覆蓋區域補最近的邊緣顏色，再交給既有 DDS 完整 mipmap 流程。4096 的四槽合成模型完整試轉約 89 秒、峰值記憶體約 1.52 GiB（本次 WSL 實測，非所有模型的上限）。

AO 使用紅色通道與 `strength`，乘進線性 diffuse，之後再套 diffuse RGB factor；alpha 不乘 AO。這是傳統 Skyrim 的陰影近似，會讓直接光照也變暗，並非 glTF 的環境光遮蔽等價實作。normal 會先套強度，再按原 UV 與新 UV 的切線方向換算；有來源 tangent 時保留其方向與 handedness，再依 UV 變換換算；沒有來源 tangent 時，從 UV 的兩個方向求切線與正反手性，包含鏡射 UV；生成方式仍是平滑近似，並非 MikkTSpace。normal 變換需要可逆的 UV 縮放。超過 NIF 頂點／三角形上限時保留切分前已生成的切線方向。負 `--scale` 的鏡射由 NIF 切線正反手性處理。Sheen 與材質 variants 仍不轉成 Skyrim 對等效果，使用來源預設材質。

公開真實模型的固定版本、試轉結果與可重跑指令見 [REAL-ASSETS.md](../REAL-ASSETS.md)。

