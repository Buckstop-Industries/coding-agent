# Stage 1: Runtime Setup
FROM node:20-slim AS base

# Install Python and other system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    git \
    docker.io \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install Gemini CLI and Maestro globally
RUN npm install -g @google/gemini-cli@latest

# Install gitlab-ci-local
RUN npm install -g gitlab-ci-local

# Install MCP Servers (these can be added as "plugins" here)
# For now, we install common ones as part of the core fleet
RUN npm install -g @structured-world/gitlab-mcp \
    @sentry/mcp-server \
    @roychri/mcp-server-asana

# Create a non-root user
RUN groupadd -g 1001 agent && \
    useradd -u 1001 -g agent -m -s /bin/bash agent && \
    usermod -aG docker agent

# Workspace directory
WORKDIR /workspace
RUN chown -R agent:agent /workspace

# Set up Python environment for app.py
USER agent
ENV VIRTUAL_ENV=/home/agent/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install Python dependencies (Bolt)
COPY --chown=agent:agent app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy application code
COPY --chown=agent:agent app/ /home/agent/app/
COPY --chown=agent:agent GEMINI.md /workspace/GEMINI.md

# Application entry point
CMD ["python", "/home/agent/app/app.py"]
