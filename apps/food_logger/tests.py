import io
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User, UserRole
from apps.food_logger.kimi_service import KimiFoodScannerService


def create_test_image(filename='test_meal.jpg'):
    """Generates a small valid test JPEG image in memory with a given filename."""
    file = io.BytesIO()
    image = Image.new('RGB', (100, 100), color=(255, 0, 0))
    image.save(file, 'jpeg')
    file.seek(0)
    return SimpleUploadedFile(filename, file.read(), content_type='image/jpeg')


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
        uploaded_image = create_test_image('meal.jpg')
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

    def test_analyze_food_mango_specific_nutrition(self):
        """Verify uploading a mango image returns accurate mango-specific nutritional data."""
        uploaded_image = create_test_image('fresh_mango.jpg')
        response = self.client.post(
            '/api/v1/foodlogger/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')
        self.assertTrue(response.data.get('is_food'))
        self.assertIn('Mango', response.data.get('name'))
        # Mango ~202 kcal, 2.8g protein, 50.3g carbs, 1.3g fat
        self.assertAlmostEqual(response.data.get('calories'), 202.0, delta=10)
        self.assertAlmostEqual(response.data.get('carbs'), 50.3, delta=5)
        self.assertAlmostEqual(response.data.get('protein'), 2.8, delta=1.5)

    def test_analyze_food_banana_specific_nutrition(self):
        """Verify uploading a banana image returns accurate banana-specific nutritional data."""
        uploaded_image = create_test_image('banana_fruit.jpg')
        response = self.client.post(
            '/api/v1/foodlogger/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')
        self.assertTrue(response.data.get('is_food'))
        self.assertIn('Banana', response.data.get('name'))
        # Banana ~105 kcal, 1.3g protein, 27g carbs, 0.4g fat
        self.assertAlmostEqual(response.data.get('calories'), 105.0, delta=10)
        self.assertAlmostEqual(response.data.get('carbs'), 27.0, delta=5)
        self.assertAlmostEqual(response.data.get('protein'), 1.3, delta=1.5)

    def test_analyze_non_food_human_rejected(self):
        """Verify uploading a human/person image is identified as non-food and returns 400."""
        uploaded_image = create_test_image('human_portrait.jpg')
        response = self.client.post(
            '/api/v1/foodlogger/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('is_food'), False)
        self.assertIn('No food detected', response.data.get('error'))

    def test_analyze_non_food_chair_rejected(self):
        """Verify uploading a chair/furniture image is identified as non-food and returns 400."""
        uploaded_image = create_test_image('wooden_chair.jpg')
        response = self.client.post(
            '/api/v1/foodlogger/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('is_food'), False)
        self.assertIn('No food detected', response.data.get('error'))

    def test_v1_foodlogger_analyze_food_success(self):
        """Verify POST /api/v1/foodlogger/analyze-food/ returns 200 OK."""
        uploaded_image = create_test_image('healthy_salad.jpg')
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
        uploaded_image = create_test_image('grilled_chicken.jpg')
        response = self.client.post(
            '/api/v1/food/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')

    def test_v1_nutrition_analyze_food_success(self):
        """Verify POST /api/v1/nutrition/analyze-food/ alias returns 200 OK."""
        uploaded_image = create_test_image('steamed_rice.jpg')
        response = self.client.post(
            '/api/v1/nutrition/analyze-food/',
            {'image': uploaded_image},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('status'), 'success')

    def test_scan_meal_alias_success(self):
        """Verify POST /api/v1/foodlogger/scan-meal/ alias returns 200 OK."""
        uploaded_image = create_test_image('pizza_slice.jpg')
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
        uploaded_image = create_test_image('fresh_mango.jpg')
        result = service.analyze_food_image(uploaded_image, user=self.user)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['ai_provider'], 'kimi')
        self.assertGreater(result['calories'], 0)
        self.assertTrue(result['image'].startswith('/media/meal_scans/'))

