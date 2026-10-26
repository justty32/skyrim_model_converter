# code-map — 程式碼導航 index（哪個檔負責什麼）

[common/README](README.md)｜[INDEX](../../INDEX.md)

碰原始碼前先查這張表，只讀相關領域列出的檔；動完再照維護鏈把表更新回去。寫碼慣例本身在 [conventions](conventions.md)。

## 領域表

| 領域 | 檔案 | 職責 | 測試在哪 |
|------|------|------|---------|
| NIF 讀取 | `nif2gltf/*` | NIF 標頭／block 解碼、Skyrim→glTF 軸轉、glTF 寫出、CLI | `tests/test_nif_reader.py`、`test_gltf_writer.py`、`test_cli.py` |
| NIF 寫出 | `gltf2nif/*` | glTF 讀入、BSTriShape、材質、切線、碰撞、CLI | `tests/test_gltf2nif_*.py` |
| 正向整包 | `any2nif/*` | 各格式 normalize、切分、烘焙、UV atlas、貼圖、碰撞、package 發布 | `tests/test_any2nif_*.py`、`test_package_*.py`、`test_bake_instances.py` 等 |
| 貼圖 | `tex2dds/*` | BC1／BC3 壓縮、DDS 寫出、pipeline | `tests/test_tex2dds_*.py` |
| 驗收工具 | `tools/*.py`、`tools/fetch_fbx2gltf.sh` | 真實模型試轉、SheenChair 驗收包、FBX2glTF 下載 | `tests/test_real_asset_validation.py` |

## 真相層優先序

各專案可以改自己的優先序，但必須明確。預設：

```text
code/tests > schema/examples/fixtures > code map > docs > generated
```

- 上層與下層衝突時，**以上層為準並修正下層**。
- generated（產生出來的檔、html）永遠不是唯一真相。
- 原始來源與摘要衝突時，以**原始來源**為準。
- code map 是**導航不是規格**；行為以 code/tests 為準。

## 維護鏈：程式碼 > code map > 文檔

**優先級**（衝突或時間不夠時，依序保持一致）：程式碼 > code map > 文檔。
**code map 與程式碼衝突時以程式碼為準，立刻改 code map。**

1. **修改前**：先讀本表找到相關領域，只讀清單裡的檔——不讀無關領域的檔。
2. **修改後**：新增／刪除了原始碼檔案，或某檔職責顯著改變，必須同步更新本表。
3. 原始碼裡**不加**「對應 code map」的註釋（維護成本過高）；反向查找直接 grep 本檔。
4. 迭代期間本表可暫時落後，**commit 前必須對齊**（見 [feature-dev](../feature-dev/README.md)）。
