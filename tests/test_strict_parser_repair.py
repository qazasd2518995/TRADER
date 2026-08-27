"""yuyu 止盈自動估算(固定間距外推)的回歸測試。

背景:yuyu 的報單永遠是「進場區間 + 三個等距(~5 點)止盈 + 止損」,偶爾其中一格
掉一位數(2026-08-27 17:56/18:15 的 `Tp 4585 4590 460`,第三格 4595 被打成 460),
過去會被 `rejected_invalid_geometry` 直接掉單。這裡驗證系統改成用另外兩個等距止盈
把壞的那格外推回來,同時「不能」動到正常訊號,也不能亂猜進場價打錯的單。
"""

from copy_trader.signal_parser.strict_parser import parse_strict_signal

PROFILE = "yuyu_range_v1"


def _parse(text: str):
    return parse_strict_signal(text, PROFILE)


class TestYuyuTakeProfitRepair:
    def test_third_tp_digit_drop_is_reconstructed_from_spacing(self):
        # 今天真實掉單的兩則就是這個格式;yuyu 22:21 自己重貼證實第三格是 4595。
        text = "黃金 4580-4581多\nTp 4585 4590 460\nSl 4574\n個人建議不構成投資計畫🫶"
        result = _parse(text)
        assert result.accepted
        assert result.reason == "tp_repaired_from_spacing"
        assert result.signal.direction == "buy"
        assert [float(v) for v in result.signal.take_profit] == [4585, 4590, 4595]

    def test_sell_side_digit_drop_still_caught_even_though_geometry_passes(self):
        # 賣單掉位數(456)仍在進場價下方,騙得過幾何檢查,必須靠間距才抓得到。
        text = "黃金 4580-4581空\nTp 4575 4570 456\nSl 4587"
        result = _parse(text)
        assert result.accepted
        assert result.reason == "tp_repaired_from_spacing"
        assert [float(v) for v in result.signal.take_profit] == [4575, 4570, 4565]

    def test_middle_tp_typo_is_reconstructed(self):
        text = "黃金 4580-4581多\nTp 4585 459 4595\nSl 4574"
        result = _parse(text)
        assert result.accepted
        assert result.reason == "tp_repaired_from_spacing"
        assert [float(v) for v in result.signal.take_profit] == [4585, 4590, 4595]

    def test_overshoot_typo_that_still_passes_geometry_is_corrected(self):
        # 2026-08-14 真實案例:`Tp 4385 4390 4495`,第三格 4495 應為 4395。
        # 過去幾何會通過、直接掛到 +115 的錯止盈;現在照間距修回 4395。
        text = "黃金 4380-4381多\nTp 4385 4390 4495\nSl 4374"
        result = _parse(text)
        assert result.accepted
        assert result.reason == "tp_repaired_from_spacing"
        assert [float(v) for v in result.signal.take_profit] == [4385, 4390, 4395]

    def test_clean_buy_signal_is_untouched(self):
        text = "黃金 4615-4616多\nTp 4620 4625 4630\nSl 4609"
        result = _parse(text)
        assert result.accepted
        assert result.reason == ""  # 沒有修復
        assert [float(v) for v in result.signal.take_profit] == [4620, 4625, 4630]

    def test_clean_sell_signal_is_untouched(self):
        text = "黃金 4575-4576空\nTp 4570 4565 4560\nSl 4581"
        result = _parse(text)
        assert result.accepted
        assert result.reason == ""
        assert [float(v) for v in result.signal.take_profit] == [4570, 4565, 4560]

    def test_one_point_first_tp_wobble_is_not_treated_as_error(self):
        # yuyu 常見的 ±1 端點抖動(第一格 4586 而非 4585),不能被當成錯誤去改。
        text = "黃金 4580-4581多\nTp 4586 4590 4595\nSl 4574"
        result = _parse(text)
        assert result.accepted
        assert result.reason == ""
        assert [float(v) for v in result.signal.take_profit] == [4586, 4590, 4595]

    def test_entry_typo_is_not_silently_guessed(self):
        # 2026-08-13 真實案例:進場價 4374 打錯(應為 4474),三個止盈本身是一致的。
        # 系統不該亂猜進場價,維持拒絕 + 告警,交給人工。
        text = "黃金 4374-4375多\nTp 4480 4485 4490\nSl 4469"
        result = _parse(text)
        assert not result.accepted
        assert result.status == "rejected_invalid_geometry"

    def test_two_broken_tps_are_not_reconstructed_from_a_single_point(self):
        # 兩格都壞就無法只靠單點外推,一律放棄(寧可拒絕也不亂補)。
        text = "黃金 4580-4581多\nTp 4585 460 46\nSl 4574"
        result = _parse(text)
        assert not result.accepted
        assert result.status == "rejected_invalid_geometry"

    def test_repair_only_applies_to_yuyu_profile(self):
        # 固定間距是 yuyu 專屬規律,別的 profile 不套用外推。
        text = "黃金 4580-4581多\nTp 4585 4590 460\nSl 4574"
        assert parse_strict_signal(text, "mid_frequency_v1").accepted is False
