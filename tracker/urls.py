from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='tracker/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    
    # Client actions
    path('client/add/', views.add_client, name='add_client'),
    path('client/<int:pk>/', views.client_detail, name='client_detail'),
    path('client/<int:pk>/update/', views.update_client, name='update_client'),
    path('client/<int:pk>/delete/', views.delete_client, name='delete_client'),
    
    # Credential actions
    path('credential/add/', views.add_credential, name='add_credential'),
    path('credential/<int:pk>/update/', views.update_credential, name='update_credential'),
    path('credential/<int:pk>/delete/', views.delete_credential, name='delete_credential'),
    
    # Starlink actions
    path('starlink/add/', views.add_starlink, name='add_starlink'),
    path('starlink/<int:pk>/update/', views.update_starlink, name='update_starlink'),
    path('starlink/<int:pk>/delete/', views.delete_starlink, name='delete_starlink'),
    path('starlink/<int:pk>/transfer/', views.transfer_starlink, name='transfer_starlink'),
    
    # Account actions
    path('account/add/', views.add_account, name='add_account'),
    path('account/<int:pk>/update/', views.update_account, name='update_account'),
    path('account/<int:pk>/delete/', views.delete_account, name='delete_account'),
    path('account/<int:pk>/pay/', views.mark_account_paid, name='mark_account_paid'),
    
    # Stats / Analytics dashboard
    path('stats/', views.stats_dashboard, name='stats_dashboard'),
    
    # Audit Logs
    path('logs/', views.audit_logs_page, name='audit_logs_page'),
]
