"""
Sandboxed Reward Rule Condition DSL & Evaluator Registry

Provides secure, deterministic, code-free evaluation of reward conditions.
No `eval()`, `exec()`, or dynamic imports are permitted.
"""
from datetime import timedelta
from typing import Dict, Any, List, Optional, Tuple
from django.utils import timezone
from apps.users.models import User


class ConditionEvaluationResult:
    def __init__(self, matched: bool, milestone_key: Optional[str] = None, debug_info: Optional[Dict[str, Any]] = None):
        self.matched = matched
        self.milestone_key = milestone_key
        self.debug_info = debug_info or {}

    def __bool__(self):
        return self.matched


class ConditionRegistry:
    """
    Registry for source extractors and operator evaluation.
    """
    
    @staticmethod
    def evaluate_operator(left_val: Any, operator: str, right_val: Any) -> Tuple[bool, Optional[str]]:
        """
        Safely evaluates an operator. Returns (matched, optional_milestone_key).
        """
        op = operator.lower()

        if op in ['equals', 'eq', '==']:
            return str(left_val).lower() == str(right_val).lower(), None

        elif op in ['not_equals', 'neq', '!=']:
            return str(left_val).lower() != str(right_val).lower(), None

        elif op in ['gt', '>']:
            try:
                return float(left_val) > float(right_val), None
            except (ValueError, TypeError):
                return False, None

        elif op in ['gte', '>=']:
            try:
                return float(left_val) >= float(right_val), None
            except (ValueError, TypeError):
                return False, None

        elif op in ['lt', '<']:
            try:
                return float(left_val) < float(right_val), None
            except (ValueError, TypeError):
                return False, None

        elif op in ['lte', '<=']:
            try:
                return float(left_val) <= float(right_val), None
            except (ValueError, TypeError):
                return False, None

        elif op in ['in']:
            if isinstance(right_val, list):
                right_norm = [str(x).lower() for x in right_val]
                return str(left_val).lower() in right_norm, None
            return str(left_val).lower() in str(right_val).lower(), None

        elif op in ['not_in']:
            if isinstance(right_val, list):
                right_norm = [str(x).lower() for x in right_val]
                return str(left_val).lower() not in right_norm, None
            return str(left_val).lower() not in str(right_val).lower(), None

        elif op in ['contains']:
            return str(right_val).lower() in str(left_val).lower(), None

        elif op in ['between']:
            if isinstance(right_val, (list, tuple)) and len(right_val) == 2:
                try:
                    num = float(left_val)
                    low, high = float(right_val[0]), float(right_val[1])
                    return (low <= num <= high), None
                except (ValueError, TypeError):
                    return False, None
            return False, None

        elif op == 'count_every':
            # Evaluates True when count is an exact non-zero multiple of target N
            try:
                count = int(left_val)
                interval = int(right_val)
                if interval > 0 and count > 0 and count % interval == 0:
                    milestone_key = f"interval:{interval}:step:{count}"
                    return True, milestone_key
            except (ValueError, TypeError):
                pass
            return False, None

        elif op == 'count_at_least':
            # Evaluates True when count >= target N (milestone triggered once)
            try:
                count = int(left_val)
                target = int(right_val)
                if count >= target:
                    milestone_key = f"at_least:{target}"
                    return True, milestone_key
            except (ValueError, TypeError):
                pass
            return False, None

        return False, None


class ValueExtractor:
    """
    Extracts metrics and values safely without raw SQL or arbitrary DB traversal.
    """

    @classmethod
    def extract_value(cls, source: str, condition: Dict[str, Any], event_payload: Dict[str, Any], user: User, tenant_id) -> Any:
        source_type = source.lower()

        if source_type in ['event', 'event_payload', 'payload']:
            field_name = condition.get('field', '')
            return event_payload.get(field_name)

        elif source_type in ['user', 'user_profile', 'profile']:
            field_name = condition.get('field', '')
            if hasattr(user, field_name):
                return getattr(user, field_name)
            if hasattr(user, 'profile') and hasattr(user.profile, field_name):
                return getattr(user.profile, field_name)
            return None

        elif source_type in ['attendance', 'attendance_count']:
            from apps.scheduling.models import Booking
            time_window = condition.get('time_window')
            qs = Booking.objects.filter(tenant_id=tenant_id, client=user, status='attended')
            if time_window and isinstance(time_window, dict):
                days = time_window.get('days')
                if days:
                    cutoff = timezone.now() - timedelta(days=int(days))
                    qs = qs.filter(created_at__gte=cutoff)
            return qs.count()

        elif source_type in ['attendance_streak', 'streak']:
            from apps.rewards.models import UserStreak
            activity = condition.get('activity_type', 'attendance')
            streak_obj = UserStreak.objects.filter(tenant_id=tenant_id, user=user, activity_type=activity).first()
            return streak_obj.current_streak if streak_obj else 0

        elif source_type in ['workout', 'workout_count']:
            from apps.workout.models import WorkoutLog
            time_window = condition.get('time_window')
            qs = WorkoutLog.objects.filter(tenant_id=tenant_id, user=user, is_completed=True)
            if time_window and isinstance(time_window, dict):
                days = time_window.get('days')
                if days:
                    cutoff = timezone.now() - timedelta(days=int(days))
                    qs = qs.filter(completed_at__gte=cutoff)
            return qs.count()

        elif source_type in ['payments', 'total_spend']:
            from apps.scheduling.models import Payment
            from django.db.models import Sum
            time_window = condition.get('time_window')
            qs = Payment.objects.filter(tenant_id=tenant_id, client=user, status='completed')
            if time_window and isinstance(time_window, dict):
                days = time_window.get('days')
                if days:
                    cutoff = timezone.now() - timedelta(days=int(days))
                    qs = qs.filter(created_at__gte=cutoff)
            total = qs.aggregate(Sum('amount'))['amount__sum']
            return float(total or 0.0)

        elif source_type in ['membership', 'package']:
            from apps.scheduling.models import Package
            active_package = Package.objects.filter(
                tenant_id=tenant_id,
                client=user,
                status='active',
                credits_remaining__gt=0,
                expires_at__gte=timezone.now()
            ).first()
            field_name = condition.get('field', 'status')
            if not active_package:
                return None if field_name != 'status' else 'none'
            if field_name == 'package_type_name':
                return active_package.package_type.name
            if field_name == 'credits_remaining':
                return active_package.credits_remaining
            return active_package.status

        elif source_type in ['referral', 'referrals']:
            from apps.rewards.models import RewardTransaction
            # Count of referred member signups
            return RewardTransaction.objects.filter(
                tenant_id=tenant_id,
                user=user,
                action_type='REFERRAL_AWARD'
            ).count()

        return None


class RuleConditionEvaluator:
    """
    High-level condition evaluator for a RewardRule.
    """

    @classmethod
    def evaluate(cls, rule, event_payload: Dict[str, Any], user: User) -> ConditionEvaluationResult:
        """
        Evaluates trigger_config and all conditions on the rule.
        """
        # 1. Evaluate Trigger Config (Quick payload filters)
        if rule.trigger_config and isinstance(rule.trigger_config, dict):
            for k, expected_v in rule.trigger_config.items():
                actual_v = event_payload.get(k)
                if actual_v is None or str(actual_v).lower() != str(expected_v).lower():
                    return ConditionEvaluationResult(matched=False, debug_info={'failed_trigger': k})

        # 2. Evaluate Conditions List
        conditions = rule.conditions
        if not conditions:
            # Rule has no extra conditions -> matches automatically on event
            return ConditionEvaluationResult(matched=True)

        milestone_keys: List[str] = []
        logic = 'AND'
        condition_list = conditions

        if isinstance(conditions, dict):
            logic = conditions.get('operator_logic', 'AND').upper()
            condition_list = conditions.get('conditions', [])

        if not condition_list:
            return ConditionEvaluationResult(matched=True)

        matched_results = []

        for cond in condition_list:
            source = cond.get('source', 'event_payload')
            operator = cond.get('operator', 'equals')
            target_value = cond.get('value')

            left_value = ValueExtractor.extract_value(source, cond, event_payload, user, rule.tenant_id)
            cond_matched, milestone = ConditionRegistry.evaluate_operator(left_value, operator, target_value)
            
            if milestone:
                milestone_keys.append(milestone)

            matched_results.append(cond_matched)

        final_matched = all(matched_results) if logic == 'AND' else any(matched_results)
        combined_milestone = ":".join(milestone_keys) if milestone_keys else None

        return ConditionEvaluationResult(
            matched=final_matched,
            milestone_key=combined_milestone,
            debug_info={'results': matched_results, 'logic': logic}
        )
