import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.tenants.models import Tenant
from apps.payments.checkout_service import CheckoutIntegrationService
from apps.payments.models import (
    BillingFeature,
    BillingPlan,
    CheckoutVerificationChallenge,
    ExternalCheckoutSession,
    PlatformLedger,
    StripeProcessedEvent,
    TenantBillingSubscription,
    TenantCheckoutConfig,
)
from apps.payments.tasks import purge_old_checkout_tracking_data_task
from apps.scheduling.models import Location, Package, PackageType, Payment
from apps.users.models import EmailOTP, OTPPurpose, TenantCustomerAccess, User, UserRole
from apps.users.services import AuthService


class CheckoutIntegrationTests(TestCase):
    def setUp(self):
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
