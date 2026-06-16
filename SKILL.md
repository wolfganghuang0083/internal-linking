---
name: internal-linking
description: 把同一個內容叢集裡「已上線」的文章用貼題、帶關鍵字的內鏈織成主題網。鐵則：只連已上線頁(避免 404)、冪等可重跑、寫入走確定性程式(不交給 LLM)、錨點要貼題、錨句扣 FACTS 不編造、預設 hub-spoke 拓樸。當要「補某叢集的內鏈/發完新文把它織進叢集/批次上線後跑全叢集互連 pass」時使用。是 content-pipeline 第⑥步「回頭把叢集互鏈」的實作，品牌無關、可套任何網站/客戶。
---

# Cluster Internal-Linking SOP（叢集內鏈 SOP）

把「內容地圖宣告要連的內鏈」對齊到「站上實際存在的連結」。內容地圖是真相源，本 skill 負責：把缺的補上、只連已上線的、可安全重跑。

本 skill 與站台無關。所有站台/帳號等可變值集中在腳本頂端的 CONFIG 區塊、argparse 參數或環境變數；機密一律走環境變數，絕不寫進檔案。參考實作以 WordPress REST API 為例，腳本註解標明可換 CMS 的位置。

## 檔案
- `scripts/interlink_apply.py` — 確定性 CMS I/O（算矩陣 / 套用 / 驗證）。**所有寫入只走這裡，不交給 LLM。**
- `scripts/interlink.js` — 工作流：每條缺連結派一個 agent 找貼題錨點 + 寫 FACTS 安全錨句 → 產 `plan.json`。
- `templates/FACTS.template.md` — 錨句護欄模板。複製成你自己的 FACTS（或指向 content-pipeline 既有的 FACTS），把 `<PLACEHOLDER>` 填好。

## 設定（CONFIG / 環境變數 / 參數）
憑證（環境變數，**不寫檔**）：
- `CMS_USER` — CMS 使用者 / 應用程式密碼帳號
- `CMS_APP_PW` — CMS 應用程式密碼或 API token

站台與路徑：
- `--base https://<your-site>`（或 `CMS_BASE` 環境變數）— 站台 base URL
- 工作流固定讀寫路徑，可用環境變數覆寫：`INTERLINK_MISSING`、`INTERLINK_PLAN`、`INTERLINK_FACTS`、`INTERLINK_DUMPDIR`

## 開工前
1. 先 Read 你的 `FACTS.md`（錨句不得違反防編造鐵則）。沒有就從 `templates/FACTS.template.md` 複製一份填好。
2. 從內容地圖取要處理的叢集 → 列出該叢集所有 post id；決定 hub（樞紐主文）。
3. 確認憑證：`CMS_USER`、`CMS_APP_PW` 已 export（走環境變數，不寫檔）。

## 鐵則
- **只連已上線（status=publish）**。排程/草稿文一律跳過——連了會 404。它們上線後再重跑一輪。
- **錨點要貼題**：連結插在主題相關的段落，帶關鍵字的描述性錨文字（不可「點這裡」）。不相關就不連，不在 footer 硬塞「相關文章」。
- **冪等**：來源文已含目標 slug 就跳過。可安全重跑。
- **錨句扣 FACTS**：不報價、不編數字、不編客戶/案例名、不指名競品、CTA 軟性。
- **寫入走確定性程式**：LLM 只決定「錨點在哪」與「錨句講什麼」，實際 read / insert / PUT / verify 由 `interlink_apply.py` 做。

## 連結拓樸（預設 hub-spoke）
- `--hub <id>`：hub ↔ 每個 spoke（雙向）；spoke → hub（必連）。
- `--mesh`：已上線文全互連（小叢集適用）。
- `--cta-path <path>`：檢查每篇是否都有連到一個共用轉換頁（例：`contact` / `book` / `demo`），缺的補。
- 不傳 `--hub` 也不傳 `--mesh` → 預設全互連（小叢集）。
- 跨叢集只走手動指定的橋接，不自動。

## SOP 三步

### ① 算矩陣（確定性，唯讀 + dump）
```
CMS_USER=<user> CMS_APP_PW=<app_pw> python3 scripts/interlink_apply.py matrix \
  --base https://<your-site> \
  --ids 101,102,103 --hub 101 --mesh --cta-path contact \
  --out /tmp/interlink_missing.json --dumpdir /tmp/interlink_raw
```
印出上線狀態 + 互鏈矩陣（v 已連 / . 未連），把「缺的連結」寫到 `--out`，每篇 content.raw dump 到 `--dumpdir/<id>.md` 供工作流找錨點。
**看矩陣**：缺 0 條就收工；有缺才往下。

### ② 找錨點（工作流，固定讀寫路徑）
```
Workflow({ scriptPath: 'scripts/interlink.js' })
```
> 注意：本工作流**不靠 args**（scriptPath 傳 args 會被字串化）。它固定讀 `INTERLINK_MISSING`（預設 `/tmp/interlink_missing.json`）、寫 `INTERLINK_PLAN`（預設 `/tmp/interlink_plan.json`），FACTS 路徑用 `INTERLINK_FACTS`。所以一定先跑 ① 產出 missing.json，並把這三個環境變數設好（或用預設）。
跑完每條缺連結有了 `anchor_marker`（逐字、唯一）+ `insertion_html`（帶連結的延伸段）。

### ③ 審稿 → 套用 → 驗證
**審稿模式（預設）**：先讀 `plan.json`，看每條的錨點與錨句合不合理（marker 貼題嗎？錨文字帶關鍵字嗎？扣 FACTS 嗎？），給使用者看過再套。
```
CMS_USER=<user> CMS_APP_PW=<app_pw> python3 scripts/interlink_apply.py apply \
  --base https://<your-site> --plan /tmp/interlink_plan.json
CMS_USER=<user> CMS_APP_PW=<app_pw> python3 scripts/interlink_apply.py verify \
  --base https://<your-site> --plan /tmp/interlink_plan.json
```
`apply` 冪等（已連跳過）、插入後即時 read-back 驗證存回；`verify` 抓公開頁確認連結在。
套完更新你的內容地圖「內鏈對象」欄與進度檔。

## 兩種模式
- **審稿模式（預設）**：① ② 做完，把 plan 給使用者看再 apply。新叢集、跨叢集、拓樸有疑慮時用。
- **自動模式**：當 (a) 目標全 publish、(b) plan 每條 marker 唯一且錨句過 FACTS、(c) 冪等 → 直接 apply + verify。適合「發完一篇新文把它織進既有叢集」「批次排程上線後的全叢集 pass」這類已驗證安全的重跑。

## 觸發時機（預設＝每篇「排程即設內鏈」，不要拖到事後批次）
**鐵則：一篇文一旦決定排程日期，就立刻處理它的內鏈——但因「只連已上線」避免 404，分兩個時點：**

1. **排程當下（決定上架日時）→「單篇 outbound」**：把這篇 → 同叢集**已上線**的相關文。
   - 用 `matrix --outbound-src <本篇> --ids <已上線姊妹...> [--cta-path ...]`（本篇可未上線；只連已上線目標，寫進本篇內文，上線時不 404）→ `interlink.js` → `apply`。
   - 連往「比它晚上線」的姊妹文先不連 → 交給下一點。
2. **這篇上線當下（publish 觸發）→「單篇 full mesh」**：本篇 ↔ 當時所有已上線姊妹（補 inbound：姊妹文 → 本篇）。`matrix --ids <本篇,已上線姊妹> --mesh` → `apply`。冪等。
3. **批次保險 pass**（選配）：整叢集都上線後再 `--mesh` 補遺漏（前兩步做對應為 0）。
4. 手動盤點某叢集補連。

> 一句話：**排程時把「本篇 → 已上線」連好；本篇上線時把「已上線 ↔ 本篇」補齊。** content-pipeline 第⑥步與 content-schedule 排程動作都應呼叫本 skill 做「單篇」這件事，不要累積到事後一次補一大票。

## 換 CMS
參考實作打 WordPress REST API（`/wp-json/wp/v2/...`、`context=edit` 取 content.raw、Gutenberg 區塊 end marker 決定插入點）。要換別的 CMS：改 `interlink_apply.py` 的 `api` / `get_public` / `fetch_post` / `cmd_apply` 的 PUT，以及 `_insert` 的 `BLOCK_ENDS`；`interlink.js` 的 `insertion_html` 模板換成目標 CMS 的區塊格式。其餘邏輯（上線判定、互鏈矩陣、缺連結、冪等、錨點工作流）與 CMS 無關。

## v2（先不做）
偵測兩篇主題高度重疊時，提示加「分流告示框」(intent-routing signpost：明講各自賽道、互導意圖) 而不只是補連。核心 skill 先只管「補貼題內鏈」。
