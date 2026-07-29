from rest_framework import serializers
from .models import Ticket, TicketComment
from apps.users.serializers import UserSerializer

class TicketCommentSerializer(serializers.ModelSerializer):
    author_details = UserSerializer(source='author', read_only=True)

    class Meta:
        model = TicketComment
        fields = ['id', 'ticket', 'author', 'author_details', 'body', 'created_at']
        read_only_fields = ['id', 'created_at', 'author']

class TicketSerializer(serializers.ModelSerializer):
    created_by_details = UserSerializer(source='created_by', read_only=True)
    assigned_to_details = UserSerializer(source='assigned_to', read_only=True)
    comments = TicketCommentSerializer(many=True, read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id', 'title', 'description', 'status', 'priority', 
            'created_by', 'created_by_details', 'assigned_to', 
            'assigned_to_details', 'comments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']
