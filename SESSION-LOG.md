# Asset converter 續行

Done when: 下列待驗項有實機／真檔結果，後續方向有另行授權與完成證據；不以離線測試代替遊戲驗收。

## 現役狀態

- 一般靜態整包仍待遊戲內外觀、尺寸方向與碰撞站立驗收，見母 repo [待驗項](../../wait-user/feature-runtime.md#asset-converter-一鍵靜態模型整包2026-09-10)；使用方式見 [PACKAGE.md](PACKAGE.md)。
- SheenChair 1024／2048 待遊戲比較；攜帶 `backend/home-validation/20260910-160024-009333/` 整批或依 [HOME-VALIDATION.md](HOME-VALIDATION.md) 重建。ZIP 未進 Git，僅 pull 不會取得。Lantern／Avocado／LanternUV 的光影與 UV 實機驗收見 [REAL-ASSETS.md](REAL-ASSETS.md)。
- MASK 透明裁切與頂點色仍待實機驗收；需重轉帶錯誤旗標的模型。SheenChair 六個來源材質為 OPAQUE、四個 primitive 無頂點色，可沿用上述批次。
- 兩個 DS 光柱需重產含 effect 材質的 NIF，再進原 cell 驗收；本輪不改 consumer skip/materials。格式證據與實機邊界見 [EFFECT-SHADER-CTD.md](EFFECT-SHADER-CTD.md)。
- LE 真檔取樣與裸 gltf2nif 的外觀／碰撞實機驗收仍待主力機，見 [README 的 Open](README.md#open)。

## 後續方向

True PBR、反向貼圖、蒙皮／動畫、凹形碰撞自動拆分與 ModForge spec 的黑盒接線仍是另外的工作，不宣稱完成。
