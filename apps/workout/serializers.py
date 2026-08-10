from rest_framework import serializers
from rest_framework.response import Response
from .models import Exercise, ExerciseSubstitutionLog, LikedExercise, Workout, WorkoutExercise, WorkoutTag, Equipment, WorkoutLog, WeightEntry, FavoriteWorkout, Product, WorkoutGroup
from django.utils import timezone
from django.db import transaction 
from django.db.models import Q
from .models import MusicPlaylist, Song

from django.contrib.auth import get_user_model
User = get_user_model()


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'

 
class ExerciseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exercise
        fields = ["id", "name", "description", "video_url", "coaching_cues", "tags" ]


class WorkoutExerciseCreateSerializer(serializers.ModelSerializer):
    exercise = serializers.PrimaryKeyRelatedField(queryset=Exercise.objects.none())

    class Meta:
        model = WorkoutExercise
        fields = [
            "exercise", "order", "sets", "reps", "is_superset",
            "video_url", "custom_cues", "superset_group"
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exercise"].queryset = Exercise.objects.all()

    def create(self, validated_data):
        return WorkoutExercise.objects.create(**validated_data)
    
    

class WorkoutExerciseSerializer(serializers.ModelSerializer):
    exercise = ExerciseSerializer(read_only=True)
    exercise_name = serializers.CharField(source="exercise.name", read_only=True)
    is_liked = serializers.SerializerMethodField()
    
    class Meta:
        model = WorkoutExercise
        fields = [
            "id", "exercise", "exercise_name", "order", "sets", "reps", 
            "rounds", "work_minutes", "work_seconds", "rest_minutes", 
            "rest_seconds", "video_url", "custom_cues", "group", "is_liked",
            "is_hold", "seconds"
        ]
        read_only_fields = ["id", "exercise", "exercise_name", "order"]

    def get_is_liked(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        return LikedExercise.objects.filter(
            user=request.user,
            exercise=obj.exercise,  
            is_liked=True
        ).exists()


class SubWorkoutSerializer(serializers.ModelSerializer):
    workout_exercises = WorkoutExerciseSerializer(many=True, read_only=True)
    tags = serializers.StringRelatedField(many=True)
    equipment = serializers.StringRelatedField(many=True)

    class Meta:
        model = Workout
        fields = [
            "id", "name", "description", "movement_level", "session_type",
            "video_url", "myzone_effort_range", "notes",
            "tags", "equipment", "workout_exercises"
        ]


class WorkoutGroupSerializer(serializers.ModelSerializer):
    exercises = WorkoutExerciseSerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutGroup
        fields = [
            "id", "group_type", "group_number", "group_work_minutes", 
            "group_work_seconds", "group_rest_minutes", "group_rest_seconds", 
            "exercises"
        ]


class WorkoutCreateWithExercisesSerializer(serializers.ModelSerializer):
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    equipment = serializers.ListField(child=serializers.CharField(), required=False)
    exercises = serializers.JSONField(write_only=True)
    groups = serializers.JSONField(write_only=True, required=False)

    class Meta:
        model = Workout
        fields = [
            "id", "name", "description", "movement_level", "session_type",
            "workout_type", "video_url", "myzone_effort_range", "notes",
            "tags", "equipment", "exercises", "groups"
        ]
        read_only_fields = ["id"]

    def create(self, validated_data):
        tags_data = validated_data.pop("tags", [])
        equipment_data = validated_data.pop("equipment", [])
        exercises_data = validated_data.pop("exercises", [])
        groups_data = validated_data.pop("groups", [])
         
        workout = Workout.objects.create(**validated_data)

        group_objects = {}
        for group_data in groups_data:
            group_obj = WorkoutGroup.objects.create(
                workout=workout,
                group_type=group_data.get('group_type'),
                group_number=group_data.get('group_number'),
                group_work_minutes=group_data.get('group_work_minutes', 0),
                group_work_seconds=group_data.get('group_work_seconds', 0),
                group_rest_minutes=group_data.get('group_rest_minutes', 0),
                group_rest_seconds=group_data.get('group_rest_seconds', 0)
            )
            group_objects[group_data.get('temp_id')] = group_obj

        for order, exercise_data in enumerate(exercises_data):
            exercise_obj = Exercise.objects.get(id=exercise_data.get("exercise_id"))
            group_id = exercise_data.get("group")
            
            WorkoutExercise.objects.create(
                workout=workout,
                exercise=exercise_obj,
                order=order,
                sets=exercise_data.get("sets"),
                reps=exercise_data.get("reps"),
                rounds=exercise_data.get("rounds"),
                work_minutes=exercise_data.get("work_minutes"),
                work_seconds=exercise_data.get("work_seconds"),
                rest_minutes=exercise_data.get("rest_minutes"),
                rest_seconds=exercise_data.get("rest_seconds"),
                group=group_objects.get(group_id),
                video_url=exercise_data.get("video_url"),
                custom_cues=exercise_data.get("custom_cues"),
            )

        tag_objs = [WorkoutTag.objects.get_or_create(name=t)[0] for t in tags_data]
        workout.tags.set(tag_objs)
        eq_objs = [Equipment.objects.get_or_create(name=e)[0] for e in equipment_data]
        workout.equipment.set(eq_objs)

        return workout


class WorkoutSerializer(serializers.ModelSerializer):
    tags = serializers.StringRelatedField(many=True, read_only=True)
    equipment = serializers.StringRelatedField(many=True, read_only=True)
    created_by = serializers.SerializerMethodField()
    exercises = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()
    is_completed = serializers.SerializerMethodField()
    
    class Meta:
        model = Workout
        fields = [
            "id", "name", "description", "movement_level", "session_type",
            "workout_type", "video_url", "myzone_effort_range", "notes",
            "tags", "equipment", "exercises", "groups", "created_by", "created_at",
            "start_date", "end_date", "deck_config", "is_completed" 
        ]

    def get_created_by(self, obj):
        if obj.created_by:
            return f"{obj.created_by.profile.first_name} {obj.created_by.profile.last_name}"
        return None

    def get_exercises(self, obj):
        if obj.workout_type == 4:
            return []
        ungrouped_exercises = obj.workout_exercises.filter(group__isnull=True).order_by("order")
        return WorkoutExerciseSerializer(ungrouped_exercises, many=True, context=self.context).data
    
    def get_is_completed(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        return WorkoutLog.objects.filter(
            user=request.user,
            workout=obj,
            is_completed=True
        ).exists()

    def get_groups(self, obj):
        if obj.workout_type == 4:
            return []
          
        group_ordering = {1: 1, 2: 2, 3: 3}  
        
        groups = list(obj.groups.all())
        groups.sort(key=lambda g: group_ordering.get(g.group_type, 99))

        return WorkoutGroupSerializer(groups, many=True, context=self.context).data


class WorkoutLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkoutLog
        fields = ["id", "user", "workout", "session", "completed_at", "duration_seconds"]
        read_only_fields = ["user", "completed_at"]


class WeightEntrySerializer(serializers.ModelSerializer):
    exercise = serializers.CharField()

    class Meta:
        model = WeightEntry
        fields = ["id", "user", "workout", "workout_log", "exercise", "weight", "date"]
        read_only_fields = ["user", "date"]

    def create(self, validated_data):
        exercise_name = validated_data.pop("exercise")
        workout_log = validated_data.pop("workout_log")
        workout = validated_data.get("workout")

        if workout_log.user != validated_data.get("user"):
            raise serializers.ValidationError("Workout log not found or does not belong to you.")

        if workout_log.workout.id != workout.id:
            raise serializers.ValidationError("Workout log does not match the workout provided.")

        try:
            exercise_obj = Exercise.objects.get(name=exercise_name)
        except Exercise.DoesNotExist:
            raise serializers.ValidationError({"exercise": "Invalid exercise name."})

        return WeightEntry.objects.create(
            exercise=exercise_obj,
            workout_log=workout_log,
            **validated_data
        )


class FavoriteWorkoutSerializer(serializers.ModelSerializer):
    workout = WorkoutSerializer(read_only=True)

    class Meta:
        model = FavoriteWorkout
        fields = ["id", "user", "workout", "favorited_at", "is_favorited"]
        read_only_fields = ["user", "favorited_at"]


class StaffWeightEntrySerializer(serializers.ModelSerializer):
    exercise = serializers.CharField(source="exercise.name")

    class Meta:
        model = WeightEntry
        fields = ["exercise", "weight", "date"]


class StaffWorkoutExerciseSerializer(serializers.ModelSerializer):
    exercise = serializers.CharField(source="exercise.name")

    class Meta:
        model = WorkoutExercise
        fields = [
            "id", "exercise", "order", "sets", "reps", "video_url", "custom_cues" 
        ]


class StaffWorkoutSerializer(serializers.ModelSerializer):
    tags = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")
    equipment = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")
    workout_exercises = StaffWorkoutExerciseSerializer(many=True, read_only=True)
    sub_workouts = SubWorkoutSerializer(many=True, read_only=True)
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = Workout
        fields = [
            "id", "name", "description", "movement_level", "session_type",
            "video_url", "myzone_effort_range", "notes", "tags", "equipment",
            "workout_exercises", "sub_workouts", "created_by", "created_at"
        ]

    def get_created_by(self, obj):
        profile = getattr(obj.created_by, "profile", None)
        if profile:
            return f"{profile.first_name or ''} {profile.last_name or ''}".strip()
        return obj.created_by.email


class StaffClientWorkoutLogSerializer(serializers.ModelSerializer):
    workout = WorkoutSerializer(read_only=True)
    user = serializers.StringRelatedField()
    session = serializers.CharField(source="session.name", read_only=True)
    weight_entries = serializers.SerializerMethodField()

    class Meta:
        model = WorkoutLog
        fields = [
            "id", "user", "workout", "session",
            "completed_at", "duration_seconds", "weight_entries"
        ]

    def get_weight_entries(self, obj):
        entries = WeightEntry.objects.filter(workout_log=obj)
        return StaffWeightEntrySerializer(entries, many=True).data


class SongSerializer(serializers.ModelSerializer):
    class Meta:
        model = Song
        fields = ['id', 'title', 'artist', 'audio_url', 'order']


class MusicPlaylistSerializer(serializers.ModelSerializer):
    songs = SongSerializer(many=True, required=False)

    class Meta:
        model = MusicPlaylist
        fields = ['id', 'name', 'description', 'songs', 'created_at']
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        request = self.context.get('request')
        tenant = getattr(request, 'tenant', None) if request else None
        if not tenant and request:
            tenant = getattr(request.user, 'tenant', None)

        songs_data = validated_data.pop('songs', [])
        playlist = MusicPlaylist.objects.create(tenant=tenant, **validated_data)
        
        for index, song_data in enumerate(songs_data):
            if 'order' not in song_data:
                song_data['order'] = index + 1
            Song.objects.create(playlist=playlist, tenant=tenant, **song_data)
            
        return playlist


class FirestoreImportSerializer(serializers.Serializer):
    file = serializers.FileField()


class ExerciseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exercise
        fields = '__all__'

class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkoutTag
        fields = ["id", "name"]

class EquipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Equipment
        fields = ["id", "name"]


class MovesRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False)


class WorkoutUpdateSerializer(serializers.ModelSerializer):
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    equipment = serializers.ListField(child=serializers.CharField(), required=False)
    exercises = serializers.JSONField(write_only=True, required=False)
    groups = serializers.JSONField(write_only=True, required=False)

    class Meta:
        model = Workout
        fields = [
            "id", "name", "description", "movement_level", "session_type",
            "workout_type", "video_url", "myzone_effort_range", "notes",
            "tags", "equipment", "exercises", "groups", "start_date", "end_date"
        ]
        read_only_fields = ["id", "created_by", "created_at"]

    def update(self, instance, validated_data):
        tags_data = validated_data.pop("tags", None)
        equipment_data = validated_data.pop("equipment", None)
        exercises_data = validated_data.pop("exercises", None)
        groups_data = validated_data.pop("groups", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if tags_data is not None:
            tag_objs = [WorkoutTag.objects.get_or_create(name=t)[0] for t in tags_data]
            instance.tags.set(tag_objs)

        if equipment_data is not None:
            eq_objs = [Equipment.objects.get_or_create(name=e)[0] for e in equipment_data]
            instance.equipment.set(eq_objs)

        if exercises_data is not None or groups_data is not None:
            self._update_exercises_and_groups(instance, exercises_data or [], groups_data or [])

        return instance

    def _update_exercises_and_groups(self, workout, exercises_data, groups_data):
        from django.db import transaction
        from django.db.models import Max, Count

        def prefixed_id(kind, val):
            return f"{kind}:{val}" if val is not None else None

        with transaction.atomic():
            existing_groups_by_id = {g.id: g for g in workout.groups.all()}
            group_map = {}
            updated_group_ids = set()

            max_group_number = workout.groups.aggregate(max_num=Max("group_number"))["max_num"] or 0
            next_group_number = max_group_number + 1

            for group_data in groups_data:
                group_id = group_data.get("id")
                temp_id = group_data.get("temp_id")

                if group_id:
                    group_obj = existing_groups_by_id.pop(group_id, None)
                    if not group_obj:
                        raise serializers.ValidationError(f"WorkoutGroup with id {group_id} not found for this workout.")
                    group_obj.group_type = group_data.get("group_type", group_obj.group_type)
                    group_obj.group_work_minutes = group_data.get("group_work_minutes", group_obj.group_work_minutes or 0)
                    group_obj.group_work_seconds = group_data.get("group_work_seconds", group_obj.group_work_seconds or 0)
                    group_obj.group_rest_minutes = group_data.get("group_rest_minutes", group_obj.group_rest_minutes or 0)
                    group_obj.group_rest_seconds = group_data.get("group_rest_seconds", group_obj.group_rest_seconds or 0)
                    if group_data.get("group_number") is not None:
                        group_obj.group_number = group_data.get("group_number")
                    group_obj.save()
                else:
                    group_number = group_data.get("group_number") or next_group_number
                    group_obj = WorkoutGroup.objects.create(
                        workout=workout,
                        group_type=group_data.get("group_type"),
                        group_number=group_number,
                        group_work_minutes=group_data.get("group_work_minutes", 0),
                        group_work_seconds=group_data.get("group_work_seconds", 0),
                        group_rest_minutes=group_data.get("group_rest_minutes", 0),
                        group_rest_seconds=group_data.get("group_rest_seconds", 0),
                    )
                    next_group_number = max(next_group_number, group_number + 1)

                group_map[prefixed_id("id", group_obj.id)] = group_obj
                if temp_id is not None:
                    group_map[prefixed_id("t", temp_id)] = group_obj

                updated_group_ids.add(group_obj.id)

            for leftover_group in existing_groups_by_id.values():
                leftover_group.delete()

            workout.workout_exercises.all().delete()

            for order, exercise_data in enumerate(exercises_data):
                try:
                    exercise_obj = Exercise.objects.get(id=exercise_data.get("exercise_id"))
                except Exercise.DoesNotExist:
                    raise serializers.ValidationError(f"Exercise with ID {exercise_data.get('exercise_id')} does not exist.")

                group_ref = exercise_data.get("group") or exercise_data.get("group_id") or exercise_data.get("group_temp_id")
                group_obj = None
                if group_ref is not None:
                    group_obj = group_map.get(prefixed_id("id", group_ref)) or group_map.get(prefixed_id("t", group_ref))

                WorkoutExercise.objects.create(
                    workout=workout,
                    exercise=exercise_obj,
                    order=order,
                    sets=exercise_data.get("sets"),
                    reps=exercise_data.get("reps"),
                    rounds=exercise_data.get("rounds"),
                    work_minutes=exercise_data.get("work_minutes", 0),
                    work_seconds=exercise_data.get("work_seconds", 0),
                    rest_minutes=exercise_data.get("rest_minutes", 0),
                    rest_seconds=exercise_data.get("rest_seconds", 0),
                    group=group_obj,
                    video_url=exercise_data.get("video_url", ""),
                    custom_cues=exercise_data.get("custom_cues", ""),
                )

            empty_groups = workout.groups.annotate(cnt=Count("exercises")).filter(cnt=0)
            for g in empty_groups:
                g.delete()


class MultiLevelWorkoutCreateSerializer(serializers.Serializer):
    base_workout_name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    
    session_type = serializers.ListField(
        child=serializers.CharField(max_length=100), required=True, min_length=1
    )
    
    workout_type = serializers.IntegerField()
    video_url = serializers.URLField(required=False, allow_blank=True)
    myzone_effort_range = serializers.CharField(max_length=50, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    equipment = serializers.ListField(child=serializers.CharField(), required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs):
        start = attrs.get("start_date")
        end = attrs.get("end_date")

        if start and end and start > end:
            raise serializers.ValidationError("start_date must be before or equal to end_date")
        if end and not start:
            raise serializers.ValidationError("start_date is required if end_date is provided")

        return attrs

    level_configurations = serializers.JSONField()
    
    def validate_level_configurations(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("level_configurations must be a dictionary")
        
        valid_levels = ['Stability', 'Strength', 'Power']
        for level in value.keys():
            if level not in valid_levels:
                raise serializers.ValidationError(f"Invalid movement level: {level}. Must be one of {valid_levels}")
            
            level_data = value[level]
            if not isinstance(level_data, dict):
                raise serializers.ValidationError(f"Configuration for {level} must be a dictionary")
            
            if 'exercises' not in level_data:
                raise serializers.ValidationError(f"'exercises' is required for {level}")
            
            if not isinstance(level_data['exercises'], list):
                raise serializers.ValidationError(f"'exercises' for {level} must be a list")
        
        return value
    
    @transaction.atomic
    def create(self, validated_data):
        base_name_prefix = validated_data.pop('base_workout_name') 
        
        session_types = validated_data.pop('session_type') 
        level_configurations = validated_data.pop('level_configurations')
        tags_data = validated_data.pop('tags', [])
        equipment_data = validated_data.pop('equipment', [])
        created_by = validated_data.pop('created_by')
        
        all_created_workouts = []
        
        tag_objs = [WorkoutTag.objects.get_or_create(name=t)[0] for t in tags_data]
        eq_objs = [Equipment.objects.get_or_create(name=e)[0] for e in equipment_data]
        
        for session_type in session_types:
            session_base_workout_name = f"{base_name_prefix} - {session_type}" 
            
            for movement_level, level_config in level_configurations.items():
                workout_name = f"{session_base_workout_name} ({movement_level})" 
                
                exercises_data = level_config.get('exercises', [])
                groups_data = level_config.get('groups', [])
                level_notes = level_config.get('notes', '')
                
                combined_notes = validated_data.get('notes', '')
                if level_notes:
                    combined_notes = f"{combined_notes}\n\nLevel-specific notes:\n{level_notes}" if combined_notes else level_notes
                
                workout = Workout.objects.create(
                    name=workout_name,
                    base_workout_name=session_base_workout_name, 
                    description=validated_data.get('description', ''),
                    movement_level=movement_level,
                    session_type=session_type, 
                    workout_type=validated_data['workout_type'],
                    video_url=validated_data.get('video_url', ''),
                    myzone_effort_range=validated_data.get('myzone_effort_range', ''),
                    notes=combined_notes,
                    created_by=created_by,
                    start_date=validated_data.get("start_date"),
                    end_date=validated_data.get("end_date"),
                )
                
                workout.tags.set(tag_objs)
                workout.equipment.set(eq_objs)
                
                group_objects = {}
                for group_data in groups_data:
                    group_obj = WorkoutGroup.objects.create(
                        workout=workout,
                        group_type=group_data.get('group_type'),
                        group_number=group_data.get('group_number'),
                        group_work_minutes=group_data.get('group_work_minutes', 0),
                        group_work_seconds=group_data.get('group_work_seconds', 0),
                        group_rest_minutes=group_data.get('group_rest_minutes', 0),
                        group_rest_seconds=group_data.get('group_rest_seconds', 0)
                    )
                    group_objects[group_data.get('temp_id')] = group_obj
                
                for order, exercise_data in enumerate(exercises_data):
                    try:
                        exercise_obj = Exercise.objects.get(id=exercise_data.get("exercise_id"))
                    except Exercise.DoesNotExist:
                        continue   
                        
                    group_id = exercise_data.get("group")
                    
                    WorkoutExercise.objects.create(
                        workout=workout,
                        exercise=exercise_obj,
                        order=order,
                        sets=exercise_data.get("sets"),
                        reps=exercise_data.get("reps"),
                        rounds=exercise_data.get("rounds"),  
                        work_seconds=exercise_data.get("work_seconds", 0),  
                        work_minutes=exercise_data.get("work_minutes"),  
                        rest_minutes=exercise_data.get("rest_minutes"),  
                        rest_seconds=exercise_data.get("rest_seconds"),
                        seconds=exercise_data.get("seconds", 0),
                        is_hold=exercise_data.get("is_hold", False),  
                        group=group_objects.get(group_id),
                        video_url=exercise_data.get("video_url"),
                        custom_cues=exercise_data.get("custom_cues"),
                    )
                
                all_created_workouts.append(workout)
        
        return all_created_workouts


class BaseWorkoutCopySerializer(serializers.Serializer):
    base_workout_name = serializers.CharField(max_length=200)
    new_base_workout_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        start = attrs.get("start_date")
        end = attrs.get("end_date")
        if start and end and start > end:
            raise serializers.ValidationError("start_date must be before or equal to end_date")
        if end and not start:
            raise serializers.ValidationError("start_date is required if end_date is provided")
        return attrs


class LikedExerciseSerializer(serializers.ModelSerializer):
    exercise = ExerciseSerializer(read_only=True)
    
    class Meta:
        model = LikedExercise
        fields = ["id", "user", "exercise", "liked_at"]
        read_only_fields = ["user", "liked_at"]


class DeckOfCardsWorkoutUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    session_type = serializers.CharField(required=False)
    movement_level = serializers.CharField(required=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    equipment = serializers.ListField(child=serializers.CharField(), required=False)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)
    myzone_effort_range = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    groups = serializers.ListField(child=serializers.DictField(), required=False)
    face_cards = serializers.JSONField(required=False)
    timer_seconds = serializers.IntegerField(required=False, allow_null=True)
    allow_staff_set_timer = serializers.BooleanField(required=False)

    @transaction.atomic
    def update(self, instance, validated_data):
        deck_config = instance.deck_config or {}
        GROUP_TYPE_MAP = {"Circuit": 1, "Superset": 2, "Finisher": 3}

        for field in [
            "name", "description", "session_type", "movement_level",
            "myzone_effort_range", "notes", "start_date", "end_date"
        ]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        if "tags" in validated_data:
            tag_objs = [WorkoutTag.objects.get_or_create(name=t)[0] for t in validated_data["tags"]]
            instance.tags.set(tag_objs)
        if "equipment" in validated_data:
            eq_objs = [Equipment.objects.get_or_create(name=e)[0] for e in validated_data["equipment"]]
            instance.equipment.set(eq_objs)

        deck_config["timer_seconds"] = validated_data.get(
            "timer_seconds", deck_config.get("timer_seconds")
        )
        deck_config["allow_staff_set_timer"] = validated_data.get(
            "allow_staff_set_timer", deck_config.get("allow_staff_set_timer", True)
        )

        def get_exercise_data(exercise_id):
            try:
                ex = Exercise.objects.get(id=exercise_id)
                return {
                    "id": ex.id,
                    "name": ex.name,
                    "description": ex.description,
                    "video_url": ex.video_url,
                    "coaching_cues": ex.coaching_cues,
                }, ex
            except Exercise.DoesNotExist:
                return None, None

        def sync_workout_exercise(workout, exercise, cue_label, group=None, is_joker=False):
            if not exercise:
                return
            existing = WorkoutExercise.objects.filter(
                workout=workout, exercise=exercise, group=group
            ).first()
            if not existing:
                WorkoutExercise.objects.create(
                    workout=workout,
                    exercise=exercise,
                    order=WorkoutExercise.objects.filter(workout=workout).count() + 1,
                    is_joker=is_joker,
                    group=group,
                    custom_cues=cue_label,
                )

        def handle_group(payload):
            group_id = payload.get("group_id")
            group_type_name = payload.get("group_type", "Circuit")
            group_number = payload.get("group_number") or (
                WorkoutGroup.objects.filter(workout=instance).count() + 1
            )
            group_type_val = GROUP_TYPE_MAP.get(group_type_name, 1)

            if group_id:
                group = WorkoutGroup.objects.filter(id=group_id, workout=instance).first()
                if group:
                    group.group_type = group_type_val
                    group.group_number = group_number
                    group.save()
                    return group
            return WorkoutGroup.objects.create(
                workout=instance,
                group_type=group_type_val,
                group_number=group_number,
            )

        if "groups" in validated_data:
            existing_groups = {g["group_id"]: g for g in deck_config.get("groups", []) if g.get("group_id")}
            updated_groups_config = []

            for g_payload in validated_data["groups"]:
                group = handle_group(g_payload)
                group_entry = existing_groups.get(group.id, {
                    "group_type": group.get_group_type_display(),
                    "group_id": group.id,
                    "group_number": group.group_number,
                    "suits": {}
                })

                suits_data = g_payload.get("suits", {})
                for suit_name, suit_payload in suits_data.items():
                    existing_suit = group_entry["suits"].get(suit_name, {"exercises": []})
                    
                    if "exercises" in suit_payload:
                        new_exercises = []
                        for ex_item in suit_payload.get("exercises", []):
                            ex_data, ex = get_exercise_data(ex_item.get("exercise_id"))
                            if ex_data:
                                new_exercises.append(ex_data)
                                sync_workout_exercise(
                                    instance,
                                    ex,
                                    f"{group_entry['group_type']} - {suit_name} - {ex.name}",
                                    group=group
                                )
                        existing_suit["exercises"] = new_exercises

                    group_entry["suits"][suit_name] = existing_suit
                updated_groups_config.append(group_entry)

            deck_config["groups"] = updated_groups_config

        if "face_cards" in validated_data:
            existing_faces = deck_config.get("face_cards", {})
            for key, payload in validated_data["face_cards"].items():
                face_entry = existing_faces.get(key, {"exercises": []})
                
                if "exercises" in payload:
                    new_exercises = []
                    for ex_item in payload.get("exercises", []):
                        ex_data, ex = get_exercise_data(ex_item.get("exercise_id"))
                        if ex_data:
                            new_exercises.append(ex_data)
                            sync_workout_exercise(
                                instance,
                                ex,
                                f"Face Card - {key} - {ex.name}",
                                is_joker=(key.lower() == "joker")
                            )
                    face_entry["exercises"] = new_exercises
                
                existing_faces[key] = face_entry
            deck_config["face_cards"] = existing_faces

        instance.deck_config = deck_config
        instance.save()
        return instance


class MultiLevelDeckWorkoutCreateSerializer(serializers.Serializer):
    base_workout_name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    session_type = serializers.ListField(child=serializers.CharField(max_length=100), required=True, min_length=1)
    movement_levels = serializers.ListField(child=serializers.CharField(), required=True)
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    equipment = serializers.ListField(child=serializers.CharField(), required=False)
    level_configurations = serializers.JSONField()
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    myzone_effort_range = serializers.CharField(max_length=50, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_level_configurations(self, value):
        valid_levels = ["Stability", "Strength", "Power"]

        if not isinstance(value, dict):
            raise serializers.ValidationError("level_configurations must be a dictionary.")

        for level, config in value.items():
            if level not in valid_levels:
                raise serializers.ValidationError(f"Invalid level '{level}'. Must be one of {valid_levels}.")
            if not isinstance(config, dict):
                raise serializers.ValidationError(f"Configuration for {level} must be a dictionary.")
            if "groups" not in config:
                raise serializers.ValidationError(f"Each level must include 'groups' list.")

            groups = config.get("groups", [])
            if not isinstance(groups, list):
                raise serializers.ValidationError(f"'groups' must be a list for level '{level}'.")

            for group in groups:
                if not isinstance(group, dict):
                    raise serializers.ValidationError(f"Each group must be a dictionary.")
                if "group_type" not in group:
                    raise serializers.ValidationError(f"Each group must include 'group_type'.")
                if "suits" not in group:
                    raise serializers.ValidationError(f"Each group must include 'suits' dictionary.")

                suits = group["suits"]
                if not isinstance(suits, dict):
                    raise serializers.ValidationError(f"'suits' must be a dictionary in group for '{level}'.")

                for suit, suit_data in suits.items():
                    if not isinstance(suit_data, dict):
                        raise serializers.ValidationError(f"Suit '{suit}' must map to a dictionary.")
                    exercises = suit_data.get("exercises", [])
                    if not isinstance(exercises, list):
                        raise serializers.ValidationError(f"'exercises' in suit '{suit}' must be a list.")
                    for ex in exercises:
                        if "exercise_id" not in ex:
                            raise serializers.ValidationError(f"Each exercise in suit '{suit}' must include 'exercise_id'.")

            for face in ["joker", "jack", "queen", "king", "ace"]:
                if face in config:
                    face_data = config[face]
                    if not isinstance(face_data, dict):
                        raise serializers.ValidationError(f"'{face}' must be a dictionary.")
                    exercises = face_data.get("exercises", [])
                    if not isinstance(exercises, list):
                        raise serializers.ValidationError(f"'exercises' in '{face}' must be a list.")
                    for ex in exercises:
                        if "exercise_id" not in ex:
                            raise serializers.ValidationError(f"Each exercise in '{face}' must include 'exercise_id'.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        base_name_prefix = validated_data["base_workout_name"]
        session_types = validated_data.pop("session_type")
        levels = validated_data["movement_levels"]
        configs = validated_data["level_configurations"]
        tags_data = validated_data.pop("tags", [])
        equipment_data = validated_data.pop("equipment", [])
        created_by = validated_data.pop("created_by")
        global_notes = validated_data.get("notes", "")

        all_created_workouts = []
        tag_objs = [WorkoutTag.objects.get_or_create(name=t)[0] for t in tags_data]
        eq_objs = [Equipment.objects.get_or_create(name=e)[0] for e in equipment_data]

        GROUP_TYPE_MAP = {"Circuit": 1, "Superset": 2, "Finisher": 3}

        for session_type in session_types:
            session_base_workout_name = f"{base_name_prefix} - {session_type}"

            for level in levels:
                config = configs.get(level)
                if not config:
                    continue

                deck_config = {
                    "groups": [],
                    "face_cards": {},
                    "timer_seconds": config.get("timer_seconds"),
                    "allow_staff_set_timer": config.get("allow_staff_set_timer", True),
                }

                workout_name = f"{session_base_workout_name} ({level})"
                workout = Workout.objects.create(
                    name=workout_name,
                    base_workout_name=session_base_workout_name,
                    description=validated_data.get("description", ""),
                    movement_level=level,
                    session_type=session_type,
                    workout_type=4,
                    notes=config.get("notes", global_notes),
                    created_by=created_by,
                    start_date=validated_data.get("start_date"),
                    end_date=validated_data.get("end_date"),
                    myzone_effort_range=validated_data.get("myzone_effort_range", ""),
                )

                workout.tags.set(tag_objs)
                workout.equipment.set(eq_objs)

                order_counter = 1

                for group_config in config.get("groups", []):
                    group_type_name = group_config.get("group_type")
                    group_number = group_config.get("group_number", order_counter)
                    suits = group_config.get("suits", {})

                    group_type_value = GROUP_TYPE_MAP.get(group_type_name, 1)
                    group = WorkoutGroup.objects.create(
                        workout=workout,
                        group_type=group_type_value,
                        group_number=group_number,
                    )

                    group_entry = {
                        "group_id": group.id,
                        "group_type": group.get_group_type_display(),
                        "suits": {}
                    }

                    for suit, suit_data in suits.items():
                        exercises = suit_data.get("exercises", [])
                        group_entry["suits"][suit] = {"exercises": []}

                        for ex_item in exercises:
                            exercise_id = ex_item.get("exercise_id")
                            if not exercise_id:
                                continue
                            try:
                                ex = Exercise.objects.get(id=exercise_id)
                                WorkoutExercise.objects.create(
                                    workout=workout,
                                    exercise=ex,
                                    suit=suit,
                                    group=group,
                                    order=ex_item.get("order", order_counter),
                                    is_joker=False,
                                    custom_cues=f"Suit: {suit} - {ex.name}",
                                )
                                group_entry["suits"][suit]["exercises"].append({
                                    "id": ex.id,
                                    "name": ex.name,
                                    "description": ex.description,
                                    "video_url": ex.video_url,
                                    "coaching_cues": ex.coaching_cues,
                                })
                                order_counter += 1
                            except Exercise.DoesNotExist:
                                group_entry["suits"][suit]["exercises"].append({"exercise": None})

                    deck_config["groups"].append(group_entry)

                for face in ["joker", "jack", "queen", "king", "ace"]:
                    face_data = config.get(face)
                    if not face_data:
                        continue

                    exercises = face_data.get("exercises", [])
                    create_group = face_data.get("create_group", False)
                    group_type_name = face_data.get("group_type")
                    group = None

                    if create_group and group_type_name:
                        group_type_value = GROUP_TYPE_MAP.get(group_type_name, 1)
                        group = WorkoutGroup.objects.create(
                            workout=workout,
                            group_type=group_type_value,
                            group_number=order_counter,
                        )

                    deck_config["face_cards"][face] = {
                        "group_type": group.get_group_type_display() if group else None,
                        "group_id": group.id if group else None,
                        "exercises": [],
                    }

                    for ex_item in exercises:
                        exercise_id = ex_item.get("exercise_id")
                        if not exercise_id:
                            continue
                        try:
                            ex = Exercise.objects.get(id=exercise_id)
                            WorkoutExercise.objects.create(
                                workout=workout,
                                exercise=ex,
                                order=ex_item.get("order", order_counter),
                                is_joker=(face == "joker"),
                                group=group,
                                custom_cues=f"{face.capitalize()} - {ex.name}",
                            )
                            deck_config["face_cards"][face]["exercises"].append({
                                "id": ex.id,
                                "name": ex.name,
                                "description": ex.description,
                                "video_url": ex.video_url,
                                "coaching_cues": ex.coaching_cues,
                            })
                            order_counter += 1
                        except Exercise.DoesNotExist:
                            deck_config["face_cards"][face]["exercises"].append({"exercise": None})

                workout.deck_config = deck_config
                workout.save(update_fields=["deck_config"])
                all_created_workouts.append(workout)

        return all_created_workouts


class DeckWorkoutResponseSerializer(WorkoutSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        deck_config = data.get("deck_config")

        if not deck_config:
            return data

        def get_exercise_detail(exercise_id):
            exercise = Exercise.objects.filter(id=exercise_id).first()
            if exercise:
                return {
                    "id": exercise.id,
                    "name": exercise.name,
                    "description": exercise.description,
                    "video_url": getattr(exercise, "video_url", None),
                    "coaching_cues": getattr(exercise, "coaching_cues", None),
                }
            return None

        suits = deck_config.get("suits", {})
        for suit_name, suit_data in suits.items():
            exercise_id = suit_data.get("exercise_id")
            if exercise_id:
                exercise_detail = get_exercise_detail(exercise_id)
                if exercise_detail:
                    suit_data["exercise"] = exercise_detail
                suit_data.pop("exercise_id", None)

        for card in ["joker", "jack", "queen", "king", "ace"]:
            if card in deck_config and deck_config[card].get("exercise_id"):
                exercise_id = deck_config[card]["exercise_id"]
                exercise_detail = get_exercise_detail(exercise_id)
                if exercise_detail:
                    deck_config[card]["exercise"] = exercise_detail
                deck_config[card].pop("exercise_id", None)

        data["deck_config"] = deck_config
        return data


class DeckWorkoutMinimalSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()
 
    class Meta:
        model = Workout
        fields = [
            "id",
            "name",
            "movement_level",
            "created_by",
            "created_at",
            "start_date",
            "end_date",
            "deck_config",
        ]

    def get_created_by(self, obj):
        if obj.created_by and hasattr(obj.created_by, "profile"):
            return f"{obj.created_by.profile.first_name} {obj.created_by.profile.last_name}"
        return None


class Saveserializer(serializers.ModelSerializer):
    class Meta:
        model = Exercise
        fields = "__all__"


class ExerciseUpdateSerializer(serializers.ModelSerializer):
    tags = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=WorkoutTag.objects.none(),
        required=False
    )
    equipment = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Equipment.objects.none(),
        required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = WorkoutTag.objects.all()
        self.fields["equipment"].queryset = Equipment.objects.all()

    class Meta:
        model = Exercise
        fields = [
            'name',
            'description',
            'video_url',
            'video_file',
            'coaching_cues',
            'tags',
            'equipment'
        ]
        extra_kwargs = {
            'name': {'required': False},
            'description': {'required': False},
            'video_url': {'required': False},
            'video_file': {'required': False},
            'coaching_cues': {'required': False},
        }


class ExerciseSaveSerializer(serializers.ModelSerializer):
    tags = serializers.PrimaryKeyRelatedField(
        queryset=WorkoutTag.objects.none(),
        many=True,
        error_messages={
            'does_not_exist': 'The tag with id={pk_value} does not exist.',
            'incorrect_type': 'Tag IDs must be integers.'
        }
    )
    equipment = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Equipment.objects.none(), required=False,
        error_messages={
            'does_not_exist': 'The equipment with id={pk_value} does not exist.',
            'incorrect_type': 'equipment IDs must be integers.'
        }
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = WorkoutTag.objects.all()
        self.fields["equipment"].queryset = Equipment.objects.all()

    class Meta:
        model = Exercise
        fields = [
            "id",
            "name",
            "description",
            "video_url",
            "video_file",
            "coaching_cues",
            "tags",
            "equipment",
        ]

    def create(self, validated_data):
        tags = validated_data.pop("tags", [])
        equipment = validated_data.pop("equipment", [])
        exercise = Exercise.objects.create(**validated_data)
        exercise.tags.set(tags)
        exercise.equipment.set(equipment)
        return exercise


class ExerciseSubstitutionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExerciseSubstitutionLog
        fields = ['id', 'user', 'workout', 'original_exercise', 'substituted_exercise', 'reason', 'created_at']
        read_only_fields = ['user', 'created_at']

class DetailedExerciseAlternativeSerializer(serializers.ModelSerializer):
    match_score = serializers.FloatField(read_only=True)
    tags = serializers.StringRelatedField(many=True)
    equipment = serializers.StringRelatedField(many=True)

    class Meta:
        model = Exercise
        fields = ['id', 'name', 'video_url', 'tags', 'equipment', 'match_score']

class ExerciseSubstitutionLogDetailSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.profile.full_name', read_only=True, default="Unknown User")
    original_exercise_name = serializers.CharField(source='original_exercise.name', read_only=True)
    substituted_exercise_name = serializers.CharField(source='substituted_exercise.name', read_only=True)
    workout_name = serializers.CharField(source='workout.name', read_only=True)
    
    substituted_exercise_video_url = serializers.URLField(source='substituted_exercise.video_url', read_only=True)
    substituted_exercise_coaching_cues = serializers.CharField(source='substituted_exercise.coaching_cues', read_only=True)
    substituted_exercise_description = serializers.CharField(source='substituted_exercise.description', read_only=True)
    
    substituted_exercise_tags = serializers.StringRelatedField(source='substituted_exercise.tags', many=True, read_only=True)
    substituted_exercise_equipment = serializers.StringRelatedField(source='substituted_exercise.equipment', many=True, read_only=True)

    class Meta:
        model = ExerciseSubstitutionLog
        fields = [
            'id', 
            'user', 'user_name', 
            'workout', 'workout_name',
            'original_exercise', 'original_exercise_name',
            'substituted_exercise', 'substituted_exercise_name',
            'substituted_exercise_video_url', 
            'substituted_exercise_tags', 
            'substituted_exercise_equipment',
            'substituted_exercise_coaching_cues',
            'substituted_exercise_description',
            'reason', 
            'created_at'
        ]
