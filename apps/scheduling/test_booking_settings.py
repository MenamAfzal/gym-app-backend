from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch

from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.users.models import User, UserRole
from apps.scheduling.models import (
    Location, Room, ClassTemplate, ClassSession,
    Booking, PackageType, Package, Appointment,
    TenantBookingSettings
)


class BookingSettingsTestCase(APITestCase):
    def setUp(self):
        self.patcher = patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
        self.mock_waitlist_delay = self.patcher.start()
        self.addCleanup(self.patcher.stop)

        self.tenant = Tenant.objects.create(name="Apex Fitness", subdomain="apex-fitness")
        self.tenant_token = set_current_tenant(self.tenant)

        self.owner = User.objects.create_user(
            email="owner@apex.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.manager = User.objects.create_user(
            email="manager@apex.com", password="password123", role=UserRole.GYM_MANAGER, tenant=self.tenant
        )
        self.trainer = User.objects.create_user(
            email="trainer@apex.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.client_user = User.objects.create_user(
            email="client@apex.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )

        self.location = Location.objects.create(
            tenant=self.tenant, name="Apex Studio", address="456 Power Ave", timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant, location=self.location, name="Main Studio", capacity=20
        )
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant, location=self.location, name="HIIT Class", duration_min=60, default_capacity=20
        )
        self.package_type = PackageType.objects.create(
            tenant=self.tenant, location=self.location, name="Standard Pass", credit_count=10, validity_days=30, price=100.00
        )
        self.client.credentials(HTTP_X_TENANT_ID=str(self.tenant.id))

    def _auth(self, user):
        self.client.force_authenticate(user=user)
        self.client.credentials(HTTP_X_TENANT_ID=str(self.tenant.id))

    def test_default_booking_settings(self):
        self._auth(self.owner)
        response = self.client.get('/api/scheduling/booking-settings/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['session_late_cancellation_hours'], 12)
        self.assertEqual(response.data['session_booking_credits'], 1)
        self.assertEqual(response.data['appointment_late_cancellation_hours'], 12)
        self.assertEqual(response.data['appointment_booking_credits'], 1)

    def test_update_booking_settings_as_admin(self):
        self._auth(self.owner)
        payload = {
            "session_late_cancellation_hours": 6,
            "session_booking_credits": 2,
            "appointment_late_cancellation_hours": 4,
            "appointment_booking_credits": 3
        }
        response = self.client.patch('/api/scheduling/booking-settings/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['session_late_cancellation_hours'], 6)
        self.assertEqual(response.data['session_booking_credits'], 2)
        self.assertEqual(response.data['appointment_late_cancellation_hours'], 4)
        self.assertEqual(response.data['appointment_booking_credits'], 3)

        settings_obj = TenantBookingSettings.objects.get(tenant=self.tenant)
        self.assertEqual(settings_obj.session_late_cancellation_hours, 6)
        self.assertEqual(settings_obj.session_booking_credits, 2)
        self.assertEqual(settings_obj.appointment_late_cancellation_hours, 4)
        self.assertEqual(settings_obj.appointment_booking_credits, 3)

    def test_update_booking_settings_using_aliases(self):
        self._auth(self.owner)
        payload = {
            "session_late_cancellation": 8,
            "session_credits": 3,
            "appointment_late_cancellation": 5,
            "appointment_credits": 2
        }
        response = self.client.patch('/api/scheduling/booking-settings/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['session_late_cancellation_hours'], 8)
        self.assertEqual(response.data['session_booking_credits'], 3)
        self.assertEqual(response.data['appointment_late_cancellation_hours'], 5)
        self.assertEqual(response.data['appointment_booking_credits'], 2)

    def test_client_cannot_update_booking_settings(self):
        self._auth(self.client_user)
        response = self.client.patch('/api/scheduling/booking-settings/', {"session_booking_credits": 5}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_validation_rejects_invalid_values(self):
        self._auth(self.owner)
        response = self.client.patch('/api/scheduling/booking-settings/', {"session_booking_credits": 0}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.patch('/api/scheduling/booking-settings/', {"session_late_cancellation_hours": -1}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_session_booking_requires_configured_credits(self):
        settings_obj = TenantBookingSettings.get_or_create_for_tenant(self.tenant)
        settings_obj.session_booking_credits = 2
        settings_obj.session_late_cancellation_hours = 6
        settings_obj.save()

        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=self.package_type,
            credits_remaining=1,
            expires_at=timezone.now() + timedelta(days=30)
        )

        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=1),
            capacity=10
        )

        self._auth(self.client_user)
        response = self.client.post('/api/scheduling/bookings/', {"session": str(session.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

        pkg.credits_remaining = 5
        pkg.save()

        response = self.client.post('/api/scheduling/bookings/', {"session": str(session.id)}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 3)

        booking = Booking.objects.get(session=session, client=self.client_user)
        self.assertEqual(booking.credits_used, 2)

    def test_session_cancellation_early_and_late(self):
        settings_obj = TenantBookingSettings.get_or_create_for_tenant(self.tenant)
        settings_obj.session_booking_credits = 2
        settings_obj.session_late_cancellation_hours = 6
        settings_obj.save()

        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=self.package_type,
            credits_remaining=5,
            expires_at=timezone.now() + timedelta(days=30)
        )

        session_early = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(hours=10),
            end_at=timezone.now() + timedelta(hours=11),
            capacity=10
        )

        self._auth(self.client_user)
        res_early_book = self.client.post('/api/scheduling/bookings/', {"session": str(session_early.id)}, format='json')
        self.assertEqual(res_early_book.status_code, status.HTTP_201_CREATED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 3)

        booking_early_id = res_early_book.data['id']
        res_early_cancel = self.client.delete(f'/api/scheduling/bookings/{booking_early_id}/')
        self.assertEqual(res_early_cancel.status_code, status.HTTP_200_OK)
        self.assertTrue(res_early_cancel.data['refunded'])
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 5)

        session_late = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(hours=3),
            end_at=timezone.now() + timedelta(hours=4),
            capacity=10
        )

        res_late_book = self.client.post('/api/scheduling/bookings/', {"session": str(session_late.id)}, format='json')
        self.assertEqual(res_late_book.status_code, status.HTTP_201_CREATED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 3)

        booking_late_id = res_late_book.data['id']
        res_late_cancel = self.client.delete(f'/api/scheduling/bookings/{booking_late_id}/')
        self.assertEqual(res_late_cancel.status_code, status.HTTP_200_OK)
        self.assertFalse(res_late_cancel.data['refunded'])
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 3)

    def test_appointment_booking_requires_configured_credits(self):
        settings_obj = TenantBookingSettings.get_or_create_for_tenant(self.tenant)
        settings_obj.appointment_booking_credits = 3
        settings_obj.appointment_late_cancellation_hours = 4
        settings_obj.save()

        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=self.package_type,
            credits_remaining=2,
            expires_at=timezone.now() + timedelta(days=30)
        )

        start_time = timezone.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=1)

        payload = {
            "provider": str(self.trainer.id),
            "location": str(self.location.id),
            "start_at": start_time.isoformat(),
            "end_at": end_time.isoformat()
        }

        self._auth(self.client_user)
        response = self.client.post('/api/scheduling/appointments/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

        pkg.credits_remaining = 6
        pkg.save()

        response = self.client.post('/api/scheduling/appointments/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 3)

        appointment = Appointment.objects.get(id=response.data['id'])
        self.assertEqual(appointment.credits_used, 3)

    def test_appointment_cancellation_early_and_late(self):
        settings_obj = TenantBookingSettings.get_or_create_for_tenant(self.tenant)
        settings_obj.appointment_booking_credits = 2
        settings_obj.appointment_late_cancellation_hours = 4
        settings_obj.save()

        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=self.package_type,
            credits_remaining=6,
            expires_at=timezone.now() + timedelta(days=30)
        )

        early_start = timezone.now() + timedelta(hours=8)
        early_end = early_start + timedelta(hours=1)
        payload_early = {
            "provider": str(self.trainer.id),
            "location": str(self.location.id),
            "start_at": early_start.isoformat(),
            "end_at": early_end.isoformat()
        }

        self._auth(self.client_user)
        res_early_book = self.client.post('/api/scheduling/appointments/', payload_early, format='json')
        self.assertEqual(res_early_book.status_code, status.HTTP_201_CREATED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 4)

        appt_early_id = res_early_book.data['id']
        res_early_cancel = self.client.post(f'/api/scheduling/appointments/{appt_early_id}/cancel/')
        self.assertEqual(res_early_cancel.status_code, status.HTTP_200_OK)
        self.assertTrue(res_early_cancel.data['refunded'])
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 6)

        late_start = timezone.now() + timedelta(hours=2)
        late_end = late_start + timedelta(hours=1)
        payload_late = {
            "provider": str(self.trainer.id),
            "location": str(self.location.id),
            "start_at": late_start.isoformat(),
            "end_at": late_end.isoformat()
        }

        res_late_book = self.client.post('/api/scheduling/appointments/', payload_late, format='json')
        self.assertEqual(res_late_book.status_code, status.HTTP_201_CREATED)
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 4)

        appt_late_id = res_late_book.data['id']
        res_late_cancel = self.client.delete(f'/api/scheduling/appointments/{appt_late_id}/')
        self.assertEqual(res_late_cancel.status_code, status.HTTP_200_OK)
        self.assertFalse(res_late_cancel.data['refunded'])
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 4)
