import io
from PIL import Image
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.socialnetwork.models import SocialPost, UserBlock, PostReport, Comment
from apps.socialnetwork.views import (
    UserBlockViewSet,
    UserBlockActionView,
    UserUnblockActionView,
    AdminUserBlockViewSet,
    PostReportViewSet,
    AdminPostReportViewSet,
    PostViewSet,
    UnifiedFeedAPIView,
)


class SocialBlockAndReportTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Apex Gym", subdomain="apex-gym")
        set_current_tenant(self.tenant)

        self.gym_owner = User.objects.create_user(
            email="owner@apexgym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )

        self.client_a = User.objects.create_user(
            email="user_a@apexgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        self.client_b = User.objects.create_user(
            email="user_b@apexgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        self.client_c = User.objects.create_user(
            email="user_c@apexgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        self.factory = APIRequestFactory()

        # Create sample posts
        self.post_a = SocialPost.objects.create(
            user=self.client_a,
            caption="User A workout post",
            tenant=self.tenant,
            visible_to_clients=True,
            visible_to_staff=True
        )

        self.post_b = SocialPost.objects.create(
            user=self.client_b,
            caption="User B meal prep post",
            tenant=self.tenant,
            visible_to_clients=True,
            visible_to_staff=True
        )

    # =========================================================================
    # 1. USER BLOCK CLIENT/STAFF CRUD & INSTAGRAM-STYLE BEHAVIOR
    # =========================================================================

    def test_client_block_user_success(self):
        view = UserBlockViewSet.as_view({'post': 'create'})
        req = self.factory.post(
            '/api/v1/socialnetwork/blocks/',
            {'blocked_user_id': str(self.client_b.id), 'reason': 'Spamming my feed'},
            format='json'
        )
        req.tenant = self.tenant
        force_authenticate(req, user=self.client_a)

        resp = view(req)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['blocked']['id'], str(self.client_b.id))
        self.assertEqual(resp.data['blocker']['id'], str(self.client_a.id))
        self.assertEqual(resp.data['reason'], 'Spamming my feed')
        self.assertTrue(UserBlock.objects.filter(blocker=self.client_a, blocked=self.client_b).exists())

    def test_client_cannot_block_self(self):
        view = UserBlockViewSet.as_view({'post': 'create'})
        req = self.factory.post(
            '/api/v1/socialnetwork/blocks/',
            {'blocked_user_id': str(self.client_a.id)},
            format='json'
        )
        req.tenant = self.tenant
        force_authenticate(req, user=self.client_a)

        resp = view(req)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot block yourself", str(resp.data))

    def test_client_duplicate_block_prevented(self):
        UserBlock.objects.create(
            blocker=self.client_a,
            blocked=self.client_b,
            tenant=self.tenant
        )

        view = UserBlockViewSet.as_view({'post': 'create'})
        req = self.factory.post(
            '/api/v1/socialnetwork/blocks/',
            {'blocked_user_id': str(self.client_b.id)},
            format='json'
        )
        req.tenant = self.tenant
        force_authenticate(req, user=self.client_a)

        resp = view(req)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already blocked", str(resp.data))

    def test_list_and_retrieve_my_blocks(self):
        block = UserBlock.objects.create(
            blocker=self.client_a,
            blocked=self.client_b,
            reason="harassment",
            tenant=self.tenant
        )

        list_view = UserBlockViewSet.as_view({'get': 'list'})
        req = self.factory.get('/api/v1/socialnetwork/blocks/')
        req.tenant = self.tenant
        force_authenticate(req, user=self.client_a)
        resp = list_view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['blocked']['id'], str(self.client_b.id))

        # Client B sees 0 blocked users in their list
        req_b = self.factory.get('/api/v1/socialnetwork/blocks/')
        req_b.tenant = self.tenant
        force_authenticate(req_b, user=self.client_b)
        resp_b = list_view(req_b)
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(len(resp_b.data), 0)

        # Retrieve block detail
        detail_view = UserBlockViewSet.as_view({'get': 'retrieve'})
        req_detail = self.factory.get(f'/api/v1/socialnetwork/blocks/{block.id}/')
        req_detail.tenant = self.tenant
        force_authenticate(req_detail, user=self.client_a)
        resp_detail = detail_view(req_detail, pk=block.id)
        self.assertEqual(resp_detail.status_code, 200)
        self.assertEqual(resp_detail.data['id'], str(block.id))

    def test_unblock_by_id_and_by_user_endpoint(self):
        block = UserBlock.objects.create(
            blocker=self.client_a,
            blocked=self.client_b,
            tenant=self.tenant
        )

        destroy_view = UserBlockViewSet.as_view({'delete': 'destroy'})
        req = self.factory.delete(f'/api/v1/socialnetwork/blocks/{block.id}/')
        req.tenant = self.tenant
        force_authenticate(req, user=self.client_a)
        resp = destroy_view(req, pk=block.id)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(UserBlock.objects.filter(id=block.id).exists())

        # Test unblock_by_user action
        UserBlock.objects.create(blocker=self.client_a, blocked=self.client_b, tenant=self.tenant)
        unblock_action = UserBlockViewSet.as_view({'post': 'unblock_by_user'})
        req_act = self.factory.post(
            '/api/v1/socialnetwork/blocks/unblock/',
            {'blocked_user_id': str(self.client_b.id)},
            format='json'
        )
        req_act.tenant = self.tenant
        force_authenticate(req_act, user=self.client_a)
        resp_act = unblock_action(req_act)
        self.assertEqual(resp_act.status_code, 200)
        self.assertFalse(UserBlock.objects.filter(blocker=self.client_a, blocked=self.client_b).exists())

    def test_direct_block_and_unblock_action_views(self):
        # POST /users/<user_id>/block/
        block_view = UserBlockActionView.as_view()
        req_block = self.factory.post(f'/api/v1/socialnetwork/users/{self.client_b.id}/block/')
        req_block.tenant = self.tenant
        force_authenticate(req_block, user=self.client_a)
        resp_block = block_view(req_block, user_id=self.client_b.id)
        self.assertEqual(resp_block.status_code, 201)
        self.assertTrue(UserBlock.objects.filter(blocker=self.client_a, blocked=self.client_b).exists())

        # DELETE /users/<user_id>/unblock/
        unblock_view = UserUnblockActionView.as_view()
        req_unblock = self.factory.delete(f'/api/v1/socialnetwork/users/{self.client_b.id}/unblock/')
        req_unblock.tenant = self.tenant
        force_authenticate(req_unblock, user=self.client_a)
        resp_unblock = unblock_view(req_unblock, user_id=self.client_b.id)
        self.assertEqual(resp_unblock.status_code, 200)
        self.assertFalse(UserBlock.objects.filter(blocker=self.client_a, blocked=self.client_b).exists())

    def test_instagram_mutual_feed_hiding(self):
        """
        When User A blocks User B:
        - User A's feed should not show User B's posts.
        - User B's feed should not show User A's posts (mutual hiding).
        - User C should see both posts.
        """
        feed_view = UnifiedFeedAPIView.as_view()

        # Before block: User A sees both posts
        req_before = self.factory.get('/api/v1/socialnetwork/feed/')
        req_before.tenant = self.tenant
        force_authenticate(req_before, user=self.client_a)
        resp_before = feed_view(req_before)
        post_ids = [item['id'] for item in resp_before.data]
        self.assertIn(str(self.post_a.id), post_ids)
        self.assertIn(str(self.post_b.id), post_ids)

        # User A blocks User B
        UserBlock.objects.create(blocker=self.client_a, blocked=self.client_b, tenant=self.tenant)

        # After block: User A sees only User A's post, NOT User B's post
        req_a = self.factory.get('/api/v1/socialnetwork/feed/')
        req_a.tenant = self.tenant
        force_authenticate(req_a, user=self.client_a)
        resp_a = feed_view(req_a)
        post_ids_a = [item['id'] for item in resp_a.data]
        self.assertIn(str(self.post_a.id), post_ids_a)
        self.assertNotIn(str(self.post_b.id), post_ids_a)

        # User B sees only User B's post, NOT User A's post (mutual invisibility)
        req_b = self.factory.get('/api/v1/socialnetwork/feed/')
        req_b.tenant = self.tenant
        force_authenticate(req_b, user=self.client_b)
        resp_b = feed_view(req_b)
        post_ids_b = [item['id'] for item in resp_b.data]
        self.assertNotIn(str(self.post_a.id), post_ids_b)
        self.assertIn(str(self.post_b.id), post_ids_b)

        # User C sees both posts
        req_c = self.factory.get('/api/v1/socialnetwork/feed/')
        req_c.tenant = self.tenant
        force_authenticate(req_c, user=self.client_c)
        resp_c = feed_view(req_c)
        post_ids_c = [item['id'] for item in resp_c.data]
        self.assertIn(str(self.post_a.id), post_ids_c)
        self.assertIn(str(self.post_b.id), post_ids_c)

    def test_blocked_user_cannot_access_post_details_or_interact(self):
        UserBlock.objects.create(blocker=self.client_a, blocked=self.client_b, tenant=self.tenant)

        post_viewset = PostViewSet.as_view({
            'get': 'retrieve',
            'post': 'like'
        })

        # User B tries to retrieve User A's post
        req_ret = self.factory.get(f'/api/v1/socialnetwork/posts/{self.post_a.id}/')
        req_ret.tenant = self.tenant
        force_authenticate(req_ret, user=self.client_b)
        resp_ret = post_viewset(req_ret, pk=self.post_a.id)
        self.assertEqual(resp_ret.status_code, 404)

        # User B tries to like User A's post
        req_like = self.factory.post(f'/api/v1/socialnetwork/posts/{self.post_a.id}/like/')
        req_like.tenant = self.tenant
        force_authenticate(req_like, user=self.client_b)
        resp_like = post_viewset(req_like, pk=self.post_a.id)
        self.assertEqual(resp_like.status_code, 404)

    # =========================================================================
    # 2. ADMIN USER BLOCK CRUD
    # =========================================================================

    def test_admin_user_block_crud(self):
        # Client cannot access admin blocks endpoint
        admin_list = AdminUserBlockViewSet.as_view({'get': 'list'})
        req_client = self.factory.get('/api/v1/socialnetwork/admin/blocks/')
        req_client.tenant = self.tenant
        force_authenticate(req_client, user=self.client_a)
        resp_forbidden = admin_list(req_client)
        self.assertEqual(resp_forbidden.status_code, 403)

        # Gym owner creates block between client_a and client_b
        admin_create = AdminUserBlockViewSet.as_view({'post': 'create'})
        req_create = self.factory.post(
            '/api/v1/socialnetwork/admin/blocks/',
            {
                'blocker_id': str(self.client_a.id),
                'blocked_user_id': str(self.client_b.id),
                'reason': 'Admin moderation restriction'
            },
            format='json'
        )
        req_create.tenant = self.tenant
        force_authenticate(req_create, user=self.gym_owner)
        resp_create = admin_create(req_create)
        self.assertEqual(resp_create.status_code, 201)
        block_id = resp_create.data['id']

        # Gym owner lists blocks
        req_admin_list = self.factory.get('/api/v1/socialnetwork/admin/blocks/')
        req_admin_list.tenant = self.tenant
        force_authenticate(req_admin_list, user=self.gym_owner)
        resp_list = admin_list(req_admin_list)
        self.assertEqual(resp_list.status_code, 200)
        self.assertEqual(len(resp_list.data), 1)

        # Gym owner updates block reason
        admin_update = AdminUserBlockViewSet.as_view({'patch': 'partial_update'})
        req_patch = self.factory.patch(
            f'/api/v1/socialnetwork/admin/blocks/{block_id}/',
            {'reason': 'Updated moderation note'},
            format='json'
        )
        req_patch.tenant = self.tenant
        force_authenticate(req_patch, user=self.gym_owner)
        resp_patch = admin_update(req_patch, pk=block_id)
        self.assertEqual(resp_patch.status_code, 200)
        self.assertEqual(resp_patch.data['reason'], 'Updated moderation note')

        # Gym owner deletes block
        admin_destroy = AdminUserBlockViewSet.as_view({'delete': 'destroy'})
        req_del = self.factory.delete(f'/api/v1/socialnetwork/admin/blocks/{block_id}/')
        req_del.tenant = self.tenant
        force_authenticate(req_del, user=self.gym_owner)
        resp_del = admin_destroy(req_del, pk=block_id)
        self.assertEqual(resp_del.status_code, 200)
        self.assertFalse(UserBlock.objects.filter(id=block_id).exists())

    # =========================================================================
    # 3. POST REPORT CLIENT/STAFF CRUD
    # =========================================================================

    def test_client_report_post_crud(self):
        # Client B reports Client A's post
        report_create = PostReportViewSet.as_view({'post': 'create'})
        req_create = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {
                'post_id': str(self.post_a.id),
                'reason': 'spam',
                'description': 'Repeated promotional messages'
            },
            format='json'
        )
        req_create.tenant = self.tenant
        force_authenticate(req_create, user=self.client_b)
        resp_create = report_create(req_create)
        self.assertEqual(resp_create.status_code, 201)
        report_id = resp_create.data['id']
        self.assertEqual(resp_create.data['reason'], 'spam')
        self.assertEqual(resp_create.data['status'], 'pending')
        self.assertEqual(resp_create.data['reported_user']['id'], str(self.client_a.id))

        # Client A cannot report their own post
        req_self = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {'post_id': str(self.post_a.id), 'reason': 'spam'},
            format='json'
        )
        req_self.tenant = self.tenant
        force_authenticate(req_self, user=self.client_a)
        resp_self = report_create(req_self)
        self.assertEqual(resp_self.status_code, 400)
        self.assertIn("cannot report your own post", str(resp_self.data))

        # Duplicate pending report prevented
        req_dup = self.factory.post(
            '/api/v1/socialnetwork/reports/',
            {'post_id': str(self.post_a.id), 'reason': 'harassment'},
            format='json'
        )
        req_dup.tenant = self.tenant
        force_authenticate(req_dup, user=self.client_b)
        resp_dup = report_create(req_dup)
        self.assertEqual(resp_dup.status_code, 400)
        self.assertIn("already reported", str(resp_dup.data))

        # Client B lists their reports
        report_list = PostReportViewSet.as_view({'get': 'list'})
        req_list = self.factory.get('/api/v1/socialnetwork/reports/')
        req_list.tenant = self.tenant
        force_authenticate(req_list, user=self.client_b)
        resp_list = report_list(req_list)
        self.assertEqual(resp_list.status_code, 200)
        self.assertEqual(len(resp_list.data), 1)

        # Client B retrieves their report
        report_detail = PostReportViewSet.as_view({'get': 'retrieve'})
        req_ret = self.factory.get(f'/api/v1/socialnetwork/reports/{report_id}/')
        req_ret.tenant = self.tenant
        force_authenticate(req_ret, user=self.client_b)
        resp_ret = report_detail(req_ret, pk=report_id)
        self.assertEqual(resp_ret.status_code, 200)
        self.assertEqual(resp_ret.data['id'], str(report_id))

        # Client B updates their pending report
        report_update = PostReportViewSet.as_view({'patch': 'partial_update'})
        req_update = self.factory.patch(
            f'/api/v1/socialnetwork/reports/{report_id}/',
            {'description': 'Updated detailed description of the spam'},
            format='json'
        )
        req_update.tenant = self.tenant
        force_authenticate(req_update, user=self.client_b)
        resp_update = report_update(req_update, pk=report_id)
        self.assertEqual(resp_update.status_code, 200)
        self.assertEqual(resp_update.data['description'], 'Updated detailed description of the spam')

        # Client B withdraws/deletes their pending report
        report_del = PostReportViewSet.as_view({'delete': 'destroy'})
        req_del = self.factory.delete(f'/api/v1/socialnetwork/reports/{report_id}/')
        req_del.tenant = self.tenant
        force_authenticate(req_del, user=self.client_b)
        resp_del = report_del(req_del, pk=report_id)
        self.assertEqual(resp_del.status_code, 200)
        self.assertFalse(PostReport.objects.filter(id=report_id).exists())

    def test_direct_post_action_report(self):
        """Test POST /api/v1/socialnetwork/posts/<id>/report/ action on PostViewSet"""
        post_view = PostViewSet.as_view({'post': 'report'})
        req = self.factory.post(
            f'/api/v1/socialnetwork/posts/{self.post_a.id}/report/',
            {'reason': 'inappropriate', 'description': 'Offensive language'},
            format='json'
        )
        req.tenant = self.tenant
        force_authenticate(req, user=self.client_c)
        resp = post_view(req, pk=self.post_a.id)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(PostReport.objects.filter(reporter=self.client_c, post=self.post_a).exists())

    # =========================================================================
    # 4. ADMIN POST REPORT CRUD & MODERATION ACTIONS
    # =========================================================================

    def test_admin_post_report_crud_and_actions(self):
        # Create report from Client B on Post A
        report = PostReport.objects.create(
            reporter=self.client_b,
            reported_user=self.client_a,
            post=self.post_a,
            reason=PostReport.ReportReason.HARASSMENT,
            description="Aggressive comments",
            tenant=self.tenant
        )

        admin_list = AdminPostReportViewSet.as_view({'get': 'list'})
        admin_detail = AdminPostReportViewSet.as_view({'get': 'retrieve'})
        admin_patch = AdminPostReportViewSet.as_view({'patch': 'partial_update'})
        admin_action = AdminPostReportViewSet.as_view({'post': 'take_action'})

        # Regular client cannot access admin reports
        req_client = self.factory.get('/api/v1/socialnetwork/admin/reports/')
        req_client.tenant = self.tenant
        force_authenticate(req_client, user=self.client_a)
        self.assertEqual(admin_list(req_client).status_code, 403)

        # Admin lists reports
        req_admin = self.factory.get('/api/v1/socialnetwork/admin/reports/?status=pending')
        req_admin.tenant = self.tenant
        force_authenticate(req_admin, user=self.gym_owner)
        resp_list = admin_list(req_admin)
        self.assertEqual(resp_list.status_code, 200)
        self.assertEqual(len(resp_list.data), 1)

        # Admin retrieves report detail
        req_ret = self.factory.get(f'/api/v1/socialnetwork/admin/reports/{report.id}/')
        req_ret.tenant = self.tenant
        force_authenticate(req_ret, user=self.gym_owner)
        resp_ret = admin_detail(req_ret, pk=report.id)
        self.assertEqual(resp_ret.status_code, 200)
        self.assertEqual(resp_ret.data['id'], str(report.id))
        self.assertIn('post_snapshot', resp_ret.data)

        # Admin updates status and notes
        req_update = self.factory.patch(
            f'/api/v1/socialnetwork/admin/reports/{report.id}/',
            {'status': 'under_review', 'admin_notes': 'Reviewing content with staff'},
            format='json'
        )
        req_update.tenant = self.tenant
        force_authenticate(req_update, user=self.gym_owner)
        resp_update = admin_patch(req_update, pk=report.id)
        self.assertEqual(resp_update.status_code, 200)
        self.assertEqual(resp_update.data['status'], 'under_review')

        # Admin takes action: 'hide_post'
        req_hide = self.factory.post(
            f'/api/v1/socialnetwork/admin/reports/{report.id}/action/',
            {'action': 'hide_post', 'admin_notes': 'Post violated gym community guidelines'},
            format='json'
        )
        req_hide.tenant = self.tenant
        force_authenticate(req_hide, user=self.gym_owner)
        resp_hide = admin_action(req_hide, pk=report.id)
        self.assertEqual(resp_hide.status_code, 200)
        self.assertEqual(resp_hide.data['status'], 'resolved')
        self.assertEqual(resp_hide.data['action_taken'], 'post_hidden')
        self.post_a.refresh_from_db()
        self.assertFalse(self.post_a.visible_to_clients)

        # Admin takes action: 'delete_post' on a new report
        report2 = PostReport.objects.create(
            reporter=self.client_c,
            reported_user=self.client_b,
            post=self.post_b,
            reason=PostReport.ReportReason.SPAM,
            tenant=self.tenant
        )
        req_delete_action = self.factory.post(
            f'/api/v1/socialnetwork/admin/reports/{report2.id}/action/',
            {'action': 'delete_post', 'admin_notes': 'Confirmed severe spam, deleted'},
            format='json'
        )
        req_delete_action.tenant = self.tenant
        force_authenticate(req_delete_action, user=self.gym_owner)
        resp_del_act = admin_action(req_delete_action, pk=report2.id)
        self.assertEqual(resp_del_act.status_code, 200)
        self.assertEqual(resp_del_act.data['status'], 'resolved')
        self.assertEqual(resp_del_act.data['action_taken'], 'post_deleted')
        self.assertFalse(SocialPost.objects.filter(id=self.post_b.id).exists())
        # Audit snapshot is preserved on the report
        report2.refresh_from_db()
        self.assertIsNone(report2.post)
        self.assertTrue(bool(report2.post_snapshot))
        self.assertEqual(report2.post_snapshot.get('caption'), "User B meal prep post")
