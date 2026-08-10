"""Static executable contract for the production-only Compose boundary."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"
EXPECTED_CORE_SERVICES = {
    "nginx",
    "web",
    "api",
    "worker",
    "scheduler",
    "postgres",
    "redis",
    "minio",
    "clamav",
}
EXPECTED_SERVICES = EXPECTED_CORE_SERVICES | {"minio-init"}
EXTERNAL_IMAGE_REPOSITORIES = {
    "nginx": "nginx",
    "postgres": "postgres",
    "redis": "redis",
    "minio": "quay.io/minio/minio",
    "minio-init": "quay.io/minio/mc",
    "clamav": "clamav/clamav",
}
DIGEST = re.compile(r"^(?P<repository>[^:@\s]+(?:/[^:@\s]+)*)@sha256:[0-9a-f]{64}$")


def _compose() -> dict[str, Any]:
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _command(service: dict[str, Any]) -> str:
    command = service.get("command", "")
    return " ".join(str(part) for part in command) if isinstance(command, list) else str(command)


def _published_host_ports(service: dict[str, Any]) -> set[int]:
    ports: set[int] = set()
    for declaration in service.get("ports", []):
        if isinstance(declaration, str):
            host_port = declaration.rsplit(":", maxsplit=1)[0].rsplit(":", maxsplit=1)[-1]
            ports.add(int(host_port))
        elif isinstance(declaration, dict):
            ports.add(int(declaration["published"]))
        else:
            raise TypeError(f"unsupported port declaration: {declaration!r}")
    return ports


def test_production_compose_has_exact_service_boundary_and_only_https_published() -> None:
    services = _compose()["services"]

    assert set(services) == EXPECTED_SERVICES
    assert _published_host_ports(services["nginx"]) == {443}
    for name in EXPECTED_SERVICES - {"nginx"}:
        assert _published_host_ports(services[name]) == set()
    assert "9001" not in _command(services["minio"])


def test_external_images_are_registry_pinned_by_sha256_with_approved_versions() -> None:
    services = _compose()["services"]
    expected_tags = {
        "postgres": "postgres:18-alpine",
        "redis": "redis:8-alpine",
        "minio": "quay.io/minio/minio:RELEASE.2025-06-13T11-33-47Z",
        "minio-init": "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z",
        "clamav": "clamav/clamav:1.5.3",
    }
    compose_source = COMPOSE_PATH.read_text(encoding="utf-8")

    for service_name, repository in EXTERNAL_IMAGE_REPOSITORIES.items():
        image = services[service_name]["image"]
        match = DIGEST.fullmatch(image)
        assert match is not None, f"{service_name} is not pinned by sha256"
        assert match.group("repository") == repository
        if service_name == "nginx":
            assert re.search(
                r"(?m)^\s*image: nginx@sha256:[0-9a-f]{64}\s+# nginx:\d+\.\d+-alpine$",
                compose_source,
            )
        else:
            assert f"# {expected_tags[service_name]}" in compose_source


def test_minio_bucket_is_idempotently_initialized_before_storage_consumers() -> None:
    services = _compose()["services"]
    initializer = services["minio-init"]
    command = _command(initializer)

    assert initializer["restart"] == "no"
    assert initializer["networks"] == ["backend"]
    assert _published_host_ports(initializer) == set()
    assert initializer["depends_on"] == {"minio": {"condition": "service_healthy"}}
    assert "mc alias set local http://minio:9000" in command
    assert "mc mb --ignore-existing" in command
    assert "$${MINIO_ROOT_USER}" in command
    assert "$${MINIO_ROOT_PASSWORD}" in command
    assert "$${MINIO_BUCKET}" in command
    assert initializer["environment"] == {
        "MINIO_ROOT_USER": "${SUPERBOSS_S3_ACCESS_KEY_ID:?set SUPERBOSS_S3_ACCESS_KEY_ID}",
        "MINIO_ROOT_PASSWORD": (
            "${SUPERBOSS_S3_SECRET_ACCESS_KEY:?set SUPERBOSS_S3_SECRET_ACCESS_KEY}"
        ),
        "MINIO_BUCKET": "${SUPERBOSS_S3_BUCKET:-superboss-files}",
    }
    for consumer in ("api", "worker"):
        assert services[consumer]["depends_on"]["minio-init"]["condition"] == (
            "service_completed_successfully"
        )


def test_application_images_are_multi_stage_and_run_as_non_root() -> None:
    dockerfiles = {
        "server": (ROOT / "server" / "Dockerfile").read_text(encoding="utf-8"),
        "web": (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8"),
    }

    for name, source in dockerfiles.items():
        assert len(re.findall(r"(?im)^FROM\s+", source)) >= 2, f"{name} is not multi-stage"
        users = re.findall(r"(?im)^USER\s+([^\s#]+)", source)
        assert users, f"{name} has no explicit runtime USER"
        assert users[-1].lower() not in {"0", "root"}

    services = _compose()["services"]
    assert services["api"]["build"]["context"] == "./server"
    assert services["worker"]["build"] == services["api"]["build"]
    assert services["scheduler"]["build"] == services["api"]["build"]
    assert services["web"]["build"]["context"] == "./web"


def test_application_base_images_are_digest_pinned_with_readable_tags() -> None:
    expected_tags = {
        "server": {"python:3.13-slim"},
        "web": {"node:24-alpine"},
    }
    for name, tags in expected_tags.items():
        source = (ROOT / name / "Dockerfile").read_text(encoding="utf-8")
        from_images = re.findall(
            r"(?im)^FROM\s+([^:@\s]+(?:/[^:@\s]+)*)@sha256:[0-9a-f]{64}(?:\s+AS\s+\w+)?$",
            source,
        )
        assert from_images
        assert len(from_images) == len(re.findall(r"(?im)^FROM\s+", source))
        for tag in tags:
            assert f"# {tag}" in source
        if name == "web":
            assert re.search(r"(?m)^# nginxinc/nginx-unprivileged:\d+\.\d+-alpine$", source)


def test_web_runtime_is_non_root_spa_server_on_internal_port_8080() -> None:
    config_path = ROOT / "web" / "nginx.conf"
    assert config_path.is_file()
    config = config_path.read_text(encoding="utf-8")
    dockerfile = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s*listen 8080;\s*$", config)
    assert re.search(r"(?m)^\s*root /usr/share/nginx/html;\s*$", config)
    assert re.search(r"(?m)^\s*try_files \$uri \$uri/ /index\.html;\s*$", config)
    assert not re.search(r"(?m)^\s*autoindex\s+on;", config)
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in dockerfile


def test_stateful_services_use_named_volumes_and_worker_is_single_concurrency() -> None:
    compose = _compose()
    services = compose["services"]
    declared_volumes = set(compose["volumes"])
    required_mount_targets = {
        "postgres": "/var/lib/postgresql",
        "redis": "/data",
        "minio": "/data",
        "clamav": "/var/lib/clamav",
        "scheduler": "/var/lib/celery",
    }

    for service_name, target in required_mount_targets.items():
        mounts = services[service_name]["volumes"]
        matching = [str(mount) for mount in mounts if str(mount).endswith(f":{target}")]
        assert len(matching) == 1
        volume_name = matching[0].split(":", maxsplit=1)[0]
        assert volume_name in declared_volumes

    worker_command = _command(services["worker"])
    assert "--queues=file-scan,file-maintenance" in worker_command
    assert "--concurrency=1" in worker_command
    assert "--prefetch-multiplier=1" in worker_command
    assert "beat" in _command(services["scheduler"])


def test_health_and_dependency_ordering_preserve_task8_worker_requirements() -> None:
    services = _compose()["services"]

    for dependency in ("postgres", "redis", "minio", "clamav"):
        assert services[dependency]["healthcheck"]
        assert services["worker"]["depends_on"][dependency]["condition"] == "service_healthy"
    assert services["scheduler"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["minio"]["condition"] == "service_healthy"
    assert "redis" not in services["api"]["depends_on"]
    assert "clamav" not in services["api"]["depends_on"]
    assert services["nginx"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["nginx"]["depends_on"]["web"]["condition"] == "service_healthy"


def test_frontend_build_receives_the_exact_public_object_storage_origin() -> None:
    services = _compose()["services"]

    assert services["web"]["build"]["args"] == {
        "VITE_OBJECT_STORAGE_ORIGIN": "https://${SUPERBOSS_OBJECTS_HOST:?set SUPERBOSS_OBJECTS_HOST}"
    }
    public_object_origin = "https://${SUPERBOSS_OBJECTS_HOST:?set SUPERBOSS_OBJECTS_HOST}"
    assert services["api"]["environment"]["SUPERBOSS_S3_ENDPOINT_URL"] == "http://minio:9000"
    assert (
        services["api"]["environment"]["SUPERBOSS_S3_PUBLIC_ENDPOINT_URL"]
        == public_object_origin
    )
    assert (
        "${SUPERBOSS_OBJECTS_HOST:?set SUPERBOSS_OBJECTS_HOST}"
        in services["nginx"]["networks"]["backend"]["aliases"]
    )


def test_production_environment_example_contains_no_secret_values() -> None:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", maxsplit=1)
        values[key] = value

    assert values["SUPERBOSS_ENVIRONMENT"] == "production"
    assert values["SUPERBOSS_APP_HOST"] == "nightforest.com"
    assert values["SUPERBOSS_OBJECTS_HOST"] == "objects.nightforest.com"
    assert values["SUPERBOSS_POSTGRES_DB"] == "superboss"
    assert values["SUPERBOSS_POSTGRES_USER"] == "superboss"
    assert values["SUPERBOSS_S3_BUCKET"] == "superboss-files"
    for sensitive_name in (
        "SUPERBOSS_POSTGRES_PASSWORD",
        "SUPERBOSS_REDIS_PASSWORD",
        "SUPERBOSS_JWT_SECRET",
        "SUPERBOSS_WECOM_CORP_SECRET",
        "SUPERBOSS_S3_ACCESS_KEY_ID",
        "SUPERBOSS_S3_SECRET_ACCESS_KEY",
        "SUPERBOSS_TLS_CERT_PATH",
        "SUPERBOSS_TLS_KEY_PATH",
        "SUPERBOSS_ALLOWLIST_PATH",
    ):
        assert values[sensitive_name] == ""
    rendered = "\n".join(values.values()).lower()
    assert "development-only" not in rendered
    assert "change_me" not in rendered
    assert "changeme" not in rendered
    assert not re.search(r"\b(?:10|127)\.\d+\.\d+\.\d+\b", rendered)
    assert not re.search(r"\b192\.168\.\d+\.\d+\b", rendered)


def test_nginx_hosts_share_tls_allowlist_security_and_proxy_boundaries() -> None:
    nginx = (ROOT / "ops" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    virtual_hosts = (ROOT / "ops" / "nginx" / "conf.d" / "superboss.conf").read_text(
        encoding="utf-8"
    )
    allowlist = (ROOT / "ops" / "nginx" / "allowlist.conf.example").read_text(encoding="utf-8")
    nginx_service = _compose()["services"]["nginx"]

    assert "include /etc/nginx/conf.d/superboss.conf;" in nginx
    assert "include /etc/nginx/conf.d/*.conf;" not in nginx
    assert "default.conf" not in nginx

    assert any(
        str(mount).endswith(
            ":/etc/nginx/templates/superboss.conf.template:ro"
        )
        for mount in nginx_service["volumes"]
    )

    assert "${SUPERBOSS_APP_HOST}" in virtual_hosts
    assert "${SUPERBOSS_OBJECTS_HOST}" in virtual_hosts
    assert virtual_hosts.count("include /etc/nginx/allowlist.conf;") == 2
    assert virtual_hosts.count("ssl_protocols TLSv1.2 TLSv1.3;") == 2
    assert virtual_hosts.count("ssl_certificate ") == 2
    assert virtual_hosts.count("ssl_certificate_key ") == 2
    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Content-Security-Policy",
    ):
        assert virtual_hosts.count(f"add_header {header} ") == 2
    for directive in (
        "client_max_body_size",
        "client_body_timeout",
        "proxy_connect_timeout",
        "proxy_read_timeout",
        "proxy_send_timeout",
        "proxy_set_header Host",
        "proxy_set_header X-Real-IP",
        "proxy_set_header X-Forwarded-For",
        "proxy_set_header X-Forwarded-Proto",
    ):
        assert directive in virtual_hosts
    assert "server_tokens off;" in nginx

    assert "Access-Control-Allow-Origin https://${SUPERBOSS_APP_HOST}" in virtual_hosts
    assert "Access-Control-Allow-Methods GET,HEAD,PUT,OPTIONS" in virtual_hosts
    assert "Access-Control-Allow-Headers Content-Type" in virtual_hosts
    assert "Access-Control-Allow-Headers *" not in virtual_hosts
    assert "Access-Control-Expose-Headers ETag" in virtual_hosts
    assert "Access-Control-Allow-Credentials true" not in virtual_hosts
    options = re.search(
        r"if \(\$request_method = OPTIONS\)\s*\{(?P<body>.*?)\}",
        virtual_hosts,
        flags=re.DOTALL,
    )
    assert options is not None
    assert "return 204;" in options.group("body")
    assert "proxy_pass" not in options.group("body")
    assert "proxy_set_header Authorization" not in options.group("body")
    assert "proxy_set_header Cookie" not in options.group("body")

    assert "deny all;" in allowlist
    assert re.search(r"\b192\.0\.2\.\d+(?:/\d+)?\b", allowlist)
    assert not re.search(r"\b(?:10|127)\.\d+\.\d+\.\d+\b", allowlist)
    assert not re.search(r"\b192\.168\.\d+\.\d+\b", allowlist)
    assert not re.search(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b", allowlist)
