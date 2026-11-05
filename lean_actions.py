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

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self.session.get(url, headers=self._headers(), timeout=(10, 45))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise LeanActionsError("استجابة GitHub غير متوقعة.")
        return payload

    def _read_artifact(self, run_id: int, request_id: str, strategy_lab: bool = False) -> dict[str, Any]:
        artifacts_payload = self._get_json(f"{self.api_base}/actions/runs/{run_id}/artifacts")
        expected_name = (
            f"lean-strategy-lab-{request_id}" if strategy_lab
            else f"lean-real-backtest-{request_id}"
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
        if pf in ("Infinity", "inf", "∞") or (isinstance(pf, (int, float)) and math.isinf(float(pf))):
            pf_text = "∞"
        elif pf is None:
            pf_text = "—"
        else:
            pf_text = f"{float(pf):.2f}"
        return (
            f"{int(metric.get('trades', 0))} صفقة | "
            f"فوز {float(metric.get('win_rate', 0.0)):.1f}% | "
            f"Gross {float(metric.get('net_r', 0.0)):+.2f}R | "
            f"PF {pf_text} | "
            f"DD {float(metric.get('max_drawdown_r', 0.0)):.2f}R"
        )

    @classmethod
    def format_strategy_lab_report(cls, summary: dict[str, Any]) -> str:
        asset_labels = {"GOLD": "الذهب XAU/USD", "BTC": "البيتكوين BTC/USDT"}
        names = summary.get("strategy_names") or LAB_STRATEGY_NAMES
        lines = [
            "🧭 <b>مختبر الاستراتيجيات — تنفيذ LEAN تاريخي حقيقي</b>",
            f"الأصل: <b>{html.escape(asset_labels.get(summary.get('asset_key', ''), str(summary.get('asset_key', ''))))}</b>",
            f"المحرك: <b>{html.escape(str(summary.get('engine', 'QuantConnect LEAN')))}</b>",
            "كل استراتيجية لها Walk-Forward مستقل؛ Python يجهّز قرارات الدخول فقط، وLEAN ينفّذ الصفقات ويحدد نتائجها.",
            "المعايرة والاحتجاز محسوبتان من سجل صفقات LEAN؛ الصفقات العابرة لنقطة الفصل لا تدخل في أي مجموعة.",
            "",
        ]
        for section in summary.get("timeframes", []):
            common = section.get("metadata", {})
            tf = html.escape(str(common.get("timeframe", "?")))
            source = html.escape(str(common.get("market_data_source", "غير معروف")))
            lines.extend([
                f"⏱️ <b>{tf}</b>",
                f"المصدر: {source}",
                f"الشموع: {int(common.get('raw_bars', 0)):,} خام / "
                f"{int(common.get('completed_bars', 0)):,} مكتملة / "
                f"{int(common.get('usable_bars', 0)):,} مستخدمة",
                f"الفترة UTC: {html.escape(str(common.get('snapshot_start', '—')))} → "
                f"{html.escape(str(common.get('snapshot_end', '—')))}",
                f"نقطة الفصل: {html.escape(str(common.get('midpoint', '—')))}",
                f"رصيد LEAN الفني: ${float(common.get('lean_initial_cash_usd', 100000.0)):,.0f} لتغطية وحدة واحدة "
                f"مع هامش {float(common.get('lean_cash_buffer_multiplier', 1.5)) * 100 - 100:.0f}%؛ "
                "كمية كل دخول/خروج ثابتة عند وحدة واحدة.",
            ])
            if common.get("history_partial"):
                lines.append("⚠️ التاريخ المتاح أقل من العدد المطلوب.")
            if common.get("volume_note"):
                lines.append(f"ملاحظة الحجم: {html.escape(str(common['volume_note']))}")
            for strategy in LAB_STRATEGIES:
                result = section.get("results", {}).get(strategy, {})
                label = html.escape(str(names.get(strategy, LAB_STRATEGY_NAMES[strategy])))
                lines.append(f"<b>{label}</b>")
                if result.get("status") == "not_testable":
                    lines.append("  ⚪ لم يصل النموذج إلى عينة Walk-Forward قابلة للاختبار داخل LEAN.")
                    lines.append(f"  السبب: {html.escape(str(result.get('error', 'غير محدد'))[:240])}")
                    continue
                if result.get("status") != "executed":
                    lines.append("  ❌ نتيجة الاستراتيجية غير متاحة أو لم يتم التحقق منها.")
                    continue
                lines.append("  المعايرة: " + cls._format_lab_metric(result.get("calibration", {})))
                lines.append("  الاحتجازي: " + cls._format_lab_metric(result.get("holdout", {})))
                c05 = result.get("holdout_cost_005R", {})
                c10 = result.get("holdout_cost_010R", {})
                lines.append(
                    f"  صافي الاحتجاز بعد التكلفة: {float(c05.get('net_after_cost_r', 0.0)):+.2f}R "
                    f"عند 0.05R/صفقة؛ {float(c10.get('net_after_cost_r', 0.0)):+.2f}R عند 0.10R/صفقة"
                )
                report = result.get("lean_report", {})
                lines.append(
                    f"  قرارات الاستراتيجية: {int(result.get('signals_count', 0))} | "
                    f"صفقات LEAN المنفذة: {int(report.get('trades', 0))} | "
                    f"غير مفعّلة: {int(result.get('unfilled_setups', 0))} | "
                    f"غير محسومة: {int(result.get('unresolved_trades', 0))}"
                )
                fit = result.get("model_fit_diagnostics", {})
                if fit.get("fit_failures") or fit.get("model_unavailable_candles"):
                    lines.append(
                        f"  التدريب: {int(fit.get('fit_attempts', 0))} محاولة، "
                        f"{int(fit.get('fit_failures', 0))} فشل إعادة تدريب، "
                        f"{int(fit.get('model_unavailable_candles', 0))} شمعة بلا نموذج"
                    )
                if int(result.get("holdout", {}).get("trades", 0)) < 30:
                    lines.append("  ⚠️ أقل من 30 صفقة في الاحتجاز؛ العينة ضعيفة للحكم.")
            lines.append("")
        lines.extend([
            "تكلفة LEAN الأساسية افتراض 0.05R لكل صفقة مغلقة؛ حساسية 0.10R سيناريو إضافي وليست بيانات سبريد أو عمولات وسيط فعلية.",
            "المختبر لا يفعّل استراتيجية تلقائيًا. يلزم احتجاز موجب بعد التكلفة، PF مناسب، تراجع مقبول، وعدد صفقات كافٍ، ثم فترة مستقبلية مستقلة قبل التداول.",
            "هذا بحث تاريخي وليس ضمانًا للأرباح أو توصية تداول.",
        ])
        return "\n".join(lines)

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

            expected_workflow = LAB_WORKFLOW_FILE if strategy_lab else WORKFLOW_FILE
            if run.get("path") != f".github/workflows/{expected_workflow}":
                raise LeanActionsError("Workflow path does not match the requested LEAN task.")
            if run.get("event") != "workflow_dispatch":
                raise LeanActionsError("Only manually dispatched LEAN runs are accepted.")
            expected_title = (
                f"LEAN strategy lab {request_id}" if strategy_lab
                else f"LEAN historical backtest {request_id}"
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
                task_name = "مختبر الاستراتيجيات على LEAN" if strategy_lab else "باكتيست LEAN الحقيقي"
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
            summary = self._read_artifact(int(run_id), request_id, strategy_lab=strategy_lab)
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
                if summary.get("report_type") != "LEAN real historical strategy research lab":
                    raise LeanActionsError("نوع التقرير لا يطابق مختبر الاستراتيجيات على LEAN.")
                for section in timeframes:
                    common = section.get("metadata", {})
                    if not common.get("market_data_source") or int(common.get("raw_bars", 0)) < 1200:
                        raise LeanActionsError("التاريخ السوقي للمختبر ناقص أو غير موثق.")
                    results = section.get("results") or {}
                    if set(results) != set(LAB_STRATEGIES):
                        raise LeanActionsError("قائمة استراتيجيات المختبر غير مكتملة.")
                    executed = 0
                    for strategy, result in results.items():
                        if result.get("status") == "not_testable":
                            if not result.get("error"):
                                raise LeanActionsError(f"سبب عدم اختبار {strategy} غير موثق.")
                            continue
                        if result.get("status") != "executed":
                            raise LeanActionsError(f"حالة استراتيجية {strategy} غير صالحة.")
                        meta = result.get("metadata", {})
                        report = result.get("lean_report", {})
                        if meta.get("strategy") != strategy or report.get("strategy") != strategy:
                            raise LeanActionsError(f"هوية استراتيجية {strategy} لا تطابق تقرير LEAN.")
                        if meta.get("market_data_source") != common.get("market_data_source"):
                            raise LeanActionsError(f"مصدر بيانات {strategy} لا يطابق تاريخ الفريم.")
                        if int(meta.get("raw_bars", 0)) < 1200 or int(report.get("signals", -1)) != int(meta.get("signals_count", -2)):
                            raise LeanActionsError(f"بيانات أو إشارات {strategy} غير مكتملة.")
                        expected_bars = int(meta.get("bars_count", -1))
                        if not (expected_bars == int(report.get("bars", -2)) == int(report.get("received_bars", -3))):
                            raise LeanActionsError(f"LEAN لم يستقبل كل شموع {strategy}.")
                        if int(report.get("reader_parsed_rows", -1)) != expected_bars or int(report.get("reader_unique_timestamps", -1)) != expected_bars:
                            raise LeanActionsError(f"فشل التحقق من صفوف OHLC داخل LEAN لاستراتيجية {strategy}.")
                        if report.get("order_errors"):
                            raise LeanActionsError(f"LEAN سجّل أخطاء أوامر في {strategy}.")
                        executed += 1
                    if executed == 0:
                        raise LeanActionsError("لا توجد استراتيجية قابلة للتنفيذ والتحقق في هذا الفريم.")
                report_text = self.format_strategy_lab_report(summary)
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
