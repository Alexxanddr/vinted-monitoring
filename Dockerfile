FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 VM_DATABASE=/data/monitor.sqlite3
WORKDIR /app
COPY pyproject.toml uv.lock LICENSE README.md ./
COPY src ./src
# Exported from uv.lock; hashes pin the full runtime dependency graph.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && pip install --no-deps . \
    && groupadd -g 10001 monitor && useradd -u 10001 -g monitor monitor \
    && mkdir /data && chown monitor:monitor /data
USER 10001:10001
EXPOSE 8000
VOLUME /data
ENTRYPOINT ["vinted-monitoring"]
