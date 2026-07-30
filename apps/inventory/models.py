from django.db import models
from core_models.mixins.tenant_mixin import TenantMixin
from core_models.mixins.uuid_mixin import UUIDMixin
from core_models.mixins.timestamps import TimestampMixin
from apps.scheduling.models import Location
from apps.users.models import User

class Product(UUIDMixin, TimestampMixin, TenantMixin):
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=100, unique=True, db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='products')

    @property
    def current_stock(self):
        # Calculate current stock by aggregating all transactions
        from apps.core.tenants.context import bypass_tenant_isolation
        with bypass_tenant_isolation():
            stock = self.transactions.aggregate(models.Sum('quantity'))['quantity__sum']
            return stock or 0

    def __str__(self):
        return f"{self.name} ({self.sku})"


class StockTransaction(UUIDMixin, TimestampMixin, TenantMixin):
    TRANSACTION_TYPES = [
        ('restock', 'Restock'),
        ('sale', 'Sale'),
        ('shrinkage', 'Shrinkage')
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='transactions')
    quantity = models.IntegerField(help_text="Positive for restock, negative for sale/shrinkage")
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    handled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='stock_transactions')
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.transaction_type} of {self.quantity} for {self.product.name}"
