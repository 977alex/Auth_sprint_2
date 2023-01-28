redis_host = os.getenv("REDIS_AUTH_HOST", "redis_auth")
redis_port = int(os.getenv("REDIS_AUTH_PORT", 6479))
redis_password = os.getenv("REDIS_AUTH_PASSWORD", "superpassword")

admin_user.password = os.getenv("ADMIN_PASSWORD")
regular_user.password = os.getenv("NOBODY_PASSWORD")