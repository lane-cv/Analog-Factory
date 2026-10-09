FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

# Snapshot the entire dependency closure, not just the top-level package names.
ARG UBUNTU_SNAPSHOT=20261007T000000Z
ENV DEBIAN_FRONTEND=noninteractive
# Ubuntu's minimal image has no TLS CA store. The validation driver copies the
# host's public trust bundle and records its digest; no private keys are copied.
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN apt-get update --snapshot "$UBUNTU_SNAPSHOT" && \
    apt-get install -y --no-install-recommends --snapshot "$UBUNTU_SNAPSHOT" \
    ca-certificates git python3 python3-venv build-essential flex bison pkg-config \
      tcl8.6-dev tk8.6-dev libx11-dev libxext-dev libxrender-dev libxpm-dev \
      libcairo2-dev libjpeg-dev libxaw7-dev libreadline-dev zlib1g-dev \
      ngspice netgen-lvs xvfb xauth fonts-dejavu-core zstd nodejs && \
    dpkg-query -W > /opt/ubuntu24-packages.txt && \
    rm -rf /var/lib/apt/lists/*
COPY build_tools.py sources.json /opt/build/
COPY archives/ /opt/build/archives/
RUN python3 /opt/build/build_tools.py && rm -rf /opt/build/work
RUN apt-get update --snapshot "$UBUNTU_SNAPSHOT" && \
    apt-get install -y --no-install-recommends --snapshot "$UBUNTU_SNAPSHOT" python3-yaml && \
    dpkg-query -W > /opt/ubuntu24-packages.txt && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/eda/bin:${PATH}"
WORKDIR /app
CMD ["python3", "-m", "analog_factory", "--workspace", "/workspace", "serve"]
