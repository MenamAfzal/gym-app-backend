import os
import io
import base64
from unittest.mock import patch, MagicMock
from PIL import Image
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User, UserRole
from apps.food_logger.models import StaffRecipe, StaffRecipeItem, UserRecipeItem


def create_dummy_image_file(filename='recipe_test.jpg', color=(0, 255, 0)):
    buf = io.BytesIO()
    img = Image.new('RGB', (120, 120), color=color)
    img.save(buf, 'jpeg')
    buf.seek(0)
    return SimpleUploadedFile(filename, buf.read(), content_type='image/jpeg')


def create_dummy_base64_image(color=(0, 0, 255)):
    buf = io.BytesIO()
    img = Image.new('RGB', (80, 80), color=color)
    img.save(buf, 'png')
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode('utf-8')
    return f"data:image/png;base64,{encoded}"


class RecipeImageStorageTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_user = User.objects.create_user(
            email='chef_staff@gymtest.com',
            password='StaffPassword123!',
            role=UserRole.TRAINER
        )
        self.client_user = User.objects.create_user(
            email='client_food@gymtest.com',
            password='ClientPassword123!',
            role=UserRole.CLIENT
        )
        self.client.force_authenticate(user=self.staff_user)

    def test_staff_create_recipe_with_uploaded_file(self):
        """
        Verify that when a staff user selects/uploads an image file when creating a recipe:
        1. The file is physically written to MEDIA_ROOT/recipe_images/
        2. StaffRecipeItem.image in DB contains the /media/recipe_images/... path
        3. The response contains the full absolute URL for the frontend
        """
        image_file = create_dummy_image_file('protein_smoothie.jpg')
        payload = {
            'category': 'Breakfast',
            'name': 'Berry Protein Smoothie',
            'calories': 280,
            'protein': 30,
            'carbs': 25,
            'fats': 4,
            'serving_qty': '1',
            'serving_unit': 'glass',
            'image': image_file
        }

        response = self.client.post(
            '/api/v1/foodlogger/staff-recipes/',
            payload,
            format='multipart'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = StaffRecipeItem.objects.filter(name='Berry Protein Smoothie').first()
        self.assertIsNotNone(item)
        self.assertTrue(item.image.startswith('/media/recipe_images/'))
        self.assertTrue(item.image.endswith('.jpg'))

        # Check physical existence on the backend filesystem
        rel_path = item.image.replace('/media/', '')
        abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
        self.assertTrue(os.path.exists(abs_path), f"File {abs_path} should exist on disk")

        # Check response contains full absolute URL
        items_resp = response.data.get('items', [])
        self.assertTrue(len(items_resp) > 0)
        returned_img = items_resp[0].get('image')
        self.assertTrue(returned_img.startswith('http://testserver/media/recipe_images/'))

    def test_staff_create_recipe_with_external_image_url(self):
        """
        Verify that when an image is provided as an external URL (e.g. from an image picker or web):
        1. The backend downloads the image and saves it to MEDIA_ROOT/recipe_images/
        2. StaffRecipeItem.image stores the backend media URL path
        3. The response contains the full absolute URL pointing to the backend media
        """
        dummy_img = io.BytesIO()
        Image.new('RGB', (100, 100), color=(255, 128, 0)).save(dummy_img, 'jpeg')
        dummy_img.seek(0)
        dummy_bytes = dummy_img.read()

        mock_resp = MagicMock()
        mock_resp.read.return_value = dummy_bytes
        mock_resp.headers.get_content_type.return_value = 'image/jpeg'
        mock_resp.__enter__.return_value = mock_resp

        with patch('urllib.request.urlopen', return_value=mock_resp):
            payload = {
                'category': 'Lunch',
                'name': 'Quinoa Chicken Salad',
                'calories': 420,
                'protein': 35,
                'carbs': 40,
                'fats': 12,
                'image': 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?w=500'
            }

            response = self.client.post(
                '/api/v1/foodlogger/staff-recipes/',
                payload,
                format='multipart'
            )

            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            item = StaffRecipeItem.objects.filter(name='Quinoa Chicken Salad').first()
            self.assertIsNotNone(item)
            self.assertTrue(item.image.startswith('/media/recipe_images/'))

            # Check that the file was written to disk
            rel_path = item.image.replace('/media/', '')
            abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
            self.assertTrue(os.path.exists(abs_path), f"File {abs_path} should exist on disk")

            # Check response contains full absolute URL
            returned_img = response.data['items'][0]['image']
            self.assertTrue(returned_img.startswith('http://testserver/media/recipe_images/'))

    def test_staff_create_recipe_with_base64_image(self):
        """
        Verify that base64 encoded data URI images are decoded and stored on backend filesystem.
        """
        b64_image = create_dummy_base64_image()
        payload = {
            'category': 'Dinner',
            'name': 'Grilled Salmon with Asparagus',
            'calories': 500,
            'protein': 45,
            'carbs': 10,
            'fats': 22,
            'image': b64_image
        }

        response = self.client.post(
            '/api/v1/foodlogger/staff-recipes/',
            payload,
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = StaffRecipeItem.objects.filter(name='Grilled Salmon with Asparagus').first()
        self.assertIsNotNone(item)
        self.assertTrue(item.image.startswith('/media/recipe_images/'))
        self.assertTrue(item.image.endswith('.png'))

        # Check file exists on disk
        rel_path = item.image.replace('/media/', '')
        abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
        self.assertTrue(os.path.exists(abs_path))

    def test_staff_recipe_item_update_with_new_image_file(self):
        """
        Verify PUT/PATCH /staff-recipie-update/ properly updates item image with newly uploaded file.
        """
        recipe = StaffRecipe.objects.create(category='Post Workout')
        item = StaffRecipeItem.objects.create(
            recipe=recipe,
            created_by=self.staff_user,
            name='Protein Shake',
            calories=200,
            image=None
        )

        new_image = create_dummy_image_file('new_shake.jpg')
        patch_resp = self.client.patch(
            f'/api/v1/foodlogger/staff-recipie-update/?recipe_id={recipe.id}&item_id={item.id}',
            {'image': new_image},
            format='multipart'
        )

        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertTrue(item.image.startswith('/media/recipe_images/'))

        # Check file on disk
        rel_path = item.image.replace('/media/', '')
        abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
        self.assertTrue(os.path.exists(abs_path))

        # Check response contains full absolute URL
        self.assertTrue(patch_resp.data['image'].startswith('http://testserver/media/recipe_images/'))

    def test_staff_recipe_item_update_without_image_preserves_existing_image(self):
        """
        Verify that a partial update (PATCH) modifying only recipe name leaves existing image unchanged.
        """
        recipe = StaffRecipe.objects.create(category='Snack')
        item = StaffRecipeItem.objects.create(
            recipe=recipe,
            created_by=self.staff_user,
            name='Original Granola Bar',
            calories=180,
            image='/media/recipe_images/existing_test_image.jpg'
        )

        patch_resp = self.client.patch(
            f'/api/v1/foodlogger/staff-recipie-update/?recipe_id={recipe.id}&item_id={item.id}',
            {'name': 'Updated Granola Bar'},
            format='json'
        )

        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.name, 'Updated Granola Bar')
        self.assertEqual(item.image, '/media/recipe_images/existing_test_image.jpg')
        self.assertEqual(patch_resp.data['image'], 'http://testserver/media/recipe_images/existing_test_image.jpg')

    def test_staff_recipe_items_list_provides_full_image_urls(self):
        """
        Verify GET /staff-recipes-items/ provides full absolute URLs for recipe images in grouped output.
        """
        recipe = StaffRecipe.objects.create(category='Pre Workout')
        StaffRecipeItem.objects.create(
            recipe=recipe,
            created_by=self.staff_user,
            name='Energy Bar',
            calories=150,
            image='/media/recipe_images/energy_bar_sample.jpg'
        )

        get_resp = self.client.get('/api/v1/foodlogger/staff-recipes-items/')
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)

        categories = get_resp.data
        found = False
        for cat in categories:
            if cat.get('category_name') == 'Pre Workout':
                items = cat.get('items', [])
                for it in items:
                    if it.get('name') == 'Energy Bar':
                        found = True
                        self.assertEqual(
                            it.get('image'),
                            'http://testserver/media/recipe_images/energy_bar_sample.jpg'
                        )
        self.assertTrue(found, "Energy Bar should be found in grouped categories with absolute image URL")

    def test_client_recipe_item_create_and_update_with_image(self):
        """
        Verify client custom recipe (/add-client-recipes/) also correctly stores uploaded images to disk.
        """
        self.client.force_authenticate(user=self.client_user)
        img_file = create_dummy_image_file('client_oatmeal.jpg')

        create_resp = self.client.post(
            '/api/v1/foodlogger/add-client-recipes/',
            {
                'name': 'My Morning Oats',
                'calories': 320,
                'protein': 12,
                'carbs': 48,
                'fats': 6,
                'image': img_file
            },
            format='multipart'
        )

        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        user_item = UserRecipeItem.objects.filter(name='My Morning Oats', user=self.client_user).first()
        self.assertIsNotNone(user_item)
        self.assertTrue(user_item.image.startswith('/media/recipe_images/'))

        # Verify on disk
        rel_path = user_item.image.replace('/media/', '')
        self.assertTrue(os.path.exists(os.path.join(settings.MEDIA_ROOT, rel_path)))

        # Verify returned representation has absolute URL
        self.assertTrue(create_resp.data['image'].startswith('http://testserver/media/recipe_images/'))
