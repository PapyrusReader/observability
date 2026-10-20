# Papyrus observability

Own shared edge routing and production observability, not application code.
Default integration branch: development. Production branch: master.
Keep image tags and digests pinned. Preserve project names, networks, volumes,
and the existing edge installation directory. Never log secrets or commit .env.
Run python3 tools/check.py and python3 -m unittest discover -s tests.
Validate Linux host collectors on Linux, not the Docker Desktop guest.
Only edge publishes ports. Grafana is the only publicly routed monitoring service.
Use isolated disposable databases in tests. Never reset production data.
