import os
import sys

# Set up paths to ensure the project modules can be loaded
sys.path.insert(0, os.path.dirname(__file__))

# Set the settings module for the Django project
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'starlink_project.settings')

# Expose the WSGI application object for Passenger
from starlink_project.wsgi import application
