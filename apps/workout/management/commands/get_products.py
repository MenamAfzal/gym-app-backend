from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
import requests
import time

try:
    from apps.authentication.services.mindbody import MindbodyAPI
except ImportError:
    class MindbodyAPI:
        def __init__(self):
            self.base_url = getattr(settings, 'MINDBODY_BASE_URL', 'https://api.mindbodyonline.com/0_5')
            self.site_id = getattr(settings, 'MINDBODY_SITE_ID', '')
            self.username = getattr(settings, 'MINDBODY_USERNAME', '')
            self.password = getattr(settings, 'MINDBODY_PASSWORD', '')
            self.api_key = getattr(settings, 'MINDBODY_API_KEY', '')


class Command(BaseCommand):
    help = "Fetch and store products from Mindbody API into the Product model (bulk update)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting Mindbody product synchronization..."))

        mindbody_api = MindbodyAPI()
        token_url = f"{mindbody_api.base_url}/usertoken/issue"
        payload = {
            "Username": settings.EVAN_EMAIL,
            "Password": settings.EVAN_PASSWORD
        }
        headers = mindbody_api._get_headers()

        try:
            resp = requests.post(token_url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            token = resp.json().get("AccessToken")
            if not token:
                self.stderr.write(self.style.ERROR("AccessToken missing in Mindbody response"))
                return
            headers = mindbody_api._get_headers(f"Bearer {token}")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to authenticate with Mindbody: {e}"))
            return

        products_data = []
        offset, limit, max_records = 0, 200, 5000

        while offset < max_records:
            params = {
                "request.limit": limit,
                "request.offset": offset,
            }
            try:
                resp = requests.get(
                    f"{mindbody_api.base_url}/sale/products",
                    headers=headers,
                    params=params,
                    timeout=30
                )
                resp.raise_for_status()
                batch = resp.json().get("Products", [])
                if not batch:
                    break
                products_data.extend(batch)
                self.stdout.write(f"[PAGE] Retrieved {len(batch)} products (offset {offset})")
                if len(batch) < limit or len(products_data) >= max_records:
                    break
                offset += limit
                time.sleep(0.2)
            except requests.exceptions.RequestException as e:
                self.stderr.write(self.style.ERROR(f"Error fetching products at offset {offset}: {e}"))
                break

        self.stdout.write(self.style.SUCCESS(f"Total fetched: {len(products_data)} products."))

        if not products_data:
            self.stdout.write(self.style.WARNING("No products found. Exiting."))
            return

        product_objs = []
        existing_products = Product.objects.in_bulk(
            [p.get("ProductId") for p in products_data], field_name="product_id"
        )

        for p in products_data:
            obj = existing_products.get(p.get("ProductId"), Product(product_id=p.get("ProductId")))
            obj.barcode = p.get("Id")
            obj.group_id = p.get("GroupId")
            obj.category_id = p.get("CategoryId")
            obj.sub_category_id = p.get("SubCategoryId")
            obj.secondary_category_id = p.get("SecondaryCategoryId")
            obj.price = p.get("Price")
            obj.online_price = p.get("OnlinePrice")
            obj.tax_included = p.get("TaxIncluded")
            obj.tax_rate = p.get("TaxRate")
            obj.name = p.get("Name")
            obj.short_description = p.get("ShortDescription")
            obj.long_description = p.get("LongDescription")
            obj.type_group = p.get("TypeGroup")
            obj.supplier_id = p.get("SupplierId")
            obj.supplier_name = p.get("SupplierName")
            obj.manufacturer_id = p.get("ManufacturerId")
            obj.image_url = p.get("ImageURL")
            obj.color_id = (p.get("Color") or {}).get("Id")
            obj.color_name = (p.get("Color") or {}).get("Name")
            obj.size_id = (p.get("Size") or {}).get("Id")
            obj.size_name = (p.get("Size") or {}).get("Name")
            product_objs.append(obj)

        with transaction.atomic():
            Product.objects.bulk_create(
                [p for p in product_objs if p.pk is None],
                batch_size=500,
                ignore_conflicts=True
            )
            Product.objects.bulk_update(
                [p for p in product_objs if p.pk is not None],
                fields=[
                    "barcode", "group_id", "category_id", "sub_category_id", "secondary_category_id",
                    "price", "online_price", "tax_included", "tax_rate", "name", "short_description",
                    "long_description", "type_group", "supplier_id", "supplier_name", "manufacturer_id",
                    "image_url", "color_id", "color_name", "size_id", "size_name"
                ],
                batch_size=500
            )

        self.stdout.write(self.style.SUCCESS(
            f"Bulk product sync complete. Inserted {len([p for p in product_objs if p.pk is None])}, "
            f"Updated {len([p for p in product_objs if p.pk is not None])}."
        ))
