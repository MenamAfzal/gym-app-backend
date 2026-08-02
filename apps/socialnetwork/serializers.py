from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.socialnetwork.models import (
    Photo, Video, Poll, PollOption, Vote, Comment, Like
)

User = get_user_model()



# class UserMinimalSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = User
#         fields = ['id', 'email', 'username' ]

class UserMinimalSerializer(serializers.ModelSerializer):
    profile_picture = serializers.SerializerMethodField()
    first_name = serializers.SerializerMethodField()
    last_name = serializers.SerializerMethodField()
    
    
    class Meta:
        model = User
        fields = ['id', 'email', 'username', 'profile_picture', 'first_name', 'last_name']
    
    def get_profile_picture(self, obj): 
        if hasattr(obj, 'profile') and obj.profile.profile_image:
            request = self.context.get('request')
            if request is not None:
                return request.build_absolute_uri(obj.profile.profile_image.url)
            return obj.profile.profile_image.url
        return None
    
    def get_first_name(self, obj):
        return obj.profile.first_name if hasattr(obj, 'profile') else None
    
    def get_last_name(self, obj):
        return obj.profile.last_name if hasattr(obj, 'profile') else None
    

class CommentSerializer(serializers.ModelSerializer):
    user = UserMinimalSerializer(read_only=True)
    replies = serializers.SerializerMethodField()
    # New fields for frontend
    has_liked = serializers.SerializerMethodField()
    has_disliked = serializers.SerializerMethodField()
    
    class Meta:
        model = Comment
        # Add new count and status fields to the list
        fields = [
            'id', 'uuid', 'user', 'content', 'parent', 'created_at', 
            'likes_count', 'dislikes_count', 'has_liked', 'has_disliked', 'replies'
        ]
        read_only_fields = ['id', 'uuid', 'user', 'created_at', 'likes_count', 'dislikes_count']
    def get_replies(self, obj):
        if obj.replies.exists():
            return CommentSerializer(obj.replies.all(), many=True).data
        return []
    
    def get_has_liked(self, obj):
        return self._get_user_reaction(obj, 'LIKE')

    def get_has_disliked(self, obj):
        return self._get_user_reaction(obj, 'DISLIKE')

    def _get_user_reaction(self, obj, r_type):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            from apps.socialnetwork.models import CommentReaction
            return CommentReaction.objects.filter(
                user=request.user, comment=obj, reaction_type=r_type
            ).exists()
        return False


class LikeSerializer(serializers.ModelSerializer):
    user = UserMinimalSerializer(read_only=True)
    
    class Meta:
        model = Like
        fields = ['id', 'user', 'created_at']
        read_only_fields = ['id', 'user', 'created_at']


class PollOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PollOption
        fields = ['id', 'text', 'votes_count']
        read_only_fields = ['id', 'votes_count']


class PhotoSerializer(serializers.ModelSerializer):
    # user = UserMinimalSerializer(read_only=True)
    likes_count = serializers.IntegerField(read_only=True)
    comments_count = serializers.IntegerField(read_only=True)
    has_liked = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    comments_enabled = serializers.BooleanField(default=True)
    has_liked = serializers.SerializerMethodField()
    
    class Meta:
        model = Photo
        fields = [
            'id', 'caption',  'image', 'created_at',
            'updated_at', 'likes_count', 'comments_count', 
            'external_link', 'internal_deep_link', 
            'has_liked', 'comments_enabled',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'likes_count', 'comments_count']
    
    def get_image(self, obj):
        # Debug print to help diagnose issues
        try:
            if obj.image:
                print(f"Image URL: {obj.image.url}")
                request = self.context.get('request')
                if request is not None:
                    return request.build_absolute_uri(obj.image.url)
                return obj.image.url
            else:
                print("Image is None")
        except Exception as e:
            print(f"Error getting image URL: {str(e)}")
        return None
    
    def get_has_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            content_type = ContentType.objects.get_for_model(Photo)
            return Like.objects.filter(
                user=request.user,
                content_type=content_type,
                object_id=obj.id
            ).exists()
        return False


# New serializer for photo uploads
class PhotoUploadSerializer(serializers.ModelSerializer):
    # Explicitly define the image field to handle file upload issues
    image = serializers.ImageField(required=True)
    
    class Meta:
        model = Photo
        fields = ['image', 'caption', 'location', 'external_link', 'internal_deep_link', 'visible_to_staff', 'visible_to_clients']
        
    def validate_image(self, value):
        # Check if file is provided
        if not value:
            raise serializers.ValidationError("Image file is required.")
            
        # Debug information
        print(f"Validating image: {type(value)}, name: {getattr(value, 'name', 'unknown')}")
        
        # Check file size
        if value.size > 10 * 1024 * 1024:  # 10MB limit
            raise serializers.ValidationError("Image file too large. Maximum size is 10MB.")
            
        return value


class PhotoDetailSerializer(PhotoSerializer):
    comments = serializers.SerializerMethodField()
    liked_by = serializers.SerializerMethodField()
    profile_picture = serializers.SerializerMethodField()
    
    class Meta(PhotoSerializer.Meta):
        fields = PhotoSerializer.Meta.fields + ['comments', 'liked_by', 'profile_picture']
    
    def get_comments(self, obj):
        content_type = ContentType.objects.get_for_model(Photo)
        comments = Comment.objects.filter(
            content_type=content_type,
            object_id=obj.id,
            parent=None  # only getting the top level comments of that post
        ).order_by('-created_at')
        return CommentSerializer(comments, many=True).data
    
    def get_liked_by(self, obj):
        content_type = ContentType.objects.get_for_model(Photo)
        likes = Like.objects.filter(content_type=content_type, object_id=obj.id).select_related('user')[:10]
        return [
            {
                'id': like.user.id,
                'username': like.user.username,
                'email': like.user.email,
                'profile_picture': like.user.profile_picture
            }
            for like in likes
        ]

    def get_profile_picture(self, user):
        return user.profile_picture


class VideoSerializer(serializers.ModelSerializer):
    # user = UserMinimalSerializer(read_only=True)
    video_file = serializers.SerializerMethodField()
    has_liked = serializers.SerializerMethodField()
    comments_enabled = serializers.BooleanField(default=True)

    class Meta:
        model = Video
        fields = [
            'id', 'caption',
            'video_file',
            'duration', 'created_at', 'updated_at', 
            'likes_count',
            'comments_count', 'external_link', 'internal_deep_link',
            'has_liked', 'comments_enabled',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'likes_count', 'comments_count']
    
    def get_video_file(self, obj):
        # Debug print to help diagnose issues
        try:
            if obj.video_file:
                print(f"Video URL: {obj.video_file.url}")
                request = self.context.get('request')
                if request is not None:
                    return request.build_absolute_uri(obj.video_file.url)
                return obj.video_file.url
            else:
                print("Video file is None")
        except Exception as e:
            print(f"Error getting video URL: {str(e)}")
        return None
    
    def get_has_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            content_type = ContentType.objects.get_for_model(Video)
            return Like.objects.filter(
                user=request.user,
                content_type=content_type,
                object_id=obj.id
            ).exists()
        return False


# New serializer for video uploads
class VideoUploadSerializer(serializers.ModelSerializer):
    # Explicitly define the video_file field to handle file upload issues c
    video_file = serializers.FileField(required=True)
    
    class Meta:
        model = Video
        fields = ['video_file', 'caption', 'location', 'external_link', 'internal_deep_link', 'visible_to_staff', 'visible_to_clients']
        
    def validate_video_file(self, value):
        # Check if file is provided
        if not value:
            raise serializers.ValidationError("No video file provided")
        
        # Get file size in MB
        file_size = value.size / (1024 * 1024)  # Convert bytes to MB
        
        # Set a higher limit for videos (5GB)
        max_size_mb = 5120  # 5GB limit (increased from 2GB)
        
        if file_size > max_size_mb:
            raise serializers.ValidationError(f"Video file too large. Maximum size is {max_size_mb}MB.")
        
        return value


class VideoDetailSerializer(VideoSerializer):
    comments = serializers.SerializerMethodField()
    liked_by = serializers.SerializerMethodField()
    profile_picture = serializers.SerializerMethodField()
    
    class Meta(VideoSerializer.Meta):
        fields = VideoSerializer.Meta.fields + ['comments', 'liked_by', 'profile_picture']
    
    def get_comments(self, obj):
        content_type = ContentType.objects.get_for_model(Video)
        comments = Comment.objects.filter(
            content_type=content_type,
            object_id=obj.id,
            parent=None  # only getting the top level comments
        ).order_by('-created_at')
        return CommentSerializer(comments, many=True).data
    def get_liked_by(self, obj):
        content_type = ContentType.objects.get_for_model(Video)
        likes = Like.objects.filter(content_type=content_type, object_id=obj.id).select_related('user')[:10]
        return [
            {
                'id': like.user.id,
                'username': like.user.username,
                'email': like.user.email,
                'profile_picture': like.user.profile_picture
            }
            for like in likes
        ]

    def get_profile_picture(self, user):
        return user.profile_picture


class PollSerializer(serializers.ModelSerializer):
    user = UserMinimalSerializer(read_only=True)
    options = PollOptionSerializer(many=True)
    likes_count = serializers.IntegerField(read_only=True)
    comments_count = serializers.IntegerField(read_only=True)
    has_liked = serializers.SerializerMethodField()
    has_voted = serializers.SerializerMethodField()
    last_voted_option = serializers.SerializerMethodField()
    # comments_enabled = serializers.BooleanField(default=True)
    
    class Meta:
        model = Poll
        fields = [
            'id', 'question', 'options', 'user',
            'end_date', 'is_multiple_choice', 'created_at', 'updated_at', 
            'likes_count', 'comments_count', 
            'external_link', 'internal_deep_link',
            'has_liked','has_voted',
            'visible_to_staff', 'visible_to_clients','last_voted_option',
        ]
        read_only_fields = ['id', 'user','created_at', 'updated_at', 'likes_count', 'comments_count']
    
    def get_has_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            content_type = ContentType.objects.get_for_model(Poll)
            return Like.objects.filter(
                user=request.user,
                content_type=content_type,
                object_id=obj.id
            ).exists()
        return False
    
    def get_last_voted_option(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                vote = Vote.objects.filter(user=request.user, poll=obj).last()
                if vote:
                    return PollOptionSerializer(vote.option).data
            except Vote.DoesNotExist:
                return None
        return None
    
    def get_has_voted(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return Vote.objects.filter(user=request.user, poll=obj).exists()
        return False
    
    @transaction.atomic
    def create(self, validated_data):
        options_data = validated_data.pop('options', [])
        poll = Poll.objects.create(**validated_data)
        
        for option_data in options_data:
            option = PollOption.objects.create(**option_data)
            poll.options.add(option)
        
        return poll
    
    @transaction.atomic
    def update(self, instance, validated_data):
        options_data = validated_data.pop('options', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        
        if options_data is not None:
           
            current_options = {option.id: option for option in instance.options.all()}
            option_ids_to_keep = []
            
            for option_data in options_data:
                option_id = option_data.get('id')
                
                if option_id and option_id in current_options:
                    # Update existing option
                    option = current_options[option_id] #updating the existing option
                    for attr, value in option_data.items():
                        setattr(option, attr, value)
                    option.save()
                    option_ids_to_keep.append(option_id)
                else:
                    
                    option = PollOption.objects.create(**option_data) # creating new options
                    instance.options.add(option)
            
            for option_id, option in current_options.items():
                if option_id not in option_ids_to_keep:
                    instance.options.remove(option)
                    option.delete()
        
        return instance





class PollOptionDetailSerializer(PollOptionSerializer):
    has_voted = serializers.SerializerMethodField()
    
    class Meta(PollOptionSerializer.Meta):
        fields = PollOptionSerializer.Meta.fields + ['has_voted']
    
    def get_has_voted(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return Vote.objects.filter(user=request.user, option=obj).exists()
        return False

class PollDetailSerializer(PollSerializer):
    options = PollOptionDetailSerializer(many=True)
    comments = serializers.SerializerMethodField()
    
    class Meta(PollSerializer.Meta):
        fields = PollSerializer.Meta.fields + ['comments']
    
    def get_comments(self, obj):
        content_type = ContentType.objects.get_for_model(Poll)
        comments = Comment.objects.filter(
            content_type=content_type,
            object_id=obj.id,
            parent=None  # only getting the top level comments
        ).order_by('-created_at')
        return CommentSerializer(comments, many=True).data


class CommentSerializer(serializers.ModelSerializer):
    """Updated serializer with reaction counts and user lists"""
    user = UserMinimalSerializer(read_only=True)
    replies = serializers.SerializerMethodField()
    liked_by = serializers.SerializerMethodField()
    disliked_by = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = [
            'id', 'uuid', 'user', 'content', 'parent', 'created_at', 
            'likes_count', 'dislikes_count', 'liked_by', 'disliked_by', 'replies'
        ]
        read_only_fields = ['id', 'uuid', 'user', 'created_at']

    def get_liked_by(self, obj):
        # Fetch the first 10 users who liked this comment
        # Requires the CommentReaction model suggested in the previous turn
        from apps.socialnetwork.models import CommentReaction
        reactions = CommentReaction.objects.filter(
            comment=obj, 
            reaction_type='LIKE'
        ).select_related('user')[:10]
        return UserMinimalSerializer([r.user for r in reactions], many=True).data

    def get_disliked_by(self, obj):
        # Fetch the first 10 users who disliked this comment
        from apps.socialnetwork.models import CommentReaction
        reactions = CommentReaction.objects.filter(
            comment=obj, 
            reaction_type='DISLIKE'
        ).select_related('user')[:10]
        return UserMinimalSerializer([r.user for r in reactions], many=True).data

    def get_replies(self, obj):
        if obj.replies.exists():
            return CommentSerializer(obj.replies.all(), many=True, context=self.context).data
        return []



class VoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vote
        fields = ['id', 'poll', 'option']
        read_only_fields = ['id']
    
    def validate(self, attrs):
        user = self.context['request'].user
        poll = attrs['poll']
        option = attrs['option']
        
        if option not in poll.options.all():
            raise serializers.ValidationError("The option does not belong to this poll.")
        
        if not poll.is_multiple_choice and Vote.objects.filter(user=user, poll=poll).exists():
            raise serializers.ValidationError("You have already voted on this poll.")
        
        if Vote.objects.filter(user=user, poll=poll, option=option).exists():
            raise serializers.ValidationError("You have already voted for this option.")
        
        if poll.end_date and poll.end_date < timezone.now():
            raise serializers.ValidationError("This poll has ended.")
        
        return attrs
    
    def create(self, validated_data):
        user = self.context['request'].user
        vote = Vote.objects.create(user=user, **validated_data)
        
        option = validated_data['option']
        option.votes_count += 1
        option.save()
        
        return vote











class MediaListSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    media_type = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    
    def get_media_type(self, obj):
        return 'photo' if isinstance(obj, Photo) else 'video'
    
    def to_representation(self, instance):
        if isinstance(instance, Photo):
            return PhotoSerializer(instance).data
        elif isinstance(instance, Video):
            return VideoSerializer(instance).data
        return super().to_representation(instance)

# Add new serializer for unified multiple media uploads
class UnifiedMediaUploadSerializer(serializers.Serializer):
    """
    Serializer for handling multiple media files via a single field.
    This allows for cleaner API design using a single 'files' parameter for uploads.
    """
    files = serializers.ListField(
        child=serializers.FileField(max_length=None, allow_empty_file=False),
        required=True
    )
    caption = serializers.CharField(required=False, allow_blank=True, default="")
    location = serializers.CharField(required=False, allow_blank=True, default="")
    external_link = serializers.URLField(required=False, allow_blank=True, default="")
    internal_deep_link = serializers.CharField(required=False, allow_blank=True, default="")
    visible_to_staff = serializers.BooleanField(default=True)
    visible_to_clients = serializers.BooleanField(default=True)