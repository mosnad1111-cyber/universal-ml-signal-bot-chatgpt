"""GitHub Actions bridge for real-history LEAN backtests requested from Telegram."""
from __future__ import annotations

import html
import json
import math
import os
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import requests


DEFAULT_REPOSITORY = "mosnad1111-cyber/universal-ml-signal-bot-chatgpt"
WORKFLOW_FILE = "lean-real-backtest.yml"
LAB_WORKFLOW_FILE = "lean-strategy-lab.yml"
THRESHOLD_WORKFLOW_FILE = "lean-threshold-test.yml"
WORKFLOW_NAME = "LEAN real market backtest"
LAB_STRATEGIES = ("baseline", "trend_pullback", "breakout_retest", "momentum_continuation")
LAB_STRATEGY_NAMES = {
    "baseline": "الاستراتيجية الحالية",
    "trend_pullback": "الارتداد داخل الاتجاه",
    "breakout_retest": "اختراق ثم إعادة اختبار",
    "momentum_continuation": "استمرار الزخم",
}
REQUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")
ALLOWED_ASSETS = {"GOLD", "BTC"}
ALLOWED_TIMEFRAMES = {"5m", "15m", "1h"}


class LeanActionsError(RuntimeError):
    pass


class LeanActionsClient:
    """Dispatch real-market work to GitHub Actions and verify callbacks."""

    def __init__(self, db, session=None):
        self.db = db
        self.session = session or requests
        self.token = os.getenv("GITHUB_ACTIONS_TOKEN", "").strip()
        self.repository = os.getenv("GITHUB_ACTIONS_REPOSITORY", DEFAULT_REPOSITORY).strip()
        self.callback_base_url = (
            os.getenv("LEAN_CALLBACK_BASE_URL", "").strip()
            or os.getenv("RENDER_EXTERNAL_URL", "").strip()
        ).rstrip("/")
        self.api_base = f"https://api.github.com/repos/{self.repository}"

    @property
    def configured(self) -> bool:
        return bool(self.token and self.repository and self.callback_base_url.startswith("https://"))

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise LeanActionsError("GITHUB_ACTIONS_TOKEN غير مضبوط في Render.")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def validate_request(asset_key: str, timeframes: list[str]) -> None:
        if asset_key not in ALLOWED_ASSETS:
            raise LeanActionsError("الأصل غير مدعوم.")
        if not timeframes or any(tf not in ALLOWED_TIMEFRAMES for tf in timeframes):
            raise LeanActionsError("الفريمات يجب أن تكون من 5m أو 15m أو 1h.")
        if len(set(timeframes)) != len(timeframes):
            raise LeanActionsError("تم اختيار فريم مكرر.")

    def dispatch(self, request_id: str, asset_key: str, timeframes: list[str]) -> None:
        if not REQUEST_ID_RE.fullmatch(request_id or ""):
            raise LeanActionsError("معرّف الاختبار غير صالح.")
        self.validate_request(asset_key, timeframes)
        if not self.configured:
            raise LeanActionsError(
                "يلزم إعداد GITHUB_ACTIONS_TOKEN ووجود رابط Render HTTPS قبل تشغيل LEAN."
            )

        response = self.session.post(
            f"{self.api_base}/actions/workflows/{WORKFLOW_FILE}/dispatches",
            headers=self._headers(),
            json={
                "ref": "main",
                "inputs": {
                    "asset": asset_key,
                    "timeframes": ",".join(timeframes),
                    "request_id": request_id,
                    "callback_url": self.callback_base_url + "/lean-backtest/callback",
                },
            },
            timeout=(10, 30),
        )
        if response.status_code != 204:
            detail = (getattr(response, "text", "") or "")[:500]
            raise LeanActionsError(
                f"GitHub رفض تشغيل LEAN ({response.status_code}). {detail}"
            )

    def dispatch_strategy_lab(self, request_id: str, asset_key: str, timeframes: list[str]) -> None:
        """Start the real-history strategy lab; each candidate is executed by LEAN."""
        if not REQUEST_ID_RE.fullmatch(request_id or ""):
            raise LeanActionsError("معرّف المختبر غير صالح.")
        self.validate_request(asset_key, timeframes)
        if not self.configured:
            raise LeanActionsError(
                "يلزم إعداد GITHUB_ACTIONS_TOKEN ووجود رابط Render HTTPS قبل تشغيل مختبر LEAN."
            )
        response = self.session.post(
            f"{self.api_base}/actions/workflows/{LAB_WORKFLOW_FILE}/dispatches",
            headers=self._headers(),
            json={
                "ref": "main",
                "inputs": {
                    "asset": asset_key,
                    "timeframes": ",".join(timeframes),
                    "request_id": request_id,
                    "callback_url": self.callback_base_url + "/lean-backtest/callback",
                },
            },
            timeout=(10, 30),
        )
        if response.status_code != 204:
            detail = (getattr(response, "text", "") or "")[:500]
            raise LeanActionsError(
                f"GitHub رفض تشغيل مختبر LEAN ({response.status_code}). {detail}"
            )

    def dispatch_threshold_test(self, request_id: str, asset_key: str, timeframes: list[str]) -> None:
        """Start the real-history Score/AI grid; every candidate is executed by LEAN."""
        if not REQUEST_ID_RE.fullmatch(request_id or ""):
            raise LeanActionsError("معرّف اختبار العتبات غير صالح.")
        self.validate_request(asset_key, timeframes)
        if not self.configured:
            raise LeanActionsError(
                "يلزم إعداد GITHUB_ACTIONS_TOKEN ووجود رابط Render HTTPS قبل تشغيل اختبار العتبات على LEAN."
            )
        response = self.session.post(
            f"{self.api_base}/actions/workflows/{THRESHOLD_WORKFLOW_FILE}/dispatches",
            headers=self._headers(),
            json={
                "ref": "main",
                "inputs": {
                    "asset": asset_key,
                    "timeframes": ",".join(timeframes),
                    "request_id": request_id,
                    "callback_url": self.callback_base_url + "/lean-backtest/callback",
                },
            },
            timeout=(10, 30),
        )
        if response.status_code != 204:
            detail = (getattr(response, "text", "") or "")[:500]
            raise LeanActionsError(
                f"GitHub رفض تشغيل اختبار العتبات على LEAN ({response.status_code}). {detail}"
            )

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self.session.get(url, headers=self._headers(), timeout=(10, 45))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise LeanActionsError("استجابة GitHub غير متوقعة.")
        return payload

    def _read_artifact(self, run_id: int, request_id: str, strategy_lab: bool = False,
                       threshold_test: bool = False) -> dict[str, Any]:
        artifacts_payload = self._get_json(f"{self.api_base}/actions/runs/{run_id}/artifacts")
        expected_name = (
            f"lean-threshold-test-{request_id}" if threshold_test
            else (f"lean-strategy-lab-{request_id}" if strategy_lab
                  else f"lean-real-backtest-{request_id}")
        )
        artifact = next(
            (
                item for item in artifacts_payload.get("artifacts", [])
                if item.get("name") == expected_name and not item.get("expired")
            ),
            None,
        )
        if not artifact:
            raise LeanActionsError("انتهى التشغيل بنجاح لكن تقرير LEAN لم يُرفق كملف Artifact.")

        response = self.session.get(
            artifact["archive_download_url"],
            headers=self._headers(),
            timeout=(15, 120),
        )
        response.raise_for_status()
        raw = response.content
        if len(raw) > 100 * 1024 * 1024:
            raise LeanActionsError("حجم تقرير LEAN تجاوز الحد الآمن للتحميل.")
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                names = archive.namelist()
                summary_name = next(
                    (name for name in names if name.endswith("/lean-summary.json") or name == "lean-summary.json"),
                    None,
                )
                if not summary_name:
                    raise LeanActionsError("ملف الملخص غير موجود داخل Artifact.")
                summary = json.loads(archive.read(summary_name).decode("utf-8"))
        except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LeanActionsError(f"تعذر قراءة Artifact الخاص بتقرير LEAN: {exc}") from exc
        if summary.get("request_id") != request_id:
            raise LeanActionsError("معرّف التقرير لا يطابق طلب Telegram.")
        return summary

    @staticmethod
    def format_report(summary: dict[str, Any]) -> str:
        asset_labels = {"GOLD": "الذهب XAU/USD", "BTC": "البيتكوين BTC/USDT"}
        asset_key = summary.get("asset_key", "")
        lines = [
            "🧪 <b>تقرير LEAN — باكتيست تاريخي ببيانات سوق فعلية</b>",
            f"الأصل: <b>{html.escape(asset_labels.get(asset_key, asset_key))}</b>",
            f"محرك التنفيذ: <b>{html.escape(str(summary.get('engine', 'QuantConnect LEAN')))}</b>",
            "مصدر الشموع: مزوّد السوق التاريخي، وليس fixture الاختبار التجريبي.",
            "",
        ]
        for tf in summary.get("timeframes", []):
            meta = tf.get("metadata", {})
            report = tf.get("lean_report", {})
            source = html.escape(str(meta.get("market_data_source", "غير معروف")))
            volume_note = html.escape(str(meta.get("volume_note", "")))
            label = html.escape(str(meta.get("timeframe", "?")))
            lines.extend([
                f"⏱️ <b>{label}</b>",
                f"المصدر: {source}",
                f"الشموع المطلوبة: {int(meta.get('requested_bars', 0)):,}",
                f"الشموع الخام المستلمة: {int(meta.get('raw_bars', 0)):,}",
                f"الشموع المكتملة: {int(meta.get('completed_bars', 0)):,}",
                f"الشموع المستخدمة بعد الميزات: {int(meta.get('usable_bars', 0)):,}",
                f"الفترة UTC: {html.escape(str(meta.get('snapshot_start', '—')))} → {html.escape(str(meta.get('snapshot_end', '—')))}",
                f"آخر شمعة تاريخية: {html.escape(str(meta.get('end', '—')))}",
                f"شموع اليوم UTC الحالي المستبعدة عمدًا: {int(meta.get('end_window_excluded_bars', 0))}",
                f"إشارات XGBoost المقبولة: {int(meta.get('signals_count', 0))}",
                f"الصفقات المنفذة داخل LEAN: {int(report.get('trades', 0))}",
                f"الرابحة / الخاسرة: {int(report.get('wins', 0))} / {int(report.get('losses', 0))}",
                f"Gross: {float(report.get('gross_r', 0.0)):+.2f}R",
                f"التكاليف المفترضة: {float(report.get('fees_r', 0.0)):.2f}R",
                f"رسوم نموذج LEAN: ${float(report.get('lean_total_fees_usd', 0.0)):.4f}",
                f"Net: <b>{float(report.get('net_r', 0.0)):+.2f}R</b>",
                f"إعدادات غير محسومة: {int(report.get('unresolved_trades', 0))} | غير مفعّلة: {int(report.get('unfilled_setups', 0))}",
            ])
            if meta.get("history_partial"):
                lines.append("⚠️ التاريخ المتاح أقل من العدد المطلوب؛ راجع أعداد الشموع أعلاه.")
            if volume_note:
                lines.append(f"ملاحظة الحجم: {volume_note}")
            errors = report.get("order_errors") or []
            if errors:
                lines.append("أخطاء الأوامر: " + html.escape("; ".join(map(str, errors[:3]))))
            details = report.get("trades_detail") or []
            if details:
                lines.append("تفاصيل آخر الصفقات:")
                for trade in details[-8:]:
                    lines.append(
                        "  • {direction} {result} | Entry {entry:.6g} | Exit {exit:.6g} | {net:+.2f}R".format(
                            direction=html.escape(str(trade.get("direction", ""))),
                            result=html.escape(str(trade.get("result", ""))),
                            entry=float(trade.get("entry_fill", trade.get("signal_entry", 0.0))),
                            exit=float(trade.get("exit_fill", 0.0)),
                            net=float(trade.get("net_r", 0.0)),
                        )
                    )
            lines.append("")
        lines.extend([
            "⚠️ رسوم LEAN هنا ناتجة عن افتراض التكلفة المحدد في التقرير (0.05R لكل صفقة ذهابًا وإيابًا)، وليست عمولات وسيط فعلية.",
            "⚠️ الذهب يستخدم شموع OHLC فعلية من Dukascopy، لكن Volume في تغذية المشروع قيمة محايدة وليست حجم تداول حقيقي.",
            "ℹ️ تُستبعد شموع يوم UTC الحالي عمدًا لأن LEAN يقفل تاريخ النهاية عند آخر يوم مكتمل؛ تدريب XGBoost والتنفيذ داخل LEAN يستخدمان النافذة التاريخية نفسها.",
            "⚠️ هذا باكتيست تاريخي، وليس تنفيذًا حقيقيًا لدى وسيط أو ضمانًا للأرباح. اختبر على Paper Trading قبل أي تداول حي.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _format_lab_metric(metric: dict[str, Any]) -> str:
        if not metric or not int(metric.get("trades", 0)):
            return "0 صفقة محسومة"
        pf = metric.get("profit_factor", 0.0)
        if pf in ("Infinity", "inf", "∞") or (
            isinstance(pf, (int, float)) and math.isinf(float(pf))
        ):
            pf_text = "∞"
        elif pf is None:
            pf_text = "—"
        else:
            pf_text = f"{float(pf):.2f}"
        net_r = metric.get("net_after_cost_r", metric.get("net_r", 0.0))
        return (
            f"{int(metric.get('trades', 0))} صفقة | "
            f"WR {float(metric.get('win_rate', 0.0)):.1f}% | "
            f"Net {float(net_r):+.2f}R | PF {pf_text} | "
            f"DD {float(metric.get('max_drawdown_r', 0.0)):.2f}R"
        )

    @classmethod
    def _format_lab_candidate(cls, candidate: dict[str, Any], include_sides: bool = True) -> list[str]:
        strategy = html.escape(str(candidate.get("strategy_name", candidate.get("strategy_type", "—"))))
        score = float(candidate.get("score_threshold", 0.0))
        ai = float(candidate.get("ai_threshold", 0.0)) * 100
        calibration = candidate.get("calibration_net", {})
        holdout = candidate.get("holdout_net", {})
        cost_005 = candidate.get("holdout_cost_005R", {})
        cost_010 = candidate.get("holdout_cost_010R", {})
        lines = [
            f"  <b>{strategy}</b> | Score ≥ {score:g} | AI ≥ {ai:.0f}%",
            f"    Calibration: {cls._format_lab_metric(calibration)}",
            f"    Holdout: {cls._format_lab_metric(holdout)}",
            f"    حساسية Holdout: {float(cost_005.get('net_after_cost_r', 0.0)):+.2f}R عند 0.05R؛ "
            f"{float(cost_010.get('net_after_cost_r', 0.0)):+.2f}R عند 0.10R",
        ]
        if include_sides:
            directional = candidate.get("directional", {})
            for direction, label in (("BUY", "BUY شراء"), ("SELL", "SELL بيع")):
                side = directional.get(direction, {})
                side_cal = side.get("calibration_net", {})
                side_hold = side.get("holdout_net", {})
                side_005 = side.get("holdout_cost_005R", {})
                side_010 = side.get("holdout_cost_010R", {})
                lines.append(
                    f"    {label}: Cal {cls._format_lab_metric(side_cal)} | "
                    f"Holdout {cls._format_lab_metric(side_hold)} | "
                    f"تكلفة 0.05/0.10R: "
                    f"{float(side_005.get('net_after_cost_r', 0.0)):+.2f}/"
                    f"{float(side_010.get('net_after_cost_r', 0.0)):+.2f}R"
                )
        return lines

    @classmethod
    def format_strategy_lab_report(cls, summary: dict[str, Any]) -> str:
        asset_labels = {"GOLD": "الذهب XAU/USD", "BTC": "البيتكوين BTC/USDT"}
        lines = [
            "🧭 <b>المختبر الموحد — الاستراتيجيات × عتبات Score/AI</b>",
            f"الأصل: <b>{html.escape(asset_labels.get(summary.get('asset_key', ''), str(summary.get('asset_key', ''))))}</b>",
            f"المحرك: <b>{html.escape(str(summary.get('engine', 'QuantConnect LEAN')))}</b>",
            "كل استراتيجية تستخدم Walk-Forward خاصًا بها؛ شبكة العتبات تُطبّق على التوقعات نفسها داخل الاستراتيجية والفريم.",
            "كل تركيبة استراتيجية/عتبة تُنفّذ بصورة مستقلة داخل LEAN؛ Python لا يحسم الصفقات.",
            "الترتيب والاختيار من Calibration فقط؛ Holdout مستقل ولا يدخل في اختيار المرشحين.",
            "",
        ]
        for section in summary.get("timeframes", []):
            common = section.get("metadata", {})
            tf = html.escape(str(common.get("timeframe", "?")))
            source = html.escape(str(common.get("market_data_source", "غير معروف")))
            active = common.get("active_thresholds", {})
            lines.extend([
                f"⏱️ <b>{tf}</b>",
                f"المصدر: {source}",
                f"الشموع: {int(common.get('raw_bars', 0)):,} خام / "
                f"{int(common.get('completed_bars', 0)):,} مكتملة / "
                f"{int(common.get('usable_bars', 0)):,} مستخدمة",
                f"الفترة UTC: {html.escape(str(common.get('snapshot_start', '—')))} → "
                f"{html.escape(str(common.get('snapshot_end', '—')))}",
                f"العتبات الحية الحالية: Score ≥ {float(active.get('score', 0.0)):g} | "
                f"AI ≥ {float(active.get('ai', 0.0))*100:.0f}%",
                f"شبكة العتبات: {len(section.get('scores', []))} Scores × "
                f"{len(section.get('ai_probabilities', []))} AI = "
                f"{int(section.get('candidate_count_per_strategy', 0))} تركيبة لكل استراتيجية",
                f"تنفيذ LEAN: {int(section.get('executed_candidates', 0))}/"
                f"{int(section.get('expected_candidates', 0))} | "
                f"غير قابلة للاختبار: {int(section.get('not_testable_candidates', 0))}",
            ])
            if common.get("history_partial"):
                lines.append("⚠️ التاريخ المتاح أقل من العدد المطلوب.")
            if common.get("volume_note"):
                lines.append(f"ملاحظة الحجم: {html.escape(str(common['volume_note']))}")

            baseline = section.get("live_baseline")
            if baseline:
                lines.append("🟦 <b>خط الأساس الحي — الاستراتيجية الحالية مع العتبات الحالية</b>")
                lines.extend(cls._format_lab_candidate(baseline))

            selected = section.get("calibration_selected")
            if selected:
                lines.append("🏆 <b>أفضل تركيبة مشتركة حسب Calibration فقط</b>")
                lines.extend(cls._format_lab_candidate(selected))
            else:
                lines.append(
                    "⚪ لا توجد تركيبة اجتازت شروط المعايرة: 30 صفقة مكتملة على الأقل، "
                    "Net موجب، وPF أكبر من 1."
                )

            lines.append("📊 <b>أفضل تركيبة مؤهلة من كل استراتيجية</b>")
            strategy_best = section.get("strategy_best", {})
            results = section.get("results", {})
            for strategy in LAB_STRATEGIES:
                strategy_result = results.get(strategy, {})
                ready = int(strategy_result.get("executed_candidates", 0))
                unavailable = int(strategy_result.get("not_testable_candidates", 0))
                best = strategy_best.get(strategy)
                name = html.escape(LAB_STRATEGY_NAMES.get(strategy, strategy))
                lines.append(f"  {name}: نفّذ {ready} تركيبة؛ غير قابلة للاختبار {unavailable}")
                if best:
                    lines.extend(cls._format_lab_candidate(best, include_sides=False)[0:3])
                else:
                    fit = strategy_result.get("model_fit_diagnostics", {})
                    if strategy_result.get("error"):
                        lines.append(f"    لا يوجد مرشح مؤهل: {html.escape(str(strategy_result['error'])[:160])}")
                    elif fit.get("fit_failures"):
                        lines.append(f"    فشل إعادة التدريب: {int(fit.get('fit_failures', 0))}")

            lines.append("🧭 <b>أفضل تركيبة لكل اتجاه — تشخيص مستقل من Calibration</b>")
            directional_best = section.get("directional_best", {})
            for direction, label in (("BUY", "BUY شراء"), ("SELL", "SELL بيع")):
                candidate = directional_best.get(direction)
                if candidate:
                    lines.extend([f"  {label}:"] + cls._format_lab_candidate(candidate, include_sides=True))
                else:
                    lines.append(f"  {label}: لا توجد تركيبة مؤهلة بعينة 30 صفقة وNet موجب وPF أكبر من 1.")

            top = section.get("top_calibration_candidates") or []
            if top:
                lines.append("📋 <b>أفضل خمس تركيبات إجمالًا حسب Calibration</b>")
                for index, candidate in enumerate(top, 1):
                    lines.append(f"  #{index}")
                    lines.extend(cls._format_lab_candidate(candidate, include_sides=False))
            lines.append("")

        lines.extend([
            "Net الأساسي مأخوذ من سجل LEAN وتكلفة LEAN المفترضة 0.05R لكل صفقة مغلقة. "
            "حساسية 0.05R و0.10R للـHoldout سيناريوهات تكلفة وليست تسعيرًا فعليًا لوسيط.",
            "اختيار BUY أو SELL بشكل منفصل تشخيصي فقط؛ المرشح المشترك يُرتّب على نتيجة الاتجاهين معًا.",
            "لا يتم تغيير عتبات أو استراتيجيات التداول الحية تلقائيًا. يلزم اختبار المرشح على فترة مستقبلية مستقلة وPaper Trading قبل اعتماده.",
            "هذا بحث تاريخي ببيانات فعلية وليس ضمانًا للربح أو تنفيذًا لدى وسيطك.",
        ])
        return "\n".join(lines)

    @classmethod
    def format_threshold_test_report(cls, summary: dict[str, Any]) -> str:
        asset_labels = {"GOLD": "الذهب XAU/USD", "BTC": "البيتكوين BTC/USDT"}
        lines = [
            "🔬 <b>اختبار عتبات AI / Score — تنفيذ LEAN تاريخي حقيقي</b>",
            f"الأصل: <b>{html.escape(asset_labels.get(summary.get('asset_key', ''), str(summary.get('asset_key', ''))))}</b>",
            "كل التركيبات تستخدم بيانات OHLC نفسها وتوقعات Walk-Forward نفسها داخل كل فريم؛ LEAN وحده يحسم الدخول والخروج والرسوم.",
            "اختيار المرشح يتم من النصف الأول (المعايرة) فقط؛ النصف الثاني (Holdout) للقياس المستقل ولا يدخل في ترتيب المرشحين.",
            "",
        ]
        for section in summary.get("timeframes", []):
            common = section.get("metadata", {})
            timeframe = html.escape(str(common.get("timeframe", "?")))
            active = section.get("active_thresholds", {})
            source = html.escape(str(common.get("market_data_source", "غير معروف")))
            lines.extend([
                f"⏱️ <b>{timeframe}</b>",
                f"المصدر: {source}",
                f"الشموع: {int(common.get('raw_bars', 0)):,} خام / {int(common.get('completed_bars', 0)):,} مكتملة / {int(common.get('usable_bars', 0)):,} مستخدمة",
                f"الفترة UTC: {html.escape(str(common.get('snapshot_start', '—')))} → {html.escape(str(common.get('snapshot_end', '—')))}",
                f"العتبات الحية الحالية: Score ≥ {float(active.get('score', 0.0)):g} | AI ≥ {float(active.get('ai', 0.0))*100:.0f}%",
                f"التركيبات المنفذة: {int(section.get('executed_candidates', 0))} | غير القابلة للاختبار: {int(section.get('not_testable_candidates', 0))}",
            ])
            if common.get("history_partial"):
                lines.append("⚠️ التاريخ المتاح أقل من العدد المطلوب.")
            by_id = {}
            baseline = section.get("live_baseline")
            selected = section.get("calibration_selected")
            for item in (baseline, selected, *(section.get("top_calibration_candidates") or [])):
                if item and item.get("status") == "executed":
                    by_id[item.get("threshold_id")] = item
            ordered = list(by_id.values())
            if baseline:
                lines.append("🟦 <b>خط الأساس — العتبات الحية</b>")
                lines.append(cls._format_threshold_candidate(baseline))
            if selected:
                lines.append("🏅 <b>أفضل مرشح في المعايرة فقط</b>")
                lines.append(cls._format_threshold_candidate(selected))
            else:
                lines.append("⚪ لم يستوفِ أي مرشح شروط الاختيار في المعايرة (30 صفقة مكتملة على الأقل، Net موجب، وPF أكبر من 1).")
            if ordered:
                lines.append("📋 <b>أفضل نتائج المعايرة المعروضة</b>")
                for item in ordered:
                    lines.append(cls._format_threshold_candidate(item))
            failed = [
                item for item in section.get("threshold_results", [])
                if item.get("status") == "not_testable"
            ]
            if failed:
                first_reason = str(failed[0].get("error", "غير محدد"))[:180]
                lines.append(f"أسباب عدم الاختبار: {len(failed)} تركيبة؛ مثال: {html.escape(first_reason)}")
            lines.append("")
        lines.extend([
            "مقياس Net في نتائج LEAN يخص افتراض تكلفة إجمالية 0.05R لكل صفقة مكتملة. حساسية 0.10R لكل صفقة معروضة للـHoldout وليست تسعيرًا فعليًا لوسيط.",
            "لا يتم تغيير العتبات الحية تلقائيًا. لا تعتمد المرشح قبل مراجعة Holdout ثم اختباره على فترة مستقبلية جديدة مستقلة.",
            "هذا اختبار تاريخي ببيانات فعلية وليس ضمانًا للربح أو تنفيذًا لدى وسيطك.",
        ])
        return "\n".join(lines)

    @classmethod
    def _format_threshold_candidate(cls, candidate: dict[str, Any]) -> str:
        score = float(candidate.get("score_threshold", 0.0))
        ai = float(candidate.get("ai_threshold", 0.0))
        calibration = candidate.get("calibration_net", {})
        holdout = candidate.get("holdout_net", {})
        stress = candidate.get("holdout_cost_010R", {})
        report = candidate.get("lean_report", {})
        pf = calibration.get("profit_factor", 0.0)
        if pf in ("Infinity", "inf", "∞") or (isinstance(pf, (int, float)) and math.isinf(float(pf))):
            pf_text = "∞"
        else:
            pf_text = f"{float(pf or 0.0):.2f}"
        directional_lines = []
        directional = candidate.get("directional") or {}
        for direction, arabic in (("BUY", "BUY شراء"), ("SELL", "SELL بيع")):
            side = directional.get(direction) or {}
            cal = side.get("calibration_net", {})
            test = side.get("holdout_net", {})
            stress_side = side.get("holdout_cost_010R", {})
            directional_lines.append(
                f"    {arabic}: Calibration {int(cal.get('trades', 0))} صفقة / "
                f"Net {float(cal.get('net_after_cost_r', 0.0)):+.2f}R / "
                f"WR {float(cal.get('win_rate', 0.0)):.1f}% / "
                f"PF {cls._format_threshold_pf(cal.get('profit_factor', 0.0))} | "
                f"Holdout {int(test.get('trades', 0))} صفقة / "
                f"Net {float(test.get('net_after_cost_r', 0.0)):+.2f}R / "
                f"ضغط 0.10R {float(stress_side.get('net_after_cost_r', 0.0)):+.2f}R"
            )
        return (
            f"  Score ≥ {score:g} | AI ≥ {ai*100:.0f}% | "
            f"Calibration: {int(calibration.get('trades', 0))} صفقة، Net {float(calibration.get('net_after_cost_r', 0.0)):+.2f}R، "
            f"WR {float(calibration.get('win_rate', 0.0)):.1f}%، PF {pf_text} | "
            f"Holdout: {int(holdout.get('trades', 0))} صفقة، Net {float(holdout.get('net_after_cost_r', 0.0)):+.2f}R، "
            f"ضغط 0.10R: {float(stress.get('net_after_cost_r', 0.0)):+.2f}R | "
            f"LEAN منفذ {int(report.get('trades', 0))} / غير مفعّل {int(candidate.get('unfilled_setups', 0))}\n"
            + "\n".join(directional_lines)
        )

    @staticmethod
    def _format_threshold_pf(value: Any) -> str:
        if value in ("Infinity", "inf", "∞") or (
            isinstance(value, (int, float)) and math.isinf(float(value))
        ):
            return "∞"
        if value is None:
            return "—"
        return f"{float(value or 0.0):.2f}"

    def handle_callback(self, run_id: int, request_id: str, telegram) -> tuple[dict[str, Any], int]:
        if not REQUEST_ID_RE.fullmatch(request_id or ""):
            return {"ok": False, "error": "invalid_request_id"}, 400
        pending_key = f"lean_request:{request_id}"
        raw_pending = self.db.get_setting(pending_key) if self.db else None
        if not raw_pending:
            return {"ok": False, "error": "unknown_or_expired_request"}, 404
        try:
            pending = json.loads(raw_pending)
        except (TypeError, json.JSONDecodeError):
            return {"ok": False, "error": "invalid_pending_request"}, 409

        strategy_lab = pending.get("mode") == "strategy_lab"
        threshold_test = pending.get("mode") == "threshold_test"
        try:
            run = self._get_json(f"{self.api_base}/actions/runs/{int(run_id)}")
            # GitHub re-runs keep the same run_id and increment run_attempt.
            # A failed first attempt may already have delivered its failure
            # notification, but that must not suppress the later success report.
            run_attempt = int(run.get("run_attempt", 1) or 1)
            if run_attempt < 1:
                raise LeanActionsError("GitHub workflow run attempt is invalid.")
            delivery_key = f"lean_delivered:{run_id}:{run_attempt}"
            legacy_delivery_key = f"lean_delivered:{run_id}"
            if (
                self.db.get_setting(delivery_key) == "1"
                or (run_attempt == 1 and self.db.get_setting(legacy_delivery_key) == "1")
            ):
                return {"ok": True, "duplicate": True}, 200

            expected_workflow = (
                THRESHOLD_WORKFLOW_FILE if threshold_test
                else (LAB_WORKFLOW_FILE if strategy_lab else WORKFLOW_FILE)
            )
            if run.get("path") != f".github/workflows/{expected_workflow}":
                raise LeanActionsError("Workflow path does not match the requested LEAN task.")
            if run.get("event") != "workflow_dispatch":
                raise LeanActionsError("Only manually dispatched LEAN runs are accepted.")
            expected_title = (
                f"LEAN threshold test {request_id}" if threshold_test
                else (f"LEAN strategy x thresholds {request_id}" if strategy_lab
                      else f"LEAN historical backtest {request_id}")
            )
            display_title = run.get("display_title")
            if display_title != expected_title:
                raise LeanActionsError("Run title does not match the Telegram request.")
            if run.get("head_branch") != "main":
                raise LeanActionsError("LEAN backtest must run from the main branch.")
            chat_id = pending.get("chat_id")
            if not chat_id or str(chat_id) != str(self.db.get_setting("telegram_chat_id", "")):
                raise LeanActionsError("Telegram chat does not match the registered bot chat.")

            workflow_status = run.get("status")
            workflow_conclusion = run.get("conclusion")
            failure_detail = None
            if workflow_status == "in_progress":
                # This is the workflow final step. It cannot become completed
                # until this callback returns, so inspect failed job steps and
                # the uploaded artifact rather than waiting for run completion.
                jobs_payload = self._get_json(f"{self.api_base}/actions/runs/{int(run_id)}/jobs")
                failed_steps = [
                    step.get("name", "unknown step")
                    for job in jobs_payload.get("jobs", [])
                    for step in job.get("steps", [])
                    if step.get("conclusion") == "failure"
                ]
                if failed_steps:
                    failure_detail = ", ".join(failed_steps[:4])
            elif workflow_status == "completed" and workflow_conclusion != "success":
                failure_detail = str(workflow_conclusion or "unknown")
            elif workflow_status != "completed":
                return {"ok": False, "error": "workflow_not_ready"}, 409

            if failure_detail:
                task_name = ("اختبار العتبات على LEAN" if threshold_test else ("مختبر الاستراتيجيات على LEAN" if strategy_lab else "باكتيست LEAN الحقيقي"))
                message = (
                    f"❌ <b>فشل {task_name}.</b>\n"
                    f"المرحلة: {html.escape(failure_detail)}\n"
                    f"<a href=\"{html.escape(run.get('html_url', ''), quote=True)}\">سجل التشغيل على GitHub</a>"
                )
                if not telegram.send(message, chat_id):
                    raise LeanActionsError("تعذر إرسال إشعار فشل LEAN إلى Telegram.")
                self.db.set_setting(delivery_key, "1")
                if run_attempt == 1:
                    self.db.set_setting(legacy_delivery_key, "1")
                self.db.set_setting(pending_key, json.dumps({**pending, "status": "failed", "run_id": run_id}))
                if self.db.get_setting("lean_active_request") == request_id:
                    self.db.set_setting("lean_active_request", "")
                return {"ok": True, "workflow_conclusion": workflow_conclusion or "failure"}, 200

            # Artifact upload is the last successful step before this callback.
            if threshold_test:
                summary = self._read_artifact(int(run_id), request_id, threshold_test=True)
            elif strategy_lab:
                summary = self._read_artifact(int(run_id), request_id, strategy_lab=True)
            else:
                summary = self._read_artifact(int(run_id), request_id)
            if summary.get("synthetic_fixture") is not False:
                raise LeanActionsError("رفض التقرير: وسم synthetic_fixture غير مطابق.")
            if summary.get("market_data_type") != "real_historical_ohlc":
                raise LeanActionsError("رفض التقرير: مصدر البيانات ليس تاريخ OHLC فعليًا.")
            if summary.get("asset_key") != pending.get("asset_key"):
                raise LeanActionsError("الأصل في التقرير لا يطابق طلب Telegram.")

            timeframes = summary.get("timeframes") or []
            expected_timeframes = pending.get("timeframes") or []
            got = {item.get("metadata", {}).get("timeframe") for item in timeframes}
            if got != set(expected_timeframes):
                raise LeanActionsError("الفريمات الموجودة في التقرير لا تطابق الطلب.")
            if strategy_lab:
                if summary.get("report_type") != "LEAN real historical strategy-threshold lab":
                    raise LeanActionsError("نوع التقرير لا يطابق المختبر الموحد للاستراتيجيات والعتبات.")
                required_metric_fields = (
                    "all_gross", "calibration_gross", "holdout_gross",
                    "all_net", "calibration_net", "holdout_net",
                    "holdout_cost_005R", "holdout_cost_010R",
                )
                for section in timeframes:
                    common = section.get("metadata", {})
                    if not common.get("market_data_source") or int(common.get("raw_bars", 0)) < 1200:
                        raise LeanActionsError("التاريخ السوقي للمختبر الموحد ناقص أو غير موثق.")
                    scores = section.get("scores") or []
                    ai_values = section.get("ai_probabilities") or []
                    threshold_order = section.get("threshold_order") or []
                    if not scores or not ai_values or len(threshold_order) != len(scores) * len(ai_values):
                        raise LeanActionsError("شبكة عتبات Score/AI غير مكتملة.")
                    if len(threshold_order) != len(set(threshold_order)):
                        raise LeanActionsError("توجد تركيبات عتبات مكررة.")
                    results = section.get("results") or {}
                    if set(results) != set(LAB_STRATEGIES):
                        raise LeanActionsError("قائمة استراتيجيات المختبر الموحد غير مكتملة.")
                    flat_candidates = {}
                    executed = 0
                    not_testable = 0
                    baseline_candidates = 0
                    for strategy in LAB_STRATEGIES:
                        strategy_result = results[strategy]
                        candidate_order = strategy_result.get("candidate_order") or []
                        candidate_map = strategy_result.get("candidates") or {}
                        expected_order = [f"{strategy}__{item}" for item in threshold_order]
                        if candidate_order != expected_order or set(candidate_order) != set(candidate_map):
                            raise LeanActionsError(f"مصفوفة عتبات {strategy} غير مكتملة.")
                        strategy_executed = 0
                        strategy_not_testable = 0
                        for candidate_id in candidate_order:
                            item = candidate_map[candidate_id]
                            if item.get("status") == "not_testable":
                                if not item.get("error"):
                                    raise LeanActionsError(f"سبب عدم اختبار {candidate_id} غير موثق.")
                                strategy_not_testable += 1
                                not_testable += 1
                                continue
                            if item.get("status") != "executed":
                                raise LeanActionsError(f"حالة تركيبة {candidate_id} غير صالحة.")
                            if item.get("candidate_id") != candidate_id or item.get("strategy_type") != strategy:
                                raise LeanActionsError(f"هوية تركيبة {candidate_id} غير مطابقة.")
                            if not isinstance(item.get("score_threshold"), (int, float)) or not isinstance(item.get("ai_threshold"), (int, float)):
                                raise LeanActionsError(f"عتبات Score/AI غير موثقة في {candidate_id}.")
                            if not isinstance(item.get("signals_count"), int):
                                raise LeanActionsError(f"عدد قرارات {candidate_id} غير موثق.")
                            for field in required_metric_fields:
                                if not isinstance(item.get(field), dict):
                                    raise LeanActionsError(f"المقياس {field} مفقود في {candidate_id}.")
                            directional = item.get("directional")
                            if not isinstance(directional, dict) or set(directional) != {"BUY", "SELL"}:
                                raise LeanActionsError(f"تفصيل BUY/SELL غير مكتمل في {candidate_id}.")
                            for direction in ("BUY", "SELL"):
                                side = directional.get(direction) or {}
                                for field in required_metric_fields:
                                    if not isinstance(side.get(field), dict):
                                        raise LeanActionsError(f"مقاييس {direction} غير مكتملة في {candidate_id}.")
                            for split_name in ("all", "calibration", "holdout"):
                                gross_key = f"{split_name}_gross"
                                combined_count = int(item[gross_key].get("trades", -1))
                                side_count = sum(
                                    int(directional[direction][gross_key].get("trades", -1))
                                    for direction in ("BUY", "SELL")
                                )
                                if side_count != combined_count:
                                    raise LeanActionsError(
                                        f"عدد صفقات BUY/SELL لا يطابق الإجمالي في {candidate_id}/{split_name}."
                                    )
                            lean = item.get("lean_report") or {}
                            if lean.get("strategy") != candidate_id:
                                raise LeanActionsError(f"هوية سجل LEAN لا تطابق {candidate_id}.")
                            if int(lean.get("signals", -1)) != int(item["signals_count"]):
                                raise LeanActionsError(f"عدد إشارات LEAN لا يطابق {candidate_id}.")
                            expected_bars = int(common.get("bars_count", -1))
                            if not (
                                expected_bars == int(lean.get("bars", -2))
                                == int(lean.get("received_bars", -3))
                                == int(lean.get("reader_parsed_rows", -4))
                                == int(lean.get("reader_unique_timestamps", -5))
                            ):
                                raise LeanActionsError(f"لم يستقبل LEAN كامل شموع OHLC في {candidate_id}.")
                            if lean.get("order_errors"):
                                raise LeanActionsError(f"LEAN سجّل أخطاء أوامر في {candidate_id}.")
                            flat_candidates[candidate_id] = item
                            strategy_executed += 1
                            executed += 1
                            baseline_candidates += int(bool(item.get("is_live_baseline")))
                        if strategy_result.get("status") == "not_testable" and strategy_executed:
                            raise LeanActionsError(f"حالة الاستراتيجية {strategy} لا تطابق نتائج تركيباتها.")
                        if int(strategy_result.get("executed_candidates", -1)) != strategy_executed:
                            raise LeanActionsError(f"عدد التركيبات المنفذة غير مطابق للاستراتيجية {strategy}.")
                        if int(strategy_result.get("not_testable_candidates", -1)) != strategy_not_testable:
                            raise LeanActionsError(f"عدد التركيبات غير القابلة للاختبار غير مطابق للاستراتيجية {strategy}.")
                    if executed != int(section.get("executed_candidates", -1)):
                        raise LeanActionsError("إجمالي تركيبات LEAN المنفذة لا يطابق نتائج الاستراتيجيات.")
                    if not_testable != int(section.get("not_testable_candidates", -1)):
                        raise LeanActionsError("إجمالي التركيبات غير القابلة للاختبار لا يطابق النتائج.")
                    if executed == 0:
                        raise LeanActionsError("لا توجد تركيبة استراتيجية/عتبة نفذها LEAN.")
                    if baseline_candidates not in (0, 1):
                        raise LeanActionsError("يجب ألا يتكرر خط الأساس الحي داخل مصفوفة التركيبات.")

                    def verify_selection(candidate):
                        if candidate is None:
                            return
                        candidate_id = candidate.get("candidate_id")
                        if candidate_id not in flat_candidates:
                            raise LeanActionsError(f"المرشح المختار {candidate_id} غير موجود ضمن نتائج LEAN.")
                    verify_selection(section.get("live_baseline"))
                    verify_selection(section.get("calibration_selected"))
                    for candidate in section.get("top_calibration_candidates") or []:
                        verify_selection(candidate)
                    for candidate in (section.get("strategy_best") or {}).values():
                        verify_selection(candidate)
                    for candidate in (section.get("directional_best") or {}).values():
                        verify_selection(candidate)
                report_text = self.format_strategy_lab_report(summary)
            elif threshold_test:
                if summary.get("report_type") != "LEAN real historical threshold test":
                    raise LeanActionsError("نوع التقرير لا يطابق اختبار العتبات على LEAN.")
                for section in timeframes:
                    common = section.get("metadata", {})
                    if not common.get("market_data_source") or int(common.get("raw_bars", 0)) < 1200:
                        raise LeanActionsError("التاريخ السوقي لاختبار العتبات ناقص أو غير موثق.")
                    results = section.get("threshold_results") or []
                    if not results:
                        raise LeanActionsError("لا توجد نتائج لتركيبات العتبات في التقرير.")
                    active = section.get("active_thresholds") or {}
                    if not isinstance(active.get("score"), (int, float)) or not isinstance(active.get("ai"), (int, float)):
                        raise LeanActionsError("العتبات التشغيلية الحالية غير موثقة في التقرير.")
                    baseline_id = section.get("live_baseline_threshold_id")
                    seen = set()
                    for result in results:
                        threshold_id = result.get("threshold_id")
                        if not threshold_id or threshold_id in seen:
                            raise LeanActionsError("معرّفات تركيبات العتبات غير صالحة أو مكررة.")
                        seen.add(threshold_id)
                        if result.get("status") == "not_testable":
                            if not result.get("error"):
                                raise LeanActionsError(f"سبب عدم اختبار {threshold_id} غير موثق.")
                            continue
                        if result.get("status") != "executed":
                            raise LeanActionsError(f"حالة تركيبة العتبات {threshold_id} غير صالحة.")
                        meta = result.get("metadata", {})
                        report = result.get("lean_report", {})
                        if meta.get("strategy") != threshold_id or report.get("strategy") != threshold_id:
                            raise LeanActionsError(f"هوية تركيبة العتبات {threshold_id} لا تطابق تقرير LEAN.")
                        if meta.get("market_data_source") != common.get("market_data_source"):
                            raise LeanActionsError(f"مصدر بيانات {threshold_id} لا يطابق تاريخ الفريم.")
                        if (
                            int(meta.get("raw_bars", 0)) < 1200
                            or int(report.get("signals", -1)) != int(meta.get("signals_count", -2))
                        ):
                            raise LeanActionsError(f"بيانات أو إشارات {threshold_id} غير مكتملة.")
                        expected_bars = int(meta.get("bars_count", -1))
                        if not (
                            expected_bars == int(report.get("bars", -2))
                            == int(report.get("received_bars", -3))
                        ):
                            raise LeanActionsError(f"LEAN لم يستقبل كل شموع {threshold_id}.")
                        if (
                            int(report.get("reader_parsed_rows", -1)) != expected_bars
                            or int(report.get("reader_unique_timestamps", -1)) != expected_bars
                            or report.get("order_errors")
                        ):
                            raise LeanActionsError(f"لم يجتز تنفيذ LEAN والتحقق من الشموع لتركيبة {threshold_id}.")
                        for field in ("score_threshold", "ai_threshold"):
                            if not isinstance(meta.get(field), (int, float)):
                                raise LeanActionsError(f"عتبات {threshold_id} غير موثقة.")
                    executed_baselines = [
                        item for item in results
                        if item.get("status") == "executed"
                        and item.get("metadata", {}).get("is_live_baseline") is True
                    ]
                    if int(section.get("executed_candidates", 0)) > 0 and len(executed_baselines) != 1:
                        raise LeanActionsError("يجب أن يحتوي كل فريم على خط أساس حي واحد ضمن نتائج LEAN.")
                    if executed_baselines:
                        baseline_meta = executed_baselines[0].get("metadata", {})
                        if baseline_id != executed_baselines[0].get("threshold_id"):
                            raise LeanActionsError("معرّف خط الأساس لا يطابق تركيبة العتبات الحية.")
                        if (
                            not math.isclose(float(baseline_meta.get("score_threshold", -1)), float(active["score"]), abs_tol=1e-9)
                            or not math.isclose(float(baseline_meta.get("ai_threshold", -1)), float(active["ai"]), abs_tol=1e-9)
                        ):
                            raise LeanActionsError("تركيبة خط الأساس لا تطابق العتبات التشغيلية الحالية.")
                    elif baseline_id is not None:
                        raise LeanActionsError("خط الأساس في الملخص لا يطابق أي نتيجة منفذة.")
                report_text = self.format_threshold_test_report(summary)
            else:
                for item in timeframes:
                    meta = item.get("metadata", {})
                    report = item.get("lean_report", {})
                    if not meta.get("market_data_source") or int(meta.get("raw_bars", 0)) < 1200:
                        raise LeanActionsError("البيانات التاريخية ناقصة أو مصدرها غير موثق.")
                    if int(report.get("bars", -1)) != int(report.get("received_bars", -2)):
                        raise LeanActionsError("LEAN لم يستقبل جميع الشموع المجهزة.")
                    if report.get("order_errors"):
                        raise LeanActionsError("LEAN سجّل أخطاء في تنفيذ الأوامر.")
                report_text = self.format_report(summary)
            if not telegram.send_long(report_text, chat_id):
                raise LeanActionsError("تعذر إرسال تقرير LEAN كاملًا إلى Telegram.")
            self.db.set_setting(delivery_key, "1")
            if run_attempt == 1:
                self.db.set_setting(legacy_delivery_key, "1")
            self.db.set_setting(pending_key, json.dumps({**pending, "status": "completed", "run_id": run_id}))
            if self.db.get_setting("lean_active_request") == request_id:
                self.db.set_setting("lean_active_request", "")
            return {"ok": True, "run_id": run_id}, 200
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500
