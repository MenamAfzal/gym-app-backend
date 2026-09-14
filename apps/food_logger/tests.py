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


class LogFoodAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='client_logger@test.com',
            password='TestPassword123!',
            role=UserRole.CLIENT
        )
        self.client.force_authenticate(user=self.user)

    def test_log_food_with_ai_analyzer_output(self):
        """Verify logging food using the exact payload output structure from the AI analyzer."""
        payload = {
            "meal_type": "breakfast",
            "date": "2026-09-13",
            "serving": 1,
            "food_item": {
                "status": "success",
                "is_food": True,
                "message": "Meal analyzed successfully with AI",
                "name": "Banana Fruit",
                "calories": 105.0,
                "protein": 1.3,
                "carbs": 26.9,
                "fats": 0.4,
                "fiber": "3.1",
                "sugars": "14.4",
                "sodium": "1.2",
                "potassium": "422.4",
                "cholesterol": "0.0",
                "saturated_fat": "0.1",
                "serving_qty": "1",
                "serving_unit": "medium (7\" to 7-7/8\" long)",
                "serving_info": "1 medium (7\" to 7-7/8\" long) (118g)",
                "serving_weight_grams": "118",
                "image": "http://16.171.26.53/media/meal_scans/bc978241-6b2f-444c-8c22-66bc93c2944f.jpg?query=param&token=abcdef1234567890",
                "tag_name": "banana fruit",
                "brand_name_item_name": "Banana Fruit",
                "locale": "en-US",
                "nix_item_id": "kimi_86af7f6ef588",
                "nix_brand_id": "kimi_ai_vision",
                "items": [{"name": "Banana"}],
                "foods": [{"name": "Banana"}],
                "ai_provider": "kimi"
            }
        }

        response = self.client.post(
            "/api/v1/foodlogger/log-food/",
            payload,
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("meal_totals", response.data)
        self.assertEqual(response.data["meal_totals"]["calories"], 105.0)

    def test_log_food_alias_route_foodloger(self):
        """Verify POST /api/v1/foodloger/log-food/ (alias route) works without DataError."""
        payload = {
            "meal_type": "Lunch",
            "date": "2026-09-13",
            "serving": 2,
            "food_item": {
                "name": "Grilled Chicken Breast with Steamed Broccoli and Brown Rice",
                "calories": 250.0,
                "protein": 35.0,
                "carbs": 15.0,
                "fats": 4.0,
                "serving_qty": "1",
                "serving_unit": "portion (approx 250g with vegetables and dressing)",
                "serving_info": "1 portion (approx 250g with vegetables and dressing)",
                "image": "https://example-s3-bucket.s3.amazonaws.com/meals/photos/custom/2026/09/13/photo_with_long_url_parameter_query_string.jpg?auth=very_long_security_token_string_here",
                "tag_name": "grilled chicken breast with steamed broccoli and brown rice"
            }
        }

        response = self.client.post(
            "/api/v1/foodloger/log-food/",
            payload,
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["meal_totals"]["calories"], 500.0)
        self.assertEqual(response.data["meal_totals"]["protein"], 70.0)

    def test_log_food_flat_payload_and_retrieve_and_delete(self):
        """Verify flat payload logging, GET /log-food/?date=..., and DELETE item."""
        flat_payload = {
            "meal_type": "dinner",
            "food_name": "Salmon Fillet",
            "calories": 300,
            "protein": 34,
            "carbs": 0,
            "fats": 18,
            "serving": 1,
            "date": "2026-09-13"
        }

        # 1. Log food
        post_resp = self.client.post(
            "/api/v1/foodlogger/log-food/",
            flat_payload,
            format="json"
        )
        self.assertEqual(post_resp.status_code, status.HTTP_201_CREATED)

        # 2. Get logged meals
        get_resp = self.client.get(
            "/api/v1/foodlogger/log-food/?date=2026-09-13"
        )
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_resp.data["meals"]), 1)
        logged_items = get_resp.data["meals"][0]["items"]
        self.assertEqual(len(logged_items), 1)
        item_id = logged_items[0]["id"]

        # 3. Delete item
        del_resp = self.client.delete(
            f"/api/v1/foodlogger/log-food/?logged_meal_id={item_id}"
        )
        self.assertEqual(del_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(del_resp.data["detail"], "Item deleted successfully")


