import sys
import os

# Add application directory to sys.path for Hostinger Phusion Passenger
app_dir = os.path.dirname(os.path.abspath(__file__))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from app import create_app

# Hostinger's WSGI server expects 'application'
application = create_app('production')

if __name__ == "__main__":
    application.run()
