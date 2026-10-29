"""Walk-forward research lab for comparing strategy hypotheses without changing live trading."""
import math
import pandas as pd

from config import (
    ASSETS, BACKTEST_BARS_5M, BACKTEST_BARS_15M, BACKTEST_BARS_1H,
    BACKTEST_RETRAIN_EVERY, GOLD_DATA_SYMBOL,
)
from backtest import GoldBacktest

STRATEGIES = (
    "baseline",
    "trend_pullback",
    "breakout_retest",
    "momentum_continuation",
)
STRATEGY_NAMES = {
    "baseline": "الاستراتيجية الحالية",
    "trend_pullback": "الارتداد داخل الاتجاه",
    "breakout_retest": "اختراق ثم إعادة اختبار",
    "momentum_continuation": "استمرار الزخم",
}
# Request a larger common history for research even when older environment
# variables were set to 5,000 bars. Providers may still return fewer bars;
# that is surfaced by the partial-history warning in the report.
BARS_BY_TF = {
    "5m": max(10000, BACKTEST_BARS_5M),
    "15m": max(10000, BACKTEST_BARS_15M),
    "1h": max(10000, BACKTEST_BARS_1H),
}


def summarize_trades(trades, cost_r=0.0):
    """Summarize completed trades; cost_r is an explicit sensitivity assumption, not broker data."""
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "net_r": 0.0, "net_after_cost_r": 0.0,
                "profit_factor": 0.0, "profit_factor_after_cost": 0.0, "max_drawdown_r": 0.0,
                "expectancy_after_cost_r": 0.0}
    gross = [float(t.get("r", 0.0)) for t in trades]
    net = [r - float(cost_r) for r in gross]

    def pf(values):
        gp = sum(max(v, 0.0) for v in values)
        gl = abs(sum(min(v, 0.0) for v in values))
        return gp / gl if gl else (math.inf if gp else 0.0)

    def drawdown(values):
        equity = peak = dd = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            dd = max(dd, peak - equity)
        return dd

    return {
        "trades": len(trades),
        "win_rate": 100.0 * sum(v > 0 for v in gross) / len(gross),
        "win_rate_after_cost": 100.0 * sum(v > 0 for v in net) / len(net),
        "net_r": sum(gross),
        "net_after_cost_r": sum(net),
        "profit_factor": pf(gross),
        "profit_factor_after_cost": pf(net),
        "max_drawdown_r": drawdown(net),
        "expectancy_after_cost_r": sum(net) / len(net),
    }


def split_completed_trades(trades, midpoint):
    """Avoid calibration leakage: calibration trades must also exit before the split."""
    calibration, holdout = [], []
    for trade in trades:
        signal_time = trade.get("time")
        exit_time = trade.get("exit_time")
        if not signal_time or not exit_time:
            continue
        signal_time = pd.Timestamp(signal_time)
        exit_time = pd.Timestamp(exit_time)
        if signal_time <= midpoint and exit_time <= midpoint:
            calibration.append(trade)
        elif signal_time > midpoint:
            holdout.append(trade)
    return calibration, holdout


class StrategyResearchLab:
    """Research-only: strategy-specific labels/models and a chronological holdout split."""

    def run_asset(self, symbol=GOLD_DATA_SYMBOL, progress=None):
        bt = GoldBacktest()
        asset = next((a for a in ASSETS.values() if a["symbol"] == symbol), {"label": symbol})
        report = {
            "symbol": symbol, "asset_label": asset["label"], "strategies": {},
            "warning": "Exploratory comparison only; do not deploy based on the same holdout used to compare multiple candidates.",
        }
        for tf in ("5m", "15m", "1h"):
            prepared = bt._prepare(tf, BARS_BY_TF[tf], symbol)
            x = prepared["features"]
            if len(x) < 1200:
                report["strategies"][tf] = {"error": f"بيانات غير كافية: {len(x)} شمعة قابلة للاستخدام"}
                continue
            midpoint = x.index[0] + (x.index[-1] - x.index[0]) / 2
            report["strategies"][tf] = {
                "snapshot_id": prepared.get("snapshot_id"),
                "start": x.index[0].isoformat(),
                "end": x.index[-1].isoformat(),
                "midpoint": midpoint.isoformat(),
                "history_partial": prepared.get("history_partial", False),
                "results": {},
            }
            for strategy in STRATEGIES:
                if progress:
                    progress(tf, strategy)
                result = bt.run(
                    tf, bars=BARS_BY_TF[tf], retrain_every=BACKTEST_RETRAIN_EVERY,
                    _prepared=prepared, symbol=symbol, strategy=strategy,
                    include_all_trades=True,
                )
                if result.get("error"):
                    report["strategies"][tf]["results"][strategy] = {"error": result["error"]}
                    continue
                calibration, holdout = split_completed_trades(result.get("trades_detail", []), midpoint)
                report["strategies"][tf]["results"][strategy] = {
                    "all": summarize_trades(result.get("trades_detail", []), 0.0),
                    "calibration": summarize_trades(calibration, 0.0),
                    "holdout": summarize_trades(holdout, 0.0),
                    "holdout_cost_005R": summarize_trades(holdout, 0.05),
                    "holdout_cost_010R": summarize_trades(holdout, 0.10),
                    "setup_candidates": result.get("setup_candidates", 0),
                    "unfilled_setups": result.get("unfilled_setups", 0),
                    "rejection_counts": result.get("rejection_counts", {}),
                    "direction_rejections": result.get("direction_rejections", {}),
                    "unresolved_at_end": result.get("unresolved_at_end", 0),
                    "snapshot_id": result.get("snapshot_id"),
                }
        return report

    @staticmethod
    def _fmt_metric(m):
        if not m or not m.get("trades"):
            return "0 صفقة"
        pf = m.get("profit_factor", 0.0)
        pf_text = "∞" if math.isinf(pf) else f"{pf:.2f}"
        return (f"{m['trades']} صفقة | WR {m['win_rate']:.1f}% | "
                f"Net {m['net_r']:+.1f}R | PF {pf_text} | "
                f"DD {m['max_drawdown_r']:.1f}R")

    def format_ar(self, report):
        lines = [
            f"🧭 <b>مختبر الاستراتيجيات — {report.get('asset_label', report.get('symbol'))}</b>",
            "تدريب منفصل لكل استراتيجية، وتوقعات Walk-Forward زمنية.",
            "النصف الأول: تقييم معايرة؛ النصف الثاني: مقارنة احتجازية. لا يُستخدم النصف الثاني لاختيار إعدادات.",
            "تكلفة 0.05R و0.10R لكل صفقة سيناريو حساسية فقط، وليست تقديرًا فعليًا لسبريد وسيطك.",
            "⚠️ لأن عدة مرشحين تتم مقارنتهم على النصف الثاني، يجب تأكيد المرشح النهائي على فترة مستقبلية جديدة قبل التداول.",
            "",
        ]
        for tf in ("5m", "15m", "1h"):
            section = report.get("strategies", {}).get(tf, {})
            lines.append(f"⏱️ <b>{tf}</b>")
            if section.get("error"):
                lines += [f"❌ {section['error']}", ""]
                continue
            lines.append(f"🗓️ {section.get('start')} → {section.get('end')}")
            if section.get("history_partial"):
                lines.append("⚠️ البيانات المتاحة أقل من العدد المطلوب.")
            lines.append(f"🧪 نقطة الفصل: {section.get('midpoint')}")
            for strategy in STRATEGIES:
                res = section.get("results", {}).get(strategy, {})
                if res.get("error"):
                    lines.append(f"❌ {STRATEGY_NAMES[strategy]}: {res['error']}")
                    continue
                cal, hold = res.get("calibration", {}), res.get("holdout", {})
                lines.append(f"<b>{STRATEGY_NAMES[strategy]}</b>")
                lines.append("  المعايرة: " + self._fmt_metric(cal))
                lines.append("  الاحتجازي: " + self._fmt_metric(hold))
                c05 = res.get("holdout_cost_005R", {})
                c10 = res.get("holdout_cost_010R", {})
                lines.append(f"  حساسية التكلفة: صافي {c05.get('net_after_cost_r', 0):+.1f}R عند 0.05R/صفقة؛ "
                             f"{c10.get('net_after_cost_r', 0):+.1f}R عند 0.10R/صفقة")
                lines.append(f"  تشخيص الإشارات: إعدادات مقبولة {res.get('setup_candidates', 0)}؛ "
                             f"إعدادات لم تُفعّل دخولًا {res.get('unfilled_setups', 0)}")
                if not hold.get("trades", 0):
                    rejected = res.get("direction_rejections", {})
                    top = sorted(rejected.items(), key=lambda item: item[1], reverse=True)[:3]
                    if top:
                        lines.append("  أكثر أسباب الرفض: " + "؛ ".join(
                            f"{reason} ({count})" for reason, count in top))
                if hold.get("trades", 0) < 30:
                    lines.append("  ⚠️ أقل من 30 صفقة في مجموعة الاحتجاز؛ العينة ضعيفة للحكم.")
            lines.append("")
        lines.append("قرار المختبر: لا يُفعّل أي مرشح تلقائيًا. المطلوب صافي موجب بعد التكلفة، PF > 1.2، "
                     "تراجع مقبول، وعينة مستقلة كافية؛ نسبة الفوز وحدها لا تكفي.")
        return "\n".join(lines)
