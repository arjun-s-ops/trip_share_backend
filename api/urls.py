from django.urls import path
from . import views

urlpatterns = [
    # Auth & Profile
    path('signup/', views.signup),
    path('login/', views.login_view),
    path('profile/', views.user_profile),
    
    # Trip Creation Flow
    path('savetrip/trip/', views.save_trip),
    path('savetrip/route/', views.save_route),
    path('savetrip/payment/', views.save_payment),
    path('savetrip/contact/', views.save_contact),
    
    # Data Retrieval & Interaction
    path('savetrip/my-trips/', views.get_user_trips),
    path('trips/search/', views.search_trips),
    path('trips/join/confirm/', views.confirm_join),
]