from django.db import models
from core_models.mixins.timestamps import TimestampMixin as TimeStampMixin
from core_models.mixins.tenant_mixin import TenantMixin
from django.contrib.auth import get_user_model
from django.utils.text import slugify

User = get_user_model()

class DailyReflection(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reflections")
    date = models.DateField()


    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]


    def __str__(self):
        return f"Reflection: {self.user} - {self.date}"
    

class MorningEntry(TimeStampMixin, TenantMixin):
    reflection = models.OneToOneField(DailyReflection, on_delete=models.CASCADE, related_name="morning", null=True)
 
    # Mood choices can be extended by admin; stored as string for flexibility
    mood = models.CharField(max_length=64, blank=True, null=True)
 
    # Sleep quality slider: 1..10
    sleep_quality = models.PositiveSmallIntegerField(blank=True, null=True)
 
    affirmation = models.TextField(blank=True, null=True)
 
    gratitude_1 = models.TextField(blank=True, null=True)
    gratitude_2 = models.TextField(blank=True, null=True)
    gratitude_3 = models.TextField(blank=True, null=True)


def __str__(self):
    return f"MorningEntry for {self.reflection.user} on {self.reflection.date}"  


class FocusOption(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="custom_focus_options")
    
    name = models.CharField(max_length=120)
     
    slug = models.SlugField(max_length=120) 
    
    icon = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["name"] 
        
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='unique_user_focus')
        ]

    def __str__(self):
        type_label = "Custom" if self.user else "System"
        return f"{self.name} ({type_label})"

class MorningFocusSelection(TimeStampMixin, TenantMixin):
    morning_entry = models.ForeignKey(MorningEntry, on_delete=models.CASCADE, related_name="focus_selections")
    focus = models.ForeignKey(FocusOption, on_delete=models.PROTECT)
    action_plan = models.TextField(blank=True, null=True)


    class Meta:
        unique_together = ("morning_entry", "focus")


    def __str__(self):
        return f"{self.focus.name} selection for {self.morning_entry.reflection.date}"
    

class EveningEntry(TimeStampMixin, TenantMixin):
    reflection = models.OneToOneField(DailyReflection, on_delete=models.CASCADE, related_name="evening", null=True)


    stress_level = models.PositiveSmallIntegerField(blank=True, null=True)
    mood_after = models.CharField(max_length=64, blank=True, null=True)


    highlight_1 = models.TextField(blank=True, null=True)
    highlight_2 = models.TextField(blank=True, null=True)
    highlight_3 = models.TextField(blank=True, null=True)


    lesson = models.TextField(blank=True, null=True)


    def __str__(self):
        return f"EveningEntry for {self.reflection.user} on {self.reflection.date}"
    

class EveningFocusReflection(TimeStampMixin, TenantMixin):
    evening_entry = models.ForeignKey(EveningEntry, on_delete=models.CASCADE, related_name="focus_reflections")
    focus = models.ForeignKey(FocusOption, on_delete=models.PROTECT)
    effort = models.PositiveSmallIntegerField(blank=True, null=True)
    improvement = models.TextField(blank=True, null=True)


    class Meta:
        unique_together = ("evening_entry", "focus")


    def __str__(self):
        return f"{self.focus.name} reflection for {self.evening_entry.reflection.date}"
    



class MenstrualCycle(TenantMixin, models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="cycles")

    last_period_start_date = models.DateField()
    cycle_length_days = models.PositiveSmallIntegerField()
    period_duration_days = models.PositiveSmallIntegerField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} - {self.last_period_start_date}"

class SymptomCategory(TimeStampMixin, TenantMixin):
    name = models.CharField(max_length=100)
    icon_url = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "Symptom Categories"
        ordering = ['order']

    def __str__(self):
        return self.name

class SymptomTag(TimeStampMixin, TenantMixin):
    category = models.ForeignKey(SymptomCategory, on_delete=models.CASCADE, related_name='tags')
    # NEW: Link to user. If null, it's a global system tag.
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='custom_symptoms')
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        # Ensure a user doesn't create the same tag twice in the same category
        unique_together = ['category', 'user', 'name']
        ordering = ['name']

    def __str__(self):
        type_label = "Custom" if self.user else "System"
        return f"{self.name} ({type_label} - {self.category.name})"

class CycleDailyLog(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cycle_logs')
    date = models.DateField()
    symptoms = models.ManyToManyField(SymptomTag, blank=True)
    notes = models.TextField(blank=True, null=True)
    
    FLOW_CHOICES = [
        (0, 'None'),
        (1, 'Spotting'),
        (2, 'Light'),
        (3, 'Medium'),
        (4, 'Heavy'),
    ]
    flow_intensity = models.IntegerField(choices=FLOW_CHOICES, default=0)

    class Meta:
        unique_together = ['user', 'date']
                