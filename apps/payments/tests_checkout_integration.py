import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.tenants.models import Tenant
from apps.payments.checkout_service import CheckoutIntegrationService
from apps.payments.models import (
    BillingFeature,
    BillingPlan,
    CheckoutVerificationChallenge,
    DEFAULT_HEALTH_COMMITMENT_BODY,
    DEFAULT_WELCOME_EMAIL_SUBJECT,
    ExternalCheckoutSession,
    HealthCommitmentSignature,
    PlatformLedger,
    StripeProcessedEvent,
    TenantBillingSubscription,
    TenantCheckoutConfig,
)
from apps.payments.tasks import (
    purge_old_checkout_tracking_data_task,
    send_checkout_welcome_email_task,
)
from apps.scheduling.models import Location, Package, PackageType, Payment
from apps.users.models import EmailOTP, OTPPurpose, TenantCustomerAccess, User, UserRole
from apps.users.services import AuthService


class CheckoutIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.tenant_a = Tenant.objects.create(name='Alpha Gym', subdomain='alpha', is_active=True)
        self.tenant_b = Tenant.objects.create(name='Beta Fitness', subdomain='beta', is_active=True)

        self.location_a = Location.all_objects.create(tenant=self.tenant_a, name='Downtown', address='123 Main St')
        self.location_b = Location.all_objects.create(tenant=self.tenant_b, name='Uptown', address='456 Elm St')

        self.package_type_a = PackageType.all_objects.create(
            tenant=self.tenant_a,
            location=self.location_a,
            name='Alpha Unlimited',
            price=Decimal('120.00'),
            credit_count=30,
            validity_days=30,
            billing_cycle='monthly',
            is_active=True,
            stripe_price_id='price_test_alpha_120'
        )

        self.package_type_b = PackageType.all_objects.create(
            tenant=self.tenant_b,
            location=self.location_b,
            name='Beta Unlimited',
            price=Decimal('150.00'),
            credit_count=20,
            validity_days=30,
            billing_cycle='monthly',
            is_active=True,
            stripe_price_id='price_test_beta_150'
        )

        self.owner_a = User.objects.create_user(
            email='owner@alphagym.com',
            password='Password123!',
            tenant=self.tenant_a,
            role=UserRole.GYM_OWNER
        )

        self.client_user = User.objects.create_user(
            email='member@example.com',
            password='Password123!',
            tenant=self.tenant_a,
            role=UserRole.CLIENT
        )

    def test_tenant_customer_access_multi_gym_login(self):
        login_url = '/api/v1/users/auth/login/'
        res_denied = self.client.post(login_url, {
            'email': 'member@example.com',
            'password': 'Password123!',
            'tenant_id': str(self.tenant_b.id)
        })
        self.assertEqual(res_denied.status_code, 400)

        TenantCustomerAccess.objects.create(
            user=self.client_user,
            tenant=self.tenant_b,
            role=UserRole.CLIENT,
            is_active=True
        )

        res_allowed = self.client.post(login_url, {
            'email': 'member@example.com',
            'password': 'Password123!',
            'tenant_id': str(self.tenant_b.id)
        })
        self.assertEqual(res_allowed.status_code, 200)
        self.assertEqual(res_allowed.data['user']['tenant_id'], str(self.tenant_b.id))

    def test_auth_service_otp_max_attempts_lockout(self):
        code = AuthService.create_email_otp('lockout@example.com', OTPPurpose.REGISTRATION)
        for _ in range(5):
            with self.assertRaises(Exception):
                AuthService.verify_email_otp('lockout@example.com', '000000', OTPPurpose.REGISTRATION)

        with self.assertRaises(Exception):
            AuthService.verify_email_otp('lockout@example.com', code, OTPPurpose.REGISTRATION)

    def test_get_public_gym_info_disabled_and_enabled(self):
        res_disabled = self.client.get('/api/v1/checkout/gym/alpha/')
        self.assertEqual(res_disabled.status_code, 404)

        config, _ = TenantCheckoutConfig.objects.get_or_create(tenant=self.tenant_a)
        config.is_enabled = True
        config.theme_primary_color = '#FF5500'
        config.save()

        res_subdomain = self.client.get('/api/v1/checkout/gym/alpha/')
        self.assertEqual(res_subdomain.status_code, 200)
        self.assertEqual(res_subdomain.data['name'], 'Alpha Gym')
        self.assertEqual(res_subdomain.data['checkout_settings']['theme_primary_color'], '#FF5500')

        res_uuid = self.client.get(f'/api/v1/checkout/gym/{self.tenant_a.id}/')
        self.assertEqual(res_uuid.status_code, 200)
        self.assertEqual(res_uuid.data['name'], 'Alpha Gym')

        res_unknown = self.client.get('/api/v1/checkout/gym/nonexistent/')
        self.assertEqual(res_unknown.status_code, 404)

    def test_get_public_gym_packages_isolation(self):
        TenantCheckoutConfig.objects.create(tenant=self.tenant_a, is_enabled=True)
        TenantCheckoutConfig.objects.create(tenant=self.tenant_b, is_enabled=True)

        res_a = self.client.get('/api/v1/checkout/gym/alpha/packages/')
        self.assertEqual(res_a.status_code, 200)
        package_ids_a = [p['id'] for p in res_a.data['packages']]
        self.assertIn(str(self.package_type_a.id), package_ids_a)
        self.assertNotIn(str(self.package_type_b.id), package_ids_a)

    def test_create_checkout_session_and_otp_flow(self):
        TenantCheckoutConfig.objects.create(tenant=self.tenant_a, is_enabled=True)

        res_invalid_pkg = self.client.post('/api/v1/checkout/sessions/', {
            'tenant_id': str(self.tenant_a.id),
            'package_type_id': str(self.package_type_b.id),
            'email': 'buyer@example.com'
        })
        self.assertEqual(res_invalid_pkg.status_code, 400)

        res_create = self.client.post('/api/v1/checkout/sessions/', {
            'tenant_id': 'alpha',
            'package_type_id': str(self.package_type_a.id),
            'email': 'buyer@example.com',
            'first_name': 'BuyerFirst',
            'last_name': 'BuyerLast'
        })
        self.assertEqual(res_create.status_code, 201)
        session_id = res_create.data['session_id']
        session_token = res_create.data['session_token']
        self.assertEqual(res_create.data['status'], 'PENDING_VERIFICATION')
        self.assertFalse(res_create.data['is_verified'])

        session = ExternalCheckoutSession.all_objects.get(id=session_id)
        expected_hash = hashlib.sha256(session_token.encode('utf-8')).hexdigest()
        self.assertEqual(session.token_hash, expected_hash)
        self.assertEqual(session.price_snapshot, Decimal('120.00'))

        res_cooldown = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/otp/resend/',
            headers={'X-Checkout-Token': session_token}
        )
        self.assertEqual(res_cooldown.status_code, 400)

        for _ in range(5):
            res_wrong = self.client.post(
                f'/api/v1/checkout/sessions/{session_id}/otp/verify/',
                {'otp_code': '000000'},
                headers={'X-Checkout-Token': session_token}
            )
            self.assertEqual(res_wrong.status_code, 400)

        session.refresh_from_db()
        self.assertEqual(session.otp_attempts, 5)

        test_code = '123456'
        session.otp_hash = hashlib.sha256(test_code.encode('utf-8')).hexdigest()
        session.otp_attempts = 0
        session.otp_expires_at = timezone.now() + timedelta(minutes=5)
        session.save()

        res_verify = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/otp/verify/',
            {'otp_code': test_code},
            headers={'X-Checkout-Token': session_token}
        )
        self.assertEqual(res_verify.status_code, 200)
        self.assertTrue(res_verify.data['is_verified'])
        self.assertEqual(res_verify.data['status'], 'READY_FOR_PAYMENT')

        res_detail = self.client.get(
            f'/api/v1/checkout/sessions/{session_id}/',
            headers={'X-Checkout-Token': session_token}
        )
        self.assertEqual(res_detail.status_code, 200)
        self.assertTrue(res_detail.data['can_proceed'])
        self.assertEqual(res_detail.data['pricing']['total'], '120.00')

    def test_duplicate_active_membership_prevention(self):
        TenantCheckoutConfig.objects.create(tenant=self.tenant_a, is_enabled=True)
        Package.objects.create(
            tenant=self.tenant_a,
            client=self.client_user,
            package_type=self.package_type_a,
            credits_remaining=30,
            total_credits_allocated=30,
            purchased_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
            status='active',
            price=Decimal('120.00')
        )

        res = self.client.post('/api/v1/checkout/sessions/', {
            'tenant_id': str(self.tenant_a.id),
            'package_type_id': str(self.package_type_a.id),
            'email': self.client_user.email
        })
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'ALREADY_SUBSCRIBED_TO_PACKAGE')

    def test_price_mismatch_webhook_quarantine(self):
        raw_token = 'token_mismatch_test'
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        session = ExternalCheckoutSession.objects.create(
            tenant=self.tenant_a,
            package_type=self.package_type_a,
            token_hash=token_hash,
            email='tamper@example.com',
            price_snapshot=Decimal('120.00'),
            currency='usd',
            billing_cycle_snapshot='monthly',
            credit_count_snapshot=30,
            validity_days_snapshot=30,
            is_verified=True,
            status=ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT,
            expires_at=timezone.now() + timedelta(minutes=30)
        )

        mismatched_event = {
            'id': 'evt_mismatch_001',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_mismatch_001',
                    'amount_total': 100,
                    'currency': 'usd',
                    'subscription': 'sub_tamper_001',
                    'metadata': {
                        'type': 'external_package_checkout',
                        'checkout_session_id': str(session.id)
                    }
                }
            }
        }

        result = CheckoutIntegrationService.fulfill_checkout_webhook(mismatched_event)
        self.assertEqual(result['status'], 'price_mismatch')
        session.refresh_from_db()
        self.assertEqual(session.status, ExternalCheckoutSession.StatusChoices.PRICE_MISMATCH)
        self.assertEqual(session.payment_status, 'failed')
        self.assertIsNone(session.created_package)

    @patch('stripe.Subscription.retrieve')
    def test_webhook_fulfillment_idempotency_and_ledger(self, mock_sub_retrieve):
        mock_sub_retrieve.return_value = {'current_period_end': 1770000000}
        raw_token = 'token_webhook_fresh'
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        session = ExternalCheckoutSession.objects.create(
            tenant=self.tenant_a,
            package_type=self.package_type_a,
            token_hash=token_hash,
            email='fresh_buyer@example.com',
            first_name='Fresh',
            last_name='Buyer',
            price_snapshot=Decimal('120.00'),
            currency='usd',
            billing_cycle_snapshot='monthly',
            credit_count_snapshot=30,
            validity_days_snapshot=30,
            is_verified=True,
            status=ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT,
            expires_at=timezone.now() + timedelta(minutes=30)
        )

        valid_event = {
            'id': 'evt_fresh_001',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_fresh_001',
                    'amount_total': 12000,
                    'currency': 'usd',
                    'subscription': 'sub_fresh_001',
                    'metadata': {
                        'type': 'external_package_checkout',
                        'checkout_session_id': str(session.id)
                    }
                }
            }
        }

        res1 = CheckoutIntegrationService.fulfill_checkout_webhook(valid_event)
        self.assertEqual(res1['status'], 'fulfilled')

        res2 = CheckoutIntegrationService.fulfill_checkout_webhook(valid_event)
        self.assertEqual(res2['status'], 'already_processed')

        self.assertEqual(User.objects.filter(email='fresh_buyer@example.com').count(), 1)
        self.assertEqual(Package.all_objects.filter(stripe_subscription_id='sub_fresh_001').count(), 1)
        self.assertEqual(Payment.all_objects.filter(provider_ref='cs_fresh_001').count(), 1)

        payment = Payment.all_objects.get(provider_ref='cs_fresh_001')
        ledger = PlatformLedger.all_objects.filter(transaction_id=str(payment.id)).first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.amount_gross, Decimal('120.00'))
        self.assertEqual(ledger.platform_fee, Decimal('12.00'))
        self.assertEqual(ledger.amount_net, Decimal('108.00'))

        res_status = self.client.get(
            f'/api/v1/checkout/sessions/{session.id}/payment-status/',
            headers={'X-Checkout-Token': raw_token}
        )
        self.assertEqual(res_status.status_code, 200)
        self.assertEqual(res_status.data['payment_status'], 'successful')
        self.assertTrue(res_status.data['is_completed'])

        res_complete = self.client.get(
            f'/api/v1/checkout/sessions/{session.id}/completion/',
            headers={'X-Checkout-Token': raw_token}
        )
        self.assertEqual(res_complete.status_code, 200)
        self.assertEqual(res_complete.data['payment_status'], 'successful')
        self.assertEqual(res_complete.data['customer']['account_status'], 'new_account')
        self.assertTrue(res_complete.data['customer']['password_setup_required'])

        res_pw = self.client.post(
            f'/api/v1/checkout/sessions/{session.id}/set-password/',
            {'new_password': 'BrandNewPassword123!'},
            headers={'X-Checkout-Token': raw_token}
        )
        self.assertEqual(res_pw.status_code, 200)

        user = User.objects.get(email='fresh_buyer@example.com')
        self.assertTrue(user.check_password('BrandNewPassword123!'))

    def test_late_payment_fulfillment_after_session_expiry(self):
        raw_token = 'token_late_payer'
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        session = ExternalCheckoutSession.objects.create(
            tenant=self.tenant_a,
            package_type=self.package_type_a,
            token_hash=token_hash,
            email='late_payer@example.com',
            price_snapshot=Decimal('120.00'),
            currency='usd',
            billing_cycle_snapshot='monthly',
            credit_count_snapshot=30,
            validity_days_snapshot=30,
            is_verified=True,
            status=ExternalCheckoutSession.StatusChoices.EXPIRED,
            expires_at=timezone.now() - timedelta(minutes=5)
        )

        late_event = {
            'id': 'evt_late_001',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_late_001',
                    'amount_total': 12000,
                    'currency': 'usd',
                    'subscription': 'sub_late_001',
                    'metadata': {
                        'type': 'external_package_checkout',
                        'checkout_session_id': str(session.id)
                    }
                }
            }
        }

        res = CheckoutIntegrationService.fulfill_checkout_webhook(late_event)
        self.assertEqual(res['status'], 'fulfilled')

        session.refresh_from_db()
        self.assertEqual(session.status, ExternalCheckoutSession.StatusChoices.COMPLETED)
        self.assertIsNotNone(session.created_package)

    def test_tracking_data_purge(self):
        raw_token = 'token_purge_test'
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        session = ExternalCheckoutSession.objects.create(
            tenant=self.tenant_a,
            package_type=self.package_type_a,
            token_hash=token_hash,
            email='purge@example.com',
            price_snapshot=Decimal('120.00'),
            currency='usd',
            billing_cycle_snapshot='monthly',
            credit_count_snapshot=30,
            validity_days_snapshot=30,
            client_ip_address='198.51.100.1',
            client_user_agent='Mozilla/5.0 Test',
            fbp='fb.1.test',
            fbc='fb.1.click',
            expires_at=timezone.now() + timedelta(minutes=30)
        )
        ExternalCheckoutSession.all_objects.filter(id=session.id).update(
            created_at=timezone.now() - timedelta(days=35)
        )

        purge_old_checkout_tracking_data_task()

        session.refresh_from_db()
        self.assertIsNone(session.client_ip_address)
        self.assertIsNone(session.client_user_agent)
        self.assertIsNone(session.fbp)
        self.assertIsNone(session.fbc)
        self.assertIsNotNone(session.tracking_purged_at)
        self.assertEqual(session.price_snapshot, Decimal('120.00'))

    def test_subscription_platform_features_customization(self):
        plan = BillingPlan.objects.create(name='Pro Plan', slug='pro', is_public=True)
        feat_a = BillingFeature.objects.create(name='Feature A', code='feat_a', price=Decimal('10.00'), is_active=True)
        feat_b = BillingFeature.objects.create(name='Feature B', code='feat_b', price=Decimal('20.00'), is_active=True)

        billing_sub = TenantBillingSubscription.objects.create(
            tenant=self.tenant_a,
            billing_plan=plan,
            status=TenantBillingSubscription.StatusChoices.ACTIVE
        )
        billing_sub.active_features.add(feat_a)

        self.client.force_authenticate(user=self.owner_a)

        res_get = self.client.get('/api/v1/payments/billing/subscription/')
        self.assertEqual(res_get.status_code, 200)
        self.assertTrue(len(res_get.data) >= 1)
        sub_data = res_get.data[0]
        self.assertIn('android', sub_data)
        self.assertIn('ios', sub_data)
        self.assertFalse(sub_data['android']['is_customized'])
        self.assertFalse(sub_data['ios']['is_customized'])
        self.assertEqual(sub_data['android']['feature_codes'], ['feat_a'])
        self.assertEqual(sub_data['ios']['feature_codes'], ['feat_a'])

        res_patch = self.client.patch('/api/v1/payments/billing/subscription/platform-features/', {
            'platform': 'android',
            'features': ['feat_a', 'feat_b']
        }, format='json')
        self.assertEqual(res_patch.status_code, 200)
        self.assertTrue(res_patch.data['android']['is_customized'])
        self.assertIn('feat_b', res_patch.data['android']['feature_codes'])

        res_get_updated = self.client.get('/api/v1/payments/billing/subscription/')
        updated_sub = res_get_updated.data[0]
        self.assertTrue(updated_sub['android']['is_customized'])
        self.assertEqual(set(updated_sub['android']['feature_codes']), {'feat_a', 'feat_b'})
        self.assertFalse(updated_sub['ios']['is_customized'])
        self.assertEqual(updated_sub['ios']['feature_codes'], ['feat_a'])

        res_action_remove = self.client.post('/api/v1/payments/billing/subscription/platform-features/', {
            'platform': 'android',
            'action': 'remove',
            'feature': 'feat_b'
        }, format='json')
        self.assertEqual(res_action_remove.status_code, 200)
        self.assertEqual(res_action_remove.data['android']['feature_codes'], ['feat_a'])

        res_action_reset = self.client.post('/api/v1/payments/billing/subscription/platform-features/', {
            'platform': 'android',
            'action': 'reset'
        }, format='json')
        self.assertEqual(res_action_reset.status_code, 200)
        self.assertFalse(res_action_reset.data['android']['is_customized'])
        self.assertEqual(res_action_reset.data['android']['feature_codes'], ['feat_a'])

    def test_allowed_origins_validation(self):
        TenantCheckoutConfig.objects.create(
            tenant=self.tenant_a,
            is_enabled=True,
            allowed_origins=['https://mygym.com']
        )

        with self.settings(CHECKOUT_ALLOWED_ORIGINS=['https://checkout.fitverx.com']):
            res_official = self.client.get(
                f'/api/v1/checkout/gym/{self.tenant_a.subdomain}/packages/',
                HTTP_ORIGIN='https://checkout.fitverx.com'
            )
            self.assertEqual(res_official.status_code, 200)

            res_allowed_custom = self.client.get(
                f'/api/v1/checkout/gym/{self.tenant_a.subdomain}/packages/',
                HTTP_ORIGIN='https://mygym.com'
            )
            self.assertEqual(res_allowed_custom.status_code, 200)

            res_blocked = self.client.get(
                f'/api/v1/checkout/gym/{self.tenant_a.subdomain}/packages/',
                HTTP_ORIGIN='https://unauthorized-site.com'
            )
            self.assertEqual(res_blocked.status_code, 403)
            self.assertEqual(res_blocked.data['detail'], 'Origin is not allowed.')

    def test_platform_admin_selects_tenant_and_changes_platform_features(self):
        admin_user = User.objects.create_user(
            email='superadmin@fitverx.com',
            password='AdminPassword123!',
            role=UserRole.PLATFORM_ADMIN,
            is_staff=True,
            is_superuser=True
        )

        plan = BillingPlan.objects.create(name='Pro Plan', slug='pro', allowed_feature_count=10, is_public=True)
        feat_a = BillingFeature.objects.create(name='Feature A', code='feat_a', price=Decimal('10.00'), is_active=True)
        feat_b = BillingFeature.objects.create(name='Feature B', code='feat_b', price=Decimal('20.00'), is_active=True)

        billing_sub = TenantBillingSubscription.objects.create(
            tenant=self.tenant_a,
            billing_plan=plan,
            status=TenantBillingSubscription.StatusChoices.ACTIVE
        )
        billing_sub.active_features.add(feat_a, feat_b)

        self.client.force_authenticate(user=admin_user)

        res_get = self.client.get(
            '/api/v1/payments/billing/subscription/',
            HTTP_X_TENANT_ID=str(self.tenant_a.id)
        )
        self.assertEqual(res_get.status_code, 200)
        self.assertTrue(len(res_get.data) >= 1)
        sub_data = res_get.data[0]
        self.assertFalse(sub_data['android']['is_customized'])
        self.assertFalse(sub_data['ios']['is_customized'])
        self.assertEqual(set(sub_data['android']['feature_codes']), {'feat_a', 'feat_b'})
        self.assertEqual(set(sub_data['ios']['feature_codes']), {'feat_a', 'feat_b'})

        res_patch = self.client.patch(
            '/api/v1/payments/billing/subscription/platform-features/',
            {
                'android': ['feat_a'],
                'ios': ['feat_b']
            },
            HTTP_X_TENANT_ID=str(self.tenant_a.id),
            format='json'
        )
        self.assertEqual(res_patch.status_code, 200)
        self.assertTrue(res_patch.data['android']['is_customized'])
        self.assertEqual(res_patch.data['android']['feature_codes'], ['feat_a'])
        self.assertTrue(res_patch.data['ios']['is_customized'])
        self.assertEqual(res_patch.data['ios']['feature_codes'], ['feat_b'])

        res_get_updated = self.client.get(
            '/api/v1/payments/billing/subscription/',
            HTTP_X_TENANT_ID=str(self.tenant_a.id)
        )
        updated_sub = res_get_updated.data[0]
        self.assertTrue(updated_sub['android']['is_customized'])
        self.assertEqual(updated_sub['android']['feature_codes'], ['feat_a'])
        self.assertTrue(updated_sub['ios']['is_customized'])
        self.assertEqual(updated_sub['ios']['feature_codes'], ['feat_b'])

    @patch('stripe.checkout.Session.create')
    @patch('stripe.Price.retrieve')
    @patch('stripe.Subscription.retrieve')
    def test_health_commitment_customization_gate_and_signed_document_audit(
        self, mock_sub_retrieve, mock_price_retrieve, mock_stripe_session_create
    ):
        mock_price_retrieve.return_value = MagicMock(active=True, currency='usd', unit_amount=12000)
        mock_stripe_session_create.return_value = MagicMock(
            id='cs_hc_test_001',
            url='https://checkout.stripe.com/pay/cs_hc_test_001',
            client_secret=None
        )
        mock_sub_retrieve.return_value = {'current_period_end': 1770000000}

        self.package_type_a.stripe_price_id = 'price_hc_test_001'
        self.package_type_a.save()

        TenantCheckoutConfig.objects.create(tenant=self.tenant_a, is_enabled=True)
        TenantCheckoutConfig.objects.create(tenant=self.tenant_b, is_enabled=True)

        self.client.force_authenticate(user=self.owner_a)
        res_cfg = self.client.get('/api/v1/payments/checkout-config/')
        self.assertEqual(res_cfg.status_code, 200)
        self.assertTrue(res_cfg.data['require_health_commitment'])
        self.assertEqual(res_cfg.data['health_commitment_version'], 1)
        self.assertEqual(res_cfg.data['health_commitment_body'], DEFAULT_HEALTH_COMMITMENT_BODY)

        custom_body = 'Alpha Gym Custom Safety Statement: Lift responsibly and report discomfort.'
        custom_label = 'I electronically sign Alpha Gym Safety Statement.'
        res_patch = self.client.patch('/api/v1/payments/checkout-config/', {
            'health_commitment_title': 'Alpha Safety Waiver',
            'health_commitment_body': custom_body,
            'health_commitment_checkbox_label': custom_label,
        }, format='json')
        self.assertEqual(res_patch.status_code, 200)
        self.assertEqual(res_patch.data['health_commitment_version'], 2)

        self.client.force_authenticate(user=None)
        res_gym_b = self.client.get(f'/api/v1/checkout/gym/{self.tenant_b.subdomain}/')
        self.assertEqual(res_gym_b.status_code, 200)
        self.assertEqual(
            res_gym_b.data['checkout_settings']['health_commitment']['body'],
            DEFAULT_HEALTH_COMMITMENT_BODY
        )
        self.assertEqual(res_gym_b.data['checkout_settings']['health_commitment']['version'], 1)

        res_gym_a = self.client.get(f'/api/v1/checkout/gym/{self.tenant_a.subdomain}/')
        self.assertEqual(res_gym_a.status_code, 200)
        self.assertEqual(res_gym_a.data['checkout_settings']['health_commitment']['body'], custom_body)
        self.assertEqual(res_gym_a.data['checkout_settings']['health_commitment']['version'], 2)

        res_create = self.client.post('/api/v1/checkout/sessions/', {
            'tenant_id': 'alpha',
            'package_type_id': str(self.package_type_a.id),
            'email': 'waiver_signer@example.com',
            'first_name': 'Jane',
            'last_name': 'Doe',
        })
        self.assertEqual(res_create.status_code, 201)
        session_id = res_create.data['session_id']
        session_token = res_create.data['session_token']
        self.assertFalse(res_create.data['health_commitment']['accepted'])
        self.assertFalse(res_create.data['can_initiate_payment'])

        session = ExternalCheckoutSession.all_objects.get(id=session_id)
        test_code = '654321'
        session.otp_hash = hashlib.sha256(test_code.encode('utf-8')).hexdigest()
        session.otp_expires_at = timezone.now() + timedelta(minutes=5)
        session.save()

        res_verify = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/otp/verify/',
            {'otp_code': test_code},
            headers={'X-Checkout-Token': session_token}
        )
        self.assertEqual(res_verify.status_code, 200)

        res_blocked_pay = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/initiate-payment/',
            {},
            headers={'X-Checkout-Token': session_token}
        )
        self.assertEqual(res_blocked_pay.status_code, 400)
        self.assertEqual(res_blocked_pay.data['code'], 'HEALTH_COMMITMENT_REQUIRED')

        res_unticked = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/health-commitment/',
            {'accepted': False},
            headers={'X-Checkout-Token': session_token},
            format='json'
        )
        self.assertEqual(res_unticked.status_code, 400)

        res_sign = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/health-commitment/',
            {'accepted': True, 'signer_name': 'Jane Doe'},
            headers={'X-Checkout-Token': session_token},
            REMOTE_ADDR='203.0.113.42',
            HTTP_USER_AGENT='FitverxTestBrowser/1.0',
            format='json'
        )
        self.assertEqual(res_sign.status_code, 200)
        self.assertTrue(res_sign.data['accepted'])
        self.assertEqual(res_sign.data['version'], 2)

        res_pay = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/initiate-payment/',
            {},
            headers={'X-Checkout-Token': session_token},
            format='json'
        )
        self.assertEqual(res_pay.status_code, 200)
        self.assertEqual(res_pay.data['checkout_url'], 'https://checkout.stripe.com/pay/cs_hc_test_001')

        self.client.force_authenticate(user=self.owner_a)
        self.client.post('/api/v1/payments/checkout-config/health-commitment/reset/')
        self.client.force_authenticate(user=None)

        webhook_event = {
            'id': 'evt_hc_test_001',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_hc_test_001',
                    'amount_total': 12000,
                    'currency': 'usd',
                    'subscription': 'sub_hc_test_001',
                    'metadata': {
                        'type': 'external_package_checkout',
                        'checkout_session_id': str(session_id)
                    }
                }
            }
        }
        fulfill_res = CheckoutIntegrationService.fulfill_checkout_webhook(webhook_event)
        self.assertEqual(fulfill_res['status'], 'fulfilled')

        signature = HealthCommitmentSignature.all_objects.get(checkout_session_id=session_id)
        self.assertEqual(signature.tenant, self.tenant_a)
        self.assertEqual(signature.signer_name, 'Jane Doe')
        self.assertEqual(signature.signer_email, 'waiver_signer@example.com')
        self.assertEqual(signature.statement_version, 2)
        self.assertEqual(signature.statement_body, custom_body)
        self.assertEqual(signature.checkbox_label, custom_label)
        self.assertEqual(signature.ip_address, '203.0.113.42')

        ExternalCheckoutSession.all_objects.filter(id=session_id).update(
            created_at=timezone.now() - timedelta(days=35)
        )
        purge_old_checkout_tracking_data_task()
        signature.refresh_from_db()
        self.assertEqual(signature.ip_address, '203.0.113.42')

        self.client.force_authenticate(user=self.owner_a)
        res_sigs_a = self.client.get('/api/v1/payments/checkout-config/health-commitment/signatures/')
        self.assertEqual(res_sigs_a.status_code, 200)
        self.assertEqual(len(res_sigs_a.data['signatures']), 1)

        owner_b = User.objects.create_user(
            email='ownerb_hc@example.com',
            password='Password123!',
            tenant=self.tenant_b,
            role=UserRole.GYM_OWNER
        )
        self.client.force_authenticate(user=owner_b)
        res_sigs_b = self.client.get('/api/v1/payments/checkout-config/health-commitment/signatures/')
        self.assertEqual(res_sigs_b.status_code, 200)
        self.assertEqual(len(res_sigs_b.data['signatures']), 0)

    @patch('stripe.checkout.Session.create')
    @patch('stripe.Price.retrieve')
    def test_health_commitment_inline_acceptance_paths(self, mock_price_retrieve, mock_stripe_session_create):
        mock_price_retrieve.return_value = MagicMock(active=True, currency='usd', unit_amount=12000)
        mock_stripe_session_create.return_value = MagicMock(
            id='cs_inline_hc_001',
            url='https://checkout.stripe.com/pay/cs_inline_hc_001',
            client_secret=None
        )
        self.package_type_a.stripe_price_id = 'price_inline_hc_001'
        self.package_type_a.save()

        TenantCheckoutConfig.objects.create(tenant=self.tenant_a, is_enabled=True)

        res_create = self.client.post('/api/v1/checkout/sessions/', {
            'tenant_id': 'alpha',
            'package_type_id': str(self.package_type_a.id),
            'email': 'inline_hc@example.com',
            'first_name': 'Inline',
            'last_name': 'Signer',
        })
        self.assertEqual(res_create.status_code, 201)
        session_id = res_create.data['session_id']
        session_token = res_create.data['session_token']

        session = ExternalCheckoutSession.all_objects.get(id=session_id)
        session.is_verified = True
        session.status = ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT
        session.save(update_fields=['is_verified', 'status'])

        res_pay = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/initiate-payment/',
            {
                'health_commitment_accepted': True,
                'health_commitment_signer_name': 'Inline Signer'
            },
            headers={'X-Checkout-Token': session_token},
            format='json'
        )
        self.assertEqual(res_pay.status_code, 200)
        session.refresh_from_db()
        self.assertTrue(session.health_commitment_accepted)
        self.assertEqual(session.health_commitment_signer_name, 'Inline Signer')

    @patch('stripe.checkout.Session.create')
    @patch('stripe.Price.retrieve')
    def test_welcome_screen_customization_agreement_download_and_email(
        self,
        mock_price_retrieve,
        mock_stripe_session_create,
    ):
        mock_price_retrieve.return_value = MagicMock(active=True, currency='gbp', unit_amount=12000)
        mock_stripe_session_create.return_value = MagicMock(
            id='cs_welcome_001',
            url='https://checkout.stripe.com/pay/cs_welcome_001',
            client_secret=None
        )
        self.package_type_a.stripe_price_id = 'price_welcome_001'
        self.package_type_a.save()

        self.client.force_authenticate(user=self.owner_a)
        res_bad = self.client.patch('/api/v1/payments/checkout-config/', {
            'welcome_headline': '   ',
        }, format='json')
        self.assertEqual(res_bad.status_code, 400)

        res_cfg = self.client.patch('/api/v1/payments/checkout-config/', {
            'is_enabled': True,
            'welcome_email_subject': 'Welcome to {tenant_name} — your purchase is confirmed',
            'welcome_headline': 'Welcome to {tenant_name}!',
            'welcome_intro_text': (
                'Thank you for joining us and purchasing {package_name}. '
                'We have received your payment.'
            ),
            'welcome_additional_benefits': 'Priority class booking access',
            'welcome_body_text': 'We are getting everything ready at {tenant_name} to welcome you.',
            'welcome_closing_text': 'The {tenant_name} Team',
            'welcome_tagline': 'Stronger Every Day',
            'welcome_reply_to_email': 'support@alphagym.example.com',
        }, format='json')
        self.assertEqual(res_cfg.status_code, 200)
        self.assertEqual(res_cfg.data['welcome_tagline'], 'Stronger Every Day')

        self.client.force_authenticate(user=None)
        res_create = self.client.post('/api/v1/checkout/sessions/', {
            'tenant_id': 'alpha',
            'package_type_id': str(self.package_type_a.id),
            'email': 'welcome.client@example.com',
            'first_name': 'Alice',
            'last_name': 'Walker',
        })
        self.assertEqual(res_create.status_code, 201)
        session_id = res_create.data['session_id']
        session_token = res_create.data['session_token']

        session = ExternalCheckoutSession.all_objects.get(id=session_id)
        session.currency = 'gbp'
        session.is_verified = True
        session.status = ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT
        session.save(update_fields=['currency', 'is_verified', 'status'])

        res_sign = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/health-commitment/',
            {'accepted': True, 'signer_name': 'Alice Walker'},
            headers={'X-Checkout-Token': session_token},
            format='json',
        )
        self.assertEqual(res_sign.status_code, 200)

        res_pay = self.client.post(
            f'/api/v1/checkout/sessions/{session_id}/initiate-payment/',
            {},
            headers={'X-Checkout-Token': session_token},
            format='json',
        )
        self.assertEqual(res_pay.status_code, 200)

        with patch('apps.payments.tasks.send_checkout_welcome_email_task.delay'):
            fulfill_res = CheckoutIntegrationService.fulfill_checkout_webhook({
                'id': 'evt_welcome_001',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_welcome_001',
                        'amount_total': 12000,
                        'currency': 'gbp',
                        'subscription': 'sub_welcome_001',
                        'metadata': {
                            'type': 'external_package_checkout',
                            'checkout_session_id': str(session_id),
                        },
                    }
                },
            })
        self.assertEqual(fulfill_res['status'], 'fulfilled')

        res_comp = self.client.get(
            f'/api/v1/checkout/sessions/{session_id}/completion/',
            headers={'X-Checkout-Token': session_token},
        )
        self.assertEqual(res_comp.status_code, 200)
        welcome = res_comp.data['welcome']
        self.assertEqual(welcome['tenant_name'], 'Alpha Gym')
        self.assertEqual(welcome['subject'], 'Welcome to Alpha Gym — your purchase is confirmed')
        self.assertEqual(welcome['headline'], 'Welcome to Alpha Gym!')
        self.assertEqual(welcome['greeting'], 'Hi Alice,')
        self.assertIn(self.package_type_a.name, welcome['intro'])
        self.assertEqual(welcome['purchase_confirmation']['amount_paid'], '£120.00')
        self.assertIn('Priority class booking access', welcome['purchase_confirmation']['whats_included'])
        self.assertEqual(
            welcome['purchase_confirmation']['payment_arrangement'],
            'Recurring monthly payment of £120.00',
        )
        self.assertTrue(welcome['agreement']['signed'])
        self.assertIn(f'/api/v1/checkout/sessions/{session_id}/agreement/?access=', welcome['agreement']['view_url'])
        self.assertEqual(welcome['closing'], 'The Alpha Gym Team')
        self.assertEqual(welcome['tagline'], 'Stronger Every Day')
        self.assertIn('We are getting everything ready at Alpha Gym', welcome['custom_body'])

        self.assertIn(
            f'/api/v1/checkout/sessions/{session_id}/agreement/?access=',
            res_comp.data['health_commitment']['view_url'],
        )

        res_unauth = self.client.get(f'/api/v1/checkout/sessions/{session_id}/agreement/')
        self.assertEqual(res_unauth.status_code, 403)

        res_bad_sig = self.client.get(f'/api/v1/checkout/sessions/{session_id}/agreement/?access=invalid')
        self.assertEqual(res_bad_sig.status_code, 403)

        access_token = CheckoutIntegrationService.generate_agreement_access_token(session_id)
        res_html = self.client.get(
            f'/api/v1/checkout/sessions/{session_id}/agreement/?access={access_token}&format=html'
        )
        self.assertEqual(res_html.status_code, 200)
        self.assertIn('Alice Walker', res_html.content.decode('utf-8'))
        self.assertIn('Alpha Gym', res_html.content.decode('utf-8'))

        res_dl = self.client.get(
            f'/api/v1/checkout/sessions/{session_id}/agreement/?access={access_token}&format=html&download=1'
        )
        self.assertEqual(res_dl.status_code, 200)
        self.assertIn('attachment;', res_dl['Content-Disposition'])

        res_json = self.client.get(
            f'/api/v1/checkout/sessions/{session_id}/agreement/',
            headers={'X-Checkout-Token': session_token},
        )
        self.assertEqual(res_json.status_code, 200)
        self.assertEqual(res_json.data['signer_email'], 'welcome.client@example.com')

        mail.outbox = []
        send_checkout_welcome_email_task(str(session_id))
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['welcome.client@example.com'])
        self.assertEqual(sent.reply_to, ['support@alphagym.example.com'])
        self.assertEqual(sent.subject, 'Welcome to Alpha Gym — your purchase is confirmed')
        self.assertIn('Hi Alice,', sent.body)
        self.assertIn('£120.00', sent.body)
        self.assertIn(f'/api/v1/checkout/sessions/{session_id}/agreement/?access={access_token}', sent.body)
        self.assertIn('The Alpha Gym Team', sent.body)
        self.assertIn('Stronger Every Day', sent.body)
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mime_type = sent.alternatives[0]
        self.assertEqual(mime_type, 'text/html')
        self.assertIn('View &amp; Download Signed Agreement', html_body)
        self.assertIn('Alice', html_body)

        self.client.force_authenticate(user=self.owner_a)
        res_reset = self.client.post('/api/v1/payments/checkout-config/welcome/reset/')
        self.assertEqual(res_reset.status_code, 200)
        self.assertEqual(res_reset.data['welcome_email_subject'], DEFAULT_WELCOME_EMAIL_SUBJECT)
        self.assertEqual(res_reset.data['welcome_tagline'], '')

    @patch('stripe.Price.retrieve')
    @patch('stripe.Price.create')
    @patch('stripe.Product.create')
    def test_package_is_featured_and_catalog_ordering(
        self,
        mock_product_create,
        mock_price_create,
        mock_price_retrieve,
    ):
        mock_product_create.return_value = MagicMock(id='prod_test_001')
        mock_price_create.return_value = MagicMock(id='price_test_001')
        mock_price_retrieve.return_value = MagicMock(active=True, unit_amount=15000, currency='usd', recurring={'interval': 'month'})

        TenantCheckoutConfig.objects.create(tenant=self.tenant_a, is_enabled=True)

        plan_a = PackageType.all_objects.create(
            tenant=self.tenant_a,
            location=self.location_a,
            name='Plan A (Starter)',
            price=Decimal('50.00'),
            credit_count=5,
            validity_days=30,
            is_active=True,
            is_featured=False,
        )
        plan_b = PackageType.all_objects.create(
            tenant=self.tenant_a,
            location=self.location_a,
            name='Plan B (Unlimited)',
            price=Decimal('150.00'),
            credit_count=30,
            validity_days=30,
            is_active=True,
            is_featured=True,
            feature_badge='Most Popular',
        )
        plan_c = PackageType.all_objects.create(
            tenant=self.tenant_a,
            location=self.location_a,
            name='Plan C (Drop-in)',
            price=Decimal('20.00'),
            credit_count=1,
            validity_days=7,
            is_active=True,
            is_featured=False,
        )

        res_catalog = self.client.get('/api/v1/checkout/gym/alpha/packages/')
        self.assertEqual(res_catalog.status_code, 200)
        packages = res_catalog.data['packages']
        self.assertTrue(len(packages) >= 3)

        self.assertEqual(packages[0]['id'], str(plan_b.id))
        self.assertTrue(packages[0]['is_featured'])
        self.assertEqual(packages[0]['feature_badge'], 'Most Popular')

        non_featured_ids = [p['id'] for p in packages[1:]]
        self.assertIn(str(plan_c.id), non_featured_ids)
        self.assertIn(str(plan_a.id), non_featured_ids)

        self.client.force_authenticate(user=self.owner_a)
        res_config = self.client.get('/api/v1/payments/checkout-config/')
        self.assertEqual(res_config.status_code, 200)
        avail = res_config.data['available_packages']
        featured_dash = next(p for p in avail if p['id'] == str(plan_b.id))
        self.assertTrue(featured_dash['is_featured'])
        self.assertEqual(featured_dash['feature_badge'], 'Most Popular')

        res_pkg_update = self.client.patch(
            f'/api/v1/scheduling/package-types/{plan_a.id}/',
            {'is_featured': True, 'feature_badge': 'Best Value'},
            headers={'X-Tenant-Id': str(self.tenant_a.id)},
            format='json'
        )
        self.assertEqual(res_pkg_update.status_code, 200)
        self.assertTrue(res_pkg_update.data['is_featured'])
        self.assertEqual(res_pkg_update.data['feature_badge'], 'Best Value')

    @patch('stripe.Price.retrieve')
    @patch('stripe.Price.create')
    @patch('stripe.Product.create')
    def test_tenant_specific_currency_flow(
        self,
        mock_product_create,
        mock_price_create,
        mock_price_retrieve,
    ):
        mock_product_create.return_value = MagicMock(id='prod_curr_001')
        mock_price_create.return_value = MagicMock(id='price_curr_001')
        mock_price_retrieve.return_value = MagicMock(
            active=True,
            unit_amount=12000,
            currency='eur',
            recurring={'interval': 'month'},
        )

        self.client.force_authenticate(user=self.owner_a)
        res_curr_list = self.client.get('/api/v1/payments/checkout-config/currencies/')
        self.assertEqual(res_curr_list.status_code, 200)
        curr_codes = [c['code'] for c in res_curr_list.data['currencies']]
        self.assertIn('eur', curr_codes)
        self.assertIn('gbp', curr_codes)
        self.assertIn('usd', curr_codes)

        res_patch_curr = self.client.patch(
            '/api/v1/payments/checkout-config/',
            {'currency': 'eur', 'is_enabled': True},
            format='json',
        )
        self.assertEqual(res_patch_curr.status_code, 200)
        self.assertEqual(res_patch_curr.data['currency'], 'eur')

        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.currency, 'eur')
        self.tenant_b.refresh_from_db()
        self.assertEqual(self.tenant_b.currency, 'usd')

        res_finance = self.client.get('/api/v1/payments/tenant/finance/summary/')
        self.assertEqual(res_finance.status_code, 200)
        self.assertEqual(res_finance.data['summary']['currency'], 'eur')
        self.assertEqual(res_finance.data['summary']['currency_symbol'], '€')

        self.client.force_authenticate(user=None)
        res_gym_info = self.client.get('/api/v1/checkout/gym/alpha/')
        self.assertEqual(res_gym_info.status_code, 200)
        self.assertEqual(res_gym_info.data['currency'], 'eur')

        res_gym_packages = self.client.get('/api/v1/checkout/gym/alpha/packages/')
        self.assertEqual(res_gym_packages.status_code, 200)
        self.assertTrue(len(res_gym_packages.data['packages']) >= 1)
        self.assertEqual(res_gym_packages.data['packages'][0]['currency'], 'eur')

        res_session = self.client.post('/api/v1/checkout/sessions/', {
            'tenant_id': 'alpha',
            'package_type_id': str(self.package_type_a.id),
            'email': 'euro.user@example.com',
            'first_name': 'Euro',
            'last_name': 'Customer',
        })
        self.assertEqual(res_session.status_code, 201)
        session_id = res_session.data['session_id']
        session_token = res_session.data['session_token']

        session = ExternalCheckoutSession.all_objects.get(id=session_id)
        self.assertEqual(session.currency, 'eur')

        session.is_verified = True
        session.health_commitment_accepted = True
        session.status = ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT
        session.save(update_fields=['is_verified', 'health_commitment_accepted', 'status'])

        with patch('apps.payments.tasks.send_checkout_welcome_email_task.delay'):
            fulfill_res = CheckoutIntegrationService.fulfill_checkout_webhook({
                'id': 'evt_eur_001',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_eur_001',
                        'amount_total': 12000,
                        'currency': 'eur',
                        'subscription': 'sub_eur_001',
                        'metadata': {
                            'type': 'external_package_checkout',
                            'checkout_session_id': str(session_id),
                        },
                    }
                },
            })
        self.assertEqual(fulfill_res['status'], 'fulfilled')

        res_comp = self.client.get(
            f'/api/v1/checkout/sessions/{session_id}/completion/',
            headers={'X-Checkout-Token': session_token},
        )
        self.assertEqual(res_comp.status_code, 200)
        self.assertEqual(res_comp.data['welcome']['purchase_confirmation']['amount_paid'], '€120.00')
        self.assertEqual(res_comp.data['welcome']['purchase_confirmation']['currency'], 'EUR')
        self.assertEqual(res_comp.data['welcome']['purchase_confirmation']['currency_symbol'], '€')
