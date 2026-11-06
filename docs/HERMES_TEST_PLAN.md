# Hermes 測試計畫與驗收報告模板

每完成一個階段，由 Claude 交付程式碼後在**真實 Hermes 環境**執行以下測試，並把結果填入本文件最下方的報告模板（或複製成 `docs/reports/phase-N.md`）。

> 所有指令皆依 Hermes 官方文件（`website/docs/developer-guide/plugins/`）確認過；若你的 Hermes 版本輸出不同，請把實際輸出貼進報告。

## 0. 環境資訊（每次測試先記錄）

```bash
hermes --version
```

```bash
python3 --version
```

```bash
node --version
```

## 測試 1：Plugin 安裝（第一階段起）

用乾淨的 git checkout 測試（與 catalog CI 相同，避免掃描到 `node_modules`）：

```bash
git clone <repo-url> ~/.hermes/plugins/papercraft-3d
```

```bash
hermes plugins validate ~/.hermes/plugins/papercraft-3d --install-deps
```

```bash
hermes plugins doctor ~/.hermes/plugins/papercraft-3d --ci
```

```bash
hermes plugins enable papercraft-3d
```

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

確認：

- [ ] `validate`：manifest fields / requires_hermes / config schema / declared tools / security scan 全部通過，記錄所有 warning
- [ ] `doctor --ci` 結束碼為 0
- [ ] `plugins list` 顯示 `papercraft-3d v0.1.0 (6 tools)`
- [ ] 啟動 `hermes` 後輸入 `/plugins`，看到 6 個 `papercraft_*` 工具

第一階段的工具呼叫行為測試（在 Hermes 對話中）：

| 輸入 | 預期 |
|---|---|
| 「用 papercraft_analyze 分析 `<一張 PNG 路徑>`」 | `ok:false`、`code:not_implemented`，`details.validated_image` 有正確寬高與格式 |
| 「用 papercraft_analyze 分析 `/etc/hosts`」 | `code:image_format` |
| 「用 papercraft_segment 處理 project `abc`」 | `code:project_not_found` |
| 任一工具 | Hermes **不可**對使用者宣稱步驟已完成 |

## 測試 2：圖片分析（第二階段）

前置：外掛已更新到 v0.2.0（`git -C ~/.hermes/plugins/papercraft-3d pull`），Hermes 已設定可看圖的模型（`hermes model`）。

在 Hermes 對話中（附上一張紙雕圖）：

> 用 papercraft_analyze 分析這張圖片，保留原始構圖，柔和午後光線，最多 5 層，然後把 needsReview 的項目列給我。

檢查專案資料夾（路徑在工具回傳的 `project_dir`）：

```powershell
Get-Content "<project_dir>\analysis.json"
```

```powershell
Get-Content "<project_dir>\logs\process.jsonl"
```

另外測試失敗路徑：暫時把模型切成不支援圖片的模型，或拔網路後呼叫一次，應回傳 `model_unavailable` 或 `timeout`，且**不能**宣稱分析完成；之後用 `project_id` 重新分析同一個專案，應成功且不會多出第二個專案。

- [ ] 輸入一張圖層清楚的紙雕圖，產生有效的 `analysis.json`（通過 schema）
- [ ] 圖層順序合理（天空/背景 order 最小）
- [ ] 調色盤 HEX 與畫面相符
- [ ] 有 `confidence` 與 `needsReview`
- [ ] 回應中不會宣稱已經產生尚未存在的資產

## 測試 2b：圖片分層（第三階段）

前置：外掛更新到 v0.3.0 後，Hermes 會要求安裝新依賴（scipy、scikit-image），請同意。

```powershell
git -C "$env:USERPROFILE\.hermes\plugins\papercraft-3d" pull
```

在 Hermes 對話中，對已分析的專案：

> 用 papercraft_segment 把專案 <project_id> 分層，完成後告訴我每層的覆蓋率、補全比例、信心值，以及檢查用圖片的路徑。

- [ ] `layers.json` 存在，`mode` 為 `layered`
- [ ] `assets/` 中每層是**全尺寸透明 PNG**；除最遠層外都有透明區（不是原圖複製）
- [ ] 打開 `review/overlay.png`：顏色分區是否符合各物件（橋、白狐、木屋、櫻花框景…）
- [ ] 打開 `review/marks.png`：數字是否清楚可讀
- [ ] `source/` 原圖未被修改

修正測試：用小畫家等工具，畫一張與原圖同尺寸的黑底圖，把某個分錯的區域塗白，然後：

> 用 papercraft_segment 對專案 <project_id> 套用 manual_masks：{"<layer_id>": "<遮罩路徑>"}

- [ ] 不會再呼叫模型（很快完成）
- [ ] 該層 provenance 為 `manual`，`layers.json` 有 `manualEdits`

失敗測試：關閉網路後呼叫一次 → 應回傳錯誤；加上 `allow_preview_fallback: true` → 應回傳 `mode: preview` 並明確說明「示意預覽」。

## 測試 3：3D 預覽（第三、四階段）

```bash
cd ~/.hermes/plugins/papercraft-3d/viewer && npm install && npm run dev
```

- [ ] 所有圖層資產載入、無嚴重透明排序錯誤
- [ ] 旋轉（有限角度）、縮放、圖層展開正常
- [ ] 光源強度、陰影開關、日/夜預設確實改變畫面
- [ ] 手機（實機或瀏覽器 DevTools 裝置模擬）可操作、無水平捲動

## 測試 4：企劃書生成（第五階段）

- [ ] 含 A–F 全部章節
- [ ] 光源、材質、深度數值與 `scene.json` 一致（可用 `jq` 比對）
- [ ] 所有檔案路徑存在、步驟可重現

## 測試 5：Agent 修改（第六階段）

輸入：「把場景改成夜間，使用冷色月光，降低環境亮度。」

- [ ] Hermes 呼叫 `papercraft_update_scene`，patch 通過驗證
- [ ] `scene.json` 的 `generator.revision` +1，`history/` 有前一版
- [ ] Viewer 重新載入後看得到差異
- [ ] `DESIGN_SPEC.md` 光影章節同步更新
- [ ] `source/` 與 `assets/` 的檔案 sha256 不變

## 測試 6：重新部署（第七階段）

在乾淨環境（另一台機器或新容器）：

```bash
cd <exported-project> && npm install && npm run build && npx vite preview
```

- [ ] 成功啟動並顯示場景
- [ ] `grep -r "/home/\|/Users/\|C:\\\\" <exported-project>` 無結果（無絕對路徑）

---

## 驗收報告模板

```markdown
# PaperCraft 3D 測試報告 — 第 N 階段

- 日期：
- Commit SHA：
- Hermes 版本：            Python：            Node：
- 測試者：

| 測試 | 結果 (✅/❌/⚠️) | 備註 / 實際輸出 |
|---|---|---|
| 1. 安裝與 validate |  |  |
| 1. doctor --ci |  |  |
| 1. 工具註冊 (6) |  |  |
| 2. 圖片分析 |  |  |
| 3. 3D 預覽 |  |  |
| 4. 企劃書一致性 |  |  |
| 5. Agent 修改 |  |  |
| 6. 重新部署 |  |  |

## validate / doctor 原始輸出

（貼上）

## 發現的問題

1.

## 測試圖片

| 圖片 | 來源 | 流程是否完成 | 備註 |
|---|---|---|---|
```
