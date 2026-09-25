import io
from PIL import Image
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.socialnetwork.models import SocialPost, PostMedia, Like, Comment
from apps.socialnetwork.views import (
    UnifiedMediaUploadAPIView,
    PostViewSet,
    UnifiedFeedAPIView,
    MediaViewSet,
)


def create_test_image(name="test.jpg"):
    file = io.BytesIO()
    image = Image.new('RGB', (40, 40), color='blue')
    image.save(file, 'jpeg')
    file.seek(0)
    return SimpleUploadedFile(name, file.getvalue(), content_type='image/jpeg')


def create_test_video(name="test.mp4"):
    return SimpleUploadedFile(name, b'fake video stream content', content_type='video/mp4')


class SocialPostTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Fit Gym", subdomain="fit-gym")
        self.other_tenant = Tenant.objects.create(name="Other Gym", subdomain="other-gym")
        set_current_tenant(self.tenant)

        self.admin = User.objects.create_user(
            email="admin@fitgym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )

        self.client_1 = User.objects.create_user(
            email="client1@fitgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        self.client_2 = User.objects.create_user(
            email="client2@fitgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        self.factory = APIRequestFactory()

    def test_create_text_only_post_via_unified_upload(self):
        view = UnifiedMediaUploadAPIView.as_view()
        request = self.factory.post(
            '/api/v1/socialnetwork/upload-unified',
            {
                'caption': 'Feeling energized after today workout!',
                'location': 'Downtown Fitness',
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['media_type'], 'text')
        self.assertEqual(response.data['caption'], 'Feeling energized after today workout!')
        self.assertEqual(len(response.data['media']), 0)
        self.assertTrue(SocialPost.objects.filter(user=self.client_1, caption='Feeling energized after today workout!').exists())
        post = SocialPost.objects.get(user=self.client_1, caption='Feeling energized after today workout!')
        self.assertEqual(post.media_items.count(), 0)

    def test_create_post_with_multiple_images(self):
        view = UnifiedMediaUploadAPIView.as_view()
        img1 = create_test_image("img1.jpg")
        img2 = create_test_image("img2.jpg")
        request = self.factory.post(
            '/api/v1/socialnetwork/upload-unified',
            {
                'caption': 'Check out my progress photos!',
                'files': [img1, img2],
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['media_type'], 'photo')
        self.assertEqual(len(response.data['media']), 2)
        post = SocialPost.objects.get(id=response.data['id'])
        self.assertEqual(post.media_items.count(), 2)
        media_types = list(post.media_items.values_list('media_type', flat=True))
        self.assertEqual(media_types, ['image', 'image'])

    def test_create_post_with_multiple_videos(self):
        view = UnifiedMediaUploadAPIView.as_view()
        vid1 = create_test_video("vid1.mp4")
        vid2 = create_test_video("vid2.mp4")
        request = self.factory.post(
            '/api/v1/socialnetwork/upload-unified',
            {
                'caption': 'Two workout clips',
                'files': [vid1, vid2],
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['media_type'], 'video')
        self.assertEqual(len(response.data['media']), 2)
        post = SocialPost.objects.get(id=response.data['id'])
        self.assertEqual(post.media_items.count(), 2)
        media_types = list(post.media_items.values_list('media_type', flat=True))
        self.assertEqual(media_types, ['video', 'video'])

    def test_create_post_with_mixed_images_and_videos(self):
        view = UnifiedMediaUploadAPIView.as_view()
        img = create_test_image("mixed1.jpg")
        vid = create_test_video("mixed2.mp4")
        request = self.factory.post(
            '/api/v1/socialnetwork/upload-unified',
            {
                'caption': 'Mixed media session recap',
                'files': [img, vid],
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['media_type'], 'mixed')
        self.assertEqual(len(response.data['media']), 2)
        post = SocialPost.objects.get(id=response.data['id'])
        self.assertEqual(post.media_items.count(), 2)
        types_set = set(post.media_items.values_list('media_type', flat=True))
        self.assertEqual(types_set, {'image', 'video'})

    def test_empty_post_validation(self):
        view = UnifiedMediaUploadAPIView.as_view()
        request = self.factory.post(
            '/api/v1/socialnetwork/upload-unified',
            {
                'caption': '',
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_post_viewset_crud_and_feed(self):
        create_view = PostViewSet.as_view({'post': 'create'})
        req_create = self.factory.post(
            '/api/v1/socialnetwork/posts/',
            {
                'caption': 'Direct PostViewSet creation',
            },
            format='multipart'
        )
        req_create.tenant = self.tenant
        force_authenticate(req_create, user=self.client_1)
        res_create = create_view(req_create)
        self.assertEqual(res_create.status_code, 201)
        post_id = res_create.data['id']

        feed_view = UnifiedFeedAPIView.as_view()
        req_feed = self.factory.get('/api/v1/socialnetwork/feed/')
        req_feed.tenant = self.tenant
        force_authenticate(req_feed, user=self.client_2)
        res_feed = feed_view(req_feed)
        self.assertEqual(res_feed.status_code, 200)
        feed_ids = [item['id'] for item in res_feed.data]
        self.assertIn(post_id, feed_ids)

        like_view = PostViewSet.as_view({'post': 'like'})
        req_like = self.factory.post(f'/api/v1/socialnetwork/posts/{post_id}/like/')
        req_like.tenant = self.tenant
        force_authenticate(req_like, user=self.client_2)
        res_like = like_view(req_like, pk=str(post_id))
        self.assertEqual(res_like.status_code, 201)
        post = SocialPost.objects.get(id=post_id)
        self.assertEqual(post.likes_count, 1)

        unlike_view = PostViewSet.as_view({'post': 'unlike'})
        req_unlike = self.factory.post(f'/api/v1/socialnetwork/posts/{post_id}/unlike/')
        req_unlike.tenant = self.tenant
        force_authenticate(req_unlike, user=self.client_2)
        res_unlike = unlike_view(req_unlike, pk=str(post_id))
        self.assertEqual(res_unlike.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.likes_count, 0)

        comment_view = PostViewSet.as_view({'post': 'comment'})
        req_comment = self.factory.post(
            f'/api/v1/socialnetwork/posts/{post_id}/comment/',
            {'content': 'Awesome work!'},
            format='json'
        )
        req_comment.tenant = self.tenant
        force_authenticate(req_comment, user=self.client_2)
        res_comment = comment_view(req_comment, pk=str(post_id))
        self.assertEqual(res_comment.status_code, 201)
        post.refresh_from_db()
        self.assertEqual(post.comments_count, 1)

        comments_list_view = PostViewSet.as_view({'get': 'comments'})
        req_comments = self.factory.get(f'/api/v1/socialnetwork/posts/{post_id}/comments/')
        req_comments.tenant = self.tenant
        force_authenticate(req_comments, user=self.client_1)
        res_comments = comments_list_view(req_comments, pk=str(post_id))
        self.assertEqual(res_comments.status_code, 200)
        self.assertEqual(len(res_comments.data), 1)

        destroy_view = PostViewSet.as_view({'delete': 'destroy'})
        req_del_unauth = self.factory.delete(f'/api/v1/socialnetwork/posts/{post_id}/')
        req_del_unauth.tenant = self.tenant
        force_authenticate(req_del_unauth, user=self.client_2)
        res_del_unauth = destroy_view(req_del_unauth, pk=str(post_id))
        self.assertEqual(res_del_unauth.status_code, 403)

        req_del_admin = self.factory.delete(f'/api/v1/socialnetwork/posts/{post_id}/')
        req_del_admin.tenant = self.tenant
        force_authenticate(req_del_admin, user=self.admin)
        res_del_admin = destroy_view(req_del_admin, pk=str(post_id))
        self.assertEqual(res_del_admin.status_code, 204)
        self.assertFalse(SocialPost.objects.filter(id=post_id).exists())

    def test_media_viewset_post_like_and_comment(self):
        post = SocialPost.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            caption="Post for media viewset test"
        )
        view = MediaViewSet.as_view({'post': 'like'})
        req = self.factory.post(f'/api/v1/socialnetwork/media/{post.id}/like/?type=post')
        req.tenant = self.tenant
        force_authenticate(req, user=self.client_2)
        res = view(req, pk=str(post.id))
        self.assertEqual(res.status_code, 201)
        post.refresh_from_db()
        self.assertEqual(post.likes_count, 1)

        c_view = MediaViewSet.as_view({'post': 'comment'})
        c_req = self.factory.post(
            f'/api/v1/socialnetwork/media/{post.id}/comment/?type=post',
            {'content': 'Great job via media viewset'},
            format='json'
        )
        c_req.tenant = self.tenant
        force_authenticate(c_req, user=self.client_2)
        c_res = c_view(c_req, pk=str(post.id))
        self.assertEqual(c_res.status_code, 201)
        post.refresh_from_db()
        self.assertEqual(post.comments_count, 1)
