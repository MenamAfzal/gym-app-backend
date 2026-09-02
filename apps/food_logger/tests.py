import io
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User, UserRole
from apps.food_logger.kimi_service import KimiFoodScannerService


def create_test_image():
    """Generates a small valid test JPEG image in memory."""
    file = io.BytesIO()
    image = Image.new('RGB', (100, 100), color=(255, 0, 0))
    image.save(file, 'jpeg')
    file.seek(0)
    return SimpleUploadedFile('test_meal.jpg', file.read(), content_type='image/jpeg')


class AIAnalyzeFoodAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='client_scanner@test.com',
            password='TestPassword123!',
            role=UserRole.CLIENT
        )
        self.client.force_authenticate(user=self.user)

    def test_top_level_ai_analyze_food_success(self):
        """Verify POST /ai/analyze-food/ successfully processes food image."""
        uploaded_image = create_test_image()
        response = self.client.post(
            '/ai/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')
        self.assertIn('calories', response.data)
        self.assertIn('protein', response.data)
        self.assertIn('carbs', response.data)
        self.assertIn('fats', response.data)
        self.assertIn('food_item', response.data)
        self.assertIn('image', response.data)
        self.assertIn('items', response.data)

    def test_v1_foodlogger_analyze_food_success(self):
        """Verify POST /api/v1/foodlogger/analyze-food/ returns 200 OK."""
        uploaded_image = create_test_image()
        response = self.client.post(
            '/api/v1/foodlogger/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')
        self.assertIsNotNone(response.data.get('food_item'))
        self.assertEqual(response.data['food_item']['name'], response.data['name'])

    def test_v1_food_analyze_food_success(self):
        """Verify POST /api/v1/food/analyze-food/ alias returns 200 OK."""
        uploaded_image = create_test_image()
        response = self.client.post(
            '/api/v1/food/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')

    def test_v1_nutrition_analyze_food_success(self):
        """Verify POST /api/v1/nutrition/analyze-food/ alias returns 200 OK."""
        uploaded_image = create_test_image()
        response = self.client.post(
            '/api/v1/nutrition/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')

    def test_scan_meal_alias_success(self):
        """Verify POST /api/v1/foodlogger/scan-meal/ alias returns 200 OK."""
        uploaded_image = create_test_image()
        response = self.client.post(
            '/api/v1/foodlogger/scan-meal/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')

    def test_analyze_food_missing_image(self):
        """Verify 400 Bad Request when no image file is sent."""
        response = self.client.post(
            '/ai/analyze-food/',
            {},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_kimi_service_unit(self):
        """Test KimiFoodScannerService logic directly."""
        service = KimiFoodScannerService()
        uploaded_image = create_test_image()
        result = service.analyze_food_image(uploaded_image, user=self.user)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['ai_provider'], 'kimi')
        self.assertGreater(result['calories'], 0)
        self.assertTrue(result['image'].startswith('/media/meal_scans/'))
