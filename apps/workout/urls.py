from django.urls import path, include
from .views import (
    BulkExerciseVideoUploadView, ExerciseAlternativeRecommendationView,
    LogExerciseSubstitutionView, MusicPlaylistDetailView, MusicPlaylistListCreateView,
    ProductListCreateAPIView, SongDetailView, StaffSubstitutionHistoryView,
    UserSubstitutionHistoryView, WorkoutExerciseUpdateCreate, ExerciseFilterAPIView
)
from . import views

urlpatterns = [
    path('products/', ProductListCreateAPIView.as_view(), name='product-list-create'),
    path("workouts/", views.WorkoutAPIView.as_view(), name="workouts"),   
    path("today/", views.TodayWorkoutAPIView.as_view(), name="today-workout"),   
    path("log-weight/", views.LogWeightAPIView.as_view(), name="log-weight"),
    path("log-completion/", views.LogCompletionAPIView.as_view(), name="log-completion"),
    path("workouts/favorites/", views.FavoriteWorkoutAPIView.as_view(), name="favorites"),
    path("staff/workouts/", views.StaffCreatedWorkoutsAPIView.as_view(), name="staff-created-workouts"),
    path("staff/client-workout-logs/", views.StaffClientWorkoutLogListAPIView.as_view(), name="staff-client-workout-logs"),
    path('playlists/', MusicPlaylistListCreateView.as_view(), name='playlist-list-create'),
    path('playlists/<int:pk>/', MusicPlaylistDetailView.as_view(), name='playlist-detail'),
    path('songs/<int:pk>/', SongDetailView.as_view(), name='song-detail'),
    path("staff/workouts/create/", views.CreateWorkoutAPIView.as_view(), name="create-workout"),
    path("staff/workouts/<int:pk>/", views.WorkoutDetailAPIView.as_view(), name="workout-detail"),
    path("import-firestore/", views.FirestoreImportView.as_view(), name="import-firestore"),
    path("lookup-data/", views.LookupDataAPIView.as_view(), name="lookup-data"),
    path("myzone/moves/", views.UserMovesView.as_view(), name="user-moves"),
    path('<int:pk>/edit/', views.WorkoutEditAPIView.as_view(), name='workout-update'),
    path('multi-level-workout-create/', views.MultipleCreateWorkoutAPIView.as_view(), name='multi-level-workout-create'),
    path('multi-level-workout-list/', views.StaffCreatedWorkoutsAPIView.as_view(), name='multi-level-workout-list'),
    path("excercises/like/", views.LikeExerciseAPIView.as_view(), name="like-exercise"),
    path("clone/workout/", views.DeepCloneBaseWorkoutAPIView.as_view(), name="deep-clone-workout"),
    path("liked/exercises/", views.LikeExerciseAPIView.as_view(), name="liked-exercises"),
    path("deck-of-cards/create/", views.MultiLevelDeckWorkoutAPIView.as_view(), name="deck-of-cards-workouts"),
    path("deck-of-cards/<int:pk>/update/", views.DeckOfCardsWorkoutUpdateAPIView.as_view(), name="deck-of-cards-workout-update"),
    path('exercises/<int:pk>/', WorkoutExerciseUpdateCreate.as_view(), name='exercise-update'),
    path('add-exercises/', WorkoutExerciseUpdateCreate.as_view(), name='exercise-create'),
    path('delete-exercises/<int:pk>/', WorkoutExerciseUpdateCreate.as_view(), name='exercise-delete'),
    path('exercises/filter/', ExerciseFilterAPIView.as_view(), name='exercise-filter'),
    path('exercises/bulk-video-upload/', BulkExerciseVideoUploadView.as_view(), name='bulk-video-upload'),
    path('alternatives/<int:exercise_id>/', ExerciseAlternativeRecommendationView.as_view(), name='exercise-alternatives'),
    path('exercises/log-substitution/', LogExerciseSubstitutionView.as_view(), name='log-substitution'),
    path('exercises/substitutions/all/', StaffSubstitutionHistoryView.as_view(), name='staff-substitution-history'),
    path('exercises/substitutions/me/', UserSubstitutionHistoryView.as_view(), name='user-substitution-history'),
]