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
