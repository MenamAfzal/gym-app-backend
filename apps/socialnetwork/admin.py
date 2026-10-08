from django.contrib import admin
from .models import Photo, Video, Poll, SocialPost, PostMedia, UserBlock, PostReport


admin.site.site_header = "Social Network Admin"

admin.site.register(Photo)
admin.site.register(Video)
admin.site.register(Poll)
admin.site.register(SocialPost)
admin.site.register(PostMedia)


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ('id', 'blocker', 'blocked', 'tenant', 'created_at')
    search_fields = ('blocker__email', 'blocked__email', 'reason')
    list_filter = ('created_at',)


@admin.register(PostReport)
class PostReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'reporter', 'reported_user', 'reason', 'status', 'action_taken', 'tenant', 'created_at')
    search_fields = ('reporter__email', 'reported_user__email', 'description', 'admin_notes')
    list_filter = ('status', 'reason', 'action_taken', 'created_at')