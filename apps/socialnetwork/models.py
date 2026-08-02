from django.db import models
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from core_models.base_models import BaseModel
from django.contrib.contenttypes.models import ContentType
import uuid

User = get_user_model()


class Post(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='%(class)s_posts')
    caption = models.CharField(max_length=2400, null=True, blank=True)

    likes_count = models.IntegerField(default=0, null=True,blank= True)
    comments_count = models.IntegerField(default=0, null=True,blank= True)
    external_link = models.URLField(blank=True, null=True)
    internal_deep_link = models.CharField(max_length=255, blank=True, null=True)
    visible_to_staff = models.BooleanField(default=True, null=True,blank= True)
    visible_to_clients = models.BooleanField(default=True, null=True,blank= True)
    location = models.CharField(max_length=255, blank=True, null=True)
    
    class Meta:
        abstract = True


class Photo(Post):
    image = models.ImageField(upload_to='photos/')
    # location = models.CharField(max_length=255, blank=True, null=True)
    
    def __str__(self):
        return f"Photo: {self.caption} by {self.user.email}"


class Video(Post):
    video_file = models.FileField(upload_to='videos/')
    duration = models.DurationField(blank=True, null=True)
    # location = models.CharField(max_length=255, blank=True, null=True)
    
    def __str__(self):
        return f"Video: {self.caption} by {self.user.email}"


class PollOption(models.Model):
    text = models.CharField(max_length=255, null=True)
    votes_count = models.IntegerField(default=0)
    
    def __str__(self):
        return self.text


class Poll(Post):
    question = models.CharField(max_length=550, null=True,blank= True)
    options = models.ManyToManyField(PollOption, related_name='polls', null=True,blank= True)
    end_date = models.DateTimeField(blank=True, null=True)
    is_multiple_choice = models.BooleanField(default=False)
    
    def __str__(self):
        return f"Poll: {self.question} by {self.user.email}"


class Vote(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='votes')
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='user_votes')
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name='user_votes')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('user', 'poll', 'option')  
        
    def __str__(self):
        return f"{self.user.email} voted for {self.option.text} in {self.poll.question}"


class Like(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='likes')
    
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    
    class Meta:
        unique_together = ('user', 'content_type', 'object_id')  #multiple vote prevention..
        
    def __str__(self):
        return f"{self.user.email} liked {self.content_object}"


class Comment(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comments')
    content = models.TextField()
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    likes_count = models.IntegerField(default=0)
    dislikes_count = models.IntegerField(default=0)
    
    def __str__(self):
        return f"Comment by {self.user.email} on {self.content_object}"
    

class CommentReaction(BaseModel):
    REACTION_CHOICES = [
        ('LIKE', 'Like'),
        ('DISLIKE', 'Dislike'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comment_reactions')
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='reactions')
    reaction_type = models.CharField(max_length=10, choices=REACTION_CHOICES)

    class Meta:
        # Ensures a user can only have one reaction (Like OR Dislike) per comment
        unique_together = ('user', 'comment')

    def __str__(self):
        return f"{self.user.email} {self.reaction_type}d comment {self.comment.id}"    


class MediaGroup(BaseModel):
    """
    A group of media items (photos and videos) that belong together.
    Used for multi-media uploads and grouped content.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='media_groups')
    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    visible_to_staff = models.BooleanField(default=True)
    visible_to_clients = models.BooleanField(default=True)
    comments_enabled = models.BooleanField(default=True)
    
    def __str__(self):
        return f"MediaGroup {self.id} by {self.user.email}"

