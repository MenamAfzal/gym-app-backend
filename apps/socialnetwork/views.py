# import mimetypes
# from itertools import chain
# from operator import attrgetter
#
# from django.contrib.auth import get_user_model
# from django.contrib.contenttypes.models import ContentType
# from django.db import transaction
# from django.db.models import Q
# from django.shortcuts import get_object_or_404
# from django.http import Http404
# from django.utils import timezone
#
# from rest_framework import generics, permissions, status, viewsets
# from rest_framework.decorators import action
# from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
# from rest_framework.permissions import AllowAny, IsAuthenticated
# from rest_framework.response import Response
# from rest_framework.views import APIView
# from rest_framework_simplejwt.tokens import AccessToken
# from .serializers import PollDetailSerializer
#
# from apps.socialnetwork.helper_functions import handle_file_response
# from apps.socialnetwork.models import Comment, Like, Photo, Poll, PollOption, Video, Vote
# from apps.socialnetwork.serializers import (
#     CommentSerializer,
#     MediaListSerializer,
#     PhotoDetailSerializer,
#     PhotoSerializer,
#     PollSerializer,
#     VideoDetailSerializer,
#     VideoSerializer,
#     VoteSerializer,
#     LikeSerializer,
#     PhotoUploadSerializer,
#     VideoUploadSerializer,
#     UnifiedMediaUploadSerializer,
# )
#
# User = get_user_model()
#
#
#
#
#
# # uploading Media
# class MultiMediaUploadAPIView(APIView):
#     """
#     A simple, unified API for uploading media files (photos and videos) and polls.
#     This endpoint handles multiple files of different types in a single request.
#     """
#     parser_classes = (MultiPartParser, FormParser, JSONParser)
#     permission_classes = [AllowAny]
#
#     @transaction.atomic
#     def post(self, request, *args, **kwargs):
#         # Handle poll creation if specified
#         media_type = request.data.get('media_type', '')
#         if media_type == 'poll':
#             return self._handle_poll_upload(request)
#
#         # Get the files from the request
#         files = request.FILES
#         if not files:
#             return Response({'error': 'No files uploaded'}, status=status.HTTP_400_BAD_REQUEST)
#
#         # Get the user or use a fallback for testing
#         user = self._get_authenticated_user(request)
#         if isinstance(user, Response):
#             return user
#
#         # Get common metadata for all uploads
#         metadata = {
#             'caption': request.data.get('caption', ''),
#             'location': request.data.get('location', ''),
#             'external_link': request.data.get('external_link', ''),
#             'internal_deep_link': request.data.get('internal_deep_link', ''),
#             'visible_to_staff': self._parse_boolean(request.data.get('visible_to_staff', 'true')),
#             'visible_to_clients': self._parse_boolean(request.data.get('visible_to_clients', 'true'))
#         }
#
#         # Process all files
#         successful_uploads = []
#         failed_uploads = []
#
#         for field_name, file_obj in files.items():
#             # Detect file type (image or video)
#             file_type = handle_file_response(file_obj)
#
#             try:
#                 if file_type == "Image":
#                     # Handle image upload - use PhotoUploadSerializer for file uploads
#                     serializer = PhotoUploadSerializer(data={'image': file_obj, **metadata})
#                     media_type = 'photo'
#                 elif file_type == "Video":
#                     # Handle video upload - use VideoUploadSerializer for file uploads
#                     serializer = VideoUploadSerializer(data={'video_file': file_obj, **metadata})
#                     media_type = 'video'
#                 else:
#                     failed_uploads.append({
#                         'file': field_name,
#                         'error': f"Unsupported file type: {file_type}"
#                     })
#                     continue
#
#                 # Validate and save
#                 if serializer.is_valid():
#                     media = serializer.save(user=user)
#
#                     # Use the appropriate read serializer to get the response data
#                     if media_type == 'photo':
#                         response_serializer = PhotoSerializer(media, context={'request': request})
#                     else:
#                         response_serializer = VideoSerializer(media, context={'request': request})
#
#                     upload_data = response_serializer.data
#                     upload_data['media_type'] = media_type
#                     successful_uploads.append(upload_data)
#                 else:
#                     failed_uploads.append({
#                         'file': field_name,
#                         'errors': serializer.errors
#                     })
#             except Exception as e:
#                 failed_uploads.append({
#                     'file': field_name,
#                     'error': str(e)
#                 })
#
#         # Return the results
#         if not successful_uploads:
#             return Response(
#                 {'error': 'No files were successfully uploaded', 'errors': failed_uploads},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         response_data = {
#             'message': f'Successfully uploaded {len(successful_uploads)} files',
#             'media': successful_uploads,
#         }
#
#         if failed_uploads:
#             response_data['errors'] = failed_uploads
#             return Response(response_data, status=status.HTTP_207_MULTI_STATUS)
#
#         return Response(response_data, status=status.HTTP_201_CREATED)
#
#     def _parse_boolean(self, value):
#         """Parse boolean values from request data"""
#         if isinstance(value, bool):
#             return value
#         return str(value).lower() == 'true'
#
#     def _get_authenticated_user(self, request):
#         """Get the authenticated user or a fallback for testing"""
#         user = request.user
#         if not user.is_authenticated:
#             try:
#                 # For testing purposes, use the first admin or active user
#                 user = User.objects.filter(is_staff=True, is_active=True).first() or User.objects.filter(is_active=True).first()
#
#                 if not user:
#                     return Response(
#                         {'error': 'Authentication required'},
#                         status=status.HTTP_401_UNAUTHORIZED
#                     )
#             except Exception as e:
#                 return Response(
#                     {'error': f'Authentication error: {str(e)}'},
#                     status=status.HTTP_500_INTERNAL_SERVER_ERROR
#                 )
#         return user
#
#     def _handle_poll_upload(self, request):
#         """Handle poll creation"""
#         try:
#             # Get the user
#             user = self._get_authenticated_user(request)
#             if isinstance(user, Response):
#                 return user
#
#             # Get poll data
#             question = request.data.get('question', '')
#             options_data = request.data.get('options', '')
#
#             # Validate required fields
#             if not question:
#                 return Response({'error': 'Question is required'}, status=status.HTTP_400_BAD_REQUEST)
#
#             # Parse options
#             try:
#                 if isinstance(options_data, str):
#                     import json
#                     options = json.loads(options_data)
#                 else:
#                     options = options_data
#
#                 if not options:
#                     return Response({'error': 'Options are required'}, status=status.HTTP_400_BAD_REQUEST)
#             except Exception as e:
#                 return Response({'error': f'Invalid options format: {str(e)}'},
#                                 status=status.HTTP_400_BAD_REQUEST)
#
#             # Create poll data
#             poll_data = {
#                 'question': question,
#                 'options': options,
#                 'is_multiple_choice': self._parse_boolean(request.data.get('is_multiple_choice', 'false')),
#                 'visible_to_staff': self._parse_boolean(request.data.get('visible_to_staff', 'true')),
#                 'visible_to_clients': self._parse_boolean(request.data.get('visible_to_clients', 'true')),
#                 'comments_enabled': self._parse_boolean(request.data.get('comments_enabled', 'true')),
#                 'external_link': request.data.get('external_link', ''),
#                 'internal_deep_link': request.data.get('internal_deep_link', '')
#             }
#
#             # Add end date if provided
#             end_date = request.data.get('end_date')
#             if end_date:
#                 poll_data['end_date'] = end_date
#
#             # Create the poll
#             serializer = PollSerializer(data=poll_data, context={'request': request})
#             if serializer.is_valid():
#                 poll = serializer.save(user=user)
#                 response_data = serializer.data
#                 response_data['media_type'] = 'poll'
#
#                 return Response({
#                     'message': 'Successfully created poll',
#                     'media': [response_data]  # Consistent format with media uploads
#                 }, status=status.HTTP_201_CREATED)
#             else:
#                 return Response({'error': 'Failed to create poll', 'details': serializer.errors},
#                                 status=status.HTTP_400_BAD_REQUEST)
#         except Exception as e:
#             return Response({'error': f'Error creating poll: {str(e)}'},
#                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
# class PollCreateAPIView(generics.CreateAPIView):
#     serializer_class = PollSerializer
#     permission_classes = [AllowAny]
#
#     def perform_create(self, serializer):
#         serializer.save(user=self.request.user)
#
# class PollAPIView(viewsets.ModelViewSet):
#
#     permission_classes = [AllowAny]
#
#     def get_queryset(self):
#         user = self.request.user
#
#         if user.user_type in [user.UserType.STAFF, user.UserType.ADMIN]:
#             return Poll.objects.all().order_by('-created_at')
#
#         return Poll.objects.filter(visible_to_clients=True).order_by('-created_at')
#
#     def get_serializer_class(self):
#         if self.action == 'retrieve':
#             return PollDetailSerializer
#         return PollSerializer
#
#     def perform_create(self, serializer):
#         serializer.save(user=self.request.user)
#
#     @action(detail=True, methods=['post'])
#     def vote(self, request, pk=None):
#         poll = self.get_object()
#         option_id = request.data.get('option_id')
#
#         if not option_id:
#             return Response(
#                 {'error': 'option_id is required'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         if poll.end_date and poll.end_date < timezone.now():
#             return Response(
#                 {'error': 'This poll has ended'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         try:
#             option = poll.options.get(id=option_id)
#         except PollOption.DoesNotExist:
#             return Response(
#                 {'error': 'Option not found for this poll'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         if not poll.is_multiple_choice and Vote.objects.filter(user=request.user, poll=poll).exists():
#             return Response(
#                 {'error': 'You have already voted on this poll'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         if Vote.objects.filter(user=request.user, poll=poll, option=option).exists():
#             return Response(
#                 {'error': 'You have already voted for this option'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         Vote.objects.create(user=request.user, poll=poll, option=option)
#
#         option.votes_count += 1
#         option.save()
#
#         return Response(
#             {'status': 'vote recorded successfully'},
#             status=status.HTTP_201_CREATED
#         )
#
#     @action(detail=True, methods=['post'])
#     def like(self, request, pk=None):
#         poll = self.get_object()
#         content_type = ContentType.objects.get_for_model(Poll)
#
#         like, created = Like.objects.get_or_create(
#             user=request.user,
#             content_type=content_type,
#             object_id=poll.id
#         )
#
#         if created:
#             poll.likes_count += 1
#             poll.save()
#             return Response({'status': 'poll liked'}, status=status.HTTP_201_CREATED)
#         else:
#             return Response({'status': 'already liked'}, status=status.HTTP_200_OK)
#
#     @action(detail=True, methods=['post'])
#     def unlike(self, request, pk=None):
#         poll = self.get_object()
#         content_type = ContentType.objects.get_for_model(Poll)
#
#         like = Like.objects.filter(
#             user=request.user,
#             content_type=content_type,
#             object_id=poll.id
#         ).first()
#
#         if like:
#             like.delete()
#             poll.likes_count = max(0, poll.likes_count - 1)
#             poll.save()
#             return Response({'status': 'poll unliked'}, status=status.HTTP_200_OK)
#         else:
#             return Response({'status': 'not liked yet'}, status=status.HTTP_400_BAD_REQUEST)
#
#     @action(detail=True, methods=['post'])
#     def comment(self, request, pk=None):
#         poll = self.get_object()
#         content_type = ContentType.objects.get_for_model(Poll)
#
#         serializer = CommentSerializer(data=request.data)
#         if serializer.is_valid():
#             parent_id = request.data.get('parent_id')
#             parent = None
#
#             if parent_id:
#                 parent = get_object_or_404(Comment, id=parent_id)
#                 if parent.object_id != poll.id or parent.content_type != content_type:
#                     return Response(
#                         {'error': 'Parent comment is not associated with this poll'},
#                         status=status.HTTP_400_BAD_REQUEST
#                     )
#
#             comment = Comment.objects.create(
#                 user=request.user,
#                 content=serializer.validated_data['content'],
#                 content_type=content_type,
#                 object_id=poll.id,
#                 parent=parent
#             )
#
#             if parent is None:
#                 poll.comments_count += 1
#                 poll.save()
#
#             return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)
#
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#
#
#
#
#
#
#
#
#
#
#
# class MediaViewSet(viewsets.ModelViewSet):
#     """
#     Media posts viewset for photos and videos.
#     """
#     parser_classes = (MultiPartParser, FormParser, JSONParser)
#     permission_classes = [AllowAny]  # TODO: Add proper permissions when auth is done
#
#     def get_queryset(self):
#         return Photo.objects.none()
#
#     def get_serializer_class(self):
#
#         if self.action == 'retrieve':
#             media_type = self.request.query_params.get('type')
#             if media_type == 'photo':
#                 return PhotoDetailSerializer
#             elif media_type == 'video':
#                 return VideoDetailSerializer
#         elif self.action == 'create':
#             media_type = self.request.data.get('media_type')
#             if media_type == 'photo':
#                 return PhotoSerializer
#             elif media_type == 'video':
#                 return VideoSerializer
#
#         return MediaListSerializer
#
#     def _get_media_object(self, pk, media_type):
#         if media_type == 'photo':
#             return get_object_or_404(Photo, pk=pk)
#         return get_object_or_404(Video, pk=pk)
#
#     def _get_media_serializer(self, media, media_type):
#         if media_type == 'photo':
#             if self.action == 'retrieve':
#                 return PhotoDetailSerializer(media)
#             return PhotoSerializer(media)
#         else:
#             if self.action == 'retrieve':
#                 return VideoDetailSerializer(media)
#             return VideoSerializer(media)
#
#     def list(self, request):
#
#         user_id = request.query_params.get('user_id')
#         media_type = request.query_params.get('type')
#
#         photos = []
#         videos = []
#
#         if not media_type or media_type == 'photo':
#             photo_qs = Photo.objects.all()
#             if user_id:
#                 photo_qs = photo_qs.filter(user_id=user_id)
#
#             p_data = PhotoSerializer(photo_qs.order_by('-created_at'), many=True).data
#             photos = [dict(item, **{'media_type': 'photo'}) for item in p_data]
#
#         if not media_type or media_type == 'video':
#             video_qs = Video.objects.all()
#             if user_id:
#                 video_qs = video_qs.filter(user_id=user_id)
#
#             v_data = VideoSerializer(video_qs.order_by('-created_at'), many=True).data
#             videos = [dict(item, **{'media_type': 'video'}) for item in v_data]
#
#         all_media = photos + videos
#         all_media.sort(key=lambda x: x['created_at'], reverse=True)
#
#         return Response(all_media)
#
#     def retrieve(self, request, pk=None):
#         media_type = request.query_params.get('type')
#
#         if not media_type or media_type not in ['photo', 'video']:
#             return Response(
#                 {'error': 'Missing or invalid type parameter'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#
#         try:
#             model_class = Photo if media_type == 'photo' else Video
#             media_obj = model_class.objects.get(pk=pk)
#
#             data = self.get_serializer(media_obj).data
#             return Response(data)
#         except (Photo.DoesNotExist, Video.DoesNotExist):
#             return Response(
#                 {'error': f"Couldn't find that {media_type}"},
#                 status=status.HTTP_404_NOT_FOUND
#             )
#
#     def create(self, request):
#         media_type = request.data.get('media_type')
#
#         if not media_type or media_type not in ['photo', 'video']:
#             return Response({'error': 'Missing or invalid media_type'}, status=status.HTTP_400_BAD_REQUEST)
#
#         # Fix for multiple files upload
#         if media_type == 'photo':
#             media_files = request.FILES.getlist('image')  # Changed from 'media' to 'image'
#         else:  # video
#             media_files = request.FILES.getlist('video_file')  # Changed from 'media' to 'video_file'
#
#         if not media_files:
#             return Response({'error': 'No media files provided'}, status=status.HTTP_400_BAD_REQUEST)
#
#         responses = []
#         for media_file in media_files:
#             data = request.data.copy()
#             if media_type == 'photo':
#                 data['image'] = media_file  # Use correct field name for photo
#             else:
#                 data['video_file'] = media_file  # Use correct field name for video
#
#             serializer_class = PhotoSerializer if media_type == 'photo' else VideoSerializer
#             serializer = serializer_class(data=data, context={'request': request})
#
#             if serializer.is_valid():
#                 new_media = serializer.save(user=request.user)
#                 result = serializer.data
#                 result['media_type'] = media_type
#                 responses.append(result)
#             else:
#                 return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#
#         return Response(responses, status=status.HTTP_201_CREATED)
#
#     def update(self, request, pk=None):
#         """Update an existing media item"""
#         # Figure out what we're updating
#         media_type = request.data.get('media_type')
#         if not media_type or media_type not in ['photo', 'video']:
#             return Response(
#                 {'error': 'Need to specify valid media_type'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         try:
#             media = self._get_media_object(pk, media_type)
#         except Http404:
#             return Response(
#                 {'error': f"{media_type} not found"},
#                 status=status.HTTP_404_NOT_FOUND
#             )
#
#         serializer = self.get_serializer(media, data=request.data, partial=True)
#
#         if serializer.is_valid():
#             serializer.save()
#
#             response_data = serializer.data
#             response_data['media_type'] = media_type
#             return Response(response_data)
#
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#
#     def destroy(self, request, pk=None):
#
#         media_type = request.query_params.get('type')
#         if not media_type or media_type not in ['photo', 'video']:
#             return Response(
#                 {'error': 'Need type parameter (photo/video)'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         try:
#             obj = self._get_media_object(pk, media_type)
#             obj.delete()
#             return Response(status=status.HTTP_204_NO_CONTENT)
#         except Http404:
#             return Response(
#                 {'error': f"{media_type} not found"},
#                 status=status.HTTP_404_NOT_FOUND
#             )
#
#     @action(detail=True, methods=['post'])
#     def like(self, request, pk=None):
#         return self._handle_like_action(request, pk, like=True)
#
#     @action(detail=True, methods=['post'])
#     def unlike(self, request, pk=None):
#         return self._handle_like_action(request, pk, like=False)
#
#     def _handle_like_action(self, request, pk, like=True):
#         media_type = request.query_params.get('type')
#         if not media_type or media_type not in ['photo', 'video']:
#             return Response(
#                 {'error': 'Missing type parameter'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         try:
#             media = self._get_media_object(pk, media_type)
#         except Http404:
#             return Response(
#                 {'error': f"{media_type} not found"},
#                 status=status.HTTP_404_NOT_FOUND
#             )
#
#         content_type = ContentType.objects.get_for_model(media.__class__)
#
#         if like:
#             like_exists = Like.objects.filter(
#                 user=request.user,
#                 content_type=content_type,
#                 object_id=media.id
#             ).exists()
#
#             if like_exists:
#                 return Response(
#                     {'status': 'already liked this'},
#                     status=status.HTTP_200_OK
#                 )
#
#             Like.objects.create(
#                 user=request.user,
#                 content_type=content_type,
#                 object_id=media.id
#             )
#
#             media.likes_count += 1
#             media.save(update_fields=['likes_count'])
#
#             return Response(
#                 {'status': 'liked!'},
#                 status=status.HTTP_201_CREATED
#             )
#         else:
#             like_obj = Like.objects.filter(
#                 user=request.user,
#                 content_type=content_type,
#                 object_id=media.id
#             ).first()
#
#             if not like_obj:
#                 return Response(
#                     {'status': "you haven't liked this yet"},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
#
#             like_obj.delete()
#             if media.likes_count > 0:
#                 media.likes_count -= 1
#                 media.save(update_fields=['likes_count'])
#
#             return Response(
#                 {'status': 'unliked'},
#                 status=status.HTTP_200_OK
#             )
#
#     @action(detail=True, methods=['post'])
#     def comment(self, request, pk=None):
#         media_type = request.query_params.get('type')
#         if not media_type or media_type not in ['photo', 'video']:
#             return Response(
#                 {'error': 'Need type parameter'},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         try:
#             media = self._get_media_object(pk, media_type)
#         except Http404:
#             return Response(
#                 {'error': f"couldn't find that {media_type}"},
#                 status=status.HTTP_404_NOT_FOUND
#             )
#
#         serializer = CommentSerializer(data=request.data)
#         if not serializer.is_valid():
#             return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#
#         content_type = ContentType.objects.get_for_model(media.__class__)
#
#         parent = None
#         parent_id = request.data.get('parent_id')
#
#         if parent_id:
#             try:
#                 parent = Comment.objects.get(id=parent_id)
#
#                 if parent.object_id != media.id or parent.content_type_id != content_type.id:
#                     return Response(
#                         {'error': "parent comment isn't on this media"},
#                         status=status.HTTP_400_BAD_REQUEST
#                     )
#             except Comment.DoesNotExist:
#                 return Response(
#                     {'error': "parent comment not found"},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
#
#         comment = Comment.objects.create(
#             user=request.user,
#             content=serializer.validated_data['content'],
#             content_type=content_type,
#             object_id=media.id,
#             parent=parent
#         )
#
#         if not parent:
#             media.comments_count += 1
#             media.save(update_fields=['comments_count'])
#
#         return Response(
#             CommentSerializer(comment).data,
#             status=status.HTTP_201_CREATED
#         )
#
# # Unified Feed API View that combines photos, videos, and polls
# class UnifiedFeedAPIView(APIView):
#     permission_classes = [AllowAny]
#
#     def get(self, request, *args, **kwargs):
#         user_id = request.query_params.get('user_id')
#         content_type = request.query_params.get('type')  # Can filter by 'photo', 'video', 'poll', or None for all
#
#         # Get photos if requested or if no specific type filter
#         photos = []
#         videos = []
#         polls = []
#
#         if not content_type or content_type == 'photo':
#             photo_qs = Photo.objects.all()
#             if user_id:
#                 photo_qs = photo_qs.filter(user_id=user_id)
#
#             p_data = PhotoSerializer(photo_qs.order_by('-created_at'), many=True, context={'request': request}).data
#             photos = [dict(item, **{'media_type': 'photo'}) for item in p_data]
#
#         if not content_type or content_type == 'video':
#             video_qs = Video.objects.all()
#             if user_id:
#                 video_qs = video_qs.filter(user_id=user_id)
#
#             v_data = VideoSerializer(video_qs.order_by('-created_at'), many=True, context={'request': request}).data
#             videos = [dict(item, **{'media_type': 'video'}) for item in v_data]
#
#         if not content_type or content_type == 'poll':
#             poll_qs = Poll.objects.all()
#             # Filter by user if requested
#             if user_id:
#                 poll_qs = poll_qs.filter(user_id=user_id)
#
#             # Apply visibility rules based on user type
#             if request.user.is_authenticated:
#                 if request.user.user_type in [request.user.UserType.STAFF, request.user.UserType.ADMIN]:
#                     # Staff and admin can see all polls
#                     pass
#                 else:
#                     # Regular users only see client-visible polls
#                     poll_qs = poll_qs.filter(visible_to_clients=True)
#             else:
#                 # Unauthenticated users only see client-visible polls
#                 poll_qs = poll_qs.filter(visible_to_clients=True)
#
#             # Sort polls by creation date
#             poll_qs = poll_qs.order_by('-created_at')
#
#             p_data = PollSerializer(poll_qs, many=True, context={'request': request}).data
#             polls = [dict(item, **{'media_type': 'poll'}) for item in p_data]
#
#         # Combine all content and sort by creation date (newest first)
#         all_content = photos + videos + polls
#         all_content.sort(key=lambda x: x.get('created_at', ''), reverse=True)
#
#         return Response(all_content)
#
# # New unified media upload view
# class UnifiedMediaUploadAPIView(APIView):
#     """
#     A simplified API for uploading multiple media files with a single 'files' parameter.
#     This makes the API more intuitive and consistent with web standards.
#     """
#     parser_classes = (MultiPartParser, FormParser, JSONParser)
#     permission_classes = [AllowAny]
#
#     @transaction.atomic
#     def post(self, request, *args, **kwargs):
#         # Handle poll creation if specified
#         media_type = request.data.get('media_type', '')
#         if media_type == 'poll':
#             return self._handle_poll_upload(request)
#
#         # Validate the data
#         serializer = UnifiedMediaUploadSerializer(data=request.data)
#         if not serializer.is_valid():
#             return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#
#         # Get the files list from the request
#         files = request.FILES.getlist('files')
#         if not files:
#             return Response({'error': 'No files uploaded'}, status=status.HTTP_400_BAD_REQUEST)
#
#         # Get the user or use a fallback for testing
#         user = self._get_authenticated_user(request)
#         if isinstance(user, Response):
#             return user
#
#         # Get common metadata for all uploads
#         metadata = {
#             'caption': request.data.get('caption', ''),
#             'location': request.data.get('location', ''),
#             'external_link': request.data.get('external_link', ''),
#             'internal_deep_link': request.data.get('internal_deep_link', ''),
#             'visible_to_staff': self._parse_boolean(request.data.get('visible_to_staff', 'true')),
#             'visible_to_clients': self._parse_boolean(request.data.get('visible_to_clients', 'true'))
#         }
#
#         # Process all files
#         successful_uploads = []
#         failed_uploads = []
#
#         for file_obj in files:
#             # Detect file type (image or video)
#             file_type = handle_file_response(file_obj)
#
#             try:
#                 if file_type == "Image":
#                     # Handle image upload - use PhotoUploadSerializer for file uploads
#                     serializer = PhotoUploadSerializer(data={'image': file_obj, **metadata})
#                     media_type = 'photo'
#                 elif file_type == "Video":
#                     # Handle video upload - use VideoUploadSerializer for file uploads
#                     serializer = VideoUploadSerializer(data={'video_file': file_obj, **metadata})
#                     media_type = 'video'
#                 else:
#                     failed_uploads.append({
#                         'file': file_obj.name,
#                         'error': f"Unsupported file type: {file_type}"
#                     })
#                     continue
#
#                 # Validate and save
#                 if serializer.is_valid():
#                     media = serializer.save(user=user)
#
#                     # Use the appropriate read serializer to get the response data
#                     if media_type == 'photo':
#                         response_serializer = PhotoSerializer(media, context={'request': request})
#                     else:
#                         response_serializer = VideoSerializer(media, context={'request': request})
#
#                     upload_data = response_serializer.data
#                     upload_data['media_type'] = media_type
#                     successful_uploads.append(upload_data)
#                 else:
#                     failed_uploads.append({
#                         'file': file_obj.name,
#                         'errors': serializer.errors
#                     })
#             except Exception as e:
#                 failed_uploads.append({
#                     'file': file_obj.name,
#                     'error': str(e)
#                 })
#
#         # Return the results
#         if not successful_uploads:
#             return Response(
#                 {'error': 'No files were successfully uploaded', 'errors': failed_uploads},
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#
#         response_data = {
#             'message': f'Successfully uploaded {len(successful_uploads)} files',
#             'media': successful_uploads,
#         }
#
#         if failed_uploads:
#             response_data['errors'] = failed_uploads
#             return Response(response_data, status=status.HTTP_207_MULTI_STATUS)
#
#         return Response(response_data, status=status.HTTP_201_CREATED)
#
#     def _parse_boolean(self, value):
#         """Parse boolean values from request data"""
#         if isinstance(value, bool):
#             return value
#         return str(value).lower() == 'true'
#
#     def _get_authenticated_user(self, request):
#         """Get the authenticated user or a fallback for testing"""
#         user = request.user
#         if not user.is_authenticated:
#             try:
#                 # For testing purposes, use the first admin or active user
#                 user = User.objects.filter(is_staff=True, is_active=True).first() or User.objects.filter(is_active=True).first()
#
#                 if not user:
#                     return Response(
#                         {'error': 'Authentication required'},
#                         status=status.HTTP_401_UNAUTHORIZED
#                     )
#             except Exception as e:
#                 return Response(
#                     {'error': f'Authentication error: {str(e)}'},
#                     status=status.HTTP_500_INTERNAL_SERVER_ERROR
#                 )
#         return user
#
#     def _handle_poll_upload(self, request):
#         """Handle poll creation"""
#         # Reuse the poll handling code from MultiMediaUploadAPIView
#         try:
#             # Get the user
#             user = self._get_authenticated_user(request)
#             if isinstance(user, Response):
#                 return user
#
#             # Get poll data
#             question = request.data.get('question', '')
#             options_data = request.data.get('options', '')
#
#             # Validate required fields
#             if not question:
#                 return Response({'error': 'Question is required'}, status=status.HTTP_400_BAD_REQUEST)
#
#             # Parse options
#             try:
#                 if isinstance(options_data, str):
#                     import json
#                     options = json.loads(options_data)
#                 else:
#                     options = options_data
#
#                 if not options:
#                     return Response({'error': 'Options are required'}, status=status.HTTP_400_BAD_REQUEST)
#             except Exception as e:
#                 return Response({'error': f'Invalid options format: {str(e)}'},
#                                 status=status.HTTP_400_BAD_REQUEST)
#
#             # Create poll data
#             poll_data = {
#                 'question': question,
#                 'options': options,
#                 'is_multiple_choice': self._parse_boolean(request.data.get('is_multiple_choice', 'false')),
#                 'visible_to_staff': self._parse_boolean(request.data.get('visible_to_staff', 'true')),
#                 'visible_to_clients': self._parse_boolean(request.data.get('visible_to_clients', 'true')),
#                 'comments_enabled': self._parse_boolean(request.data.get('comments_enabled', 'true')),
#                 'external_link': request.data.get('external_link', ''),
#                 'internal_deep_link': request.data.get('internal_deep_link', '')
#             }
#
#             # Add end date if provided
#             end_date = request.data.get('end_date')
#             if end_date:
#                 poll_data['end_date'] = end_date
#
#             # Create the poll
#             serializer = PollSerializer(data=poll_data, context={'request': request})
#             if serializer.is_valid():
#                 poll = serializer.save(user=user)
#                 response_data = serializer.data
#                 response_data['media_type'] = 'poll'
#
#                 return Response({
#                     'message': 'Successfully created poll',
#                     'media': [response_data]  # Consistent format with media uploads
#                 }, status=status.HTTP_201_CREATED)
#             else:
#                 return Response({'error': 'Failed to create poll', 'details': serializer.errors},
#                                 status=status.HTTP_400_BAD_REQUEST)
#         except Exception as e:
#             return Response({'error': f'Error creating poll: {str(e)}'},
#                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)
import mimetypes
from collections import defaultdict
from itertools import chain
from operator import attrgetter

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from rest_framework import generics, permissions, status, viewsets, mixins
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken
from .serializers import PollDetailSerializer, UserMinimalSerializer
from .models import CommentReaction
from .permissions import is_admin_user, IsOwnerOrAdmin, IsAdminOrModerator
from apps.socialnetwork.helper_functions import handle_file_response
from apps.socialnetwork.models import (
    Comment, Like, Photo, Poll, PollOption, Video, Vote, Post, SocialPost, PostMedia,
    UserBlock, PostReport
)
from apps.socialnetwork.serializers import (
    CommentSerializer,
    MediaListSerializer,
    PhotoDetailSerializer,
    PhotoSerializer,
    PollSerializer,
    VideoDetailSerializer,
    VideoSerializer,
    VoteSerializer,
    LikeSerializer,
    PhotoUploadSerializer,
    VideoUploadSerializer,
    UnifiedMediaUploadSerializer,
    SocialPostSerializer,
    SocialPostDetailSerializer,
    PostMediaSerializer,
    UserBlockSerializer,
    AdminUserBlockSerializer,
    PostReportSerializer,
    PostReportCreateSerializer,
    PostReportUpdateSerializer,
    AdminPostReportSerializer,
    AdminReportActionSerializer,
)

import logging

logger = logging.getLogger(__name__)

User = get_user_model()


# Utility function for standardized error responses
def format_error_response(message, details=None, status_code=status.HTTP_400_BAD_REQUEST):
    response = {'error': message}
    if details:
        response['details'] = details
    return Response(response, status=status_code)


# Utility function for poll creation
def handle_poll_upload(request, user):
    try:
        question = request.data.get('question', '')
        options_data = request.data.get('options', '')

        if not question:
            return format_error_response('Question is required')

        try:
            if isinstance(options_data, str):
                import json
                options = json.loads(options_data)
            else:
                options = options_data
            if not options:
                return format_error_response('Options are required')
        except Exception as e:
            logger.error(f"Invalid options format: {str(e)}", exc_info=True)
            return format_error_response(f'Invalid options format: {str(e)}')

        poll_data = {
            'question': question,
            'options': options,
            'is_multiple_choice': str(request.data.get('is_multiple_choice', 'false')).lower() == 'true',
            'visible_to_staff': str(request.data.get('visible_to_staff', 'true')).lower() == 'true',
            'visible_to_clients': str(request.data.get('visible_to_clients', 'true')).lower() == 'true',
            'comments_enabled': str(request.data.get('comments_enabled', 'true')).lower() == 'true',
            'external_link': request.data.get('external_link', ''),
            'internal_deep_link': request.data.get('internal_deep_link', '')
        }

        end_date = request.data.get('end_date')
        if end_date:
            poll_data['end_date'] = end_date

        serializer = PollSerializer(data=poll_data, context={'request': request})
        if serializer.is_valid():
            poll = serializer.save(user=user)
            response_data = serializer.data
            response_data['media_type'] = 'poll'
            return Response({
                'message': 'Successfully created poll',
                'media': [response_data]
            }, status=status.HTTP_201_CREATED)
        else:
            return format_error_response('Failed to create poll', serializer.errors)
    except Exception as e:
        logger.error(f"Error creating poll: {str(e)}", exc_info=True)
        return format_error_response(f'Error creating poll: {str(e)}',
                                     status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _parse_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    val_str = str(value).strip().lower()
    if val_str in ['true', '1', 'yes']:
        return True
    if val_str in ['false', '0', 'no']:
        return False
    return default


def create_social_post(request, user):
    caption = (
        request.data.get('caption')
        or request.data.get('content')
        or request.data.get('text')
        or request.data.get('description')
        or ''
    )
    if isinstance(caption, str):
        caption = caption.strip()
    else:
        caption = str(caption)

    files_to_process = []
    for key in ['files', 'media', 'images', 'videos', 'image', 'video_file', 'file']:
        if key in request.FILES:
            for f in request.FILES.getlist(key):
                files_to_process.append(f)
    for key, f_list in request.FILES.lists():
        if key not in ['files', 'media', 'images', 'videos', 'image', 'video_file', 'file']:
            for f in f_list:
                files_to_process.append(f)

    if not caption and not files_to_process:
        return format_error_response('Post must contain text or at least one media file')

    tenant = getattr(user, 'tenant', None)
    if not tenant:
        from apps.core.tenants.context import get_current_tenant
        tenant = get_current_tenant()

    post_kwargs = {
        'user': user,
        'caption': caption,
        'location': request.data.get('location') or None,
        'external_link': request.data.get('external_link') or None,
        'internal_deep_link': request.data.get('internal_deep_link') or None,
        'visible_to_staff': _parse_bool(request.data.get('visible_to_staff'), True),
        'visible_to_clients': _parse_bool(request.data.get('visible_to_clients'), True),
        'comments_enabled': _parse_bool(request.data.get('comments_enabled'), True),
    }
    if tenant:
        post_kwargs['tenant'] = tenant

    post = SocialPost.objects.create(**post_kwargs)

    for idx, file_obj in enumerate(files_to_process):
        file_type = handle_file_response(file_obj)
        if file_type == 'Image':
            m_type = 'image'
        elif file_type == 'Video':
            m_type = 'video'
        else:
            ct = getattr(file_obj, 'content_type', '') or ''
            if ct.startswith('video/'):
                m_type = 'video'
            else:
                m_type = 'image'

        media_kwargs = {
            'post': post,
            'file': file_obj,
            'media_type': m_type,
            'order': idx,
        }
        if tenant:
            media_kwargs['tenant'] = tenant
        PostMedia.objects.create(**media_kwargs)

    try:
        from apps.rewards.events import RewardEvent
        from apps.rewards.services import RewardEngineService
        tenant_id = getattr(user, 'tenant_id', None)
        if tenant_id:
            RewardEngineService.handle_event(RewardEvent.create_social_post_created(
                tenant_id=tenant_id,
                user_id=user.id,
                post_id=post.id
            ))
    except Exception:
        pass

    serializer = SocialPostSerializer(post, context={'request': request})
    post_data = serializer.data
    response_data = {
        'message': 'Successfully created post',
        'post': post_data,
        'media': post_data.get('media', []),
        **post_data
    }
    return Response(response_data, status=status.HTTP_201_CREATED)


# Signals for updating likes and comments counts
@receiver(post_save, sender=Like)
def update_likes_count_on_create(sender, instance, created, **kwargs):
    if created:
        content_object = instance.content_object
        content_object.likes_count += 1
        content_object.save(update_fields=['likes_count'])


@receiver(post_delete, sender=Like)
def update_likes_count_on_delete(sender, instance, **kwargs):
    content_object = instance.content_object
    if content_object is not None and hasattr(content_object, 'likes_count'):
        content_object.likes_count = max(0, content_object.likes_count - 1)
        content_object.save(update_fields=['likes_count'])


@receiver(post_save, sender=Comment)
def update_comments_count_on_create(sender, instance, created, **kwargs):
    if created:
        content_object = instance.content_object
        content_object.comments_count += 1
        content_object.save(update_fields=['comments_count'])


@receiver(post_delete, sender=Comment)
def update_comments_count_on_delete(sender, instance, **kwargs):  
        content_object = instance.content_object
        if content_object is not None and hasattr(content_object, 'comments_count'):
            content_object.comments_count = max(0, content_object.comments_count - 1)
            content_object.save(update_fields=['comments_count'])



# Uploading Media
class MultiMediaUploadAPIView(APIView):
    """
    A simple, unified API for uploading media files (photos and videos) and polls.
    This endpoint handles multiple files of different types in a single request.
    """
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        media_type = request.data.get('media_type', '')
        if media_type == 'poll':
            return self._handle_poll_upload(request)

        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user

        if media_type == 'photo' and 'image' in request.FILES:
            return self._handle_legacy_photo_upload(request, user)
        if media_type == 'video' and 'video_file' in request.FILES:
            return self._handle_legacy_video_upload(request, user)

        return create_social_post(request, user)

    def _handle_legacy_photo_upload(self, request, user):
        metadata = {
            'caption': request.data.get('caption', ''),
            'location': request.data.get('location', ''),
            'external_link': request.data.get('external_link', ''),
            'internal_deep_link': request.data.get('internal_deep_link', ''),
            'visible_to_staff': self._parse_boolean(request.data.get('visible_to_staff', 'true')),
            'visible_to_clients': self._parse_boolean(request.data.get('visible_to_clients', 'true'))
        }
        successful_uploads = []
        failed_uploads = []
        for field_name, file_obj in request.FILES.items():
            try:
                serializer = PhotoUploadSerializer(data={'image': file_obj, **metadata})
                if serializer.is_valid():
                    media = serializer.save(user=user)
                    response_serializer = PhotoSerializer(media, context={'request': request})
                    upload_data = response_serializer.data
                    upload_data['media_type'] = 'photo'
                    successful_uploads.append(upload_data)
                else:
                    failed_uploads.append({'file': field_name, 'errors': serializer.errors})
            except Exception as e:
                failed_uploads.append({'file': field_name, 'error': str(e)})
        if not successful_uploads:
            return format_error_response('No files were successfully uploaded', failed_uploads)
        response_data = {
            'message': f'Successfully uploaded {len(successful_uploads)} files',
            'media': successful_uploads,
        }
        if failed_uploads:
            response_data['errors'] = failed_uploads
            return Response(response_data, status=status.HTTP_207_MULTI_STATUS)
        return Response(response_data, status=status.HTTP_201_CREATED)

    def _handle_legacy_video_upload(self, request, user):
        metadata = {
            'caption': request.data.get('caption', ''),
            'location': request.data.get('location', ''),
            'external_link': request.data.get('external_link', ''),
            'internal_deep_link': request.data.get('internal_deep_link', ''),
            'visible_to_staff': self._parse_boolean(request.data.get('visible_to_staff', 'true')),
            'visible_to_clients': self._parse_boolean(request.data.get('visible_to_clients', 'true'))
        }
        successful_uploads = []
        failed_uploads = []
        for field_name, file_obj in request.FILES.items():
            try:
                serializer = VideoUploadSerializer(data={'video_file': file_obj, **metadata})
                if serializer.is_valid():
                    media = serializer.save(user=user)
                    response_serializer = VideoSerializer(media, context={'request': request})
                    upload_data = response_serializer.data
                    upload_data['media_type'] = 'video'
                    successful_uploads.append(upload_data)
                else:
                    failed_uploads.append({'file': field_name, 'errors': serializer.errors})
            except Exception as e:
                failed_uploads.append({'file': field_name, 'error': str(e)})
        if not successful_uploads:
            return format_error_response('No files were successfully uploaded', failed_uploads)
        response_data = {
            'message': f'Successfully uploaded {len(successful_uploads)} files',
            'media': successful_uploads,
        }
        if failed_uploads:
            response_data['errors'] = failed_uploads
            return Response(response_data, status=status.HTTP_207_MULTI_STATUS)
        return Response(response_data, status=status.HTTP_201_CREATED)

    def _parse_boolean(self, value):
        if isinstance(value, bool):
            return value
        return str(value).lower() == 'true'

    def _get_authenticated_user(self, request):
        user = request.user
        if not user.is_authenticated:
            return format_error_response(
                'Authentication required',
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        return user

    def _handle_poll_upload(self, request):
        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user
        return handle_poll_upload(request, user)


class PollCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user
        if not user or not user.is_authenticated:
            return format_error_response(
                'Authentication required',
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        return handle_poll_upload(request, user)


class PollAPIView(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        from apps.users.models import UserRole
        qs = Poll.objects.all()
        if user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(user)
            if blocked_ids:
                qs = qs.exclude(user_id__in=blocked_ids)
            if user.is_staff or is_admin_user(user) or user.role != UserRole.CLIENT:
                return qs.order_by('-created_at')
            return qs.filter(Q(visible_to_clients=True) | Q(user=user)).order_by('-created_at')
        return qs.filter(visible_to_clients=True).order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PollDetailSerializer
        return PollSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        poll = self.get_object()
        if poll.user_id != request.user.id and not is_admin_user(request.user):
            return Response(
                {"error": "You do not have permission to delete this poll."},
                status=status.HTTP_403_FORBIDDEN
            )
        self.perform_destroy(poll)
        return Response(
            {"message": "Poll deleted successfully."},
            status=status.HTTP_204_NO_CONTENT
        )

    def update(self, request, *args, **kwargs):
        poll = self.get_object() 
        if poll.user_id != request.user.id and not is_admin_user(request.user):
            return Response(
                {"error": "You do not have permission to edit this poll."},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def vote(self, request, pk=None):
        poll = self.get_object()
        option_id = request.data.get('option_id')

        if not option_id:
            return format_error_response('option_id is required')

        if poll.end_date and poll.end_date < timezone.now():
            return format_error_response('This poll has ended')

        try:
            option = poll.options.get(id=option_id)
        except PollOption.DoesNotExist:
            return format_error_response('Option not found for this poll')

        if not poll.is_multiple_choice and Vote.objects.filter(user=request.user, poll=poll).exists():
            return format_error_response('You have already voted on this poll')

        if Vote.objects.filter(user=request.user, poll=poll, option=option).exists():
            return format_error_response('You have already voted for this option')

        Vote.objects.create(user=request.user, poll=poll, option=option)
        option.votes_count += 1
        option.save()

        return Response({'status': 'vote recorded successfully', 'option_id:': option_id}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        poll = self.get_object()
        content_type = ContentType.objects.get_for_model(Poll)

        like, created = Like.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=poll.id
        )

        if created:
            return Response({'status': 'poll liked'}, status=status.HTTP_201_CREATED)
        return Response({'status': 'already liked'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def unlike(self, request, pk=None):
        poll = self.get_object()
        content_type = ContentType.objects.get_for_model(Poll)

        like = Like.objects.filter(
            user=request.user,
            content_type=content_type,
            object_id=poll.id
        ).first()

        if like:
            like.delete()
            return Response({'status': 'poll unliked'}, status=status.HTTP_200_OK)
        return format_error_response('not liked yet')

    @action(detail=True, methods=['post'])
    def comment(self, request, pk=None):
        poll = self.get_object()
        content_type = ContentType.objects.get_for_model(Poll)

        serializer = CommentSerializer(data=request.data)
        if serializer.is_valid():
            parent_id = request.data.get('parent_id')
            parent = None

            if parent_id:
                parent = get_object_or_404(Comment, id=parent_id)
                if parent.object_id != poll.id or parent.content_type != content_type:
                    return format_error_response('Parent comment is not associated with this poll')

            comment = Comment.objects.create(
                user=request.user,
                content=serializer.validated_data['content'],
                content_type=content_type,
                object_id=poll.id,
                parent=parent
            )

            return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)

        return format_error_response('Invalid comment data', serializer.errors)


class MediaViewSet(viewsets.ModelViewSet):
    """
    Media posts viewset for photos and videos.
    """
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    permission_classes = [AllowAny]

    media_types = {
        'photo': {'model': Photo, 'serializer': PhotoSerializer, 'detail_serializer': PhotoDetailSerializer},
        'video': {'model': Video, 'serializer': VideoSerializer, 'detail_serializer': VideoDetailSerializer},
        'poll': {'model': Poll, 'serializer': PollSerializer, 'detail_serializer': PollDetailSerializer},
        'post': {'model': SocialPost, 'serializer': SocialPostSerializer, 'detail_serializer': SocialPostDetailSerializer},
        'socialpost': {'model': SocialPost, 'serializer': SocialPostSerializer, 'detail_serializer': SocialPostDetailSerializer},
        'text': {'model': SocialPost, 'serializer': SocialPostSerializer, 'detail_serializer': SocialPostDetailSerializer},
        'mixed': {'model': SocialPost, 'serializer': SocialPostSerializer, 'detail_serializer': SocialPostDetailSerializer},
    }

    def _find_media_object(self, pk, media_type=None):
        def _check_not_blocked(item):
            if not item:
                return None
            request = getattr(self, 'request', None)
            if request and request.user.is_authenticated:
                blocked_ids = UserBlock.get_blocked_user_ids_for(request.user)
                if getattr(item, 'user_id', None) in blocked_ids:
                    return None
            return item

        if media_type and media_type in self.media_types:
            model = self.media_types[media_type]['model']
            obj = model.objects.filter(pk=pk).first()
            obj = _check_not_blocked(obj)
            if obj:
                return obj, media_type

        social_post = _check_not_blocked(SocialPost.objects.filter(pk=pk).first())
        if social_post:
            return social_post, 'post'

        photo = _check_not_blocked(Photo.objects.filter(pk=pk).first())
        if photo:
            return photo, 'photo'

        video = _check_not_blocked(Video.objects.filter(pk=pk).first())
        if video:
            return video, 'video'

        poll = _check_not_blocked(Poll.objects.filter(pk=pk).first())
        if poll:
            return poll, 'poll'

        post_media = PostMedia.objects.filter(pk=pk).select_related('post').first()
        if post_media and post_media.post:
            post_obj = _check_not_blocked(post_media.post)
            if post_obj:
                return post_obj, 'post'

        return None, None

    def get_queryset(self):
        media_type = self.request.query_params.get('type') or self.request.data.get('media_type')
        if media_type in self.media_types:
            qs = self.media_types[media_type]['model'].objects.all()
            if self.request.user.is_authenticated:
                blocked_ids = UserBlock.get_blocked_user_ids_for(self.request.user)
                if blocked_ids:
                    qs = qs.exclude(user_id__in=blocked_ids)
            return qs
        return Photo.objects.none()

    def get_serializer_class(self):
        media_type = self.request.query_params.get('type') or self.request.data.get('media_type')
        if media_type in self.media_types:
            if self.action == 'retrieve':
                return self.media_types[media_type]['detail_serializer']
            return self.media_types[media_type]['serializer']
        return MediaListSerializer

    def list(self, request):
        user_id = request.query_params.get('user_id')
        media_type = request.query_params.get('type')

        all_media = []
        seen_models = set()
        for m_type, config in self.media_types.items():
            if media_type and m_type != media_type:
                continue
            if not media_type and config['model'] in seen_models:
                continue
            seen_models.add(config['model'])
            qs = config['model'].objects.all()
            if user_id:
                qs = qs.filter(user_id=user_id)
            data = config['serializer'](qs.order_by('-created_at'), many=True, context={'request': request}).data
            all_media.extend([dict(item, **{'media_type': m_type}) for item in data])

        all_media.sort(key=lambda x: x['created_at'], reverse=True)
        return Response(all_media)

    def retrieve(self, request, pk=None):
        media_type = request.query_params.get('type') or request.data.get('media_type')
        media_obj, resolved_type = self._find_media_object(pk, media_type)
        if not media_obj:
            return format_error_response(f"Couldn't find that {media_type or 'media'}", status_code=status.HTTP_404_NOT_FOUND)

        config = self.media_types.get(resolved_type)
        serializer_class = config['detail_serializer'] if config else None
        if not serializer_class:
            if isinstance(media_obj, SocialPost):
                serializer_class = SocialPostDetailSerializer
            elif isinstance(media_obj, Photo):
                serializer_class = PhotoDetailSerializer
            elif isinstance(media_obj, Video):
                serializer_class = VideoDetailSerializer
            elif isinstance(media_obj, Poll):
                serializer_class = PollDetailSerializer
            else:
                serializer_class = MediaListSerializer

        serializer = serializer_class(media_obj, context={'request': request})
        return Response(serializer.data)

    def create(self, request):
        media_type = request.data.get('media_type')
        if media_type in ['post', 'socialpost', 'text', 'mixed']:
            return create_social_post(request, request.user)
        if not media_type or media_type not in self.media_types:
            return format_error_response('Missing or invalid media_type')

        media_files = request.FILES.getlist('image' if media_type == 'photo' else 'video_file')
        if not media_files:
            return format_error_response('No media files provided')

        responses = []
        for media_file in media_files:
            data = request.data.copy()
            data['image' if media_type == 'photo' else 'video_file'] = media_file
            serializer_class = self.media_types[media_type]['serializer']
            serializer = serializer_class(data=data, context={'request': request})

            if serializer.is_valid():
                new_media = serializer.save(user=request.user)
 
                try:
                    from apps.rewards.events import RewardEvent
                    from apps.rewards.services import RewardEngineService
                    tenant_id = getattr(request.user, 'tenant_id', None)
                    if tenant_id:
                        RewardEngineService.handle_event(RewardEvent.create_social_post_created(
                            tenant_id=tenant_id,
                            user_id=request.user.id,
                            post_id=new_media.id
                        ))
                except Exception:
                    pass

                result = serializer.data
                result['media_type'] = media_type
                responses.append(result)
            else:
                return format_error_response('Invalid media data', serializer.errors)

        return Response(responses, status=status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        media_type = request.data.get('media_type') or request.query_params.get('type')
        media, resolved_type = self._find_media_object(pk, media_type)
        if not media:
            return format_error_response(f"{media_type or 'media'} not found", status_code=status.HTTP_404_NOT_FOUND)

        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user

        if media.user_id != user.id and not is_admin_user(user):
            return format_error_response(
                f"You do not have permission to edit this {resolved_type or 'media'}.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        config = self.media_types.get(resolved_type)
        serializer_class = config['serializer'] if config else None
        if not serializer_class:
            if isinstance(media, SocialPost):
                serializer_class = SocialPostSerializer
            elif isinstance(media, Photo):
                serializer_class = PhotoSerializer
            elif isinstance(media, Video):
                serializer_class = VideoSerializer
            elif isinstance(media, Poll):
                serializer_class = PollSerializer

        serializer = serializer_class(media, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            response_data = serializer.data
            response_data['media_type'] = resolved_type
            return Response(response_data)
        return format_error_response('Invalid update data', serializer.errors)

    def destroy(self, request, pk=None):
        media_type = request.query_params.get('type') or request.data.get('media_type')
        media, resolved_type = self._find_media_object(pk, media_type)
        if not media:
            return format_error_response(f"{media_type or 'media'} not found", status_code=status.HTTP_404_NOT_FOUND)

        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user

        if media.user_id != user.id and not is_admin_user(user):
            return format_error_response(
                f"You do not have permission to delete this {resolved_type or 'media'}.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        media.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_authenticated_user(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return format_error_response(
                'Authentication required',
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        return user

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        media_type = request.query_params.get('type') or request.data.get('media_type')
        media, resolved_type = self._find_media_object(pk, media_type)
        if not media:
            return format_error_response(f"{media_type or 'media'} not found", status_code=status.HTTP_404_NOT_FOUND)

        content_type = ContentType.objects.get_for_model(media.__class__)
        like, created = Like.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=media.id
        )

        if created:
            try:
                from apps.rewards.events import RewardEvent
                from apps.rewards.services import RewardEngineService
                tenant_id = getattr(request.user, 'tenant_id', None)
                if tenant_id:
                    RewardEngineService.handle_event(RewardEvent.create_social_like_created(
                        tenant_id=tenant_id,
                        user_id=request.user.id,
                        like_id=like.id,
                        media_id=media.id
                    ))
            except Exception:
                pass

            return Response({'status': 'liked!'}, status=status.HTTP_201_CREATED)
        return Response({'status': 'already liked'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], permission_classes=[AllowAny])
    def comments(self, request, pk=None):
        media_type = request.query_params.get('type') or request.data.get('media_type')
        media, resolved_type = self._find_media_object(pk, media_type)
        if not media:
            return format_error_response(f"{media_type or 'media'} not found", status_code=status.HTTP_404_NOT_FOUND)

        ct = ContentType.objects.get_for_model(media.__class__)
        top_comments = Comment.objects.filter(
            content_type=ct,
            object_id=media.id,
            parent=None
        ).select_related('user__profile').prefetch_related('replies__user__profile')
        results = []
        for c in top_comments:
            comment_data = CommentSerializer(c, context={'request': request}).data
            user_data = UserMinimalSerializer(c.user, context={'request': request}).data
            results.append({
                'comment': comment_data,
                'user': user_data
            })
        return Response(results)

    @action(detail=True, methods=['post'])
    def unlike(self, request, pk=None):
        media_type = request.query_params.get('type') or request.data.get('media_type')
        media, resolved_type = self._find_media_object(pk, media_type)
        if not media:
            return format_error_response(f"{media_type or 'media'} not found", status_code=status.HTTP_404_NOT_FOUND)

        content_type = ContentType.objects.get_for_model(media.__class__)
        like = Like.objects.filter(
            user=request.user,
            content_type=content_type,
            object_id=media.id
        ).first()

        if like:
            like.delete()
            return Response({'status': 'unliked'}, status=status.HTTP_200_OK)
        return format_error_response("you haven't liked this yet")

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def comment(self, request, pk=None):
        media_type = request.query_params.get('type') or request.data.get('media_type')
        media, resolved_type = self._find_media_object(pk, media_type)
        if not media:
            return format_error_response(f"couldn't find that {media_type or 'media'}", status_code=status.HTTP_404_NOT_FOUND)

        if hasattr(media, 'comments_enabled') and not media.comments_enabled:
            return format_error_response('Comments are disabled for this post', status_code=status.HTTP_403_FORBIDDEN)

        serializer = CommentSerializer(data=request.data)
        if not serializer.is_valid():
            return format_error_response('Invalid comment data', serializer.errors)

        content_type = ContentType.objects.get_for_model(media.__class__)
        parent = None
        parent_id = request.data.get('parent_id')

        if parent_id:
            try:
                parent = Comment.objects.get(id=parent_id)
                if parent.object_id != media.id or parent.content_type_id != content_type.id:
                    return format_error_response("parent comment isn't on this media")
            except Comment.DoesNotExist:
                return format_error_response("parent comment not found")

        comment = Comment.objects.create(
            user=request.user, 
            content=serializer.validated_data['content'],
            content_type=content_type,
            object_id=media.id,
            parent=parent
        )

        try:
            from apps.rewards.events import RewardEvent
            from apps.rewards.services import RewardEngineService
            tenant_id = getattr(request.user, 'tenant_id', None)
            if tenant_id:
                RewardEngineService.handle_event(RewardEvent.create_social_comment_created(
                    tenant_id=tenant_id,
                    user_id=request.user.id,
                    comment_id=comment.id,
                    media_id=media.id
                ))
        except Exception:
            pass

        return Response(CommentSerializer(comment, context={'request': request}).data, status=status.HTTP_201_CREATED)


# Updated UnifiedFeedAPIView with the desired output format
class UnifiedFeedAPIView(APIView):
    permission_classes = [AllowAny]

    # def get(self, request, *args, **kwargs):
    #     user_id = request.query_params.get('user_id')
    #     content_type = request.query_params.get('type')
    #
    #     photos, videos, polls = [], [], []
    #     users = {}
    #
    #     if not content_type or content_type == 'photo':
    #         photo_qs = Photo.objects.select_related('user').all()
    #         if user_id:
    #             photo_qs = photo_qs.filter(user_id=user_id)
    #
    #         for photo in photo_qs.order_by('-created_at'):
    #             users[photo.user.id] = photo.user
    #             data = PhotoSerializer(photo, context={'request': request}).data
    #             data.pop('user', None)
    #             data['user_id'] = photo.user.id
    #             data['media_type'] = 'photo'
    #             photos.append(data)
    #
    #     if not content_type or content_type == 'video':
    #         video_qs = Video.objects.select_related('user').all()
    #         if user_id:
    #             video_qs = video_qs.filter(user_id=user_id)
    #
    #         for video in video_qs.order_by('-created_at'):
    #             users[video.user.id] = video.user
    #             data = VideoSerializer(video, context={'request': request}).data
    #             data.pop('user', None)
    #             data['user_id'] = video.user.id
    #             data['media_type'] = 'video'
    #             videos.append(data)
    #
    #     if not content_type or content_type == 'poll':
    #         poll_qs = Poll.objects.select_related('user').all()
    #         if user_id:
    #             poll_qs = poll_qs.filter(user_id=user_id)
    #
    #         if request.user.is_authenticated:
    #             if request.user.user_type not in [request.user.UserType.STAFF, request.user.UserType.ADMIN]:
    #                 poll_qs = poll_qs.filter(visible_to_clients=True)
    #         else:
    #             poll_qs = poll_qs.filter(visible_to_clients=True)
    #
    #         for poll in poll_qs.order_by('-created_at'):
    #             users[poll.user.id] = poll.user
    #             data = PollSerializer(poll, context={'request': request}).data
    #             data.pop('user', None)
    #             data['user_id'] = poll.user.id
    #             data['media_type'] = 'poll'
    #             polls.append(data)
    #
    #     all_content = photos + videos + polls
    #     all_content.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    #
    #     serialized_users = {
    #         str(uid): UserMinimalSerializer(user, context={'request': request}).data
    #         for uid, user in users.items()
    #     }
    #
    #     return Response({
    #         'users': serialized_users,
    #         'content': all_content
    #     })
    from collections import defaultdict
    from rest_framework import status
    from rest_framework.response import Response
    
    from django.db.models import Q

    # def get(self, request, *args, **kwargs):
    #     # 1) Collect raw content lists
    #     photos, videos, polls = [], [], []
    #     users = {}  # user_id -> User instance
    #
    #     # — PHOTOS —
    #     photo_qs = Photo.objects.select_related('user').all()
    #     for photo in photo_qs.order_by('-created_at'):
    #         users[photo.user.id] = photo.user
    #         data = PhotoSerializer(photo, context={'request': request}).data
    #         data.pop('user', None)
    #         data.update({
    #             'user_id': photo.user.id,
    #             'media_type': 'photo'
    #         })
    #         photos.append(data)
    #
    #     # — VIDEOS —
    #     video_qs = Video.objects.select_related('user').all()
    #     for video in video_qs.order_by('-created_at'):
    #         users[video.user.id] = video.user
    #         data = VideoSerializer(video, context={'request': request}).data
    #         data.pop('user', None)
    #         data.update({
    #             'user_id': video.user.id,
    #             'media_type': 'video'
    #         })
    #         videos.append(data)
    #
    #     # — POLLS (with visibility) —
    #     poll_qs = Poll.objects.select_related('user').all()
    #     # apply client/staff visibility exactly as you had it…
    #     if request.user.is_authenticated:
    #         if request.user.user_type not in [request.user.UserType.STAFF, request.user.UserType.ADMIN]:
    #             poll_qs = poll_qs.filter(visible_to_clients=True)
    #     else:
    #         poll_qs = poll_qs.filter(visible_to_clients=True)
    #
    #     for poll in poll_qs.order_by('-created_at'):
    #         users[poll.user.id] = poll.user
    #         data = PollSerializer(poll, context={'request': request}).data
    #         data.pop('user', None)
    #         data.update({
    #             'user_id': poll.user.id,
    #             'media_type': 'poll'
    #         })
    #         polls.append(data)
    #
    #     all_content = sorted(
    #         photos + videos + polls,
    #         key=lambda x: x.get('created_at', ''),
    #         reverse=True
    #     )
    #
    #     content_by_user = defaultdict(list)
    #     for item in all_content:
    #         content_by_user[item['user_id']].append(item)
    #
    #     # 4) Serialize minimal user info
    #     serialized_users = {
    #         user_id: UserMinimalSerializer(user, context={'request': request}).data
    #         for user_id, user in users.items()
    #     }
    #
    #
    #     ordering = []
    #     for user_id, items in content_by_user.items():
    #         latest = max(i['created_at'] for i in items)
    #         ordering.append((user_id, latest))
    #     ordered_user_ids = [
    #         uid for uid, _ in sorted(ordering, key=lambda x: x[1], reverse=True)
    #     ]
    #
    #     # 6) Assemble blocks
    #     response_blocks = [
    #         {
    #             'user': serialized_users[uid],
    #             'content': content_by_user[uid]
    #         }
    #         for uid in ordered_user_ids
    #     ]
    #
    #     # **return the list directly** instead of wrapping in a dict
    #     return Response(response_blocks, status=status.HTTP_200_OK)

    def get(self, request, *args, **kwargs):
        # 1) Collect raw content lists
        items = []
        user_cache = {}

        # Collect blocked user IDs (mutual blocking like Instagram)
        blocked_user_ids = set()
        if request.user.is_authenticated:
            blocked_user_ids = UserBlock.get_blocked_user_ids_for(request.user)

        def add_items(qs, serializer_class, media_type):
            for obj in qs.order_by('-created_at'):
                user = obj.user
                # cache minimal user serialization
                if user.id not in user_cache:
                    user_cache[user.id] = UserMinimalSerializer(
                        user, context={'request': request}
                    ).data

                data = serializer_class(obj, context={'request': request}).data
                data.update({
                    'media_type': media_type,
                    'user': user_cache[user.id],  # nest the minimal user info
                })
                items.append(data)

        # PHOTOS
        photo_qs = Photo.objects.select_related('user__profile')
        if blocked_user_ids:
            photo_qs = photo_qs.exclude(user_id__in=blocked_user_ids)
        add_items(
            photo_qs.all(),
            PhotoSerializer,
            media_type='photo'
        )

        # VIDEOS
        video_qs = Video.objects.select_related('user__profile')
        if blocked_user_ids:
            video_qs = video_qs.exclude(user_id__in=blocked_user_ids)
        add_items(
            video_qs.all(),
            VideoSerializer,
            media_type='video'
        )
        from django.db import models

        # POLLS (apply visibility)
        poll_qs = Poll.objects.select_related('user__profile').filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gt=timezone.now()))

        from apps.users.models import UserRole
        if request.user.is_authenticated and (request.user.is_staff or is_admin_user(request.user) or request.user.role != UserRole.CLIENT):
            pass
        elif request.user.is_authenticated:
            poll_qs = poll_qs.filter(models.Q(visible_to_clients=True) | models.Q(user=request.user))
        else:
            poll_qs = poll_qs.filter(visible_to_clients=True)

        if blocked_user_ids:
            poll_qs = poll_qs.exclude(user_id__in=blocked_user_ids)

        add_items(poll_qs, PollSerializer, media_type='poll')

        post_qs = SocialPost.objects.select_related('user__profile').prefetch_related('media_items')
        if request.user.is_authenticated and (request.user.is_staff or is_admin_user(request.user) or request.user.role != UserRole.CLIENT):
            pass
        elif request.user.is_authenticated:
            post_qs = post_qs.filter(models.Q(visible_to_clients=True) | models.Q(user=request.user))
        else:
            post_qs = post_qs.filter(visible_to_clients=True)

        if blocked_user_ids:
            post_qs = post_qs.exclude(user_id__in=blocked_user_ids)

        for obj in post_qs.order_by('-created_at'):
            user = obj.user
            if user.id not in user_cache:
                user_cache[user.id] = UserMinimalSerializer(
                    user, context={'request': request}
                ).data
            data = SocialPostSerializer(obj, context={'request': request}).data
            data.update({
                'user': user_cache[user.id],
            })
            items.append(data)

        items.sort(key=lambda x: x.get('created_at'), reverse=True)
        return Response(items, status=status.HTTP_200_OK)


class UnifiedMediaUploadAPIView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        media_type = request.data.get('media_type', '')
        if media_type == 'poll':
            return self._handle_poll_upload(request)

        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user

        serializer = UnifiedMediaUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return format_error_response('Invalid data', serializer.errors)

        return create_social_post(request, user)

    def _parse_boolean(self, value):
        if isinstance(value, bool):
            return value
        return str(value).lower() == 'true'

    def _get_authenticated_user(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return format_error_response(
                'Authentication required',
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        return user

    def _handle_poll_upload(self, request):
        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user
        return handle_poll_upload(request, user)


class PostViewSet(viewsets.ModelViewSet):
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = SocialPost.objects.select_related('user__profile').prefetch_related('media_items')
        from apps.users.models import UserRole
        if user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(user)
            if blocked_ids:
                qs = qs.exclude(user_id__in=blocked_ids)
            if user.is_staff or is_admin_user(user) or user.role != UserRole.CLIENT:
                return qs.order_by('-created_at')
            return qs.filter(Q(visible_to_clients=True) | Q(user=user)).order_by('-created_at')
        return qs.filter(visible_to_clients=True).order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return SocialPostDetailSerializer
        return SocialPostSerializer

    def retrieve(self, request, *args, **kwargs):
        post = self.get_object()
        if request.user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(request.user)
            if post.user_id in blocked_ids:
                return format_error_response("Post not found.", status_code=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(post)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        return create_social_post(request, request.user)

    def update(self, request, *args, **kwargs):
        post = self.get_object()
        if post.user_id != request.user.id and not is_admin_user(request.user):
            return format_error_response('You do not have permission to edit this post.', status_code=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(post, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return format_error_response('Invalid update data', serializer.errors)

    def destroy(self, request, *args, **kwargs):
        post = self.get_object()
        if post.user_id != request.user.id and not is_admin_user(request.user):
            return format_error_response('You do not have permission to delete this post.', status_code=status.HTTP_403_FORBIDDEN)
        post.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        post = self.get_object()
        if request.user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(request.user)
            if post.user_id in blocked_ids:
                return format_error_response("Post not found.", status_code=status.HTTP_404_NOT_FOUND)
        content_type = ContentType.objects.get_for_model(SocialPost)
        like, created = Like.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=post.id
        )
        if created:
            try:
                from apps.rewards.events import RewardEvent
                from apps.rewards.services import RewardEngineService
                tenant_id = getattr(request.user, 'tenant_id', None)
                if tenant_id:
                    RewardEngineService.handle_event(RewardEvent.create_social_like_created(
                        tenant_id=tenant_id,
                        user_id=request.user.id,
                        like_id=like.id,
                        media_id=post.id
                    ))
            except Exception:
                pass
            return Response({'status': 'liked!'}, status=status.HTTP_201_CREATED)
        return Response({'status': 'already liked'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def unlike(self, request, pk=None):
        post = self.get_object()
        if request.user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(request.user)
            if post.user_id in blocked_ids:
                return format_error_response("Post not found.", status_code=status.HTTP_404_NOT_FOUND)
        content_type = ContentType.objects.get_for_model(SocialPost)
        like = Like.objects.filter(
            user=request.user,
            content_type=content_type,
            object_id=post.id
        ).first()
        if like:
            like.delete()
            return Response({'status': 'unliked'}, status=status.HTTP_200_OK)
        return format_error_response("You haven't liked this post yet")

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def comment(self, request, pk=None):
        post = self.get_object()
        if request.user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(request.user)
            if post.user_id in blocked_ids:
                return format_error_response("Post not found.", status_code=status.HTTP_404_NOT_FOUND)
        if not post.comments_enabled:
            return format_error_response('Comments are disabled for this post', status_code=status.HTTP_403_FORBIDDEN)
        serializer = CommentSerializer(data=request.data)
        if not serializer.is_valid():
            return format_error_response('Invalid comment data', serializer.errors)
        content_type = ContentType.objects.get_for_model(SocialPost)
        parent = None
        parent_id = request.data.get('parent_id')
        if parent_id:
            try:
                parent = Comment.objects.get(id=parent_id)
                if parent.object_id != post.id or parent.content_type_id != content_type.id:
                    return format_error_response("Parent comment isn't on this post")
            except Comment.DoesNotExist:
                return format_error_response('Parent comment not found')
        comment = Comment.objects.create(
            user=request.user,
            content=serializer.validated_data['content'],
            content_type=content_type,
            object_id=post.id,
            parent=parent
        )
        try:
            from apps.rewards.events import RewardEvent
            from apps.rewards.services import RewardEngineService
            tenant_id = getattr(request.user, 'tenant_id', None)
            if tenant_id:
                RewardEngineService.handle_event(RewardEvent.create_social_comment_created(
                    tenant_id=tenant_id,
                    user_id=request.user.id,
                    comment_id=comment.id,
                    media_id=post.id
                ))
        except Exception:
            pass
        return Response(CommentSerializer(comment, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], permission_classes=[AllowAny])
    def comments(self, request, pk=None):
        post = self.get_object()
        if request.user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(request.user)
            if post.user_id in blocked_ids:
                return format_error_response("Post not found.", status_code=status.HTTP_404_NOT_FOUND)
        content_type = ContentType.objects.get_for_model(SocialPost)
        top_comments = Comment.objects.filter(
            content_type=content_type,
            object_id=post.id,
            parent=None
        ).select_related('user__profile').prefetch_related('replies__user__profile')
        if request.user.is_authenticated:
            blocked_ids = UserBlock.get_blocked_user_ids_for(request.user)
            if blocked_ids:
                top_comments = top_comments.exclude(user_id__in=blocked_ids)
        results = []
        for c in top_comments:
            results.append({
                'comment': CommentSerializer(c, context={'request': request}).data,
                'user': UserMinimalSerializer(c.user, context={'request': request}).data
            })
        return Response(results)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def report(self, request, pk=None):
        post = self.get_object()
        if post.user_id == request.user.id:
            return format_error_response("You cannot report your own post.")

        existing = PostReport.objects.filter(
            reporter=request.user,
            post=post,
            status__in=[PostReport.ReportStatus.PENDING, PostReport.ReportStatus.UNDER_REVIEW]
        ).first()
        if existing:
            return format_error_response("You have already reported this post. Your report is currently under review.")

        report_data = {'post_id': str(post.id), **request.data}
        serializer = PostReportCreateSerializer(data=report_data, context={'request': request})
        if not serializer.is_valid():
            return format_error_response("Invalid report data", serializer.errors)

        tenant = getattr(request.user, 'tenant', None) or getattr(post, 'tenant', None)
        report = PostReport.objects.create(
            reporter=request.user,
            reported_user=post.user,
            post=post,
            reason=serializer.validated_data.get('reason', PostReport.ReportReason.OTHER),
            description=serializer.validated_data.get('description', ''),
            tenant=tenant
        )
        return Response(PostReportSerializer(report, context={'request': request}).data, status=status.HTTP_201_CREATED)
    
class CommentViewSet(mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = Comment.all_objects.all()
    # Ensure you are using the correct serializer for retrieval/deletion
    serializer_class = CommentSerializer 
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Comment.objects.all()

    def destroy(self, request, *args, **kwargs):
        comment = self.get_object()

        # Security Check: Allow deletion ONLY if the user is the author OR is gym admin
        if comment.user != request.user and not is_admin_user(request.user):
            return Response(
                {"error": "You do not have permission to delete this comment."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Proceed with standard deletion
        self.perform_destroy(comment)
        return Response(
            {"message": "Comment deleted successfully."}, 
            status=status.HTTP_204_NO_CONTENT
        )

    @action(detail=True, methods=['post'])
    def react(self, request, pk=None):
        comment = self.get_object()
        reaction_type = request.data.get('type', 'LIKE').upper() # 'LIKE' or 'DISLIKE'
        
        if reaction_type not in ['LIKE', 'DISLIKE']:
            return Response({'error': 'Invalid reaction type'}, status=400)

        with transaction.atomic():
            # Check for existing reaction
            existing = CommentReaction.objects.filter(user=request.user, comment=comment).first()

            if existing:
                if existing.reaction_type == reaction_type:
                    # Toggle OFF: User clicked the same reaction again
                    existing.delete()
                    self._update_comment_counts(comment)
                    return Response({'status': 'Reaction removed'})
                else:
                    # Swap: User changed from Like to Dislike (or vice versa)
                    existing.reaction_type = reaction_type
                    existing.save()
                    self._update_comment_counts(comment)
                    return Response({'status': f'Changed to {reaction_type}'})

            # Create new reaction
            CommentReaction.objects.create(user=request.user, comment=comment, reaction_type=reaction_type)
            self._update_comment_counts(comment)
            return Response({'status': f'{reaction_type} recorded'}, status=201)

    def _update_comment_counts(self, comment):
        """Isolated helper to update counts without touching post signals."""
        comment.likes_count = comment.reactions.filter(reaction_type='LIKE').count()
        comment.dislikes_count = comment.reactions.filter(reaction_type='DISLIKE').count()
        comment.save(update_fields=['likes_count', 'dislikes_count'])


class UserBlockViewSet(viewsets.ModelViewSet):
    """
    CRUD API for clients and staff to manage their blocked users.
    - list: GET /api/v1/socialnetwork/blocks/ (list users blocked by current user)
    - retrieve: GET /api/v1/socialnetwork/blocks/<id>/
    - create: POST /api/v1/socialnetwork/blocks/ (body: {"blocked_user_id": "<uuid>", "reason": "..."})
    - destroy: DELETE /api/v1/socialnetwork/blocks/<id>/ (unblock)
    - unblock: POST /api/v1/socialnetwork/blocks/unblock/ (body: {"blocked_user_id": "<uuid>"})
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserBlockSerializer

    def get_queryset(self):
        return UserBlock.objects.filter(blocker=self.request.user).select_related('blocked__profile')

    def create(self, request, *args, **kwargs):
        blocked_user_id = (
            request.data.get('blocked_user_id')
            or request.data.get('user_id')
            or request.data.get('blocked')
        )
        if not blocked_user_id:
            return format_error_response("blocked_user_id is required")

        if str(blocked_user_id) == str(request.user.id):
            return format_error_response("You cannot block yourself.")

        target_user = User.objects.filter(id=blocked_user_id).first()
        if not target_user:
            return format_error_response("User not found.", status_code=status.HTTP_404_NOT_FOUND)

        existing = UserBlock.all_objects.filter(blocker=request.user, blocked=target_user).first()
        if existing:
            return format_error_response("You have already blocked this user.", status_code=status.HTTP_400_BAD_REQUEST)

        tenant = getattr(request.user, 'tenant', None) or getattr(target_user, 'tenant', None)
        block = UserBlock.objects.create(
            blocker=request.user,
            blocked=target_user,
            reason=request.data.get('reason'),
            tenant=tenant
        )
        serializer = self.get_serializer(block)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        block = self.get_object()
        if block.blocker_id != request.user.id and not is_admin_user(request.user):
            return format_error_response("You do not have permission to delete this block.", status_code=status.HTTP_403_FORBIDDEN)
        block.delete()
        return Response({"message": "User unblocked successfully."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='unblock')
    def unblock_by_user(self, request):
        blocked_user_id = (
            request.data.get('blocked_user_id')
            or request.data.get('user_id')
            or request.data.get('blocked')
        )
        if not blocked_user_id:
            return format_error_response("blocked_user_id is required")

        block = UserBlock.all_objects.filter(blocker=request.user, blocked_id=blocked_user_id).first()
        if not block:
            return format_error_response("Block record not found.", status_code=status.HTTP_404_NOT_FOUND)

        block.delete()
        return Response({"message": "User unblocked successfully."}, status=status.HTTP_200_OK)


class UserBlockActionView(APIView):
    """
    Direct endpoint to block a user:
    POST /api/v1/socialnetwork/users/<user_id>/block/
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, user_id):
        if str(user_id) == str(request.user.id):
            return format_error_response("You cannot block yourself.")

        target_user = User.objects.filter(id=user_id).first()
        if not target_user:
            return format_error_response("User not found.", status_code=status.HTTP_404_NOT_FOUND)

        existing = UserBlock.all_objects.filter(blocker=request.user, blocked=target_user).first()
        if existing:
            return format_error_response("You have already blocked this user.", status_code=status.HTTP_400_BAD_REQUEST)

        tenant = getattr(request.user, 'tenant', None) or getattr(target_user, 'tenant', None)
        block = UserBlock.objects.create(
            blocker=request.user,
            blocked=target_user,
            reason=request.data.get('reason'),
            tenant=tenant
        )
        serializer = UserBlockSerializer(block, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class UserUnblockActionView(APIView):
    """
    Direct endpoint to unblock a user:
    POST or DELETE /api/v1/socialnetwork/users/<user_id>/unblock/
    """
    permission_classes = [permissions.IsAuthenticated]

    def _unblock(self, request, user_id):
        block = UserBlock.all_objects.filter(blocker=request.user, blocked_id=user_id).first()
        if not block:
            return format_error_response("Block record not found.", status_code=status.HTTP_404_NOT_FOUND)
        block.delete()
        return Response({"message": "User unblocked successfully."}, status=status.HTTP_200_OK)

    def post(self, request, user_id):
        return self._unblock(request, user_id)

    def delete(self, request, user_id):
        return self._unblock(request, user_id)


class AdminUserBlockViewSet(viewsets.ModelViewSet):
    """
    Admin CRUD for gym owners, managers, and platform admins to oversee blocks.
    - list: GET /api/v1/socialnetwork/admin/blocks/
    - retrieve: GET /api/v1/socialnetwork/admin/blocks/<id>/
    - create: POST /api/v1/socialnetwork/admin/blocks/
    - update: PATCH /api/v1/socialnetwork/admin/blocks/<id>/
    - destroy: DELETE /api/v1/socialnetwork/admin/blocks/<id>/
    """
    permission_classes = [IsAdminOrModerator]
    serializer_class = AdminUserBlockSerializer

    def get_queryset(self):
        user = self.request.user
        from apps.users.models import UserRole
        if user.role == UserRole.PLATFORM_ADMIN or user.is_superuser:
            qs = UserBlock.all_objects.select_related('blocker__profile', 'blocked__profile', 'tenant')
        else:
            qs = UserBlock.objects.select_related('blocker__profile', 'blocked__profile', 'tenant')

        blocker_id = self.request.query_params.get('blocker_id')
        if blocker_id:
            qs = qs.filter(blocker_id=blocker_id)

        blocked_id = self.request.query_params.get('blocked_id')
        if blocked_id:
            qs = qs.filter(blocked_id=blocked_id)

        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(blocker__email__icontains=search) |
                Q(blocked__email__icontains=search) |
                Q(reason__icontains=search)
            )

        return qs.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        blocker_id = request.data.get('blocker_id') or request.user.id
        blocked_user_id = (
            request.data.get('blocked_user_id')
            or request.data.get('user_id')
            or request.data.get('blocked')
        )

        if not blocked_user_id:
            return format_error_response("blocked_user_id is required")

        if str(blocker_id) == str(blocked_user_id):
            return format_error_response("Blocker and blocked user cannot be the same user.")

        blocker = User.objects.filter(id=blocker_id).first()
        blocked = User.objects.filter(id=blocked_user_id).first()
        if not blocker or not blocked:
            return format_error_response("Blocker or blocked user not found.", status_code=status.HTTP_404_NOT_FOUND)

        existing = UserBlock.all_objects.filter(blocker=blocker, blocked=blocked).first()
        if existing:
            return format_error_response("This block relationship already exists.", status_code=status.HTTP_400_BAD_REQUEST)

        tenant = getattr(blocker, 'tenant', None) or getattr(blocked, 'tenant', None) or getattr(request.user, 'tenant', None)
        block = UserBlock.objects.create(
            blocker=blocker,
            blocked=blocked,
            reason=request.data.get('reason'),
            tenant=tenant
        )
        return Response(AdminUserBlockSerializer(block, context={'request': request}).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        block = self.get_object()
        serializer = self.get_serializer(block, data=request.data, partial=True)
        if not serializer.is_valid():
            return format_error_response("Invalid update data", serializer.errors)
        serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        block = self.get_object()
        block.delete()
        return Response({"message": "Block removed successfully by admin."}, status=status.HTTP_200_OK)


class PostReportViewSet(viewsets.ModelViewSet):
    """
    CRUD API for clients and staff to submit and manage reports on posts.
    - list: GET /api/v1/socialnetwork/reports/ (reports submitted by current user)
    - retrieve: GET /api/v1/socialnetwork/reports/<id>/
    - create: POST /api/v1/socialnetwork/reports/ (payload: post_id, reason, description)
    - update: PATCH /api/v1/socialnetwork/reports/<id>/ (update pending report)
    - destroy: DELETE /api/v1/socialnetwork/reports/<id>/ (cancel pending report)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = PostReport.objects.filter(reporter=self.request.user).select_related(
            'reporter__profile', 'reported_user__profile', 'post'
        )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param.lower())
        reason_param = self.request.query_params.get('reason')
        if reason_param:
            qs = qs.filter(reason=reason_param.lower())
        return qs.order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'create':
            return PostReportCreateSerializer
        if self.action in ['update', 'partial_update']:
            return PostReportUpdateSerializer
        return PostReportSerializer

    def create(self, request, *args, **kwargs):
        serializer = PostReportCreateSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return format_error_response("Invalid report data", serializer.errors)

        post = SocialPost.objects.filter(id=serializer.validated_data['post_id']).first()
        if not post:
            return format_error_response("Post not found.", status_code=status.HTTP_404_NOT_FOUND)

        tenant = getattr(request.user, 'tenant', None) or getattr(post, 'tenant', None)
        report = PostReport.objects.create(
            reporter=request.user,
            reported_user=post.user,
            post=post,
            reason=serializer.validated_data.get('reason', PostReport.ReportReason.OTHER),
            description=serializer.validated_data.get('description', ''),
            tenant=tenant
        )
        return Response(PostReportSerializer(report, context={'request': request}).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        report = self.get_object()
        if report.reporter_id != request.user.id and not is_admin_user(request.user):
            return format_error_response("You do not have permission to edit this report.", status_code=status.HTTP_403_FORBIDDEN)
        if report.status != PostReport.ReportStatus.PENDING:
            return format_error_response("Cannot modify a report that is already under review or resolved.")

        serializer = PostReportUpdateSerializer(report, data=request.data, partial=True)
        if not serializer.is_valid():
            return format_error_response("Invalid update data", serializer.errors)
        serializer.save()
        return Response(PostReportSerializer(report, context={'request': request}).data)

    def destroy(self, request, *args, **kwargs):
        report = self.get_object()
        if report.reporter_id != request.user.id and not is_admin_user(request.user):
            return format_error_response("You do not have permission to withdraw this report.", status_code=status.HTTP_403_FORBIDDEN)
        if report.status != PostReport.ReportStatus.PENDING:
            return format_error_response("Cannot withdraw a report that is already under review or resolved.")
        report.delete()
        return Response({"message": "Report withdrawn successfully."}, status=status.HTTP_200_OK)


class AdminPostReportViewSet(viewsets.ModelViewSet):
    """
    Complete moderation CRUD and action API for gym admins & platform staff.
    - list: GET /api/v1/socialnetwork/admin/reports/ (filter by status, reason, post, user)
    - retrieve: GET /api/v1/socialnetwork/admin/reports/<id>/
    - update/patch: PATCH /api/v1/socialnetwork/admin/reports/<id>/ (update status, notes, action)
    - destroy: DELETE /api/v1/socialnetwork/admin/reports/<id>/
    - take_action: POST /api/v1/socialnetwork/admin/reports/<id>/action/
    """
    permission_classes = [IsAdminOrModerator]
    serializer_class = AdminPostReportSerializer

    def get_queryset(self):
        user = self.request.user
        from apps.users.models import UserRole
        if user.role == UserRole.PLATFORM_ADMIN or user.is_superuser:
            qs = PostReport.all_objects.select_related(
                'reporter__profile', 'reported_user__profile', 'post', 'reviewed_by__profile', 'tenant'
            )
        else:
            qs = PostReport.objects.select_related(
                'reporter__profile', 'reported_user__profile', 'post', 'reviewed_by__profile', 'tenant'
            )

        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param.lower())

        reason_param = self.request.query_params.get('reason')
        if reason_param:
            qs = qs.filter(reason=reason_param.lower())

        post_id = self.request.query_params.get('post_id')
        if post_id:
            qs = qs.filter(post_id=post_id)

        reported_user_id = self.request.query_params.get('reported_user_id')
        if reported_user_id:
            qs = qs.filter(reported_user_id=reported_user_id)

        reporter_id = self.request.query_params.get('reporter_id')
        if reporter_id:
            qs = qs.filter(reporter_id=reporter_id)

        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(reporter__email__icontains=search) |
                Q(reported_user__email__icontains=search) |
                Q(description__icontains=search) |
                Q(admin_notes__icontains=search)
            )

        return qs.order_by('-created_at')

    def update(self, request, *args, **kwargs):
        report = self.get_object()
        serializer = self.get_serializer(report, data=request.data, partial=True)
        if not serializer.is_valid():
            return format_error_response("Invalid update data", serializer.errors)
        serializer.save(
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        report = self.get_object()
        report.delete()
        return Response({"message": "Report deleted successfully by admin."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='action')
    def take_action(self, request, pk=None):
        report = self.get_object()
        serializer = AdminReportActionSerializer(data=request.data)
        if not serializer.is_valid():
            return format_error_response("Invalid action data", serializer.errors)

        action_type = serializer.validated_data['action']
        admin_notes = serializer.validated_data.get('admin_notes', '')

        if admin_notes:
            timestamp_str = timezone.now().strftime('%Y-%m-%d %H:%M')
            if report.admin_notes:
                report.admin_notes += f"\n[{timestamp_str}] {admin_notes}"
            else:
                report.admin_notes = f"[{timestamp_str}] {admin_notes}"

        report.reviewed_by = request.user
        report.reviewed_at = timezone.now()

        if action_type == 'delete_post':
            if report.post:
                report.post.delete()
                report.post = None
            report.status = PostReport.ReportStatus.RESOLVED
            report.action_taken = PostReport.ReportAction.POST_DELETED

        elif action_type == 'hide_post':
            if report.post:
                report.post.visible_to_clients = False
                report.post.visible_to_staff = False
                report.post.save(update_fields=['visible_to_clients', 'visible_to_staff'])
            report.status = PostReport.ReportStatus.RESOLVED
            report.action_taken = PostReport.ReportAction.POST_HIDDEN

        elif action_type == 'dismiss':
            report.status = PostReport.ReportStatus.DISMISSED
            report.action_taken = PostReport.ReportAction.DISMISSED

        elif action_type == 'warn_user':
            report.status = PostReport.ReportStatus.RESOLVED
            report.action_taken = PostReport.ReportAction.USER_WARNED

        elif action_type == 'block_user':
            if report.reported_user:
                tenant = report.tenant or getattr(request.user, 'tenant', None)
                UserBlock.objects.get_or_create(
                    blocker=request.user,
                    blocked=report.reported_user,
                    defaults={'reason': f'Blocked via report #{report.id}', 'tenant': tenant}
                )
            report.status = PostReport.ReportStatus.RESOLVED
            report.action_taken = PostReport.ReportAction.USER_BLOCKED

        report.save()
        return Response(AdminPostReportSerializer(report, context={'request': request}).data, status=status.HTTP_200_OK)
