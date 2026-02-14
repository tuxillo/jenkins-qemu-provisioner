FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY control_plane /app/control_plane
COPY node_agent /app/node_agent
COPY fake_node_agent /app/fake_node_agent

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "control_plane.main:app", "--host", "0.0.0.0", "--port", "8000"]
