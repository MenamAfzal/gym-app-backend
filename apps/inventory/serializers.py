from rest_framework import serializers
from .models import Product, StockTransaction
from apps.users.serializers import UserSerializer

class StockTransactionSerializer(serializers.ModelSerializer):
    handled_by_details = UserSerializer(source='handled_by', read_only=True)

    class Meta:
        model = StockTransaction
        fields = ['id', 'product', 'quantity', 'transaction_type', 'handled_by', 'handled_by_details', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at', 'handled_by']

class ProductSerializer(serializers.ModelSerializer):
    current_stock = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        fields = ['id', 'name', 'sku', 'price', 'location', 'current_stock', 'created_at', 'updated_at']
        read_only_fields = ['id', 'current_stock', 'created_at', 'updated_at']

    def get_current_stock(self, obj):
        if hasattr(obj, 'annotated_stock') and obj.annotated_stock is not None:
            return obj.annotated_stock
        return obj.current_stock
