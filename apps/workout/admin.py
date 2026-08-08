from django.contrib import admin
 
from .models import Product, Workout, FavoriteWorkout, WeightEntry, WorkoutLog, WorkoutExercise, WorkoutTag, Equipment, \
    MusicPlaylist, Song, Exercise, WorkoutGroup, LikedExercise
 
  
admin.site.register(Product)
admin.site.register(Workout)
admin.site.register(FavoriteWorkout)
admin.site.register(WeightEntry)
admin.site.register(WorkoutLog)
admin.site.register(WorkoutExercise)
admin.site.register(WorkoutTag)
admin.site.register(Equipment)
 
admin.site.register(MusicPlaylist)
admin.site.register(Song)
 
admin.site.register(Exercise)
admin.site.register(WorkoutGroup)
admin.site.register(LikedExercise)

 
