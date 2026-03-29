"""Curated package lists for dependency extraction filtering.

These sets are used by extract_dependencies_from_imports() to distinguish
third-party packages from stdlib modules, internal project code, and
well-known libraries.
"""

from __future__ import annotations

# ~120 Python standard library module names (top-level only).
# Source: https://docs.python.org/3/py-modindex.html (Python 3.10+)
PYTHON_STDLIB: set[str] = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
    "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
    "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb",
    "chunk", "cmath", "cmd", "code", "codecs", "codeop", "collections",
    "colorsys", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
    "difflib", "dis", "distutils", "doctest", "email", "encodings",
    "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev",
    "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
    "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
    "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr",
    "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "sqlite3",
    "ssl", "stat", "statistics", "string", "stringprep", "struct",
    "subprocess", "sunau", "symtable", "sys", "sysconfig", "syslog",
    "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token",
    "tokenize", "tomllib", "trace", "traceback", "tracemalloc",
    "tty", "turtle", "turtledemo", "types", "typing", "unicodedata",
    "unittest", "urllib", "uu", "uuid", "venv", "warnings", "wave",
    "weakref", "webbrowser", "winreg", "winsound", "wsgiref",
    "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport",
    "zlib", "_thread",
}

# ~30 Node.js builtin module names.
# Source: https://nodejs.org/api/
NODE_BUILTINS: set[str] = {
    "assert", "async_hooks", "buffer", "child_process", "cluster",
    "console", "constants", "crypto", "dgram", "diagnostics_channel",
    "dns", "domain", "events", "fs", "http", "http2", "https",
    "inspector", "module", "net", "os", "path", "perf_hooks",
    "process", "punycode", "querystring", "readline", "repl",
    "stream", "string_decoder", "timers", "tls", "trace_events",
    "tty", "url", "util", "v8", "vm", "wasi", "worker_threads",
    "zlib", "node:fs", "node:path", "node:http", "node:https",
    "node:crypto", "node:os", "node:url", "node:util", "node:stream",
    "node:events", "node:child_process", "node:buffer", "node:net",
    "node:zlib", "node:readline", "node:assert", "node:test",
}

# Common internal/project-local module names that are NOT third-party packages.
COMMON_INTERNAL_NAMES: set[str] = {
    "backend", "frontend", "utils", "shared", "core", "api", "lib",
    "test", "tests", "config", "helpers", "common", "models", "views",
    "controllers", "services", "schemas", "routes", "middleware",
    "handlers", "components", "pages", "layouts", "hooks", "store",
    "stores", "types", "interfaces", "constants", "assets", "styles",
    "database", "db", "migrations", "seeders", "fixtures", "factories",
    "plugins", "modules", "providers", "guards", "interceptors",
    "decorators", "pipes", "filters", "entities", "repositories",
    "adapters", "ports", "domain", "infra", "infrastructure",
    "presentation", "application", "server", "client", "app",
    "internal", "pkg", "cmd", "tools", "scripts", "setup",
    "settings", "conf", "main", "index", "src",
}

# ~200 well-known third-party packages across ecosystems.
# Names here always pass filtering even if heuristics would reject them.
WELL_KNOWN_PACKAGES: set[str] = {
    # Python
    "pydantic", "fastapi", "flask", "django", "requests", "httpx",
    "aiohttp", "starlette", "uvicorn", "gunicorn", "celery",
    "sqlalchemy", "alembic", "tortoise", "peewee", "pymongo",
    "motor", "redis", "aioredis", "boto3", "botocore",
    "numpy", "pandas", "scipy", "matplotlib", "seaborn",
    "scikit-learn", "sklearn", "tensorflow", "torch", "pytorch",
    "transformers", "datasets", "huggingface_hub", "tokenizers",
    "langchain", "openai", "anthropic", "cohere", "replicate",
    "gradio", "streamlit", "dash", "plotly", "bokeh",
    "click", "typer", "rich", "tqdm", "loguru",
    "pytest", "hypothesis", "coverage", "mypy", "ruff",
    "black", "isort", "flake8", "pylint", "bandit",
    "pyyaml", "toml", "tomli", "python-dotenv", "environs",
    "jinja2", "mako", "marshmallow", "attrs", "cattrs",
    "pillow", "opencv-python", "imageio",
    "stripe", "twilio", "sendgrid", "sentry-sdk",
    "cryptography", "paramiko", "fabric",
    "beautifulsoup4", "lxml", "scrapy", "selenium", "playwright",
    "arrow", "pendulum", "dateutil", "pytz",
    # JavaScript / TypeScript
    "react", "react-dom", "next", "vue", "nuxt", "angular",
    "svelte", "solid-js", "preact", "lit",
    "express", "fastify", "koa", "hapi", "nest", "nestjs",
    "axios", "node-fetch", "got", "ky", "superagent",
    "lodash", "underscore", "ramda", "immer",
    "redux", "zustand", "mobx", "jotai", "recoil", "valtio",
    "tailwindcss", "styled-components", "emotion",
    "prisma", "drizzle", "knex", "sequelize", "typeorm", "mongoose",
    "zod", "yup", "joi", "ajv", "io-ts",
    "jest", "vitest", "mocha", "chai", "cypress", "puppeteer",
    "eslint", "prettier", "typescript", "babel", "webpack", "vite",
    "rollup", "esbuild", "tsup", "turbo",
    "socket.io", "ws",
    "three", "d3", "chart.js", "recharts",
    "dayjs", "moment", "date-fns", "luxon",
    "uuid", "nanoid", "cuid",
    "dotenv", "commander", "inquirer", "chalk", "ora",
    "sharp", "jimp", "multer",
    "jsonwebtoken", "passport", "bcrypt", "argon2",
    "nodemailer", "bull", "bullmq",
    "winston", "pino", "morgan", "bunyan",
    # Ruby
    "rails", "sinatra", "hanami", "grape",
    "rspec", "minitest", "capybara", "factory_bot",
    "devise", "pundit", "cancancan",
    "sidekiq", "resque", "delayed_job",
    "pg", "mysql2", "sqlite3", "redis",
    "puma", "unicorn", "thin",
    "nokogiri", "faraday", "httparty", "rest-client",
    "rubocop", "bundler", "rake",
    # Rust
    "serde", "tokio", "reqwest", "clap", "actix-web", "axum",
    "diesel", "sqlx", "sea-orm",
    "tracing", "log", "env_logger",
    "anyhow", "thiserror",
    # Go
    "gin", "echo", "fiber", "chi", "mux",
    "gorm", "ent", "sqlc",
    "cobra", "viper", "zap", "logrus",
}
