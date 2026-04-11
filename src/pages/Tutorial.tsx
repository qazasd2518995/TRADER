import { S } from "../lib/constants";

export default function Tutorial() {
  async function handleDownloadEA() {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const dest = await invoke("download_ea") as string;
      alert(`橋接 EA（MT5_File_Bridge_Enhanced.mq5）已儲存至：${dest}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("not a function") || msg.includes("invoke")) {
        alert("EA 下載功能需在完整應用中使用");
      } else {
        alert(`下載失敗：${msg}`);
      }
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="section stagger-1" style={{ padding: "24px 28px" }}>
        <div className="flex items-center justify-between">
          <div>
            <span
              className="heading-md"
              style={{
                fontFamily: "var(--font-serif)",
                fontSize: "22px",
                fontWeight: 400,
                color: "var(--color-ink)",
              }}
            >
              {S.TUTORIAL_TITLE}
            </span>
            <div style={{ fontSize: "12px", color: "var(--color-ink-muted)", marginTop: "4px" }}>
              MetaTrader 5 EA 安裝與系統使用指南
            </div>
          </div>
          <button onClick={handleDownloadEA} className="btn-gold">
            {S.BTN_DOWNLOAD_EA}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="section stagger-2" style={{ padding: "32px 36px" }}>
        <div className="tutorial-content">
          <h2>步驟一：安裝 MetaTrader 5</h2>
          <ol>
            <li>前往 <b>MetaTrader 5</b> 官網下載並安裝</li>
            <li>開設模擬帳戶或連接您的經紀商帳戶</li>
            <li>確認可以看到 <b>XAUUSD</b>（黃金）交易品種</li>
          </ol>

          <h2>步驟二：安裝 EA（專家顧問）</h2>
          <ol>
            <li>點擊上方「下載 EA 檔案」按鈕，下載橋接 EA <code>MT5_File_Bridge_Enhanced.mq5</code></li>
            <li>在 MT5 中開啟 <b>MetaEditor</b>（按 F4 或點選工具列圖示）</li>
            <li>將 <code>MT5_File_Bridge_Enhanced.mq5</code> 複製到 <code>MQL5/Experts/</code> 資料夾</li>
            <li>在 MetaEditor 中按 <b>F7</b> 編譯</li>
            <li>回到 MT5 主視窗，在「導航」面板找到編譯好的 <code>MT5_File_Bridge_Enhanced</code></li>
          </ol>

          <h2>步驟三：掛載 EA 到圖表</h2>
          <ol>
            <li>開啟一張 <b>XAUUSD</b> 圖表</li>
            <li>從「導航」面板將 EA 拖曳到圖表上</li>
            <li>在彈出的設定視窗中：
              <ul>
                <li>勾選「允許自動交易」</li>
                <li>確認 <code>EnableTrading = true</code></li>
              </ul>
            </li>
            <li>確認 MT5 工具列上的「自動交易」按鈕已啟用（綠色）</li>
            <li>EA 啟動後會開始寫入 JSON 檔案到 <code>MQL5/Files/</code> 資料夾</li>
          </ol>

          <h2>步驟四：設定擷取視窗</h2>
          <ol>
            <li>開啟 <b>LINE 桌面版</b>，打開您要跟單的聊天視窗</li>
            <li>在本系統的「設定」&gt;「訊號擷取」頁面</li>
            <li>點擊「偵測視窗」按鈕，選擇您的 LINE 聊天視窗</li>
            <li>或手動輸入視窗名稱（部分匹配即可）</li>
          </ol>

          <h2>步驟五：開始交易</h2>
          <ol>
            <li>確認 MT5 已連線（狀態列顯示「已連線」）</li>
            <li>確認設定無誤（手數、馬丁格爾、安全設定等）</li>
            <li>點擊「開始交易」按鈕</li>
            <li>系統將自動：擷取螢幕 &gt; OCR 辨識 &gt; 解析訊號 &gt; 執行交易</li>
            <li>在「儀表板」監控即時狀態和統計數據</li>
          </ol>

          <h2>步驟六：監控與管理</h2>
          <ul>
            <li><b>儀表板</b> — 帳戶資訊、馬丁格爾狀態、今日統計、持倉一覽</li>
            <li><b>持倉</b> — 所有開倉部位和掛單</li>
            <li><b>歷史</b> — 已平倉交易記錄</li>
            <li><b>日誌</b> — 系統運行日誌，排查問題</li>
          </ul>

          <h2>常見問題</h2>
          <p><b>Q: MT5 顯示「未連線」？</b></p>
          <p>確認 MT5 已開啟且 EA 已掛載到 XAUUSD 圖表。檢查「設定」&gt;「MT5 橋接」中的路徑是否正確。</p>

          <p style={{ marginTop: 16 }}><b>Q: 沒有偵測到訊號？</b></p>
          <p>確認 LINE 視窗已開啟，且在「設定」&gt;「訊號擷取」中正確配置視窗名稱。查看「日誌」頁面了解詳情。</p>

          <p style={{ marginTop: 16 }}><b>Q: 馬丁格爾層級不對？</b></p>
          <p>馬丁格爾狀態會自動儲存，重啟後不會遺失。如需手動重置，在「儀表板」點擊「重置馬丁格爾」。</p>

          <p style={{ marginTop: 16 }}><b>Q: 如何停止交易？</b></p>
          <p>點擊「停止交易」按鈕。已掛出的訂單不會自動取消，需在 MT5 中手動管理。</p>
        </div>
      </div>
    </div>
  );
}
