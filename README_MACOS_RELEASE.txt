============================================
  macOS 對外發佈說明
============================================

目前專案的本機 build 只適合自己測試，不適合直接把 DMG 傳給別人。

原因:
  - macOS Gatekeeper 會檢查 Developer ID 簽章與 notarization
  - 沒有這兩步時，其他人下載後常會看到:
    「黃金跟單系統」已損毀，無法打開
    或
    無法驗證開發者


【正式發佈前需要】

  1. Apple Developer Program 帳號
  2. Keychain 中可用的 Developer ID Application 憑證
  3. notarytool 憑證設定完成


【建議流程】

  1. 設定簽章身份:

     export DEVELOPER_ID_APPLICATION="Developer ID Application: Your Name (TEAMID)"

  2. 設定 notarytool profile:

     export NOTARYTOOL_PROFILE="your-notarytool-profile"

  3. 執行正式發佈腳本:

     ./build_release_macos.sh


【本機測試】

  若只是自己機器測試，可用:

     ./build_tauri_macos.sh

  但這種產出的 DMG 不應直接發給其他人。


【少量測試者分享】

  若只是發給少數熟人測試，且接受對方手動按 Open Anyway，
  可先完成本機 app build，再重包一個帶 Applications 捷徑的 DMG:

     ./rebuild_dmg_local.sh

  注意:
    - 這仍然不是正式可公開分發的 macOS 發佈包
    - 對方第一次開啟仍可能需要到 Privacy & Security 按 Open Anyway
