# imageppt 圖片式簡報技能

可重複使用的 Codex 技能 `native-image-slides`。提供文稿與視覺母版後，由 Web GPT 原生生圖與圖片編修製作獨立 PNG，再整理 Space 素材入口和交付 ZIP。

本套件為 2026-10-05 已安裝版本的可攜副本；只有兩份參考文件的私人來源連結被移除，工具程式與製作規則保留。

## 下載與安裝

- [下載技能 ZIP](dist/native-image-slides-20261005.zip)
- [直接瀏覽技能](native-image-slides/SKILL.md)

解壓縮後，把整個 `native-image-slides` 資料夾放到你的 Codex 使用者技能目錄 `~/.codex/skills/`；Windows 通常是 `%USERPROFILE%\.codex\skills\`。已有同名技能時先備份舊版，再替換。重新開啟 Codex 工作階段確認技能可用。

## 使用

附上純文字文稿與參考圖，交代頁數、用途及漸進頁需求，例如：

> 使用 native-image-slides，依附上的文稿與母版製作十頁簡報，至少三頁有漸進式圖片。由 Luna 執行，完成獨立 PNG、ZIP 與 Space 素材入口，記錄耗用和意外問題。

技能會按頁組規劃每批最多十張；同頁完整圖與漸進圖留在同批，先做完整圖，再以上一張實際圖片逐層刪減。每張核對語意及關鍵資訊，容許同義改寫。

## 執行環境

- 可操作已登入 ChatGPT 的瀏覽器與原生圖片生成／編修。
- Codex 的瀏覽器操作工具及 Pages 連接器；具體介面依當前工具文件確認。
- Python 3；`scripts/artifacts.py` 只使用標準函式庫，不需額外 Python 套件。
- 這是 Codex 技能，並非獨立背景服務或可編輯 PowerPoint 產生器。使用者仍須授權素材與外部操作。

## 本機整理工具

```text
python native-image-slides/scripts/artifacts.py plan run.json
python native-image-slides/scripts/artifacts.py status run.json
python native-image-slides/scripts/artifacts.py check run.json
python native-image-slides/scripts/artifacts.py pack run.json --output slides.zip
```

任務檔結構與其他操作見 [run-format.md](native-image-slides/references/run-format.md)。工具負責切批、檔案核對、命名、封裝和頁面草稿，不會自行上傳、生圖或代替目視品檢。

## 已驗證與限制

已實跑兩頁六張同批漸進圖、逐張收件與品檢，以及缺圖單張補件。Space 交付曾需要主代理救場，修正後 Luna 完成頁面更新和重開核對；不承諾每次完全無人介入。

手機 App 曾無法顯示 Space 內嵌圖片，即使電腦版與雲端原圖可讀；手機問題仍未解。電腦顯示成功不算手機驗收。精確 Token／額度及模型內部圖片來源 ID 不可觀察時，必須記錄為未知。

套件不包含任何專案文稿、生成圖片、聊天紀錄、帳號資料或本機設定。
