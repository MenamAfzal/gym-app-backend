from django.contrib import admin
from .models import Photo, Video, Poll, SocialPost, PostMedia


admin.site.site_header = "Social Network Admin"

admin.site.register(Photo)
admin.site.register(Video)
admin.site.register(Poll)
admin.site.register(SocialPost)
admin.site.register(PostMedia)