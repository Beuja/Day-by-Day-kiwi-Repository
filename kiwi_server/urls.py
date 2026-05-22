from django.urls import path, include

urlpatterns = [
    path('api/', include('analyzer.urls')),
    path('api/api/', include('analyzer.urls')),  # Handles double-prefix cases gracefully
    path('', include('analyzer.urls')),          # Shortcut fallback
]
