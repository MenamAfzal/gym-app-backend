import os

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from core_models.mixins.tenant_mixin import TenantMixin

try:
    from apps.scheduling.models import ClassSession as Session
except ImportError:
    Session = 'scheduling.ClassSession'


class Product(TenantMixin):
    product_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    barcode = models.CharField(max_length=50, null=True, blank=True)
    group_id = models.PositiveIntegerField(null=True, blank=True)

    category_id = models.PositiveIntegerField(null=True, blank=True)
    sub_category_id = models.PositiveIntegerField(null=True, blank=True)
    secondary_category_id = models.PositiveIntegerField(null=True, blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    online_price = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    tax_included = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    name = models.CharField(max_length=255, null=True, blank=True)
    short_description = models.TextField(null=True, blank=True)
    long_description = models.TextField(null=True, blank=True)

    type_group = models.PositiveIntegerField(null=True, blank=True)
    supplier_id = models.PositiveIntegerField(null=True, blank=True)
    supplier_name = models.CharField(max_length=255, null=True, blank=True)
    manufacturer_id = models.PositiveIntegerField(null=True, blank=True)

    image_url = models.URLField(max_length=500, null=True, blank=True)

    color_id = models.PositiveIntegerField(null=True, blank=True)
    color_name = models.CharField(max_length=100, null=True, blank=True)

    size_id = models.PositiveIntegerField(null=True, blank=True)
    size_name = models.CharField(max_length=100, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name or 'Unnamed Product'} (ID: {self.product_id or 'N/A'})"


MOVEMENT_PATTERNS = {
    100: "Dynamic Stretch",
    101: "Static Stretch",
    102: "Anti-Extension",
    103: "Anti-Rotation",
    104: "Horizontal Push",
    105: "Horizontal Pull",
    106: "Hip Dominant",
    107: "Knee Dominant",
    108: "Vertical Push",
    109: "Vertical Pull",
    110: "Corrective",
    111: "Explosive",
    112: "Isolation",
    113: "General Conditioning",
    114: "Finisher",
    115: "Bi-Lateral",
    116: "Uni-Lateral",
    117: "Ipsi-Lateral",
}

EQUIPMENT = {
    1000: "Acumobility Ball",
    1001: "Agility Ladder",
    1002: "Barbell",
    1003: "Battle Ropes",
    1004: "Bench",
    1005: "Bike",
    1006: "Blue Step",
    1007: "Body Weight",
    1008: "BOSU",
    1009: "Bulgarian Stand",
    1010: "Cable Handle",
    1011: "Cable Rope",
    1012: "Cone",
    1013: "Connect Four",
    1014: "Deck of Cards",
    1015: "Dowel",
    1016: "Dumbbell",
    1017: "Foam Roller",
    1018: "Green Step",
    1019: "Kettlebell",
    1020: "Medicine Ball",
    1021: "Mini Band",
    1022: "Partner",
    1023: "Plate",
    1024: "Plyobox",
    1025: "Power Wheel",
    1026: "Pull Up Bar",
    1027: "Rope",
    1028: "Rower",
    1029: "Sandbag",
    1030: "Slam Ball",
    1031: "Sliders",
    1032: "Stability Ball",
    1033: "Steel Bell",
    1034: "Superband",
    1035: "Tank",
    1036: "TRX",
    1037: "TerraCore",
}

class Workout(TenantMixin):
    WORKOUT_TYPES = (
        (1, "Regular"),
        (2, "Interval"),
        (3, "Mixed"),
        (4, "Deck of Cards"),
        (5, "AMRAP"),
        (6, "Minuteman"),
        (7, "Dice"),
    ) 

    MOVEMENT_LEVELS = [
        ("Stability", "Stability"),
        ("Strength", "Strength"),
        ("Power", "Power"),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    movement_level = models.CharField(max_length=50, choices=MOVEMENT_LEVELS)
    session_type = models.CharField(max_length=50)
    workout_type = models.PositiveSmallIntegerField(choices=WORKOUT_TYPES, default=1)

    video_url = models.URLField(blank=True, null=True)
    myzone_effort_range = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    deck_config = models.JSONField(null=True, blank=True)

    tags = models.ManyToManyField("WorkoutTag", blank=True)
    equipment = models.ManyToManyField("Equipment", blank=True)
    base_workout_name = models.CharField(max_length=200, null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_workouts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name    

class WorkoutGroup(TenantMixin):
    GROUP_TYPES = (
        (1, "Circuit"),
        (2, "Superset"),
        (3, "Finisher"),
    )

    workout = models.ForeignKey(Workout, on_delete=models.CASCADE, related_name="groups")
    group_type = models.PositiveSmallIntegerField(choices=GROUP_TYPES)
    group_number = models.PositiveIntegerField(blank=True, null=True, help_text="Sequential ID for circuit/superset")
    
    group_work_minutes = models.PositiveIntegerField(default=0)
    group_work_seconds = models.PositiveIntegerField(default=0)
    group_rest_minutes = models.PositiveIntegerField(default=0)
    group_rest_seconds = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.get_group_type_display()} (Workout: {self.workout.name})"

class Exercise(TenantMixin):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    video_url = models.URLField(blank=True, null=True)
    video_file = models.FileField(upload_to='videos/', null=True, blank=True)
    coaching_cues = models.TextField(blank=True, null=True)

    upload_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('uploaded', 'Uploaded'),
            ('failed', 'Failed'),
        ],
        default='pending'
    )
     
    tags = models.ManyToManyField("WorkoutTag", blank=True)
    equipment = models.ManyToManyField("Equipment", blank=True)

    def __str__(self):
        return self.name

class WorkoutExercise(TenantMixin):

    SUIT_CHOICES = [
        ('hearts', 'Hearts'),
        ('spades', 'Spades'),
        ('clubs', 'Clubs'),
        ('diamonds', 'Diamonds'),
    ]

    suit = models.CharField(max_length=20, choices=SUIT_CHOICES, null=True, blank=True)
    is_joker = models.BooleanField(default=False)
    
    workout = models.ForeignKey(Workout, on_delete=models.CASCADE, related_name='workout_exercises')
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE)
    order = models.PositiveIntegerField()

    group = models.ForeignKey(WorkoutGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="exercises")

    sets = models.PositiveIntegerField(blank=True, null=True)
    reps = models.PositiveIntegerField(blank=True, null=True)

    rounds = models.PositiveIntegerField(blank=True, null=True)
    work_minutes = models.PositiveIntegerField(blank=True, null=True)
    work_seconds = models.PositiveIntegerField(blank=True, null=True)
    rest_minutes = models.PositiveIntegerField(blank=True, null=True)
    rest_seconds = models.PositiveIntegerField(blank=True, null=True)
    seconds = models.PositiveIntegerField(blank=True, null=True)
    is_hold = models.BooleanField(default=False)

    video_url = models.URLField(blank=True, null=True)
    custom_cues = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.exercise.name} in {self.workout.name}"

class WorkoutTag(TenantMixin):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class Equipment(TenantMixin):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name                        

class WorkoutLog(TenantMixin):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workout = models.ForeignKey(Workout, on_delete=models.CASCADE)
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True)
    completed_at = models.DateTimeField(auto_now_add=True)
    duration_seconds = models.PositiveIntegerField(blank=True, null=True)
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.email} - {self.workout.name}"

class WeightEntry(TenantMixin):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workout_log = models.ForeignKey(WorkoutLog, on_delete=models.CASCADE, null=True)
    workout = models.ForeignKey(Workout, on_delete=models.CASCADE)
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE)
    weight = models.FloatField()
    date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} - {self.exercise.name}: {self.weight} kg"

class FavoriteWorkout(TenantMixin):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workout = models.ForeignKey(Workout, on_delete=models.CASCADE)
    is_favorited = models.BooleanField(default=False)
    favorited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'workout')

class LikedExercise(TenantMixin):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name="liked_by")
     
    is_liked = models.BooleanField(default=True)
    liked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "exercise")

def song_upload_path(instance, filename):
    return os.path.join('songs', str(instance.playlist.name), filename)

class MusicPlaylist(TenantMixin):
    name = models.CharField(max_length=100, null=False, blank=False)
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Song(TenantMixin):
    playlist = models.ForeignKey(MusicPlaylist, on_delete=models.CASCADE, related_name='songs')
    title = models.CharField(max_length=255, null=True, blank=True)
    artist = models.CharField(max_length=255, blank=True, null=True)
    audio_file = models.FileField(upload_to=song_upload_path, blank=True, null=True)
    audio_url = models.URLField(blank=True, null=True)
    order = models.PositiveIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(blank=True, null=True)

    def __str__(self):
        return self.playlist.name + " " + self.title

class ExerciseSubstitutionLog(TenantMixin):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workout = models.ForeignKey('workout.Workout', on_delete=models.CASCADE, null=True, blank=True, related_name='substitutions') 
    original_exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name='originally_prescribed')
    substituted_exercise = models.ForeignKey(Exercise, on_delete=models.CASCADE, related_name='substituted_with')
    reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} swapped in {self.workout} @ {self.created_at}"