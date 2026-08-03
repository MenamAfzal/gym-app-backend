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
from apps.socialnetwork.helper_functions import handle_file_response
from apps.socialnetwork.models import Comment, Like, Photo, Poll, PollOption, Video, Vote, Post
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

        files = request.FILES
        if not files:
            return format_error_response('No files uploaded')

        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user

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

        for field_name, file_obj in files.items():
            file_type = handle_file_response(file_obj)

            try:
                if file_type == "Image":
                    serializer = PhotoUploadSerializer(data={'image': file_obj, **metadata})
                    media_type = 'photo'
                elif file_type == "Video":
                    serializer = VideoUploadSerializer(data={'video_file': file_obj, **metadata})
                    media_type = 'video'
                else:
                    failed_uploads.append({
                        'file': field_name,
                        'error': f"Unsupported file type: {file_type}"
                    })
                    continue

                if serializer.is_valid():
                    media = serializer.save(user=user)
                    response_serializer = PhotoSerializer(media, context={
                        'request': request}) if media_type == 'photo' else VideoSerializer(media,
                                                                                           context={'request': request})
                    upload_data = response_serializer.data
                    upload_data['media_type'] = media_type
                    successful_uploads.append(upload_data)
                else:
                    failed_uploads.append({
                        'file': field_name,
                        'errors': serializer.errors
                    })
            except Exception as e:
                logger.error(f"Error uploading file {field_name}: {str(e)}", exc_info=True)
                failed_uploads.append({
                    'file': field_name,
                    'error': str(e)
                })

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
        user = request.user
        if not user.is_authenticated:
            return format_error_response(
                'Authentication required',
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        from apps.users.models import UserRole
        if not (user.is_staff or user.role != UserRole.CLIENT):
            return format_error_response(
                'Staff privileges required',
                status_code=status.HTTP_403_FORBIDDEN
            )
        return handle_poll_upload(request, user)


class PollCreateAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return format_error_response(
                'Authentication required',
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        from apps.users.models import UserRole
        if not (user.is_staff or user.role != UserRole.CLIENT):
            return format_error_response(
                'Staff privileges required',
                status_code=status.HTTP_403_FORBIDDEN
            )
        return handle_poll_upload(request, user)


class PollAPIView(viewsets.ModelViewSet):
    permission_classes = [AllowAny]

    def get_queryset(self):
        user = self.request.user
        from apps.users.models import UserRole
        if user.is_authenticated and (user.is_staff or user.role != UserRole.CLIENT):
            return Poll.objects.all().order_by('-created_at')
        return Poll.objects.filter(visible_to_clients=True).order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PollDetailSerializer
        return PollSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

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
    }


        

    def get_queryset(self):
        media_type = self.request.query_params.get('type') or self.request.data.get('media_type')
        if media_type in self.media_types:
            return self.media_types[media_type]['model'].objects.all()
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
        for m_type, config in self.media_types.items():
            if media_type and m_type != media_type:
                continue
            qs = config['model'].objects.all()
            if user_id:
                qs = qs.filter(user_id=user_id)
            data = config['serializer'](qs.order_by('-created_at'), many=True, context={'request': request}).data
            all_media.extend([dict(item, **{'media_type': m_type}) for item in data])

        all_media.sort(key=lambda x: x['created_at'], reverse=True)
        return Response(all_media)

    def retrieve(self, request, pk=None):
        media_type = request.query_params.get('type')
        if not media_type or media_type not in self.media_types:
            return format_error_response('Missing or invalid type parameter')

        try:
            media_obj = self.media_types[media_type]['model'].objects.get(pk=pk)
            serializer = self.get_serializer(media_obj)
            return Response(serializer.data)
        except (Photo.DoesNotExist, Video.DoesNotExist):
            return format_error_response(f"Couldn't find that {media_type}", status_code=status.HTTP_404_NOT_FOUND)

    def create(self, request):
        media_type = request.data.get('media_type')
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
                result = serializer.data
                result['media_type'] = media_type
                responses.append(result)
            else:
                return format_error_response('Invalid media data', serializer.errors)

        return Response(responses, status=status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        media_type = request.data.get('media_type')
        if not media_type or media_type not in self.media_types:
            return format_error_response('Need to specify valid media_type')

        try:
            media = self.media_types[media_type]['model'].objects.get(pk=pk)
        except (Photo.DoesNotExist, Video.DoesNotExist):
            return format_error_response(f"{media_type} not found", status_code=status.HTTP_404_NOT_FOUND)

        serializer = self.get_serializer(media, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            response_data = serializer.data
            response_data['media_type'] = media_type
            return Response(response_data)
        return format_error_response('Invalid update data', serializer.errors)

    def destroy(self, request, pk=None):
        media_type = request.query_params.get('type')
        if not media_type or media_type not in self.media_types:
            return format_error_response('Need type parameter (photo/video/poll)')

        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user

        try:
            obj = self.media_types[media_type]['model'].objects.get(pk=pk)
            obj.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except (Photo.DoesNotExist, Video.DoesNotExist):
            return format_error_response(f"{media_type} not found", status_code=status.HTTP_404_NOT_FOUND)
        

    def _get_authenticated_user(self, request):
        user = request.user
        if not user.is_authenticated:
            return format_error_response(
                'Authentication required',
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        from apps.users.models import UserRole
        if not (user.is_staff or user.role != UserRole.CLIENT):
            return format_error_response(
                'Staff privileges required',
                status_code=status.HTTP_403_FORBIDDEN
            )

        return user

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        is_liked = False
        media_type = request.query_params.get('type')
        if not media_type or media_type not in self.media_types:
            return format_error_response('Missing type parameter')

        try:
            media = self.media_types[media_type]['model'].objects.get(pk=pk)
        except (Photo.DoesNotExist, Video.DoesNotExist):
            return format_error_response(f"{media_type} not found", status_code=status.HTTP_404_NOT_FOUND)

        content_type = ContentType.objects.get_for_model(media.__class__)
        like, created = Like.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=media.id
        )

        if created:
            return Response({'status': 'liked!'}, status=status.HTTP_201_CREATED)
        return Response({'status': 'already liked'}, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['get'], permission_classes=[AllowAny])
    def comments(self, request, pk=None):
        """
        GET /api/media/<pk>/comments/?type=<photo|video|poll>
        Returns a list of top-level comments for this media item, each with:
        - 'comment': full CommentSerializer data
        - 'user': minimal user info (UserMinimalSerializer)
        """
        media_type = request.query_params.get('type')
        if not media_type or media_type not in self.media_types:
            return format_error_response('Missing or invalid type parameter')
        # 1) Retrieve the actual media object (Photo, Video, or Poll)
        model = self.media_types[media_type]['model']
        obj = get_object_or_404(model, pk=pk)
        # 2) Build content type filter and fetch only top-level comments
        ct = ContentType.objects.get_for_model(model)
        top_comments = Comment.objects.filter(
            content_type=ct,
            object_id=obj.id,
            parent=None
        ).select_related('user__profile').prefetch_related('replies__user__profile')
        # 3) Serialize each comment plus the minimal user data
        results = []
        for c in top_comments:
            comment_data = CommentSerializer(c, context={'request': request}).data
            user_data    = UserMinimalSerializer(c.user, context={'request': request}).data
            results.append({
                'comment': comment_data,
                'user':    user_data
            })
        return Response(results)

    @action(detail=True, methods=['post'])
    def unlike(self, request, pk=None):
        media_type = request.query_params.get('type')
        if not media_type or media_type not in self.media_types:
            return format_error_response('Missing type parameter')

        try:
            media = self.media_types[media_type]['model'].objects.get(pk=pk)
        except (Photo.DoesNotExist, Video.DoesNotExist):
            return format_error_response(f"{media_type} not found", status_code=status.HTTP_404_NOT_FOUND)

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
        media_type = request.query_params.get('type')
        if not media_type or media_type not in self.media_types:
            return format_error_response('Need type parameter')

        try:
            media = self.media_types[media_type]['model'].objects.get(pk=pk)
        except (Photo.DoesNotExist, Video.DoesNotExist):
            return format_error_response(f"couldn't find that {media_type}", status_code=status.HTTP_404_NOT_FOUND)

        serializer = CommentSerializer(data=request.data)
        if not serializer.is_valid():
            return format_error_response('Invalid comment data', serializer.errors)

        content_type = ContentType.objects.get_for_model(media.__class__)
        parent = None
        parent_id = request.data.get('parent_id')

        # Logic for replies: Handles parent_id to nest the comment
        if parent_id:
            try:
                parent = Comment.objects.get(id=parent_id)
                if parent.object_id != media.id or parent.content_type_id != content_type.id:
                    return format_error_response("parent comment isn't on this media")
            except Comment.DoesNotExist:
                return format_error_response("parent comment not found")

        # Now safe from AnonymousUser error
        comment = Comment.objects.create(
            user=request.user, 
            content=serializer.validated_data['content'],
            content_type=content_type,
            object_id=media.id,
            parent=parent
        )

        return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)


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
        add_items(
            Photo.objects.select_related('user__profile').all(),
            PhotoSerializer,
            media_type='photo'
        )

        # VIDEOS
        add_items(
            Video.objects.select_related('user__profile').all(),
            VideoSerializer,
            media_type='video'
        )
        from django.db import models
 

        # POLLS (apply visibility)
        poll_qs = Poll.objects.select_related('user__profile').filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gt=timezone.now()))

        from apps.users.models import UserRole
        if request.user.is_authenticated and (request.user.is_staff or request.user.role != UserRole.CLIENT):
            pass
        else:
            poll_qs = poll_qs.filter(visible_to_clients=True)

        add_items(poll_qs, PollSerializer, media_type='poll')

        # 2) Sort everything by created_at descending
        items.sort(key=lambda x: x.get('created_at'), reverse=True)

        # 3) Return the full feed
        return Response(items, status=status.HTTP_200_OK)


class UnifiedMediaUploadAPIView(APIView):
    """
    A simplified API for uploading multiple media files with a single 'files' parameter.
    """
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        media_type = request.data.get('media_type', '')
        if media_type == 'poll':
            return self._handle_poll_upload(request)

        serializer = UnifiedMediaUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return format_error_response('Invalid data', serializer.errors)

        files = request.FILES.getlist('files')
        if not files:
            return format_error_response('No files uploaded')

        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user

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

        for file_obj in files:
            file_type = handle_file_response(file_obj)

            try:
                if file_type == "Image":
                    serializer = PhotoUploadSerializer(data={'image': file_obj, **metadata})
                    media_type = 'photo'
                elif file_type == "Video":
                    serializer = VideoUploadSerializer(data={'video_file': file_obj, **metadata})
                    media_type = 'video'
                else:
                    failed_uploads.append({
                        'file': file_obj.name,
                        'error': f"Unsupported file type: {file_type}"
                    })
                    continue

                if serializer.is_valid():
                    media = serializer.save(user=user)
                    response_serializer = PhotoSerializer(media, context={
                        'request': request}) if media_type == 'photo' else VideoSerializer(media,
                                                                                           context={'request': request})
                    upload_data = response_serializer.data
                    upload_data['media_type'] = media_type
                    successful_uploads.append(upload_data)
                else:
                    failed_uploads.append({
                        'file': file_obj.name,
                        'errors': serializer.errors
                    })
            except Exception as e:
                logger.error(f"Error uploading file {file_obj.name}: {str(e)}", exc_info=True)
                failed_uploads.append({
                    'file': file_obj.name,
                    'error': str(e)
                })

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
            try:
                user = User.objects.filter(is_staff=True, is_active=True).first() or User.objects.filter(
                    is_active=True).first()
                if not user:
                    return format_error_response('Authentication required', status_code=status.HTTP_401_UNAUTHORIZED)
            except Exception as e:
                logger.error(f"Authentication error: {str(e)}", exc_info=True)
                return format_error_response(f'Authentication error: {str(e)}',
                                             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return user

    def _handle_poll_upload(self, request):
        user = self._get_authenticated_user(request)
        if isinstance(user, Response):
            return user
        from apps.users.models import UserRole
        if not (user.is_staff or user.role != UserRole.CLIENT):
            return format_error_response(
                'Staff privileges required',
                status_code=status.HTTP_403_FORBIDDEN
            )
        return handle_poll_upload(request, user)
    
class CommentViewSet(mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = Comment.objects.all()
    # Ensure you are using the correct serializer for retrieval/deletion
    serializer_class = CommentSerializer 
    permission_classes = [permissions.IsAuthenticated]

    def destroy(self, request, *args, **kwargs):
        comment = self.get_object()

        # Security Check: Allow deletion ONLY if the user is the author OR is staff/admin
        if comment.user != request.user and not request.user.is_staff:
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
