"""yuyu 止盈自動估算(固定間距外推)的回歸測試。

背景:yuyu 的報單永遠是「進場區間 + 三個等距(~5 點)止盈 + 止損」,偶爾其中一格
掉一位數(2026-08-27 17:56/18:15 的 `Tp 4585 4590 460`,第三格 4595 被打成 460),
過去會被 `rejected_invalid_geometry` 直接掉單。這裡驗證系統改成用另外兩個等距止盈
把壞的那格外推回來。同檔也驗證新的 ±100 共識修正：只有 entry、SL 或整組 TP
其中一個欄位家族明顯落在相鄰百點，且另外四個點位給出唯一解時才修正。
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

    def test_entry_hundred_offset_is_repaired_when_other_four_points_agree(self):
        # 2026-08-13 真實案例：SL 與三個 TP 都圍繞 4474，只有 entry 少 100。
        text = "黃金 4374-4375多\nTp 4480 4485 4490\nSl 4469"
        result = _parse(text)
        assert result.accepted
        assert result.reason == "point_repaired_hundred_offset"
        assert float(result.signal.entry_price) == 4474
        assert result.repair.field == "entry_price"
        assert result.repair.original == (4374,)
        assert result.repair.corrected == (4474,)

    def test_today_stop_loss_hundred_offset_is_repaired(self):
        # 2026-09-01 16:45：entry 與三個 TP 都在 4374–4390，只有 SL 高 100。
        text = "黃金 4374-4375多\nTp 4380 4385 4390\nSl 4469"
        result = _parse(text)
        assert result.accepted
        assert result.reason == "point_repaired_hundred_offset"
        assert float(result.signal.stop_loss) == 4369
        assert result.repair.field == "stop_loss"
        assert result.repair.original == (4469,)
        assert result.repair.corrected == (4369,)

    def test_second_today_stop_loss_hundred_offset_is_repaired(self):
        text = "黃金 4380-4381多\nTp 4385 4390 4395\nSl 4474"
        result = _parse(text)
        assert result.accepted
        assert float(result.signal.stop_loss) == 4374
        assert result.repair.field == "stop_loss"

    def test_sell_stop_loss_hundred_offset_uses_sell_geometry(self):
        text = "黃金 4380-4381空\nTp 4375 4370 4365\nSl 4287"
        result = _parse(text)
        assert result.accepted
        assert float(result.signal.stop_loss) == 4387
        assert result.repair.field == "stop_loss"
        assert result.repair.corrected == (4387,)

    def test_whole_take_profit_family_hundred_offset_is_repaired(self):
        # 2026-03-13：entry/SL 在 5070，三個等距 TP 集體高 100。
        text = "黃金 5070-5071多\nTp 5180 5190 5200\nSl 5060"
        result = _parse(text)
        assert result.accepted
        assert [float(v) for v in result.signal.take_profit] == [5080, 5090, 5100]
        assert result.repair.field == "take_profit"
        assert result.repair.corrected == (5080, 5090, 5100)

    def test_two_broken_tps_are_not_reconstructed_from_a_single_point(self):
        # 兩格都壞就無法只靠單點外推,一律放棄(寧可拒絕也不亂補)。
        text = "黃金 4580-4581多\nTp 4585 460 46\nSl 4574"
        result = _parse(text)
        assert not result.accepted
        assert result.status == "rejected_invalid_geometry"

    def test_non_hundred_geometry_error_still_rejected(self):
        # 可以有多種人工猜法，沒有唯一的 ±100 共識答案就不動。
        text = "黃金 4560-4561多\nTp 4555 4560 4565\nSl 4555"
        result = _parse(text)
        assert not result.accepted
        assert result.status == "rejected_invalid_geometry"
        assert result.repair is None

    def test_repair_only_applies_to_yuyu_profile(self):
        # 固定間距是 yuyu 專屬規律,別的 profile 不套用外推。
        text = "黃金 4580-4581多\nTp 4585 4590 460\nSl 4574"
        assert parse_strict_signal(text, "mid_frequency_v1").accepted is False

    def test_hundred_offset_repair_does_not_apply_to_mid_frequency(self):
        # 中頻歷史的風控距離較廣；即使數字剛好能套 yuyu 模型也不得代改。
        text = "Buy：4374\n止損：4469\n止盈：4380 4385 4390"
        result = parse_strict_signal(text, "mid_frequency_v1")
        assert not result.accepted
        assert result.status == "rejected_invalid_geometry"
        assert result.repair is None
