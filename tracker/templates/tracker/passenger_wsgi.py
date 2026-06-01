import os
import sys

# Set up paths and environment variables
sys.path.insert(0, os.path.dirname(__file__))

# Replace 'my_project_name' with the directory containing your settings.py and wsgi.py
os.environ['DJANGO_SETTINGS_MODULE'] = 'starlink_project.settings'

from starlink_project.wsgi import application