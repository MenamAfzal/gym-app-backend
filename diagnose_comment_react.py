import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.prod')
django.setup()

from apps.socialnetwork.models import Comment
from apps.core.tenants.models import Tenant
from apps.users.models import User
from apps.core.tenants.context import set_current_tenant, get_current_tenant
from apps.socialnetwork.views import CommentViewSet
from rest_framework.test import APIRequestFactory, force_authenticate

def diagnose():
    print("=== STARTING DIAGNOSTIC ===")
    
    # 1. Fetch Tenant
    tenant_id = '5771abc2-61a1-404a-8d29-d6ee4331cb6e'
    try:
        tenant = Tenant.objects.get(id=tenant_id)
        print(f"Loaded tenant: {tenant.name} ({tenant.id})")
    except Tenant.DoesNotExist:
        print("Tenant not found!")
        return

    # 2. Fetch User
    email = 'abdullahafzal2122@yopmail.com'
    try:
        user = User.objects.get(email=email)
        print(f"Loaded user: {user.email} (Tenant: {user.tenant_id})")
    except User.DoesNotExist:
        print("User not found!")
        return

    # 3. Set Context and check exists
    token = set_current_tenant(tenant)
    comment_id = '1194bd0b-61be-4ebe-8cb7-74d386f489cf'
    try:
        print("Current tenant context:", get_current_tenant())
        exists_in_all = Comment.all_objects.filter(id=comment_id).exists()
        exists_in_tenant = Comment.objects.filter(id=comment_id).exists()
        print(f"Exists in all_objects: {exists_in_all}")
        print(f"Exists in default objects (filtered by tenant): {exists_in_tenant}")
        print(f"SQL for Comment.objects.all(): {Comment.objects.all().query}")
    finally:
        from apps.core.tenants.context import reset_current_tenant
        reset_current_tenant(token)

    # 4. Invoke ViewSet directly using request factory
    print("\n--- Invoking CommentViewSet.react() ---")
    factory = APIRequestFactory()
    url = f"/api/v1/socialnetwork/comments/{comment_id}/react/"
    request = factory.post(url, {'type': 'LIKE'}, format='json')
    
    # Mock tenant middleware properties
    request.tenant = tenant
    
    # Set credentials
    force_authenticate(request, user=user)
    
    # Instantiate view instance to inspect its query
    view_instance = CommentViewSet()
    view_instance.request = request
    view_instance.format_kwarg = None
    
    token = set_current_tenant(tenant)
    try:
        qs = view_instance.get_queryset()
        filtered_qs = view_instance.filter_queryset(qs)
        print("ViewSet get_queryset() SQL:", qs.query)
        print("ViewSet filter_queryset() SQL:", filtered_qs.query)
        print("ViewSet filtered queryset exists:", filtered_qs.filter(pk=comment_id).exists())
    finally:
        reset_current_tenant(token)

    # Instantiate view
    view = CommentViewSet.as_view({'post': 'react'})
    
    try:
        # Wrap request in tenant context just like middleware does
        token = set_current_tenant(tenant)
        try:
            response = view(request, pk=comment_id)
            print("Response Status Code:", response.status_code)
            print("Response Data:", response.data)
        finally:
            reset_current_tenant(token)
    except Exception as e:
        print("Exception raised during view execution:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    diagnose()
