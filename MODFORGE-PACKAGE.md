# 單模型 → ModForge 完整包

這是 opt-in 的單一靜態模型流程：自包含 GLB → 現有 `any2nif --package --collision box`
→ 單一 `StaticSpec` → 現有 ModForge `package`。不需要 Blender，不部署到遊戲。
`any2nif` 原有用法與 ModForge 的 spec／package 行為不變；`modelSource`、`importmesh`、
多模型／蒙皮／動畫與 True PBR 尚未接線。

## 重建合成箱子

需要本專案既有 Python 依賴，以及同層 ModForge 的 .NET 10 SDK／既有套件。
先在 ModForge 依[它的建置說明](../ModForge/workflows/dev-env.md)建置目前原始碼，
不可拿過時 DLL 冒充目前程式；再回到本專案執行：

```bash
.venv-wsl/bin/python tools/build_modforge_box.py backend/modforge-validation/box.glb
.venv-wsl/bin/python -m modforge_package \
  backend/modforge-validation/box.glb backend/modforge-validation/SyntheticBox \
  --modforge-dll ../ModForge/src/ModForge.Cli/bin/Debug/net10.0/ModForge.Cli.dll
```

原生 Windows 改用自己的 Python／DLL 路徑。WSL 使用 Windows SDK 時另加
`--dotnet '/mnt/c/Program Files/dotnet/dotnet.exe'`；工具只為該行程的檔案參數呼叫
`wslpath -w`。來源與輸出都放 Windows 可讀的掛載磁碟；明示的錯誤工具路徑會失敗，
不會改找另一個 SDK。工具不安裝依賴、不改全域環境。

預設產生 `SyntheticBox.esp`、`Meshes/any2nif/synthetic_box/synthetic_box.nif`、
實際被 NIF 引用的 `Textures/any2nif/synthetic_box/*.dds`，以及完成包 manifest。
`--plugin`、`--editor-id`、`--asset-name` 可另命名；完整參數見 `python -m modforge_package --help`。
箱子由本專案自行生成，含嵌入橘色 PNG、非對稱尺寸與偏移，不含遊戲或第三方 mod 素材。
GLB／ESP／NIF／DDS 成品留忽略的 `backend/`，不加入 Git。

## 引用與安全契約

- 本切片只收 GLB；buffer／image 必須嵌入 GLB 或使用 data URI，不讀外部相對檔案或 URL。
  來源、DLL、輸出路徑與其父層拒絕 symlink／reparse point，檢查後正規化 `..`。
  輸出不能包含來源或工具 DLL。
- converter manifest 必須精確涵蓋所有資產 bytes，所有路徑為安全相對路徑；本切片的
  permitted Data folders 僅 `Meshes`、`Textures`，拒絕其他樹、drive／UNC／ADS、`..`、
  大小寫碰撞與 Windows 保留名稱。最終包另含 ESP 及明列的 JSON metadata。
- converter 的 `mesh` 包含 `meshes/`；轉為 `StaticSpec.Model` 時移除此首段，因為
  MODL 相對於模型的 permitted Data folder `Meshes`。NIF 貼圖引用保留 `textures\` 前綴。
  ModForge 將首層資料夾拼法正規化成 `Meshes`／`Textures`，工具逐檔比對複製後 bytes。
- 真實 ESP 由 ModForge 既有 `catalog build` 的 Mutagen overlay 讀回，再以
  `catalog export-json` 驗唯一 major record、Static 類型、EditorID 與模型路徑。
  暫存 catalog DB 僅本次工作使用，不動 mod-library 的資料庫。
- NIF 用既有 reader 讀回幾何與貼圖引用，DDS 驗 BC1／BC3、完整 mipmaps 與實際解碼。
  box 碰撞追 root → collision object → rigid body → convex shape，驗八角點與幾何邊界；
  薄軸沿用 converter 最少 5 公分總厚度。沒有引用的 DDS 不為湊數生成。

`modforge-model-package.json` 是完成包與 ownership 的權威：記 source SHA-256、
可重建的單 StaticSpec、真 ESP 讀回、碰撞邊界與全部其他成品雜湊。
`converter-provenance.json` 是原始 converter manifest 的來源證據，仍保留 producer 的
小寫 Data 路徑；在最後包對照它時要將首段 `meshes/textures` 映成 `Meshes/Textures`。
它不能當最後包的 ownership marker，也不能將完成包直接當成可重用的 converter 輸出。

## 發布與失敗

整個轉換／組包／讀回先在目的地旁的新暫存目錄完成。只有全部通過才發布；
缺工具、轉換失敗、漏貼圖、壞 manifest、錯誤 ESP 讀回均非零退出且保留原目標 bytes。
既有目標只有自有完成包 manifest、全部雜湊符合且無額外檔案時才可替換；手改或未知目錄拒絕覆蓋。

替換分成舊包移到旁邊 backup、新包改名到目標兩步；失敗嘗試還原，還原也失敗時
保留 backup 並報實際位置。成功發布後 backup／scratch 清理失敗只警告，不把成功回報為失敗。
這是可回復的目錄發布，不保證斷電、行程被殺或其他行程同時改檔時的全域原子性。

## 離線驗證

```bash
MODFORGE_DLL="$(realpath ../ModForge/src/ModForge.Cli/bin/Debug/net10.0/ModForge.Cli.dll)" \
  .venv-wsl/bin/python -m pytest -q tests/test_modforge_package.py
```

WSL／Windows SDK 另設 `MODFORGE_DOTNET`。未提供已建置 DLL 時明確 skip live contract；
完整本機驗收需帶它實跑。測試涵蓋真 GLB／converter／ModForge／Mutagen／NIF／DDS，
重建一致性、失敗保留舊包、無權覆蓋／路徑拒絕，以及發布／還原失敗反例。
遊戲內外觀、尺寸方向、碰撞站立仍未驗；這份離線包不代表實機通過。
