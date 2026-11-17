FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY runner.py /opt/ada/runner.py
USER 65534:65534
ENTRYPOINT ["python3", "-I", "/opt/ada/runner.py"]
