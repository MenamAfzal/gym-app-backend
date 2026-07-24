from rest_framework import serializers
from .models import (
    CycleDailyLog,
    DailyReflection,
    MenstrualCycle,
    MorningEntry,
    EveningEntry,
    FocusOption,
    MorningFocusSelection,
    EveningFocusReflection,
    SymptomCategory,
    SymptomTag,
)
from django.db import models

class MorningFocusSelectionListSerializer(serializers.ListSerializer):
    def create(self, validated_data):
        items = []
        parent = self.context.get("parent")
        for item in validated_data:
            item["morning_entry"] = parent
            items.append(MorningFocusSelection.objects.create(**item))
        return items

    def update(self, instance, validated_data):
        instance.all().delete()
        return self.create(validated_data)


class FocusOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FocusOption
        fields = ("id", "name", "slug", "icon", "is_active")


class MorningFocusSelectionSerializer(serializers.ModelSerializer):
    focus = FocusOptionSerializer(read_only=True)
    
    # Make focus_id optional to allow creating new focus options via name
    focus_id = serializers.PrimaryKeyRelatedField(
        queryset=FocusOption.objects.filter(is_active=True),
        source="focus",
        write_only=True,
        required=False,
        allow_null=True
    )
    
    # New fields to handle dynamic creation
    focus_name = serializers.CharField(write_only=True, required=False)
    focus_icon = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = MorningFocusSelection
        fields = ("id", "focus", "focus_id", "focus_name", "focus_icon", "action_plan")
        list_serializer_class = MorningFocusSelectionListSerializer

    def validate(self, attrs):
        focus_obj = attrs.get("focus")
        focus_name = attrs.get("focus_name")
        focus_icon = attrs.get("focus_icon")
        
        # Get the current user
        user = self.context['request'].user 

        if not focus_obj and not focus_name:
            raise serializers.ValidationError("Either 'focus_id' or 'focus_name' must be provided.")

        if not focus_obj and focus_name:
            # SEARCH: Look for an existing option that is either Global or belongs to THIS User
            focus_obj = FocusOption.objects.filter(
                name=focus_name
            ).filter(
                models.Q(user=None) | models.Q(user=user)
            ).first()

            # CREATE: If not found, create a PRIVATE custom option for this user
            if not focus_obj:
                focus_obj = FocusOption.objects.create(
                    name=focus_name,
                    user=user,  # <--- CRITICAL: Assign the user here
                    icon=focus_icon if focus_icon else "star",
                    is_active=True
                )
            
            attrs["focus"] = focus_obj

        attrs.pop("focus_name", None)
        attrs.pop("focus_icon", None)
        
        return attrs


class EveningFocusReflectionSerializer(serializers.ModelSerializer):
    focus = FocusOptionSerializer(read_only=True)
    
    # Make focus_id optional
    focus_id = serializers.PrimaryKeyRelatedField(
        queryset=FocusOption.objects.filter(is_active=True), 
        source="focus", 
        write_only=True,
        required=False,
        allow_null=True
    )

    # New fields to handle dynamic creation
    focus_name = serializers.CharField(write_only=True, required=False)
    focus_icon = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = EveningFocusReflection
        fields = ("id", "focus", "focus_id", "focus_name", "focus_icon", "effort", "improvement")

    def validate(self, attrs):
        focus_obj = attrs.get("focus")
        focus_name = attrs.get("focus_name")
        focus_icon = attrs.get("focus_icon")
        
        # Get the current user
        user = self.context['request'].user 

        if not focus_obj and not focus_name:
            raise serializers.ValidationError("Either 'focus_id' or 'focus_name' must be provided.")

        if not focus_obj and focus_name:
            # SEARCH: Look for an existing option that is either Global or belongs to THIS User
            focus_obj = FocusOption.objects.filter(
                name=focus_name
            ).filter(
                models.Q(user=None) | models.Q(user=user)
            ).first()

            # CREATE: If not found, create a PRIVATE custom option for this user
            if not focus_obj:
                focus_obj = FocusOption.objects.create(
                    name=focus_name,
                    user=user,  # <--- CRITICAL: Assign the user here
                    icon=focus_icon if focus_icon else "star",
                    is_active=True
                )
            
            attrs["focus"] = focus_obj

        attrs.pop("focus_name", None)
        attrs.pop("focus_icon", None)
        
        return attrs


class MorningEntrySerializer(serializers.ModelSerializer):
    focus_selections = MorningFocusSelectionSerializer(many=True, required=False)

    class Meta:
        model = MorningEntry
        fields = (
            "id", "mood", "sleep_quality", "affirmation",
            "gratitude_1", "gratitude_2", "gratitude_3",
            "focus_selections",
        )

    def create(self, validated_data):
        # 1. Pop the ALREADY VALIDATED data (contains Model Objects, not IDs)
        focus_data = validated_data.pop("focus_selections", [])
        
        # 2. Create the parent
        morning_entry = MorningEntry.objects.create(**validated_data)

        # 3. Create children directly
        for item in focus_data:
            # item is {'focus': <FocusOption object>, 'action_plan': '...'}
            MorningFocusSelection.objects.create(morning_entry=morning_entry, **item)

        return morning_entry

    def update(self, instance, validated_data):
        focus_data = validated_data.pop("focus_selections", None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if focus_data is not None:
            instance.focus_selections.all().delete()
            for item in focus_data:
                MorningFocusSelection.objects.create(morning_entry=instance, **item)
        return instance


class EveningEntrySerializer(serializers.ModelSerializer):
    focus_reflections = EveningFocusReflectionSerializer(many=True, required=False)

    class Meta:
        model = EveningEntry
        fields = (
            "id", "stress_level", "mood_after",
            "highlight_1", "highlight_2", "highlight_3",
            "lesson", "focus_reflections",
        )

    def create(self, validated_data):
        focus_data = validated_data.pop("focus_reflections", [])
        evening_entry = EveningEntry.objects.create(**validated_data)
        
        for item in focus_data:
            EveningFocusReflection.objects.create(evening_entry=evening_entry, **item)
        return evening_entry

    def update(self, instance, validated_data):
        focus_data = validated_data.pop("focus_reflections", None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if focus_data is not None:
            instance.focus_reflections.all().delete()
            for item in focus_data:
                EveningFocusReflection.objects.create(evening_entry=instance, **item)
        return instance


class DailyReflectionSerializer(serializers.ModelSerializer):
    morning = MorningEntrySerializer(required=False, allow_null=True)
    evening = EveningEntrySerializer(required=False, allow_null=True)
    date = serializers.DateField()

    class Meta:
        model = DailyReflection
        fields = ("id", "user", "date", "morning", "evening")
        read_only_fields = ("user",)

    def validate(self, attrs):
        morning = attrs.get("morning")
        evening = attrs.get("evening")
        if not morning and not evening:
            raise serializers.ValidationError("Provide at least one of: morning, evening")
        return attrs

    def _handle_nested(self, serializer_class, instance, data, parent_obj, field_name):
        """
        Generic helper to create or update nested entries.
        NOTICE: We do NOT call is_valid() here because 'data' is already validated.
        """
        if not data:
            return None

        # Link the data to the parent (DailyReflection)
        data[field_name] = parent_obj  

        # Instantiate serializer only to access its methods/context
        ser = serializer_class(context=self.context)

        if instance:
            # Pass validated data directly to update
            return ser.update(instance, data)
        else:
            # Pass validated data directly to create
            return ser.create(data)

    def create(self, validated_data):
        user = self.context["request"].user
        morning_data = validated_data.pop("morning", None)
        evening_data = validated_data.pop("evening", None)

        reflection, _ = DailyReflection.objects.get_or_create(
            user=user,
            date=validated_data["date"]
        )

        reflection.morning = self._handle_nested(
            MorningEntrySerializer, getattr(reflection, "morning", None), morning_data, reflection, "reflection"
        )
        reflection.evening = self._handle_nested(
            EveningEntrySerializer, getattr(reflection, "evening", None), evening_data, reflection, "reflection"
        )

        for attr, value in validated_data.items():
            setattr(reflection, attr, value)
        reflection.save()
        return reflection

    def update(self, instance, validated_data):
        morning_data = validated_data.pop("morning", None)
        evening_data = validated_data.pop("evening", None)

        instance.morning = self._handle_nested(
            MorningEntrySerializer, getattr(instance, "morning", None), morning_data, instance, "reflection"
        )
        instance.evening = self._handle_nested(
            EveningEntrySerializer, getattr(instance, "evening", None), evening_data, instance, "reflection"
        )

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class MenstrualCycleCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MenstrualCycle
        fields = (
            "last_period_start_date",
            "cycle_length_days",
            "period_duration_days",
        )

    def validate_cycle_length_days(self, value):
        if value < 21 or value > 35:
            raise serializers.ValidationError(
                "Cycle length outside normal range (21–35 days)."
            )
        return value

    def validate(self, attrs):
        instance = self.instance

        cycle_length = (
            attrs.get("cycle_length_days")
            if "cycle_length_days" in attrs
            else instance.cycle_length_days if instance else None
        )

        period_duration = (
            attrs.get("period_duration_days")
            if "period_duration_days" in attrs
            else instance.period_duration_days if instance else None
        )

        # Only validate if both values exist
        if cycle_length and period_duration:
            if period_duration >= cycle_length:
                raise serializers.ValidationError(
                    "Period duration cannot be equal or longer than cycle length."
                )

        return attrs

class SymptomTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = SymptomTag
        fields = ['id', 'name', 'user'] # Sending 'user' lets frontend know if it's custom

class SymptomCategorySerializer(serializers.ModelSerializer):
    # We use a MethodField to filter tags dynamically
    tags = serializers.SerializerMethodField()

    class Meta:
        model = SymptomCategory
        fields = ['id', 'name', 'icon_url', 'tags']

    def get_tags(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return []
            
        # FETCH: System Tags (user=None) OR Custom Tags for this user
        tags = SymptomTag.objects.filter(
            category=obj,
            is_active=True
        ).filter(
            models.Q(user=None) | models.Q(user=request.user)
        ).order_by('name')
        
        return SymptomTagSerializer(tags, many=True).data

# Serializer for Creating a New Custom Tag
class CreateCustomTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = SymptomTag
        fields = ['category', 'name']

    def create(self, validated_data):
        # Automatically assign the logged-in user
        user = self.context['request'].user
        return SymptomTag.objects.create(user=user, **validated_data)

class CycleDailyLogSerializer(serializers.ModelSerializer):
    symptoms = serializers.PrimaryKeyRelatedField(
        many=True, 
        queryset=SymptomTag.objects.all(),
        required=False
    )
    
    # Read: We use a method field to categorize symptoms for the frontend
    symptom_details = serializers.SerializerMethodField()

    class Meta:
        model = CycleDailyLog
        fields = ['id', 'date', 'flow_intensity', 'notes', 'symptoms', 'symptom_details', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_symptom_details(self, obj):
        """
        Groups the selected symptoms by category for easy display.
        Example: { "Pain": ["Cramps", "Headache"], "Mood": ["Happy"] }
        """
        data = {}
        for tag in obj.symptoms.all():
            cat_name = tag.category.name
            if cat_name not in data:
                data[cat_name] = []
            data[cat_name].append(tag.name)
        return data        
    