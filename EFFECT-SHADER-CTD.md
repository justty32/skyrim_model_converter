# BSEffectShaderProperty CTD 調查（2026-09-14）

## 結論與修正

`gltf2nif` 原本把 `BSLightingShaderProperty` 的 16-byte 前綴照搬到
`BSEffectShaderProperty`。Skyrim SSE 的 lighting property 在 `NiObjectNET` 前有專屬
`Shader Type`，effect property 沒有；它應直接寫 `Name` (i32)、
`Num Extra Data List` (u32)、`Controller` (i32)，共 12 bytes。

舊 bytes 開頭是 `0, 0xFFFFFFFF, 0, -1`。引擎按 effect 佈局讀取時，
`Num Extra Data List` 因此成為 `0xFFFFFFFF`；這與 crash log 在
`BSEffectShaderProperty` / `BSEffectShaderMaterial` 處發生大型 `memcpy` 越界一致。
修正後前綴是 `-1, 0, -1`，後續 shader fields 全部前移 4 bytes；
block size 由內容重新計算，沒有硬編數值。

## 格式證據

- NifTools `nif.xml` develop 固定在 commit
  [`292bb9403cbf4052c58d66e80906b6bde1700779`](https://github.com/niftools/nifxml/blob/292bb9403cbf4052c58d66e80906b6bde1700779/nif.xml#L3363-L3371)：
  `NiObjectNET` 依序定義 Name、extra-data list count/list、Controller。
- 同一 schema 的
  [`BSEffectShaderProperty`](https://github.com/niftools/nifxml/blob/292bb9403cbf4052c58d66e80906b6bde1700779/nif.xml#L6651-L6682)
  直接繼承 `BSShaderProperty`，沒有 lighting-only `Shader Type`。
- 工作區真實 Campfire NIF
  `../ModForge/examples/assets/skilltree/Meshes/campfire/_camp_intperkline01.nif`
  的 BSVersion 83 effect block 亦是 12-byte prefix：Name `-1`、count `0`、
  Controller ref，Shader Flags 1 緊接在 offset 12。這是輔助取樣，不冒充
  Skyrim SSE BSVersion 100 實機驗收。

## 回歸與邊界

`tests/test_gltf2nif_effect.py::test_effect_property_uses_12_byte_niobjectnet_prefix`
僅借用通用 header parser 定位 block，effect payload 則直接用
`struct.unpack_from("<iIi", ...)` 獨立解碼產出 bytes。
修正前實跑失敗為 `(0, 4294967295, 0) != (-1, 0, -1)`；因此不是
writer 與舊測試共用錯誤 offset 後的自我印證。同檔佈局測試亦直接驗證
effect block 尾端等於 source string 後 48 bytes，防止後續欄位或 block size 錯位。

離線測試可證明欄位佈局、block size 與其他 converter 契約，但不能證明
兩個 DS 光柱在 Skyrim 中已不會 CTD，也不能驗收實際 blending/視覺。
需在有 Skyrim 的環境重產含 effect 材質的 NIF，再進入原 cell 驗收。
