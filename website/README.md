# 官網 — AI 黃金自動跟單系統

純靜態網站。**沒有建置步驟、沒有 npm、沒有相依套件。**
改了檔案存檔，重新整理瀏覽器就看得到。

設計規格：[`docs/superpowers/specs/2026-08-24-marketing-website-design.md`](../docs/superpowers/specs/2026-08-24-marketing-website-design.md)

---

## 本機預覽

```bash
cd website
python3 -m http.server 8080
```

然後開 <http://localhost:8080/>。

> 必須透過 HTTP 伺服器開，不能用 `file://` 直接雙擊 HTML ——
> 語言檔是用 `fetch()` 載入的，`file://` 會被瀏覽器的同源政策擋掉。

切語言：右上角的地球圖示，或直接在網址加 `?lang=en` / `?lang=zh-Hant`。

---

## 檔案結構

```
website/
├── index.html              首頁
├── features/index.html     功能總覽
├── pricing/index.html      方案與定價（含 13 列比較表）
├── download/index.html     下載與安裝
├── faq/index.html          常見問題與支援
├── assets/
│   ├── css/
│   │   ├── tokens.css      顏色 / 字級 / 間距 / 動效。全站唯一的色碼來源
│   │   ├── base.css        reset、排版、共用工具
│   │   └── site.css        版面與元件
│   ├── js/
│   │   ├── site-config.js  ★ 待補的值都在這裡（績效數字、下載連結、聯絡方式、價格）
│   │   ├── i18n.js         語言切換引擎
│   │   ├── site.js         導覽、手風琴、捲動進場、數字跑動
│   │   ├── chart.js        示範 K 線圖
│   │   └── tape.js         頂端即時行情條
│   ├── img/                自製 SVG 與圖片
│   └── vendor/             lightweight-charts（Apache 2.0，附授權檔）
├── i18n/
│   ├── zh-Hant.json        繁體中文
│   └── en.json             English
├── robots.txt
└── sitemap.xml
```

---

## 改東西改哪裡

| 想改什麼 | 改哪個檔 |
|---|---|
| 文案（中英） | `i18n/zh-Hant.json` / `i18n/en.json` |
| 價格、績效數字、下載連結、聯絡方式 | `assets/js/site-config.js` |
| 顏色、字級、間距 | `assets/css/tokens.css` |
| 版面與元件外觀 | `assets/css/site.css` |
| 頁面結構 | `index.html` |

**顏色一律只在 `tokens.css` 定義**，其他檔案用 `var(--c-xxx)` 引用。
要加新的文字色，先跑對比檢查再加（見規格第 9 節，`--c-text-3` 就是這樣被抓出來調整的）。

### 兩支維護腳本

改完之後跑這兩支：

```bash
python3 ../scripts/check-website-i18n.py      # 兩份語言檔的 key 有沒有對齊
python3 ../scripts/sync-website-partials.py   # 把 index.html 的導覽/Footer 同步到其他四頁
```

**導覽與 Footer 只在 `index.html` 維護。** 其他四頁各自持有一份拷貝
（純靜態、沒有建置步驟的代價），改完 `index.html` 一定要跑一次同步腳本。
加 `--check` 只比對不寫入，適合放進 CI。

新增頁面時：複製一份現有子頁，改 `<title>`、`canonical`、`hreflang`，
並在 `<html>` 上宣告 `data-meta-title` / `data-meta-desc` 指向該頁自己的 i18n key，
否則切語言時標題不會跟著換。

---

## 上線前必做

- [ ] **換掉 `site-config.js` 裡所有標了 `TODO` 的值**
      —— 尤其是績效數字。金融產品行銷頁掛虛構績效有法律風險，
      必須換成真實統計，或把 `showStats` 設成 `false` 讓整段消失。
- [ ] 填 `site-config.js` 的 `site.origin`（你的網域，結尾不要斜線）
- [ ] 全站換掉網域佔位字串：
      ```bash
      cd website
      grep -rl REPLACE-WITH-YOUR-DOMAIN . | xargs sed -i '' 's|REPLACE-WITH-YOUR-DOMAIN|你的網域|g'
      ```
- [ ] 改 `index.html` `<head>` 裡的 `canonical` / `hreflang` / `og:image` 為絕對網址
- [ ] 補上服務條款與隱私政策頁面

---

## 部署（Vercel）

網站是純靜態，**沒有建置步驟**。`vercel.json` 已經設好，import repo 就能上線。

### 第一次部署

1. 到 <https://vercel.com/new>，選 **Import Git Repository**，挑這個 repo
2. 設定頁面**全部保持預設就好** —— `vercel.json` 已經指定：
   - Build Command：無（純靜態）
   - Output Directory：`website`
   - Framework：None
3. 按 **Deploy**

之後每次 push 到 `main` 會自動重新部署。其他分支會拿到獨立的 preview 網址。

### 或用 CLI

```bash
npm i -g vercel
cd /path/to/TRADER
vercel            # 第一次會問幾個問題，之後記在 .vercel/
vercel --prod     # 部署到正式環境
```

### 自訂網域

Vercel 專案 → Settings → Domains → 加網域，照它給的 DNS 設定改。
網域生效後記得回來做「上線前必做」那份清單的第 2、3 項。

### 為什麼要 `.vercelignore`

repo 根目錄放的是跟單系統的原始碼，裡面有 `requirements.txt`、`package.json`、
`Dockerfile`、`fly.toml`。Vercel 的零設定偵測看到這些會把整包當成 Python 或
Node 專案，然後去裝相依 —— 而 `requirements.txt` 裡的 `pywin32` 是 Windows
專用套件，在 Linux 建置機上必定失敗：

```
× No solution found when resolving dependencies:
  pywin32 has no wheels with a matching platform tag (manylinux_2_34_x86_64)
Error: Command "uv pip install" exited with 1
```

`.vercelignore` 只讓 `website/` 與 `vercel.json` 進到部署範圍，偵測就沒有東西
可以誤判。另外 `vercel.json` 把 `installCommand` 與 `buildCommand` 都設成
**空字串** —— 設 `null` 是「用預設偵測」，不是「不執行」，這個差別會讓人踩坑。

### vercel.json 裡設定了什麼

| 設定 | 為什麼 |
|---|---|
| `outputDirectory: "website"` | repo 根目錄是跟單系統的原始碼，不是網站 |
| `cleanUrls` + `trailingSlash` | `/pricing` 與 `/pricing/` 都能開 |
| `/assets/*` 長快取（一年） | 圖片與 CSS/JS 改了檔名才會變，可以放心長快取 |
| `/i18n/*` 短快取（5 分鐘） | 語言檔會改，不能鎖太久 |
| CSP | 只開實際用到的來源：TradingView 的 widget 與行情、Google Fonts |

**改動 CSP 前先在本機驗**，不然上線才發現 widget 全白很難查：

```bash
python3 ../scripts/serve-website-with-headers.py --port 8282
# 開 http://127.0.0.1:8282/ 看主控台有沒有 CSP violation
```

那支腳本會讀 `vercel.json` 的 headers 套到本機伺服器上，跟線上環境一致。

### 也可以用 Cloudflare Pages

Build command 留空、Build output directory 填 `website` 即可。
但 `vercel.json` 的標頭設定不會生效，要另外寫一份 `_headers`。

## 第三方相依

只有兩個，都跟 TradingView 有關，都是他們公開提供給第三方使用的：

### 介面截圖

`assets/img/console-dashboard.*` 是**真實的會員端畫面**，不是設計稿。
會員端改版後要重截：

```bash
python3 ../scripts/make-console-screenshot.py --tier flagship
# 瀏覽器開 http://127.0.0.1:8199/ 截圖，轉成 webp + jpg 覆蓋掉舊的
```

那支腳本會餵合成的 MT5 資料檔給真正的 `build_stats()`，畫面上每個數字都是產品算的。
`--tier` 可換 trial / basic / advanced / flagship，用來截不同等級的鎖定畫面。

### 圖片素材

`assets/img/hero-gold-*.webp` 三張 hero 背景是用 **Codex CLI 內建的 ImageGen** 生成的
（`codex exec "產生一張圖片：…"`，不需要安裝任何 plugin）。
目前用的是 `hero-gold-topography`，換另一張只要改 `site.css` 裡的 `--hero-img`。

| 項目 | 授權 | 掛掉會怎樣 |
|---|---|---|
| `lightweight-charts` v4.2.3（本機檔案） | Apache 2.0，附在 `assets/vendor/` | 不會掛，檔案在本機。載入失敗時圖表區顯示「圖表元件未載入」 |
| Ticker Tape Widget（執行期載入） | TradingView 免費嵌入式 Widget | 6 秒內沒長出來就切成靜態退回列，版面不會塌 |

**沒有使用 TradingView 的 logo、圖示、字型或任何圖片素材。**
設計是臨摹其排版與互動語言，所有圖示都是自己畫的 inline SVG。

字型走 Google Fonts：Manrope（標題）、Noto Sans TC（內文）、JetBrains Mono（數字）。
Google Fonts 連不上時會退回系統字型，版面不會壞。
