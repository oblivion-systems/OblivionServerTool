FROM ubuntu:22.04

# Prevent interactive apt prompts during build
ENV DEBIAN_FRONTEND=noninteractive

# i386 support — required by SteamCMD and CS2 server runtime
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y \
        python3 python3-pip \
        # SteamCMD and CS2 dedicated server runtime dependencies
        lib32gcc-s1 libstdc++6 libstdc++6:i386 \
        # iproute2 provides `ss`, used by platform._listeners_linux()
        iproute2 \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cached unless requirements change)
COPY requirements-headless.txt .
RUN pip3 install --no-cache-dir -r requirements-headless.txt

# Copy app source
COPY . .

# App config lives here (platform.app_data_dir() reads XDG_CONFIG_HOME on Linux)
ENV XDG_CONFIG_HOME=/config
VOLUME ["/config"]

# CS2 server installation directory (set server_dir to /srv/cs2 in the web UI)
VOLUME ["/srv/cs2"]

# Web panel (default Flask port — operator can change via flask_port in config)
EXPOSE 5050
# CS2 RCON (TCP) + game traffic (UDP) + secondary game port
EXPOSE 27015/tcp
EXPOSE 27015/udp
EXPOSE 27016/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/api/state')" || exit 1

CMD ["python3", "main.py", "--headless"]
