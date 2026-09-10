# Asset converter 續行

Done when: 一般模型、貼圖與碰撞能用一條命令轉成完整 Data 目錄；模型引用的 DDS 全數存在且可解碼；失敗保留既有成品；glTF/GLB、OBJ、FBX 等路徑有離線驗證，CLI／測試／文件對齊。遊戲內外觀與站立驗收另行記錄，不用離線測試冒充。

## 現役狀態

2026-09-10 高面數模型切分修正完成：NIF 三角形數也是 16-bit，舊流程只查頂點數，65,536 面寫出後會讀回 0 面。any2nif 現在同時按頂點與面數切分，裸 writer 超限明確拒絕。33,489 頂點／66,248 面的共用頂點網格驗實際 NIF 逐角點、材質與切線；33,153 頂點／65,536 面的完整 package 驗 65,535＋1 面、DDS 解碼與單一來源凸包的邊界。恢復舊切分的反例確實變紅，完整 suite **478 passed**。SheenChair 各分件低於門檻，最新驗收批次仍為下列 160024 批，不需因此換包。

2026-09-10 生成切線的鏡射 UV 接縫修正完成：共用頂點按 face 正反手性拆開，先生成 TANGENT 再做 NIF 頂點上限切分；來源與 atlas 皆以逐角點方向重烘，負縮放不再額外翻 normal Y。實際 NIF／DDS 回歸包含鏡射 U/V、node／全域反射、共用接縫與 65,538 頂點；完整 suite **470 passed**，四個真實模型通過。真實 SheenChair 曾抓到舊算法在小面邊中點的 basis 抵消，修正後兩種尺寸都通過。

最新驗收批次為 `backend/home-validation/20260910-160024-009333/`：1024／2048 分別約 31／53 秒，ZIP 4,078,095／8,686,369 bytes。兩版皆 4 shapes、39,936 三角形、6 引用 DDS、4 凸包；幾何、尺寸、diffuse、manifest、ZIP CRC 與逐檔雜湊通過。新包包含切線／normal 方向修正，雜湊不同於舊批次；請攜帶最新整批或照 [HOME-VALIDATION](HOME-VALIDATION.md) 重建。遊戲內驗收仍待回家。

2026-09-10 直通貼圖流程的來源 TANGENT 保留已完成：any2nif 啟用 reader opt-in，Mesh／切分／來源軸向／負縮放／writer 一路保留方向與 W；烘焙沿用相同驗證，缺 NORMAL 時忽略來源切線。無 NORMAL 的 atlas 案例另以刻意不同的有效 TANGENT，比較移除前後整包完全相同。22 個 normal-frame tests 直接解碼 NIF T/B/N 與 DDS，涵蓋旋轉、鏡射、不等比 node scale 及 `--scale -2 --up-axis z`，直通保持原 UV 與 8×8 normal DDS。13 個 tangent contracts 驗無效資料保留舊包、65,538 頂點的實際 NIF 切分，以及裸 reader 預設仍忽略來源 tangent；強制關閉 opt-in 的反例使 authored-normal 測試失敗。四個真實模型試轉通過，SheenChair diffuse 結果不變。

2026-09-10 SheenChair 1024／2048 回家驗收包完成：`tools/build_sheen_validation.py` 用不同資產路徑產出兩版，逐檔驗 manifest、實際 NIF 幾何／DDS 尺寸／diffuse 與 4 凸包，再驗 ZIP CRC／雜湊。成品在忽略的 `backend/home-validation/20260910-153248-865768/`；ZIP 約 4.08／8.69 MB，整批附署名、操作說明、報告與 SHA256SUMS。兩版檔案路徑無交集；不含 ESP、沒有部署。本輪用修正後程式重建，兩個 ZIP 雜湊與前一批相同。當輪完整 suite 437 passed，操作見 [HOME-VALIDATION](HOME-VALIDATION.md)，遊戲外觀與碰撞仍待回家。

2026-09-10 共用 UV 的 normal 變換已完成：旋轉、鏡射、不等比縮放自動重烘；零縮放明確拒絕且保留舊包。15 組實際 NIF／DDS 法線方向測試通過（含來源提供／未提供 tangent）；shared 測試移除 AO 干擾，恢復舊路由的反例讓全部 10 組 shared cases 失敗。SheenChair 等四個真實模型試轉再次通過。

2026-09-10 共用 UV 的 sampler 相容性已完成：邊緣延伸、鏡射重複與 nearest 放大採樣自動路由貼圖重烘；無效 sampler 索引／wrap／filter 明確拒絕並保留舊成品。新增 11 個實際 DDS／NIF 測試，一般 REPEAT＋linear 保持原 UV。完整 suite 與四個真實模型案例通過，SheenChair 顏色誤差與下列紀錄一致。

2026-09-10 跨 UV 重烘已完成：不同槽的座標／變換自動排成逐 primitive／實例獨立 atlas，支援 wrap／採樣、線性 diffuse × AO、normal 切線換算及負縮放；`--bake-size` 64～4096，預設 1024。所有面皆檢查像素覆蓋，極細面配置獨立小區塊；沒有安全空間就要求提高尺寸並保留舊成品。新增 xatlas 0.0.11，僅裝專案 `.venv-wsl`。

本輪 Done when 已達成：SheenChair 原版成功產出 4 shapes、39,936 三角形、6 個實際 DDS 引用、4 凸包；四材質 diffuse 抽查平均誤差 0.0044～0.0097，黑圖反例確實被拒。最終真實模型腳本四案例全數通過；細節與固定來源在 [REAL-ASSETS](REAL-ASSETS.md)。normal 方向、紅色大面／藍色微小島、重複擺放、非 triangle 略過與錯誤保護另有合成測試。

4096 四槽合成模型完整 CLI 試轉 exit 0，約 89 秒、峰值 RSS 1,595,832 KiB（約 1.52 GiB）；烘焙逐槽／分塊處理，不把所有槽的浮點 atlas 同時常駐。來源／成品留忽略的 `backend/bake-memory/`。

2026-09-10 真實模型與 UV 續行已完成：整包支援替代 UV、共用 KHR_texture_transform 的位移／旋轉／縮放／texCoord 覆寫，烘進單一 NIF UV。當時帶 normal 時限正值等比縮放與位移，不同槽的 UV 對應也仍拒絕；這些限制現已由重烘補上，normal 零縮放仍拒絕。新增 25 個測試，實際讀回 NIF 座標、normal DDS 引用並驗失敗保護。

公開模型試轉與來源見 [REAL-ASSETS.md](REAL-ASSETS.md)：Lantern、Avocado 原版與 LanternUV 衍生版通過；SheenChair 原先因混合 UV 拒絕，現已成功試轉。`tools/smoke_real_assets.py` 固定來源版本與 SHA-256，素材／產物留忽略的 `backend/real-assets/`。整支腳本已實跑通過，包含三角形／位置／UV 讀回；尚無遊戲畫面驗收。

2026-09-10 分件碰撞已完成：`--collision convex-mesh` 依正規化後的 node × primitive 各自建立凸包。離線驗證分件門框保留空隙、重複擺放與尺度軸向、多凸包 NIF 引用，以及 65,538 頂點完整整包的渲染切分不改碰撞分組。單一 primitive 的凹洞仍不會自動拆分。

2026-09-10 續行已完成：`normalTexture.scale` 改為烘進法線貼圖，支援歸零、減弱、加強與負值；非有限值仍拒絕。整包測試實際讀回 NIF 引用的 DDS，確認共用 diffuse 不受影響、重跑一致、壞設定保留既有整包。

2026-09-10 公司 WSL：使用者選定的一鍵一般模型流程已完成離線實作，使用方式見 [PACKAGE.md](PACKAGE.md)。實機外觀／尺寸方向／碰撞站立仍待回家驗收，記在母 repo 的 [WAIT_USER](../../wait-user/feature-runtime.md#asset-converter-一鍵靜態模型整包2026-09-10)。

本次獨立 WSL 環境在 `.venv-wsl`，由既有 uv 建立；FBX2glTF 在忽略的 `tools/bin/`。Windows `.venv` 保留。使用者已授權專案內安裝依賴與 push。

驗證：`.venv-wsl/bin/python -m pytest -q --disable-warnings` → **478 passed、零 skipped**；包含真 FBX2glTF、跨格式完整 Data 目錄、所有 DDS 槽位、65535 頂點切分、負縮放、錯誤資料與發布／回復。仍有既有套件的 deprecation warnings。

跨 repo darksouls-port live contract 再次嘗試：`.venv-wsl/bin/python -m unittest discover -s ../darksouls-port/tests -p test_model_converter_contract.py -v`，其 `initial_state.py` import 仍缺少 soulstruct，測試未能啟動。本輪 writer 新增可選 authored tangents，但裸 reader 預設不啟用；既有單／雙 shape 的 NIF SHA-256 基準與新 opt-in 隔離測試通過。未改 darksouls-port，不能把本 repo 測試當成跨 repo live contract 已通過。

## 後續方向

跨 UV 重烘已完成；共用 UV 的 normal 旋轉／鏡射／不等比縮放也已路由重烘，零縮放仍拒絕。共用 UV 的非 REPEAT／nearest sampler 已路由重烘。True PBR、反向貼圖、蒙皮／動畫、凹形碰撞自動拆分與 ModForge spec 的黑盒接線仍是另外的工作。本輪不宣稱這些已完成；舊 idea 的入口已改標目前實作與歷史方案。
