FROM hpretl/iic-osic-tools:2026.08
USER root
RUN apt-get update && apt-get install -y --no-install-recommends fluxbox x11vnc && rm -rf /var/lib/apt/lists/*
USER 1000:1000
WORKDIR /app
ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["exec .venv/bin/python scripts/run_deployment.py --config .tools/deployment/config.json"]
