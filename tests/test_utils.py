import pytest
from src.utils import clean_app_label, _friendly_from_package

def test_clean_app_label():
    assert clean_app_label("WhatsApp", "com.whatsapp") == "WhatsApp"
    assert clean_app_label("SOME_APP", "com.some.app") == "Some App"
    assert clean_app_label(None, "com.example.app") == "Example App"

def test_friendly_from_package():
    assert _friendly_from_package("com.example.my_cool_app") == "My Cool App"
