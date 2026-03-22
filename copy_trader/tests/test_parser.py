"""
Test Signal Parser
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_parser import SignalParser


def test_parser():
    """Test the signal parser with sample inputs."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return

    parser = SignalParser(api_key)

    test_cases = [
        # Case 1: Chinese sell signal
        """乘XAUUSD 黃金
Sell ：4903
止損：4915
止盈 : 4885
（純粹個人投資分享）""",

        # Case 2: Chinese buy signal with multiple TPs
        """黃金 做多
進場: 2850
止損: 2840
止盈1: 2865
止盈2: 2880""",

        # Case 3: Not a trading signal
        "今天天氣很好，適合出門",

        # Case 4: English signal with multiple TPs
        """XAUUSD BUY
Entry: 2855.50
SL: 2845
TP1: 2870
TP2: 2890
TP3: 2920""",

        # Case 5: Mixed format
        """Gold 空單
2860 進場
SL 2875
TP 2840"""
    ]

    print("=" * 60)
    print("Signal Parser Test")
    print("=" * 60)

    for i, text in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"Test Case {i}:")
        print("-" * 60)
        print(f"Input:\n{text}")
        print("-" * 60)

        signal = parser.parse(text)

        print(f"Result: {signal}")
        print(f"  Valid: {signal.is_valid}")
        print(f"  Direction: {signal.direction}")
        print(f"  Symbol: {signal.symbol}")
        print(f"  Entry: {signal.entry_price}")
        print(f"  SL: {signal.stop_loss}")
        print(f"  TP: {signal.take_profit}")
        print(f"  Confidence: {signal.confidence:.0%}")

        if signal.is_valid:
            print("  ✓ PASSED")
        else:
            print("  ✗ Invalid signal (may be expected)")


if __name__ == "__main__":
    test_parser()
