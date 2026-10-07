import json
import logging
import os
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.utils import timezone

from .models import AttendanceTrend, ChurnRiskLevel, ClientRetentionMetrics

logger = logging.getLogger(__name__)


class RetentionAIService:
    """
    Predictive churn risk and natural-language retention insight service.
    Combines behavioral profile metrics with LLM synthesis (Gemini/Kimi) or
    a deterministic high-performance rule-based synthesizer fallback.
    """

    @classmethod
    def generate_client_risk_insight(
        cls,
        tenant,
        metrics: ClientRetentionMetrics,
        use_llm: bool = True
    ) -> dict:
        """
        Evaluates a client's retention metrics, churn risk score, and behavioral profile
        to generate actionable natural-language insights and staff recommendations.
        Persists ai_risk_summary and ai_evaluated_at on ClientRetentionMetrics.
        """
        now = timezone.now()
        client = metrics.client
 
        profile = {
            "client_id": str(metrics.client_id),
            "client_name": client.full_name,
            "client_email": client.email,
            "lifecycle_status": client.lifecycle_status,
            "churn_risk_score": metrics.churn_risk_score,
            "risk_level": metrics.risk_level,
            "days_since_last_visit": metrics.days_since_last_visit,
            "visits_last_7d": metrics.visits_last_7d,
            "visits_last_30d": metrics.visits_last_30d,
            "visits_prev_30d": metrics.visits_prev_30d,
            "attendance_trend": metrics.attendance_trend,
            "cancellation_rate": float(metrics.cancellation_rate),
            "no_show_rate": float(metrics.no_show_rate),
            "active_packages_count": metrics.active_packages_count,
            "total_credits_remaining": metrics.total_credits_remaining,
            "credit_utilization_rate": float(metrics.credit_utilization_rate),
            "nearest_package_expiry_at": metrics.nearest_package_expiry_at.isoformat() if metrics.nearest_package_expiry_at else None,
            "lifetime_value": float(metrics.lifetime_value),
            "estimated_monthly_value": float(metrics.estimated_monthly_value),
            "at_risk_revenue": float(metrics.at_risk_revenue),
            "failed_payments_last_90d": metrics.failed_payments_last_90d,
            "is_high_value": metrics.is_high_value,
            "risk_factors": metrics.risk_factors or [],
        }

        insight = None
 
        gemini_key = getattr(settings, 'GEMINI_API_KEY', os.environ.get('GEMINI_API_KEY', ''))
        kimi_key = getattr(settings, 'KIMI_API_KEY', os.environ.get('KIMI_API_KEY', os.environ.get('MOONSHOT_API_KEY', '')))

        if use_llm and (gemini_key or kimi_key):
            try:
                insight = cls._call_llm_synthesizer(profile, gemini_key, kimi_key)
            except Exception as e:
                logger.warning(f"LLM risk synthesis failed or unavailable ({e}). Falling back to rule-based engine.")
                insight = None
 
        if not insight:
            insight = cls._deterministic_synthesizer(metrics, profile)
 
        metrics.ai_risk_summary = insight.get("ai_risk_summary", "")
        metrics.ai_evaluated_at = now
        metrics.save(update_fields=['ai_risk_summary', 'ai_evaluated_at'])

        insight["ai_evaluated_at"] = now.isoformat()
        insight["client_id"] = str(metrics.client_id)
        insight["behavioral_profile"] = profile

        return insight

    @classmethod
    def _deterministic_synthesizer(cls, metrics: ClientRetentionMetrics, profile: dict) -> dict:
        """
        Fast (<5ms), deterministic synthesizer producing tailored natural-language
        risk summaries and specific staff recommendations based on risk factors.
        """
        client = metrics.client
        risk_score = metrics.churn_risk_score
        risk_factors = metrics.risk_factors or []
        is_high_val = metrics.is_high_value 

        if metrics.failed_payments_last_90d > 0:
            primary_driver = "Billing Failure"
            recommended_action = (
                f"Contact {client.full_name} to update billing info. "
                f"{metrics.failed_payments_last_90d} failed payment attempt(s) in last 90 days."
            )
        elif metrics.days_since_last_visit is not None and metrics.days_since_last_visit >= 60:
            primary_driver = "Prolonged Inactivity (60+ days)"
            recommended_action = (
                f"Initiate re-activation campaign for {client.full_name} with a personalized "
                "complimentary personal training session or buddy pass."
            )
        elif metrics.days_since_last_visit is not None and metrics.days_since_last_visit >= 30:
            primary_driver = "Inactivity (30+ days)"
            recommended_action = (
                f"Send friendly SMS check-in to {client.full_name} and invite to their favorite class format."
            )
        elif metrics.visits_prev_30d > 0 and metrics.visits_last_30d < (metrics.visits_prev_30d * 0.5):
            primary_driver = "Steep Attendance Drop (>50%)"
            recommended_action = (
                f"Assign instructor to check in with {client.full_name} on training roadblocks or schedule changes."
            )
        elif metrics.attendance_trend == AttendanceTrend.DECLINING:
            primary_driver = "Declining Attendance Velocity"
            recommended_action = (
                f"Proactively reach out to {client.full_name} to review progress milestones and adjust class schedules."
            )
        elif metrics.total_credits_remaining > 0 and metrics.credit_utilization_rate < Decimal('30.0'):
            primary_driver = "Credit Under-utilization"
            recommended_action = (
                f"Remind {client.full_name} of {metrics.total_credits_remaining} unused credits before expiry."
            )
        elif metrics.nearest_package_expiry_at and (metrics.nearest_package_expiry_at.date() - timezone.now().date()).days <= 7:
            days_exp = (metrics.nearest_package_expiry_at.date() - timezone.now().date()).days
            primary_driver = "Impending Package Expiry"
            recommended_action = (
                f"Offer package renewal incentive before pass expires in {max(0, days_exp)} day(s)."
            )
        elif metrics.no_show_rate >= Decimal('25.0') or metrics.cancellation_rate >= Decimal('40.0'):
            primary_driver = "High Cancellation/No-Show Frequency"
            recommended_action = (
                f"Discuss timetable flexibility or spot reservation alternatives with {client.full_name}."
            )
        elif risk_score >= 45:
            primary_driver = "Multi-Factor Disengagement"
            recommended_action = (
                f"Schedule high-touch manager review for {client.full_name} to re-establish regular studio routine."
            )
        else:
            primary_driver = "Routine Engagement Maintenance"
            recommended_action = (
                f"Maintain standard positive reinforcement touchpoints and celebrate milestones with {client.full_name}."
            )
 
        vip_tag = " (High-Value Client)" if is_high_val else ""
        risk_level_str = metrics.risk_level.upper()
        summary_chunks = [
            f"{client.full_name}{vip_tag} is evaluated at {risk_level_str} churn risk (Score: {risk_score}/100)."
        ]
        if metrics.at_risk_revenue > Decimal('0.00'):
            summary_chunks.append(f"${metrics.at_risk_revenue} in monthly revenue is currently at stake.")

        if metrics.days_since_last_visit is not None:
            summary_chunks.append(
                f"Last visited {metrics.days_since_last_visit} day(s) ago with {metrics.attendance_trend} attendance."
            )
        elif metrics.total_attended_classes == 0 and metrics.total_facility_checkins == 0:
            summary_chunks.append("Client has never checked in or attended a session.")

        if risk_factors:
            summary_chunks.append(f"Observed signals: {'; '.join(risk_factors)}.")

        summary_chunks.append(f"Primary driver: {primary_driver}. Action: {recommended_action}")

        ai_summary = " ".join(summary_chunks)

        return {
            "predictive_churn_probability": risk_score,
            "primary_churn_driver": primary_driver,
            "ai_risk_summary": ai_summary,
            "recommended_action": recommended_action,
        }

    @classmethod
    def _call_llm_synthesizer(cls, profile: dict, gemini_key: str, kimi_key: str) -> dict:
        """
        Executes a structured LLM prompt against Gemini or Kimi API to return
        validated JSON analysis.
        """
        import requests

        prompt = (
            "You are an expert retention and customer churn intelligence AI for fitness studios.\n"
            "Analyze this client behavioral profile and return a strictly valid JSON object with keys:\n"
            "- 'predictive_churn_probability': integer 0-100\n"
            "- 'primary_churn_driver': short string\n"
            "- 'ai_risk_summary': 2-3 sentence plain language explanation of churn risk\n"
            "- 'recommended_action': concrete recommended action for gym staff\n\n"
            f"Client Profile:\n{json.dumps(profile, indent=2)}\n"
        )
 
        if gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code == 200:
                data = res.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                parsed = json.loads(text)
                if all(k in parsed for k in ["predictive_churn_probability", "primary_churn_driver", "ai_risk_summary", "recommended_action"]):
                    return parsed
 
        if kimi_key:
            url = "https://api.moonshot.cn/v1/chat/completions"
            headers = {"Authorization": f"Bearer {kimi_key}", "Content-Type": "application/json"}
            payload = {
                "model": "moonshot-v1-8k",
                "messages": [
                    {"role": "system", "content": "You are a customer churn retention AI that returns JSON."},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"}
            }
            res = requests.post(url, headers=headers, json=payload, timeout=5)
            if res.status_code == 200:
                data = res.json()
                text = data['choices'][0]['message']['content']
                parsed = json.loads(text)
                if all(k in parsed for k in ["predictive_churn_probability", "primary_churn_driver", "ai_risk_summary", "recommended_action"]):
                    return parsed

        raise RuntimeError("LLM synthesis did not return expected response structure.")

    @classmethod
    def generate_personalized_winback_copy(
        cls,
        metrics: Optional[ClientRetentionMetrics] = None,
        client=None,
        trigger=None,
        use_llm: bool = True
    ) -> str:
        """
        Generates personalized, high-conversion win-back copy tailored to a client's
        specific risk factors, attendance lapse, or billing issues.
        Uses Gemini/Kimi if configured, otherwise falls back to a deterministic synthesizer.
        """
        if metrics is None and client is not None:
            metrics = getattr(client, 'retention_metrics', None)
            if metrics is None:
                metrics = ClientRetentionMetrics.all_objects.filter(client=client).first()

        client_user = client or (metrics.client if metrics else None)
        first_name = (client_user.first_name if client_user else "") or "there"
        studio_name = trigger.tenant.name if (trigger and getattr(trigger, 'tenant', None)) else "our studio"

        gemini_key = os.environ.get("GEMINI_API_KEY")
        kimi_key = os.environ.get("KIMI_API_KEY")

        if use_llm and (gemini_key or kimi_key):
            try:
                prompt = (
                    "You are a retention and member experience specialist for a boutique fitness studio.\n"
                    f"Write a warm, concise, and compelling 2-sentence winback message for a client named {first_name}.\n"
                    f"Studio name: {studio_name}.\n"
                    f"Client metrics:\n"
                    f"- Days inactive: {metrics.days_since_last_visit if metrics else 'Unknown'}\n"
                    f"- Failed payments: {metrics.failed_payments_last_90d if metrics else 0}\n"
                    f"- Risk factors: {metrics.risk_factors if metrics else []}\n"
                    f"- Active packages: {metrics.active_packages_count if metrics else 0}\n"
                    "Keep the tone encouraging, empathetic, and action-oriented. Return plain text only."
                )
                if gemini_key:
                    import requests
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
                    payload = {"contents": [{"parts": [{"text": prompt}]}]}
                    res = requests.post(url, json=payload, timeout=5)
                    if res.status_code == 200:
                        text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                        if text:
                            return text
                elif kimi_key:
                    import requests
                    url = "https://api.moonshot.cn/v1/chat/completions"
                    headers = {"Authorization": f"Bearer {kimi_key}", "Content-Type": "application/json"}
                    payload = {
                        "model": "moonshot-v1-8k",
                        "messages": [
                            {"role": "system", "content": "You write personalized customer win-back messages."},
                            {"role": "user", "content": prompt}
                        ]
                    }
                    res = requests.post(url, headers=headers, json=payload, timeout=5)
                    if res.status_code == 200:
                        text = res.json()['choices'][0]['message']['content'].strip()
                        if text:
                            return text
            except Exception as e:
                logger.warning(f"RetentionAIService winback LLM synthesis fallback: {e}")

        # Deterministic high-performance synthesizer fallback
        days_inactive = metrics.days_since_last_visit if metrics else 0
        failed_pay = metrics.failed_payments_last_90d if metrics else 0
        factors = metrics.risk_factors if metrics else []

        if failed_pay > 0 or any("payment" in str(f).lower() for f in factors):
            return (
                f"Hi {first_name}, we noticed an issue processing your latest membership payment at {studio_name}. "
                "To ensure uninterrupted booking access and keep your momentum going, please update your payment details or reach out to our front desk team."
            )
        elif days_inactive and days_inactive >= 30:
            return (
                f"Hi {first_name}, it's been {days_inactive} days since your last workout at {studio_name}, and your community misses your energy! "
                "Let's get back into rhythm—book your next class today and take that next step toward your goals."
            )
        elif days_inactive and days_inactive >= 14:
            return (
                f"Hi {first_name}, we've missed seeing you at {studio_name} over the past couple weeks! "
                "Your favorite instructors and classes are ready for you—reserve your spot today and reignite your routine."
            )
        elif metrics and metrics.nearest_package_expiry_at:
            return (
                f"Hi {first_name}, your class package at {studio_name} has credits that are expiring soon! "
                "Don't let your hard work go to waste—check our upcoming schedule and reserve your sessions now."
            )
        else:
            return (
                f"Hi {first_name}, we'd love to see you back on the floor at {studio_name}! "
                "Check out the latest schedule and book your next session with us today."
            )
