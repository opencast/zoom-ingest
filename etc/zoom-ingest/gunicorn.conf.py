# Gunicorn configuration for pyCA user interface
#
# For details of the available optiuons see:
# https://docs.gunicorn.org/en/stable/settings.html#settings

# The socket to bind.
# This can be a TCP socket:
#   bind = "127.0.0.1:8000"
# …or a UNIX socket:
#   bind = "unix:/var/run/pyca/uisocket"
#
# Default: "127.0.0.1:8000"
bind = "0.0.0.0:8000"

# The number of worker processes for handling requests.
# Default: 2
workers = 2

# The numer of seconds to wait for a worker to finish processing
# This timeout may seem large, but setting it smaller will cause
# CRITICAL WORKER TIMEOUT exceptions due to optimistic connections
# from your browser
timeout = 200

# Preload to run a single copy of the background tasks, rather than
# multiple (conflicting) copies
preload = True
