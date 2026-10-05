# 任務檔與機械工具

在開始分批、續跑或交付時讀本檔。只用既有 `scripts/artifacts.py`，不另寫封裝／分批程式。腳本只處理檔案、JSON、PNG結構與ZIP，沒有瀏覽器或生圖能力，不改圖片像素。

## 檔案與第一次規劃

每案一個資料夾，原文、母版、規劃、實際批次MD、PNG與 `run.json` 都在其中。原始規劃保存一次；先讓 Web GPT 依以下結構輸出頁組及逐圖提示，Luna只整理檔案與引用，不重寫視覺內容。`run.json` 是唯一當前進度，已有歷史說明可保留，但續跑只讀當前資料。

請 Web GPT 提供：每圖 `file/page/stage/level/source`、原提示文字，以及各頁按製作順序的輸出清單。檔名沿用既有規劃（中文前綴或P01-final均可），同案一致；`level`是剩餘揭露層級，倒拆逐次減一，與檔名數字不混用。下面為一頁兩階段的結構示意；不要把示意字串當真實hash、網址或正式素材。

```json
{
  "mode": "production",
  "planned_pages": ["P01"],
  "selected_pages": ["P01"],
  "inputs": [
    {"file": "文稿.md", "sha256": "填實際檔案雜湊"},
    {"file": "母版.png", "sha256": "填實際檔案雜湊"}
  ],
  "expected_outputs": [
    {"file": "P01-step01.png", "page": "P01"},
    {"file": "P01-final.png", "page": "P01"}
  ],
  "page_groups": [
    {"page": "P01", "outputs": ["P01-final.png", "P01-step01.png"]}
  ],
  "batches": [],
  "artifacts": [],
  "deliverables": ["文稿.md", "完整施工稿.md"]
}
```

- `expected_outputs`是**完整交付清單，按播放順序**，不能因缺件把該項刪掉。一般製作 selected_pages 等於 planned_pages；使用者明確要求部分頁才縮小。
- `page_groups`是**此次待生成輸出，按製作順序**。續跑先核對既有成果與未結束批次；已合格的圖不放進新頁組。缺件的同頁前置圖仍放同一組，並在任務包附上需要的真實上一張來源。
- `inputs`與所有 `sha256`都由實際位元組計算，不請模型猜填。所有檔案路徑相對於run資料夾，禁止穿越；原圖保留原始位元組。
- `deliverables`只列要封裝且已存在的 PNG／MD／TXT／JSON；原始母版放素材區與 `inputs`，不列入成品包，尤其 JPG 不受封裝工具支援。不預填不存在的檔案。

## 機械命令

先找現有可用Python，找不到PATH命令可用桌面工具提供的bundled Python；不因此安裝套件。以下 `python`代表已找到的執行檔，`artifacts.py`用技能資料夾中的絕對路徑。不要把提示詞內容直接拼進shell命令。

```text
python artifacts.py plan <run.json>
python artifacts.py status <run.json>
python artifacts.py check <run.json>
python artifacts.py pack <run.json> --output <全新檔名.zip>
python artifacts.py register <run.json> --record <單張紀錄.json>
python artifacts.py preflight <run.json>
python artifacts.py space <run.json>
```

`plan`只讀，返回 B01、B02等批次；每包<=10張、同頁不拆。10頁、P3/P6/P8各三張的範例會得到10+6兩包。重複頁、重複輸出、空組、單頁>10應修正規劃，不能忽略錯誤繼續。

## 保存實際批次與送出狀態

依 `plan`結果，把Web GPT共通批次規則及對應逐圖原文按順序組成 `batches/B01.md`。共通規則只寫一次。**先存檔並讀回，才填入瀏覽器；真正送出的任何控制語都必須包含在此檔。** 送出後保留此原檔，不反向同步覆寫。計算雜湊後，把plan返回的id、outputs加上以下資料寫回run：

```json
{
  "id": "B01",
  "outputs": ["P01-final.png", "P01-step01.png"],
  "prompt": "batches/B01.md",
  "prompt_sha256": "實際MD雜湊",
  "state": "prepared",
  "chat_url": null
}
```

| state | 何時記錄 | 下次動作 |
|---|---|---|
| prepared | 提示MD已實存、內容與hash已核對 | status顯示可送，且不存在送出證據／已存圖，才附檔送出 |
| submitting | 即將點傳送，或點擊結果未確認 | 查原分頁／訊息，不能直接重送 |
| sent | 使用者訊息已出現，記正式chat網址 | 等整批或查看是否已有結果，不能再次送包 |
| collected | 該批所有實際產物已收件（可含待修圖） | 做QA；完整度由check判定，不由這個字判定 |
| blocked | 平台異常／有限排錯後仍失敗 | 記原因及最小next_action；恢復先查舊對話 |

耗時計錄只用實際牆鐘時間：`sent_at` 為確認送出時、`generation_finished_at` 為首次觀察回覆完成時、批次 `collected_at` 為最後一張原圖收件時。取得觀察時間即可，不追查模型內部結束時間。不可把數次等待秒數相加當整段耗時；漏記就寫未知，已收件圖片可用各張記錄的最晚時間作批次收件時間，勿另增同義時間欄位。

失敗原因、下一步與已上傳引用可直接留在對應batch/artifact，不建立第二份台帳。`status`只提供缺檔及下一步建議，不會操作瀏覽器，也不能證明遠端未送出。它返回的可送狀態只是本機條件，仍要核對當前分頁。

## 收件與品質

每個實際產物依**製作順序**加入 `artifacts`，包括不合格產物（qa_passed=false）。每圖一筆：

```json
{
  "file": "P01-final.png",
  "page": "P01",
  "stage": "final",
  "level": 1,
  "sha256": "實際PNG雜湊",
  "package": "B01",
  "chat_url": "實際正式https://chatgpt.com/c/網址",
  "prompt": "batches/B01.md",
  "runtime_evidence": "實際附件、產圖序號、可見來源及內部來源不可驗證等事實",
  "qa_passed": true,
  "qa_note": "語意與關鍵資訊一致；文字清楚、版面無遮擋；本張為完整揭露態。"
}
```

倒拆圖 `stage: reverse`另填 `source`（上一張實際檔名）及 `source_sha256`，level減一。修圖用新乾淨對話與新的package，`stage: repair`、新的file、真實source/hash，另填 `target`指向被補正的expected_outputs檔名；例如 `P08-step01-repair1.png`取代`P08-step01.png`。先確認修過的上游QA通過，才以它倒拆下游，不用舊來源跳過修正版。

修圖成功只替換交付位置，不覆寫舊原件。多個產物指向同target時選最後一個QA通過者；qa_passed不應由檔案檢查自動填true，必須看實際圖，判定語意及關鍵資訊，不以文字逐字相同作通過條件。同義改寫、語序／標點差異與不失原意的精簡可直接通過；數字、條件、否定或功能承諾改變才修正。已有 `page_reference`可原樣保存工具返回的引用，表格／預覽共用，不重傳。

品檢必須具體到每張PNG，final與每張漸進圖都各有`qa_passed`及簡短`qa_note`，不能只寫「整批通過」。逐張核對：語意及關鍵資訊、可讀性與遮擋、應顯示／移除的層次，以及倒拆保留元素的位置。記錄本張實際發現；若只有同義改寫，直接記「語意相同，通過」。不合格需寫明哪個區域、哪項意思或層次錯誤，讓修圖只處理該處；不要複製整份逐字比對表。

## 降低手動紀錄與交付返工

- `register` 接收一筆明確目視品檢後的artifact紀錄，計算PNG與來源hash並檢查，拒絕重複或不合法紀錄；失败不改run。不會替你判定圖片品質。引用可在該筆的 `page_reference` 保存工具實際回傳值。
- `preflight` 在封裝前列出缺檔／不支援格式，並依每頁原圖位元組保守估算10MiB小包分組；實際ZIP仍要確認容量，單頁超限須個別處理，不能反覆上傳試錯。
- `space` 只讀並產清單與預覽草稿，依預定播放順序、採用的品檢合格artifact及真實引用；沒引用寫待上傳，不能捏造連結。素材區仍用實際附件引用填入。
- 正常流程逐圖只目視一次，遇具体疑問才交主代理複核；最後寫定耗用及問題紀錄再封裝、上傳，避免反覆重包及重傳紀錄。

## 完整與部分交付

`check`分開返回：

- `mechanical_pass`：檔案、PNG、雜湊及來源紀錄有無錯誤；不代表已證明AI內部用圖或目視品質。
- `complete`／`missing`：預定交付位置是否都有合格產物。前置圖缺一張也不能完成。
- `selected_files`：依expected_outputs順序選出的QA通過原件，供Space／ZIP使用。Luna沿同清單更新頁面，不再手算另一套張數。

完整度未滿且確定本輪受阻時，才用：

```text
python artifacts.py pack <run.json> --output <明示缺件的全新ZIP名.zip> --allow-partial
```

部分包會加入 `delivery-status.json`說明缺件；雜湊錯誤或其他機械錯誤不能用此旗標略過。ZIP不是修改狀態的工作目錄，不拿部分包冒充全案。腳本拒絕覆寫既有ZIP；補件後產新版本並更新同一Page。工具內部檢查PNG完整性及封裝位元組，不需每張額外重跑多個hash腳本。

`mode: acceptance`或`--mode acceptance`只供明確的技能開發驗收，要求三代表頁、兩層倒拆及修圖；不套到一般成品。機械測試與離線Luna演練通過仍不等於Web GPT端到端通過。
