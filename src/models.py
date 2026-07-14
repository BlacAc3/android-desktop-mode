from dataclasses import dataclass

_PACKAGE_JUNK_SEGMENTS = {
    "com",
    "org",
    "net",
    "io",
    "co",
    "app",
    "apps",
    "android",
    "inc",
    "corp",
    "ltd",
    "mobile",
    "client",
    "prod",
    "release",
}

@dataclass
class AndroidApp:
    package: str
    label: str
    app_type: str  # "user" or "system"
