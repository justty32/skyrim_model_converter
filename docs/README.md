# 架構與後端導覽

← [使用入口](../README.md)

<!-- wf-nav -->
- [NIF 雙向後端](nif-backends.md)：nif2gltf 與 gltf2nif 的模組、驗證與座標。
- [整包與貼圖後端](package-backends.md)：any2nif 與 tex2dds 的模組及測試責任。

## 關係定位（不重造輪子）

| 既有資產 | 方向 | 角色 |
|---|---|---|
| [model-porting/](../../ModForge/workflows/idea/asset-pipelines/model-porting/README.md)（01–10） | **正向** 外部→nif | 正向 deep-dive 的真相在那裡；本工具正向部分**沿用**其工具決策（NifTools 靜態 / PyNifly 蒙皮 / Compressonator dds），不複製內容 |
| [gemini-research/.../nif-gltf-conversion.md](../../ModForge/sub_projs/gemini-research/worldspace-editor/nif-gltf-conversion.md) | **反向** nif→glTF | 反向工具調查的**唯一**現有來源（已帶幻覺更正 banner）；工具表見 [tool-survey.md](../tool-survey.md)，原稿留存 |
| [godot-worldspace-editor](../../godot-worldspace-editor/README.md) | 消費者 | nif→glTF 代理是它物件編輯的前置依賴 |
| ModForge `package`（`StaticSpec.Model`） | 消費者 | 正向產出的 `.nif`+`.dds` 由它打包進 Meshes/Textures 樹 |

---

## 外部工具與格式調查

「實作」見 [NIF 雙向後端](nif-backends.md)：自寫純 Python，不依賴外部 NIF 工具。外部工具盤點（NifSkope fork / PyNifly / Outfit Studio / ck-cmd / Compressonator + 幻覺黑名單）、跨平台 dual-backend 架構難點、紋理 round-trip 踩坑 → 全留 [tool-survey.md](../tool-survey.md) 作 **MVP 後**選型參考。

---

## Scope

**MVP：vanilla `.nif` → glTF 批量轉換**——靜態 mesh、Linux 原生、**跳紋理用平色 proxy**，輸出餵 [worldspace editor](../../godot-worldspace-editor/README.md) 當物件代理。
外部工具的「無已驗證批量 nif→glTF」限制見工具調查；本工具的靜態轉換由純 Python 後端提供。

**完整目標（MVP 後）**：`.nif`+紋理 ↔ glTF/Godot ↔ FBX/OBJ 全矩陣雙向，含紋理重映射與蒙皮（蒙皮走 Windows/PyNifly 後端）。
