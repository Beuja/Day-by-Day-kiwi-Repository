from django.urls import path
from . import views

urlpatterns = [
    path('analyze/', views.analyze_text_view, name='analyze_text_view'),
]
