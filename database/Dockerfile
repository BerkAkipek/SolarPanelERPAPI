FROM python:3.12-slim
WORKDIR /workspace
COPY database/requirements.txt /workspace/database/requirements.txt
RUN pip install --no-cache-dir -r database/requirements.txt
COPY alembic.ini /workspace/alembic.ini
COPY database /workspace/database
COPY app /workspace/app
COPY tests /workspace/tests
COPY scripts /workspace/scripts
CMD ["alembic", "upgrade", "head"]
