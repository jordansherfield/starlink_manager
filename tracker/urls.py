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
    path('credential/<int:pk>/archive/', views.archive_credential, name='archive_credential'),
    path('credential/<int:pk>/unarchive/', views.unarchive_credential, name='unarchive_credential'),

    # Starlink actions
    path('starlink/add/', views.add_kit, name='add_kit'),
    path('starlink/add/options/', views.kit_form_options, name='kit_form_options'),
    path('starlink/add/check/', views.kit_check, name='kit_check'),
    path('starlink/<int:pk>/update/', views.update_starlink, name='update_starlink'),
    path('starlink/<int:pk>/delete/', views.delete_starlink, name='delete_starlink'),
    path('starlink/<int:pk>/transfer/', views.transfer_starlink, name='transfer_starlink'),
    path('starlink/<int:pk>/archive/', views.archive_starlink, name='archive_starlink'),
    path('starlink/<int:pk>/unarchive/', views.unarchive_starlink, name='unarchive_starlink'),

    # Account actions
    path('account/add/', views.add_account, name='add_account'),
    path('account/<int:pk>/update/', views.update_account, name='update_account'),
    path('account/<int:pk>/delete/', views.delete_account, name='delete_account'),
    path('account/<int:pk>/pay/', views.mark_account_paid, name='mark_account_paid'),
    path('account/pay-day/', views.mark_accounts_paid_for_day, name='mark_accounts_paid_for_day'),
    path('account/<int:pk>/archive/', views.archive_account, name='archive_account'),
    path('account/<int:pk>/unarchive/', views.unarchive_account, name='unarchive_account'),

    # Starlink inbox (new units from the harvest, status mismatches, install queue)
    path('starlink/', views.starlink_inbox, name='starlink_inbox'),
    path('starlink/<int:pk>/assign/', views.assign_starlink, name='assign_starlink'),

    # Service plans
    path('plans/', views.plans_page, name='plans_page'),
    path('plans/add/', views.add_plan, name='add_plan'),
    path('plans/<int:pk>/update/', views.update_plan, name='update_plan'),
    path('plans/<int:pk>/delete/', views.delete_plan, name='delete_plan'),

    # Archived items
    path('archived/', views.archived_items, name='archived_items'),

    # Stats / Analytics dashboard
    path('stats/', views.stats_dashboard, name='stats_dashboard'),
    
    # Audit Logs
    path('logs/', views.audit_logs_page, name='audit_logs_page'),

    # Import / Export
    path('data/', views.import_export_page, name='import_export_page'),
    path('data/import/preview/', views.import_csv_preview, name='import_csv_preview'),
    path('data/import/confirm/', views.import_csv_confirm, name='import_csv_confirm'),
    path('data/import/cancel/', views.import_csv_cancel, name='import_csv_cancel'),
    path('data/export/', views.export_csv_view, name='export_csv_view'),
    
    # Undo / Redo actions
    path('undo/', views.undo_action, name='undo_action'),
    path('redo/', views.redo_action, name='redo_action'),
]
