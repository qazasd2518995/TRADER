# TradingView 的實際排版方法（從 tw.tradingview.com 的 HTML/CSS 拆出來）

抓了 /features/ /pricing/ /about/ /screener/ /brokers/ 與首頁，
72 支 CSS bundle（548 KB）解析後的結論。

## 一、垂直節奏（--v-rhythm-spacing）

| 級距 | desktop | laptop | tablet | phone |
|---|---|---|---|---|
| 1（大段落之間） | **200px** | 160 | 120 | 80 |
| 2 | 160px | 120 | 88 | 64 |
| 3 | 120px | 88 | 64 | 48 |
| 4（區塊內） | 80px | 64 | 48 | 48 |
| 5（元件內） | 48px | 40 | 32 | 32 |

## 二、字級：比想像中大得多

- 段落主標 `title`：phone **40px/40px** → desktop **100px/100px**
  - line-height 等於 font-size，就是 1.0
  - letter-spacing：40–64px 時 `-.04em`，100px 時 `-.02em`
  - font-weight 600（不是 800）
- 段落副標 `description`：phone 18px/28px → desktop **24px/32px**，margin-top 16px
- 卡片標題 `cardTitle`：24px → **36px/44px**，weight 600，margin-top 32px（圖與標題之間）
- 卡片內文 `cardDescription`：16px → **18px/28px**

## 三、容器寬度

1000 / 1200 / 1280 / **1480** / 1660 / 1720px —— 依區塊性質選，不是全站一個值。
主要內容區用 1480px。

## 四、關鍵結構手法（這才是「不像 AI」的原因）

1. **完全沒有 eyebrow / kicker 小標。**
   三個頁面 grep `eyebrow|kicker|overline` = 0 次。
   我原本每一段都放一個，這是最明顯的 AI 味。

2. **功能項目用細線分隔，不是描邊盒子。**
   `/features/` 的功能區塊是兩欄，中間一條 1px 垂直細線，
   沒有卡片底色、沒有圓角外框。

3. **每個功能配一張真的產品介面縮圖**，不是「圓角方塊裡放一個 icon」。
   31 張卡 = 31 個 `imageWrapper`。圖片容器 min-height 從 159px 一路到 **600px**。

4. **視覺刻意溢出容器。** 產品截圖比它的容器寬，疊在其他元素上面，
   卡片列用 `margin-inline: -32px` 流出內容區、直接貼到螢幕邊。

5. **卡片是橫向捲動列**（`overflow: scroll` + 隱藏捲軸 + `scroll-padding-inline`），
   `flex-shrink: 0; width: 380px; border-radius: 24px; padding: 24px`。
   不是規矩的等分網格。

6. **標題刻意控制斷行**（`twoLines` / `lastLine` / `lastWord` 這些 class），
   讓兩行長度形成節奏，而不是交給瀏覽器自動換行。

7. **滿版背景影片**（autoplay muted loop）+ 遮罩層 + 裡面再框一支產品影片。

8. 按鈕是 pill 形外框 + 右側小圖示。

## 五、對照我第一版做錯的地方

| 我做的 | 他們做的 |
|---|---|
| 每段都有 eyebrow 小標 | 沒有 |
| h2 = 48px / line-height 1.12 | 100px / line-height 1.0 |
| lede = 18px | 24px |
| 六個等大的圓角描邊卡 | 細線分隔欄 + 真實產品縮圖 |
| 圓角方塊裡放線條 icon | 直接放介面截圖 |
| 內容寬 1200px | 1480px |
| 區塊間距 96px | 200px |
| 所有東西都在容器內對齊 | 刻意溢出、重疊、貼邊 |
