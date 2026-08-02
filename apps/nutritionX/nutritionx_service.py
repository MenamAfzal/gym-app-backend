import json
import requests

class NutritionXService:
    NUTRITIONIX_BASE_URL = "https://trackapi.nutritionix.com/v2"
    NUTRITIONIX_APP_ID = "d6461528"
    NUTRITIONIX_API_KEY = "8c1164a5498c1bb72fbe34bed6eb3346"

    def nutrition_instant_search(self, query):
        url = f"{self.NUTRITIONIX_BASE_URL}/search/instant"
        headers = {
            "Content-Type": "application/json",
            "x-app-id": self.NUTRITIONIX_APP_ID,
            "x-app-key": self.NUTRITIONIX_API_KEY,
        }
        data = {
            "query": query
        }

        response = requests.get(url, headers=headers, params=data, timeout=10)
        return response

    def nutrients(self, drink_name):
        url = f"{self.NUTRITIONIX_BASE_URL}/natural/nutrients"
        headers = {
            "Content-Type": "application/json",
            "x-app-id": self.NUTRITIONIX_APP_ID,
            "x-app-key": self.NUTRITIONIX_API_KEY,
        }
        data = {
            "query": drink_name
        }

        response = requests.post(url, headers=headers, json=data, timeout=10)
        return response
