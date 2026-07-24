from datetime import timedelta
from django.contrib import admin
from .models import (
    CycleDailyLog,
    DailyReflection,
    MorningEntry,
    EveningEntry,
    FocusOption,
    MorningFocusSelection,
    EveningFocusReflection,
    MenstrualCycle,
    SymptomCategory,
    SymptomTag,

)


@admin.register(DailyReflection)
class DailyReflectionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "date", "created_at")
    search_fields = ("user__username", "user__email")
    list_filter = ("date",)


@admin.register(MorningEntry)
class MorningEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "reflection", "mood", "sleep_quality")
    search_fields = ("reflection__user__username",)


@admin.register(EveningEntry)
class EveningEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "reflection", "stress_level", "mood_after")


@admin.register(FocusOption)
class FocusOptionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(MorningFocusSelection)
class MorningFocusSelectionAdmin(admin.ModelAdmin):
    list_display = ("id", "morning_entry", "focus")


@admin.register(EveningFocusReflection)
class EveningFocusReflectionAdmin(admin.ModelAdmin):
    list_display = ("id", "evening_entry", "focus", "effort")

@admin.register(MenstrualCycle)
class MenstrualCycleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "period_start",
        "period_end",
        "cycle_length_days",
    )

    search_fields = ("user__username", "user__email")

    def period_start(self, obj):
        return obj.last_period_start_date

    def period_end(self, obj):
        return obj.last_period_start_date + timedelta(
            days=obj.period_duration_days - 1
        )

    period_start.short_description = "Period Start"
    period_end.short_description = "Period End"


class SymptomTagInline(admin.TabularInline):
    model = SymptomTag
    extra = 1
    fields = ['name', 'is_active', 'user']
    verbose_name = "System/Default Tag"
    verbose_name_plural = "System/Default Tags"

    # Only show this inline for System tags (User is None) to avoid clutter
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(user__isnull=True)

# -----------------------------------------------------------------------------
# 1. Symptom Categories (Pain, Mood, etc.)
# -----------------------------------------------------------------------------
@admin.register(SymptomCategory)
class SymptomCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'order', 'is_active', 'total_tags']
    list_editable = ['order', 'is_active']
    inlines = [SymptomTagInline]
    ordering = ['order']

    def total_tags(self, obj):
        # Counts only system tags
        return obj.tags.filter(user__isnull=True).count()
    total_tags.short_description = "System Tags"

# -----------------------------------------------------------------------------
# 2. Symptom Tags (The actual options)
# -----------------------------------------------------------------------------
@admin.register(SymptomTag)
class SymptomTagAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'tag_type', 'user', 'is_active']
    list_filter = ['category', 'is_active', ('user', admin.EmptyFieldListFilter)]
    search_fields = ['name', 'user__email']
    ordering = ['category', 'name']
    
    # Organize fields nicely
    fieldsets = (
        (None, {
            'fields': ('category', 'name', 'is_active')
        }),
        ('Custom Tag Owner (Leave Empty for System Tag)', {
            'fields': ('user',),
            'classes': ('collapse',), # Hide by default to keep UI clean
        }),
    )

    def tag_type(self, obj):
        if obj.user:
            return "👤 Custom (User-Created)"
        return "🌐 System (Global)"
    tag_type.short_description = "Type"

# -----------------------------------------------------------------------------
# 3. User Cycle Logs (The history of what users felt)
# -----------------------------------------------------------------------------
@admin.register(CycleDailyLog)
class CycleDailyLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'date', 'flow_intensity', 'symptom_count', 'created_at']
    list_filter = ['date', 'flow_intensity']
    search_fields = ['user__email', 'user__first_name', 'notes']
    date_hierarchy = 'date'
    
    # Use a nice selector for the many-to-many symptoms field
    filter_horizontal = ['symptoms']
    
    fieldsets = (
        ('Log Info', {
            'fields': ('user', 'date', 'created_at')
        }),
        ('Details', {
            'fields': ('flow_intensity', 'symptoms', 'notes')
        }),
    )
    readonly_fields = ['created_at']

    def symptom_count(self, obj):
        return obj.symptoms.count()
    symptom_count.short_description = "# Symptoms"
    