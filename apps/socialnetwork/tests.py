import io
import json
from PIL import Image
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.socialnetwork.models import Photo, Video, Poll, PollOption, SocialPost, PostReport
from apps.socialnetwork.views import (
    PollCreateAPIView,
    PollAPIView,
    MediaViewSet,
    MultiMediaUploadAPIView,
    UnifiedMediaUploadAPIView,
    PostReportViewSet,
    AdminPostReportViewSet,
)
from apps.notifications.models import NotificationInbox


def create_test_image(name="test.jpg"):
    file = io.BytesIO()
    image = Image.new('RGB', (50, 50), color='red')
    image.save(file, 'jpeg')
    file.seek(0)
    return SimpleUploadedFile(name, file.getvalue(), content_type='image/jpeg')


class SocialNetworkPermissionsTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Fit Gym", subdomain="fit-gym")
        self.tenant_b = Tenant.objects.create(name="Other Gym", subdomain="other-gym")
        set_current_tenant(self.tenant)

        # Admin (Gym Owner)
        self.admin_user = User.objects.create_user(
            email="admin@fitgym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )

        # Staff (Trainer)
        self.staff_user = User.objects.create_user(
            email="trainer@fitgym.com",
            password="password123",
            role=UserRole.TRAINER,
            tenant=self.tenant
        )

        # Client 1
        self.client_1 = User.objects.create_user(
            email="client1@fitgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        # Client 2
        self.client_2 = User.objects.create_user(
            email="client2@fitgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        # Admin from another gym
        self.other_admin = User.objects.create_user(
            email="admin@othergym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant_b
        )

        self.factory = APIRequestFactory()

    def test_client_can_create_poll_via_upload_poll_api(self):
        """Verify clients can create polls via PollCreateAPIView (/upload-poll)."""
        view = PollCreateAPIView.as_view()
        request = self.factory.post(
            '/api/v1/socialnetwork/upload-poll',
            {
                'question': 'What is your favorite workout time?',
                'options': json.dumps([{'text': 'Morning'}, {'text': 'Evening'}]),
                'is_multiple_choice': 'false'
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Poll.objects.filter(user=self.client_1).count(), 1)
        created_poll = Poll.objects.get(user=self.client_1)
        self.assertEqual(created_poll.question, 'What is your favorite workout time?')

    def test_client_can_create_poll_via_multimedia_upload_api(self):
        """Verify clients can create polls via MultiMediaUploadAPIView (/upload with media_type=poll)."""
        view = MultiMediaUploadAPIView.as_view()
        request = self.factory.post(
            '/api/v1/socialnetwork/upload',
            {
                'media_type': 'poll',
                'question': 'Best recovery meal?',
                'options': json.dumps([{'text': 'Protein Shake'}, {'text': 'Chicken & Rice'}]),
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Poll.objects.filter(user=self.client_1, question='Best recovery meal?').exists())

    def test_client_can_create_photo_post(self):
        """Verify clients can upload photo posts via MultiMediaUploadAPIView."""
        view = MultiMediaUploadAPIView.as_view()
        img = create_test_image("client_post.jpg")
        request = self.factory.post(
            '/api/v1/socialnetwork/upload',
            {
                'media_type': 'photo',
                'caption': 'Leg day complete!',
                'image': img
            },
            format='multipart'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Photo.objects.filter(user=self.client_1, caption='Leg day complete!').exists())

    def test_client_can_delete_own_poll(self):
        """Verify a client can delete their own poll via PollAPIView and MediaViewSet."""
        poll = Poll.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            question="Client's poll to delete"
        )
        view = PollAPIView.as_view({'delete': 'destroy'})
        request = self.factory.delete(f'/api/v1/socialnetwork/polls/{poll.id}/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request, pk=str(poll.id))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Poll.objects.filter(id=poll.id).exists())

    def test_client_can_delete_own_photo_via_media_viewset(self):
        """Verify a client can delete their own photo via MediaViewSet."""
        photo = Photo.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            caption="Client's photo to delete",
            image="photos/test.jpg"
        )
        view = MediaViewSet.as_view({'delete': 'destroy'})
        request = self.factory.delete(f'/api/v1/socialnetwork/media/{photo.id}/?type=photo')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)

        response = view(request, pk=str(photo.id))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Photo.objects.filter(id=photo.id).exists())

    def test_client_cannot_delete_another_users_content(self):
        """Verify Client 2 CANNOT delete Client 1's or Staff's content."""
        poll = Poll.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            question="Client 1's poll"
        )
        photo = Photo.objects.create(
            tenant=self.tenant,
            user=self.staff_user,
            caption="Trainer photo",
            image="photos/staff.jpg"
        )

        # Client 2 tries to delete Client 1's poll
        view_poll = PollAPIView.as_view({'delete': 'destroy'})
        req_poll = self.factory.delete(f'/api/v1/socialnetwork/polls/{poll.id}/')
        req_poll.tenant = self.tenant
        force_authenticate(req_poll, user=self.client_2)

        res_poll = view_poll(req_poll, pk=str(poll.id))
        self.assertEqual(res_poll.status_code, 403)
        self.assertTrue(Poll.objects.filter(id=poll.id).exists())

        # Client 2 tries to delete Trainer's photo
        view_media = MediaViewSet.as_view({'delete': 'destroy'})
        req_photo = self.factory.delete(f'/api/v1/socialnetwork/media/{photo.id}/?type=photo')
        req_photo.tenant = self.tenant
        force_authenticate(req_photo, user=self.client_2)

        res_photo = view_media(req_photo, pk=str(photo.id))
        self.assertEqual(res_photo.status_code, 403)
        self.assertTrue(Photo.objects.filter(id=photo.id).exists())

    def test_admin_can_delete_content_created_by_staff_and_clients(self):
        """Verify Gym Admin has moderation access and can delete both Staff and Client posts."""
        client_poll = Poll.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            question="Inappropriate poll"
        )
        staff_photo = Photo.objects.create(
            tenant=self.tenant,
            user=self.staff_user,
            caption="Staff post to moderate",
            image="photos/staff.jpg"
        )

        # Admin deletes client poll
        view_poll = PollAPIView.as_view({'delete': 'destroy'})
        req1 = self.factory.delete(f'/api/v1/socialnetwork/polls/{client_poll.id}/')
        req1.tenant = self.tenant
        force_authenticate(req1, user=self.admin_user)
        res1 = view_poll(req1, pk=str(client_poll.id))
        self.assertEqual(res1.status_code, 204)
        self.assertFalse(Poll.objects.filter(id=client_poll.id).exists())

        # Admin deletes staff photo
        view_media = MediaViewSet.as_view({'delete': 'destroy'})
        req2 = self.factory.delete(f'/api/v1/socialnetwork/media/{staff_photo.id}/?type=photo')
        req2.tenant = self.tenant
        force_authenticate(req2, user=self.admin_user)
        res2 = view_media(req2, pk=str(staff_photo.id))
        self.assertEqual(res2.status_code, 204)
        self.assertFalse(Photo.objects.filter(id=staff_photo.id).exists())

    def test_other_gym_admin_cannot_delete_content_cross_tenant(self):
        """Verify Admin of Gym B cannot delete content from Gym A."""
        poll = Poll.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            question="FitGym exclusive poll"
        )
        view = PollAPIView.as_view({'delete': 'destroy'})
        request = self.factory.delete(f'/api/v1/socialnetwork/polls/{poll.id}/')
        # Setting tenant to tenant_b as would happen for requests from other gym
        request.tenant = self.tenant_b
        set_current_tenant(self.tenant_b)
        force_authenticate(request, user=self.other_admin)

        # Tenant isolation will raise 404 (object doesn't exist for tenant_b) or 403
        response = view(request, pk=str(poll.id))
        self.assertIn(response.status_code, [403, 404])
        
        # Reset tenant to self.tenant and verify poll is still intact
        set_current_tenant(self.tenant)
        self.assertTrue(Poll.objects.filter(id=poll.id).exists())


class PostReportAndWarningTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Fit Gym", subdomain="fit-gym")
        set_current_tenant(self.tenant)

        # Admin (Gym Owner)
        self.admin_user = User.objects.create_user(
            email="admin@fitgym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )

        # Author of content (Client 1)
        self.client_1 = User.objects.create_user(
            email="author@fitgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        # Reporter (Client 2)
        self.client_2 = User.objects.create_user(
            email="reporter@fitgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        self.factory = APIRequestFactory()

        # Create sample Poll authored by client_1
        self.poll = Poll.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            question="Is this poll offensive?"
        )
        opt1 = PollOption.objects.create(text="Yes")
        opt2 = PollOption.objects.create(text="No")
        self.poll.options.add(opt1, opt2)

        # Create sample SocialPost authored by client_1
        self.social_post = SocialPost.objects.create(
            tenant=self.tenant,
            user=self.client_1,
            caption="Offensive social post caption"
        )

    def test_report_poll_with_post_id(self):
        """Verify user can report a poll by passing Poll ID in post_id field."""
        view = PostReportViewSet.as_view({'post': 'create'})
        request = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {
                'post_id': str(self.poll.id),
                'reason': 'inappropriate',
                'description': 'This poll violates community guidelines.'
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_2)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['content_type'], 'poll')
        self.assertIsNotNone(response.data.get('poll'))
        self.assertEqual(response.data['poll']['id'], str(self.poll.id))
        self.assertEqual(response.data['reported_user']['id'], str(self.client_1.id))

        report = PostReport.objects.get(id=response.data['id'])
        self.assertEqual(report.poll, self.poll)
        self.assertEqual(report.reported_user, self.client_1)
        self.assertEqual(report.post_snapshot.get('type'), 'poll')
        self.assertEqual(report.post_snapshot.get('question'), self.poll.question)

    def test_report_poll_with_poll_id(self):
        """Verify user can report a poll by passing Poll ID separately in poll_id field."""
        view = PostReportViewSet.as_view({'post': 'create'})
        request = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {
                'poll_id': str(self.poll.id),
                'reason': 'spam',
                'description': 'Spam poll content.'
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_2)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['content_type'], 'poll')
        self.assertEqual(response.data['poll']['id'], str(self.poll.id))

        report = PostReport.objects.get(id=response.data['id'])
        self.assertEqual(report.poll, self.poll)
        self.assertEqual(report.reported_user, self.client_1)

    def test_report_poll_with_poll_alias(self):
        """Verify user can report a poll by passing Poll ID in 'poll' parameter."""
        view = PostReportViewSet.as_view({'post': 'create'})
        request = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {
                'poll': str(self.poll.id),
                'reason': 'harassment',
                'description': 'Harassing poll.'
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_2)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['content_type'], 'poll')

    def test_report_poll_cannot_report_own_poll(self):
        """Verify user cannot report their own poll."""
        view = PostReportViewSet.as_view({'post': 'create'})
        request = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {
                'poll_id': str(self.poll.id),
                'reason': 'inappropriate',
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_1)  # Author reporting own poll

        response = view(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn('poll_id', response.data.get('details', response.data))

    def test_report_poll_duplicate_rejected(self):
        """Verify duplicate active report on the same poll is rejected."""
        PostReport.objects.create(
            tenant=self.tenant,
            reporter=self.client_2,
            reported_user=self.client_1,
            poll=self.poll,
            status=PostReport.ReportStatus.PENDING,
        )

        view = PostReportViewSet.as_view({'post': 'create'})
        request = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {
                'poll_id': str(self.poll.id),
                'reason': 'spam',
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_2)

        response = view(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn("already reported this poll", str(response.data))

    def test_report_nonexistent_returns_appropriate_error(self):
        """Verify appropriate validation error when reporting nonexistent post/poll ID."""
        import uuid
        view = PostReportViewSet.as_view({'post': 'create'})
        request = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {
                'post_id': str(uuid.uuid4()),
                'reason': 'spam',
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client_2)

        response = view(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn('post_id', response.data.get('details', response.data))

    def test_admin_warn_user_on_reported_post_delivers_notification(self):
        """Verify Admin issuing warning on reported post generates NotificationInbox for reported member."""
        report = PostReport.objects.create(
            tenant=self.tenant,
            reporter=self.client_2,
            reported_user=self.client_1,
            post=self.social_post,
            reason=PostReport.ReportReason.INAPPROPRIATE,
            description="Violates community standards."
        )

        admin_notes = "Please review our terms. Repeated violations may result in account suspension."
        view = AdminPostReportViewSet.as_view({'post': 'take_action'})
        request = self.factory.post(
            f'/api/v1/socialnetwork/admin/reports/{report.id}/action/',
            {
                'action': 'warn_user',
                'admin_notes': admin_notes,
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin_user)

        response = view(request, pk=str(report.id))
        self.assertEqual(response.status_code, 200)

        report.refresh_from_db()
        self.assertEqual(report.status, PostReport.ReportStatus.RESOLVED)
        self.assertEqual(report.action_taken, PostReport.ReportAction.USER_WARNED)
        self.assertIn(admin_notes, report.admin_notes)

        # Verify NotificationInbox item was created for the reported user
        inbox_items = NotificationInbox.all_objects.filter(
            recipient=self.client_1,
            tenant=self.tenant
        )
        self.assertTrue(inbox_items.exists())
        warning_item = inbox_items.latest('created_at')
        self.assertEqual(warning_item.title, "Community Guidelines Warning")
        self.assertIn("inappropriate", warning_item.body.lower())
        self.assertIn(admin_notes, warning_item.body)
        self.assertEqual(warning_item.priority, "HIGH")
        self.assertEqual(warning_item.notification_type, "SYSTEM")
        self.assertEqual(warning_item.delivery_policy, "PUSH_AND_EMAIL")
        self.assertEqual(str(warning_item.action_payload.get('report_id')), str(report.id))

    def test_admin_warn_user_on_reported_poll_delivers_notification(self):
        """Verify Admin issuing warning on reported poll generates NotificationInbox for reported member."""
        report = PostReport.objects.create(
            tenant=self.tenant,
            reporter=self.client_2,
            reported_user=self.client_1,
            poll=self.poll,
            reason=PostReport.ReportReason.HARASSMENT,
            description="Harassing question in poll."
        )

        admin_notes = "Poll removed. Final warning before temporary ban."
        view = AdminPostReportViewSet.as_view({'post': 'take_action'})
        request = self.factory.post(
            f'/api/v1/socialnetwork/admin/reports/{report.id}/action/',
            {
                'action': 'warn_user',
                'admin_notes': admin_notes,
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin_user)

        response = view(request, pk=str(report.id))
        self.assertEqual(response.status_code, 200)

        report.refresh_from_db()
        self.assertEqual(report.status, PostReport.ReportStatus.RESOLVED)
        self.assertEqual(report.action_taken, PostReport.ReportAction.USER_WARNED)

        # Verify notification delivered to reported member
        inbox_items = NotificationInbox.all_objects.filter(
            recipient=self.client_1,
            tenant=self.tenant
        )
        self.assertTrue(inbox_items.exists())
        warning_item = inbox_items.latest('created_at')
        self.assertEqual(warning_item.title, "Community Guidelines Warning")
        self.assertIn("poll", warning_item.body.lower())
        self.assertIn("harassment", warning_item.body.lower())
        self.assertEqual(str(warning_item.action_payload.get('poll_id')), str(self.poll.id))

    def test_admin_delete_reported_poll(self):
        """Verify Admin can delete a reported poll through moderation action."""
        report = PostReport.objects.create(
            tenant=self.tenant,
            reporter=self.client_2,
            reported_user=self.client_1,
            poll=self.poll,
            reason=PostReport.ReportReason.SPAM
        )

        view = AdminPostReportViewSet.as_view({'post': 'take_action'})
        request = self.factory.post(
            f'/api/v1/socialnetwork/admin/reports/{report.id}/action/',
            {
                'action': 'delete_poll',
                'admin_notes': 'Spam poll deleted.'
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin_user)

        response = view(request, pk=str(report.id))
        self.assertEqual(response.status_code, 200)

        report.refresh_from_db()
        self.assertEqual(report.status, PostReport.ReportStatus.RESOLVED)
        self.assertEqual(report.action_taken, PostReport.ReportAction.POST_DELETED)
        self.assertFalse(Poll.objects.filter(id=self.poll.id).exists())

    def test_admin_hide_reported_poll(self):
        """Verify Admin can hide a reported poll through moderation action."""
        report = PostReport.objects.create(
            tenant=self.tenant,
            reporter=self.client_2,
            reported_user=self.client_1,
            poll=self.poll,
            reason=PostReport.ReportReason.INAPPROPRIATE
        )

        view = AdminPostReportViewSet.as_view({'post': 'take_action'})
        request = self.factory.post(
            f'/api/v1/socialnetwork/admin/reports/{report.id}/action/',
            {
                'action': 'hide_poll',
                'admin_notes': 'Poll hidden pending investigation.'
            },
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin_user)

        response = view(request, pk=str(report.id))
        self.assertEqual(response.status_code, 200)

        report.refresh_from_db()
        self.assertEqual(report.status, PostReport.ReportStatus.RESOLVED)
        self.assertEqual(report.action_taken, PostReport.ReportAction.POST_HIDDEN)

        self.poll.refresh_from_db()
        self.assertFalse(self.poll.visible_to_clients)
        self.assertFalse(self.poll.visible_to_staff)
