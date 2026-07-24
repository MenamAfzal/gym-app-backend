from collections import defaultdict
from django.db.models import Q
from datetime import date, timedelta
from ..models import DailyReflection


def _date_range(start_date, end_date):
    d = start_date
    while d <= end_date:
        yield d
        d += timedelta(days=1)


def get_user_mood_trends(user, start_date, end_date):
    reflections = DailyReflection.objects.filter(user=user, date__range=(start_date, end_date)).select_related("morning", "evening")
    results = []
    for r in reflections.order_by("date"):
        results.append(
            {
                "date": r.date,
                "morning_mood": getattr(r.morning, "mood", None) if hasattr(r, "morning") else None,
                "evening_mood": getattr(r.evening, "mood_after", None) if hasattr(r, "evening") else None,
            }
        )
    return results


def get_sleep_vs_mood_correlation(user, start_date, end_date):
    reflections = DailyReflection.objects.filter(user=user, date__range=(start_date, end_date)).select_related("morning", "evening")
    pairs = []
    for r in reflections.order_by("date"):
        sleep = None
        mood = None
        if hasattr(r, "morning") and r.morning and r.morning.sleep_quality is not None:
            sleep = r.morning.sleep_quality
        if hasattr(r, "evening") and r.evening and r.evening.mood_after is not None:
            mood = r.evening.mood_after
        # if you store mood as categorical, correlation requires mapping. We'll return raw pairs for now.
        pairs.append({"date": r.date, "sleep_quality": sleep, "evening_mood": mood})
    return pairs


def get_focus_effort_stats(user, start_date, end_date):
    reflections = DailyReflection.objects.filter(user=user, date__range=(start_date, end_date)).select_related("evening").prefetch_related("morning__focus_selections", "evening__focus_reflections")
    stats = defaultdict(lambda: {"count": 0, "total_effort": 0, "effort_values": []})
    for r in reflections:
        for f in getattr(r, "evening", []).focus_reflections.all() if hasattr(r, "evening") and r.evening else []:
            stats[f.focus.name]["count"] += 1
            if f.effort is not None:
                stats[f.focus.name]["total_effort"] += f.effort
                stats[f.focus.name]["effort_values"].append(f.effort)
    # summarize
    out = {}
    for focus, values in stats.items():
        avg = values["total_effort"] / values["count"] if values["count"] > 0 else None
        out[focus] = {"count": values["count"], "average_effort": avg, "effort_values": values["effort_values"]}
    return out


def calculate_streaks(user):
    # returns current_streak, longest_streak
    qs = DailyReflection.objects.filter(user=user).values_list("date", flat=True).order_by("date")
    dates = sorted(list(qs))
    if not dates:
        return 0, 0
    longest = 0
    current = 0
    prev = None
    for d in dates:
        if prev is None:
            current = 1
        else:
            if (d - prev).days == 1:
                current += 1
            else:
                current = 1
        if current > longest:
            longest = current
        prev = d
    # compute current streak (from today backwards)
    today = date.today()
    streak = 0
    for delta in range(0, 10000):
        check_date = today - timedelta(days=delta)
        exists = DailyReflection.objects.filter(user=user, date=check_date).exists()
        if exists:
            streak += 1
        else:
            break
    return streak, longest



class CycleAnalytics:
    def __init__(self, start_date, cycle_length, period_length):
        self.start_date = start_date
        self.cycle_length = cycle_length
        self.period_length = period_length

    def cycle_window(self, start):
        end = start + timedelta(days=self.period_length - 1)
        return start, end
    def get_fertile_window(self, current_cycle_start):
        """
        Calculates the fertile window based on the start date of the current cycle.
        Logic:
        1. Predict Next Period Start = Current Start + Cycle Length
        2. Ovulation = Next Period Start - 14 Days
        3. Fertile Window = Ovulation - 5 days TO Ovulation + 1 day
        """
        # 1. Predict when the NEXT period starts
        next_period_start = current_cycle_start + timedelta(days=self.cycle_length)
        
        # 2. Pinpoint Ovulation (14 days before the next period)
        ovulation_date = next_period_start - timedelta(days=14)
        
        # 3. Define the High Chance Window (5 days before + 1 day after)
        fertile_start = ovulation_date - timedelta(days=5)
        fertile_end = ovulation_date + timedelta(days=1)
        
        return fertile_start, fertile_end, ovulation_date

    def generate_year(self, year: int):
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)

        cycles = []

        # Step 1: move start date backwards until before year_start
        current_start = self.start_date
        while current_start > year_start:
            current_start -= timedelta(days=self.cycle_length)

        # Step 2: move forward and collect cycles
        while current_start <= year_end:
            start, end = self.cycle_window(current_start)
            
            # --- CALL THE NEW METHOD HERE ---
            f_start, f_end, ovulation = self.get_fertile_window(current_start)

            if end >= year_start and start <= year_end:
                cycles.append({
                    "type": "menstruation", # Good to label this clearly
                    "start_date": start,
                    "end_date": end,
                    "duration_days": self.period_length,
                    
                    # Add the new fertility data
                    "fertility": {
                        "start_date": f_start,
                        "end_date": f_end,
                        "ovulation_date": ovulation,
                        "pregnancy_chance": "High"
                    }
                })

            current_start += timedelta(days=self.cycle_length)

        return cycles
    
    
    
