"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.food_logger.views import AnalyzeFoodAPIView

urlpatterns = [
    path('admin/', admin.site.urls),
 
    path('ai/analyze-food/', AnalyzeFoodAPIView.as_view(), name='top-ai-analyze-food'),
    path('api/v1/ai/analyze-food/', AnalyzeFoodAPIView.as_view(), name='v1-ai-analyze-food'),
    path('api/v1/ai/scan-meal/', AnalyzeFoodAPIView.as_view(), name='v1-ai-scan-meal'),
    path('api/v1/ai/scan-food/', AnalyzeFoodAPIView.as_view(), name='v1-ai-scan-food'),

    # API Version 1
    path('api/v1/platform/', include('apps.core.urls')), 
    path('api/v1/users/', include('apps.users.urls')),

    path('api/v1/scheduling/', include('apps.scheduling.urls')),  
    path('api/v1/', include('apps.reflection_logger.urls')),
    path('api/v1/reflection_logger/', include('apps.reflection_logger.urls')),
    path('api/v1/nutritionx/', include('apps.nutritionX.urls')),
    path('api/v1/nutrition/', include('apps.nutritionX.urls')),
    path('api/v1/foodlogger/', include('apps.food_logger.urls')),
    path('api/v1/foodloger/', include('apps.food_logger.urls')),
    path('api/v1/food/', include('apps.food_logger.urls')),
    path('api/v1/support/', include('apps.support.urls')),
    path('api/v1/inventory/', include('apps.inventory.urls')),
    path("api/v1/socialnetwork/", include("apps.socialnetwork.urls")),
    path('api/v1/payments/', include('apps.payments.urls')),
    path('api/v1/workout/', include('apps.workout.urls')),
    path('api/v1/workouts/', include('apps.workout.urls')),
    path('api/v1/assesments/', include('apps.client_assements.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/rewards/', include('apps.rewards.urls')),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
