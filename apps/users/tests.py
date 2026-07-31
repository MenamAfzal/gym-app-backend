from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.scheduling.models import Location, ClassTemplate, ClassSession, Booking, Package, PackageType
from django.utils import timezone
from datetime import timedelta

class ClientDetailedSchedulingAPITest(TestCase):
    def setUp(self):
        # Create tenant
        self.tenant = Tenant.objects.create(name="Padel Gym", subdomain="padel")
        
        # Create Gym Owner
        self.owner = User.objects.create_user(
            email="owner@padel.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        
        # Create Client
        self.client_user = User.objects.create_user(
            email="client@padel.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        
        # Setup API Client
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.owner)

    def test_clients_detailed_scheduling(self):
        # Create a location
        location = Location.objects.create(
            tenant=self.tenant,
            name="Downtown Studio",
            address="123 Street",
            timezone="America/New_York"
        )
        
        # Create class template
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=location,
            name="Power Pilates",
            duration_min=60
        )
        
        # Create a past and an upcoming class session
        now = timezone.now()
        past_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        upcoming_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        # Create PackageType and assign Package to client
        pkg_type = PackageType.objects.create(
            tenant=self.tenant,
            location=location,
            name="Standard 5-Pack",
            credit_count=5,
            price="100.00",
            validity_days=30
        )
        
        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=pkg_type,
            credits_remaining=5,
            expires_at=now + timedelta(days=30)
        )
        
        # Bookings for client
        past_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=past_session,
            credit_source=pkg,
            status="attended"
        )
        
        upcoming_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=upcoming_session,
            credit_source=pkg,
            status="booked"
        )
        
        # Request API with subdomain HTTP_HOST header
        url = reverse('users-clients-detailed-scheduling')
        response = self.api_client.get(url, HTTP_HOST='padel.testserver')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check pagination fields
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)
        
        results = response.data['results']
        self.assertEqual(len(results), 1)
        client_data = results[0]
        
        self.assertEqual(client_data['email'], self.client_user.email)
        
        # Verify stats
        stats = client_data['stats']
        self.assertEqual(stats['total_classes_booked'], 1)
        self.assertEqual(stats['total_classes_attended'], 1)
        self.assertEqual(stats['total_packages_purchased'], 1)
        
        # Verify next/previous class session info
        self.assertIsNotNone(client_data['next_class_session'])
        self.assertEqual(client_data['next_class_session']['booking_id'], str(upcoming_booking.id))
        self.assertEqual(client_data['next_class_session']['class_name'], "Power Pilates")
        self.assertEqual(client_data['next_class_session']['status'], "booked")
        
        self.assertIsNotNone(client_data['previous_class_session'])
        self.assertEqual(client_data['previous_class_session']['booking_id'], str(past_booking.id))
        self.assertEqual(client_data['previous_class_session']['class_name'], "Power Pilates")
        self.assertEqual(client_data['previous_class_session']['status'], "attended")


class StaffDetailedSchedulingAPITest(TestCase):
    def setUp(self):
        # Create tenant
        self.tenant = Tenant.objects.create(name="Padel Gym", subdomain="padel")
        
        # Create Gym Owner
        self.owner = User.objects.create_user(
            email="owner@padel.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        
        # Create Trainer
        self.trainer = User.objects.create_user(
            email="trainer@padel.com",
            password="password123",
            role=UserRole.TRAINER,
            tenant=self.tenant
        )
        
        # Create Client
        self.client_user = User.objects.create_user(
            email="client@padel.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        
        # Setup API Client
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.owner)

    def test_staff_detailed_scheduling(self):
        from apps.scheduling.models import StaffLocation, StaffAvailability, StaffClientAssignment, Appointment, SubstituteRequest
        
        # Create a location
        location = Location.objects.create(
            tenant=self.tenant,
            name="Downtown Studio",
            address="123 Street",
            timezone="America/New_York"
        )
        
        # Map staff to location
        StaffLocation.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            location=location
        )
        
        # Define availability
        StaffAvailability.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            weekday_or_date="monday",
            start_time="08:00:00",
            end_time="17:00:00",
            is_blackout=False
        )
        
        # Assign client
        StaffClientAssignment.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            client=self.client_user
        )
        
        # Create template & sessions
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=location,
            name="Power Pilates",
            duration_min=60
        )
        
        now = timezone.now()
        past_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            staff=self.trainer,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        upcoming_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            staff=self.trainer,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        # Private Appointment
        appt = Appointment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            provider=self.trainer,
            location=location,
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, hours=-1),
            status="scheduled"
        )
        
        # Substitute request
        sub_req = SubstituteRequest.objects.create(
            tenant=self.tenant,
            session=past_session,
            requested_by_staff=self.trainer,
            status="open"
        )
        
        # Request API
        url = reverse('users-staff-detailed-scheduling')
        response = self.api_client.get(url, HTTP_HOST='padel.testserver')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('count', response.data)
        
        results = response.data['results']
        # Gym owner and Trainer are both staff (non-client)
        self.assertEqual(len(results), 2)
        
        # Find trainer's data
        trainer_data = next(item for item in results if item['id'] == str(self.trainer.id))
        self.assertEqual(trainer_data['email'], self.trainer.email)
        
        # Verify stats
        stats = trainer_data['stats']
        self.assertEqual(stats['total_upcoming_classes'], 1)
        self.assertEqual(stats['total_past_classes'], 1)
        self.assertEqual(stats['total_hours_taught'], 1.0)
        self.assertEqual(stats['total_private_appointments'], 1)
        self.assertEqual(stats['total_assigned_clients'], 1)
        self.assertEqual(stats['total_substitute_requests_raised'], 1)
        
        # Verify location, availability, assignments, next/prev pointers
        self.assertEqual(len(trainer_data['locations']), 1)
        self.assertEqual(trainer_data['locations'][0]['name'], "Downtown Studio")
        
        self.assertEqual(len(trainer_data['availabilities']), 1)
        self.assertEqual(trainer_data['availabilities'][0]['weekday_or_date'], "monday")
        
        self.assertEqual(len(trainer_data['assigned_clients']), 1)
        self.assertEqual(trainer_data['assigned_clients'][0]['client_email'], self.client_user.email)
        
        self.assertIsNotNone(trainer_data['next_class_session'])
        self.assertEqual(trainer_data['next_class_session']['session_id'], str(upcoming_session.id))
        
        self.assertIsNotNone(trainer_data['previous_class_session'])
        self.assertEqual(trainer_data['previous_class_session']['session_id'], str(past_session.id))
        
        self.assertIsNotNone(trainer_data['next_appointment'])
        self.assertEqual(trainer_data['next_appointment']['appointment_id'], str(appt.id))
        
        self.assertEqual(len(trainer_data['substitute_requests_raised']), 1)
        self.assertEqual(trainer_data['substitute_requests_raised'][0]['id'], str(sub_req.id))

