from django.contrib import admin
from .models import Photo, Video, Poll


admin.site.site_header = "Social Network Admin"

admin.site.register(Photo)
admin.site.register(Video)
admin.site.register(Poll)