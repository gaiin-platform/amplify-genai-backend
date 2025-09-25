"""
Configuration for backend API tests
"""

import os

# API Configuration
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:3016')
TEST_API_KEY = os.getenv('TEST_API_KEY', '')

# Test timeouts
REQUEST_TIMEOUT = 30

# Expected response codes for different scenarios
EXPECTED_SUCCESS_CODES = [200]
EXPECTED_AUTH_ERROR_CODES = [401, 403]
EXPECTED_VALIDATION_ERROR_CODES = [400]