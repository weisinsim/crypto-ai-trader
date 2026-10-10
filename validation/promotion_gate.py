#!/usr/bin/env python3
"""Conservative screening helpers; passing is not production approval."""
import math

def classify_candidate(dev_net_r, holdout_net_r, holdout_trades, holdout_profit_factor, min_trades=30):
    if not isinstance(holdout_trades, int) or holdout_trades < min_trades:
        return "FAIL_INSUFFICIENT_HOLDOUT_TRADES"
    values = (dev_net_r, holdout_net_r)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return "FAIL_MISSING_OR_INVALID_NET_R"
    if dev_net_r <= 0 or holdout_net_r <= 0:
        return "FAIL_NONPOSITIVE_DEVELOPMENT_OR_HOLDOUT"
    if not isinstance(holdout_profit_factor, (int, float)) or not math.isfinite(holdout_profit_factor) or holdout_profit_factor <= 1:
        return "FAIL_HOLDOUT_PROFIT_FACTOR"
    return "PASS_PRELIMINARY_SCREEN_ONLY"

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev-net-r', type=float, required=True)
    parser.add_argument('--holdout-net-r', type=float, required=True)
    parser.add_argument('--holdout-trades', type=int, required=True)
    parser.add_argument('--holdout-profit-factor', type=float, required=True)
    parser.add_argument('--min-trades', type=int, default=30)
    args = parser.parse_args()
    print(classify_candidate(args.dev_net_r, args.holdout_net_r, args.holdout_trades, args.holdout_profit_factor, args.min_trades))
